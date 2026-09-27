"""Company-declared website and email domains from the Estonian register's general-data file (yldandmed).

The official RIK open-data portal publishes `ettevotja_rekvisiidid__yldandmed.json.zip` (one large JSON
array, one element per register entry). Each entry lists the contact means the company declared to the
register (`sidevahendid`). Only active (`lopp_kpv == null`) WWW and EMAIL entries are read, and for EMAIL
only the domain part after `@` is kept: mailbox local-parts, phone and fax numbers are never stored, logged
or snapshotted. Shared free-mail hosts are dropped.

The rows go through the standard ingestion pipeline (permission gate, allowed fields, provenance, snapshots,
idempotency) as enrichment of companies already in the database. Register-declared domains are not used
as identity keys: group companies share them.
"""

import io
import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.base import ConnectorError, FetchedSnapshot
from app.connectors.ee_ariregister import PORTAL, DatasetFile, EeAriregisterFiles
from app.domain.normalize import is_generic_domain, normalize_domain
from app.models import CompanyIdentifier, IngestionRun
from app.services import ingestion

SOURCE_ID = "ee-ariregister"
DATASET = "yldandmed"
GENERAL_NAME = "ettevotja_rekvisiidid__yldandmed.json.zip"
GENERAL_URL = f"{PORTAL}/sites/default/files/avaandmed/{GENERAL_NAME}"
GENERAL_KIND = "general"
PARSER_VERSION = "ee-general-domains-2026.09.1"
QUERY = {"dataset": DATASET, "fields": ["website", "email_domain"]}
_CHUNK = 1 << 22  # characters per read
_SEPARATORS = " \t\r\n,["
_EMAIL_SPLIT = re.compile(r"[;,\s]+")


def cache_dir() -> Path:
    return get_settings().ee_cache_dir.parent / "ee_ariregister_general"


# ------------------------------------------------------------------ streaming parse


def iter_json_array(fh: TextIO, chunk: int = _CHUNK) -> Iterator[Any]:
    """Yield the elements of a top-level JSON array one at a time without loading the whole document."""
    decoder = json.JSONDecoder()
    buf = ""
    pos = 0
    eof = False
    while True:
        while True:
            while pos < len(buf) and buf[pos] in _SEPARATORS:
                pos += 1
            if pos < len(buf) or eof:
                break
            more = fh.read(chunk)
            if not more:
                eof = True
            buf, pos = more, 0
        if pos >= len(buf) or buf[pos] == "]":
            return
        try:
            obj, end = decoder.raw_decode(buf, pos)
        except json.JSONDecodeError as exc:
            if eof:
                raise ConnectorError(f"malformed general-data JSON near character {exc.pos}") from None
            more = fh.read(chunk)
            if not more:
                eof = True
            buf, pos = buf[pos:] + more, 0
            continue
        yield obj
        pos = end


def iter_companies(file: DatasetFile) -> Iterator[dict[str, Any]]:
    try:
        zf = zipfile.ZipFile(file.path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ConnectorError(f"{file.name} is not a readable zip: {exc}") from exc
    with zf:
        members = [i for i in zf.infolist() if i.filename.lower().endswith(".json")]
        if len(members) != 1:
            raise ConnectorError(f"{file.name}: expected exactly one JSON file, found {len(members)}")
        with zf.open(members[0]) as raw:
            for item in iter_json_array(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                if isinstance(item, dict):
                    yield item


def contact_domains(entry: dict[str, Any]) -> dict[str, list[str]]:
    """Active WWW hosts and EMAIL domains (domain part only), normalized, generic hosts dropped."""
    www: list[str] = []
    email: list[str] = []
    general = entry.get("yldandmed") or {}
    for means in general.get("sidevahendid") or []:
        if not isinstance(means, dict) or means.get("lopp_kpv") is not None:
            continue
        kind = means.get("liik")
        value = str(means.get("sisu") or "").strip()
        if not value:
            continue
        found: list[str | None] = []
        if kind == "WWW":
            if "@" in value:
                continue  # an address in the web field is not a website
            found.append(normalize_domain(value))
        elif kind == "EMAIL":
            # Keep only what follows '@'; the local-part is discarded here and never stored.
            found.extend(normalize_domain(p.rsplit("@", 1)[1]) for p in _EMAIL_SPLIT.split(value) if "@" in p)
        else:
            continue
        target = www if kind == "WWW" else email
        for domain in found:
            if domain and not is_generic_domain(domain) and domain not in target:
                target.append(domain)
    return {"www": www, "email": email}


# ------------------------------------------------------------------ pipeline


class _GeneralDataRows:
    """Tiny connector: each snapshot payload already is one canonical enrichment row (domains only)."""

    parser_version = PARSER_VERSION

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        return [dict(snapshot.payload)]


def _row(code: str, domains: dict[str, list[str]], file: DatasetFile) -> dict[str, Any]:
    first_www = domains["www"][0] if domains["www"] else None
    row: dict[str, Any] = {
        "source_key": f"ee-general:{code}",
        "country": "EE",
        "registry_id": code,
        "enrichment_only": True,
        "website": first_www,
        "domain_is_identity": False,
        "email_domain": domains["email"][0] if domains["email"] else None,
        "registry_domains": {"www": list(domains["www"]), "email": list(domains["email"])},
        "source_url": file.url,
        "observed_at": file.last_modified,
    }
    if first_www:
        row["website_verified"] = True  # declared by the company to the official register
    return row


def _known_codes(session: Session) -> set[str]:
    values = session.scalars(
        select(CompanyIdentifier.value).where(
            CompanyIdentifier.kind == "registry_id",
            CompanyIdentifier.derived.is_(False),
            CompanyIdentifier.value.like("EE:%"),
        )
    )
    return {v.split(":", 1)[1] for v in values}


def sync_register_domains(
    session: Session,
    *,
    actor: str,
    live: bool | None = None,
    retry_of_id: str | None = None,
) -> IngestionRun:
    """Import register-declared website/email domains for EE companies already in the database.

    `live=True` downloads (or re-validates) the portal file and needs the live gate; `live=False` uses the
    verified cached copy only. Default: the source's connector_config."""
    from app.services.ee_import import _file_snapshot

    settings = get_settings()
    source = ingestion._registered_or_deny(session, SOURCE_ID, actor)
    run = ingestion._new_run(
        session,
        source,
        SOURCE_ID,
        kind="discovery",
        actor=actor,
        min_employees=settings.min_employees_default,
        query=dict(QUERY),
        retry_of_id=retry_of_id,
    )
    use_live = bool((source.connector_config or {}).get("live")) if live is None else live
    ingestion._gate_or_reject(session, run, source, live=use_live)
    files = EeAriregisterFiles(cache_dir(), live=use_live, rate_limit_per_minute=source.rate_limit_per_minute)
    try:
        file = (
            files.fetch_single(GENERAL_KIND, GENERAL_URL) if use_live else files.resolve_single(GENERAL_NAME)
        )
    except (ConnectorError, OSError, ValueError) as exc:
        return ingestion._fail(session, run, "resolve", str(exc))
    _file_snapshot(session, run, source, file)

    wanted = _known_codes(session)
    snapshots: list[FetchedSnapshot] = []
    seen = with_www = with_email = 0
    try:
        for entry in iter_companies(file):
            code = str(entry.get("ariregistri_kood") or "").strip()
            if code not in wanted:
                continue
            seen += 1
            domains = contact_domains(entry)
            if not (domains["www"] or domains["email"]):
                continue
            with_www += bool(domains["www"])
            with_email += bool(domains["email"])
            snapshots.append(
                FetchedSnapshot(
                    reference=f"ee-general:{code}",
                    payload=_row(code, domains, file),
                    source_url=file.url,
                    http_status=200,
                    request_id=f"file:{file.name}",
                )
            )
    except (ConnectorError, OSError, ValueError, zipfile.BadZipFile) as exc:
        return ingestion._fail(session, run, "parse", str(exc))
    run.counts = {
        **run.counts,
        "discovered": len(snapshots),
        "register_entries_matched": seen,
        "companies_with_www": with_www,
        "companies_with_email_domain": with_email,
    }
    return ingestion._execute(session, run, source, _GeneralDataRows(), snapshots)
