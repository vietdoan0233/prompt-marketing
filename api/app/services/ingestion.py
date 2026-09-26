"""Ingestion pipeline: IMPORT_STARTED -> FETCHED -> PARSED -> VALIDATED -> UPSERTED (or REJECTED/FAILED).

Runs execute inline (synchronously) in this prototype; each stage is recorded on the run so it is
observable and safe to retry. Records are upserted in per-record savepoints.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import active_countries, get_settings
from app.connectors.base import CandidateRef, ConnectorError, FetchedSnapshot
from app.connectors.csv_connector import CsvConnector
from app.connectors.registries import BrregConnector, CvrConnector, PrhConnector, ZefixConnector
from app.connectors.website import WebsiteConnector
from app.domain.normalize import NORMALIZATION_VERSION, normalize_name
from app.domain.records import ParsedContact, ParsedRecord, build_record, stable_hash
from app.models import (
    Company,
    CompanyIdentifier,
    Contact,
    ContactSuppression,
    IngestionRecord,
    IngestionRun,
    Source,
    SourceSnapshot,
    utcnow,
)
from app.services import audit
from app.services.permissions import PermissionDenied, ingestion_gate
from app.services.resolution import (
    ensure_identifiers,
    link_duplicate,
    recompute_company,
    resolve,
    upsert_facts,
)

log = logging.getLogger("mergero.ingestion")

COUNT_KEYS = [
    "discovered",
    "accepted",
    "updated",
    "unchanged",
    "duplicates",
    "rejected",
    "facts_added",
    "facts_changed",
    "contacts_added",
    "contacts_suppressed",
    "duplicate_candidates",
    "qualified",
    "borderline",
    "below_threshold",
    "sub_scale",
    "unknown_headcount",
    "warnings",
]


def contact_identity_hash(name: str, company: Company) -> str:
    return hashlib.sha256(
        f"contact|{normalize_name(name)}|{company.country}|{company.normalized_name}".encode()
    ).hexdigest()


def email_hash(email: str) -> str:
    return hashlib.sha256(f"email|{email.strip().lower()}".encode()).hexdigest()


def build_connector(source: Source):
    settings = get_settings()
    cfg = source.connector_config or {}
    fixture = settings.fixtures_dir / cfg["fixture"] if cfg.get("fixture") else None
    live = bool(cfg.get("live")) and settings.live_connectors_enabled
    if source.connector_type == "csv":
        return CsvConnector(source.field_mapping)
    rate = source.rate_limit_per_minute
    if source.connector_type == "brreg":
        return BrregConnector(fixture, live, rate)
    if source.connector_type == "prh":
        return PrhConnector(fixture, live, rate)
    if source.connector_type == "zefix":
        return ZefixConnector(fixture, live, rate, (settings.zefix_username, settings.zefix_password))
    if source.connector_type == "cvr":
        return CvrConnector(fixture, live, rate, (settings.cvr_username, settings.cvr_password))
    if source.connector_type == "website":
        if not live:
            raise ConnectorError("website crawling requires live: true and LIVE_CONNECTORS_ENABLED=true")
        return WebsiteConnector(rate)
    raise ConnectorError(f"no connector implementation for '{source.connector_type}'")


def website_targets(
    session: Session, source: Source, query: dict[str, Any], min_employees: int
) -> list[dict]:
    """Domains published by approved registries for companies in this source's coverage.
    Only registry-published websites are crawled; the crawler never picks arbitrary URLs."""
    countries = [c for c in (query.get("countries") or source.countries) if c in source.countries]
    stmt = (
        select(Company, CompanyIdentifier.value)
        .join(CompanyIdentifier, CompanyIdentifier.company_id == Company.id)
        .where(
            Company.merged_into_id.is_(None),
            Company.country.in_(countries),
            CompanyIdentifier.kind == "domain",
        )
        .order_by(Company.estimated_employee_min.desc().nulls_last(), Company.legal_name)
    )
    if query.get("qualified_only", True) and min_employees > 0:
        stmt = stmt.where(Company.estimated_employee_min >= min_employees)
    if query.get("company_ids"):
        stmt = stmt.where(Company.id.in_(query["company_ids"]))
    limit = int(query.get("max_records", 25))
    targets, seen = [], set()
    for company, domain in session.execute(stmt):
        if domain in seen:
            continue
        seen.add(domain)
        targets.append({"domain": domain, "country": company.country, "company_id": company.id})
        if len(targets) >= limit:
            break
    return targets


def _config_hash(source: Source, parser_version: str, min_employees: int) -> str:
    return stable_hash(
        {
            "allowed_fields": sorted(source.allowed_fields),
            "field_mapping": source.field_mapping,
            "countries": sorted(source.countries),
            "connector_config": source.connector_config,
            "base_confidence": source.base_confidence,
            "min_employees": min_employees,
            "normalization": NORMALIZATION_VERSION,
            "parser": parser_version,
        }
    )


def _new_run(
    session: Session,
    source: Source | None,
    source_id: str,
    *,
    kind: str,
    actor: str,
    min_employees: int,
    file_name: str | None = None,
    query: dict[str, Any] | None = None,
    retry_of_id: str | None = None,
) -> IngestionRun:
    run = IngestionRun(
        source_id=source_id,
        kind=kind,
        file_name=file_name,
        query=query or {},
        region=source.region if source else None,
        parser_version="n/a",
        config_hash="n/a",
        min_employees=min_employees,
        counts={k: 0 for k in COUNT_KEYS},
        warnings=[],
        errors=[],
        retry_of_id=retry_of_id,
        actor=actor,
        started_at=utcnow(),
    )
    session.add(run)
    session.flush()
    return run


def _registered_or_deny(session: Session, source_id: str, actor: str) -> Source:
    """Unregistered sources cannot even own a run row; deny and audit before anything is stored."""
    source = session.get(Source, source_id)
    if source is None:
        reasons = ingestion_gate(None)
        audit.record(
            session,
            actor=actor,
            action="ingestion.rejected_by_permission_gate",
            entity_type="source",
            entity_id=source_id[:300],
            details={"reasons": reasons},
        )
        session.commit()
        raise PermissionDenied(reasons)
    return source


def _gate_or_reject(session: Session, run: IngestionRun, source: Source | None, live: bool) -> None:
    reasons = ingestion_gate(source, live=live)
    if not reasons:
        return
    run.status = "REJECTED"
    run.errors = [{"stage": "permission_gate", "message": r} for r in reasons]
    run.finished_at = utcnow()
    audit.record(
        session,
        actor=run.actor,
        action="ingestion.rejected_by_permission_gate",
        entity_type="ingestion_run",
        entity_id=run.id,
        details={"source_id": run.source_id, "reasons": reasons},
    )
    session.commit()
    raise PermissionDenied(reasons, run_id=run.id)


def start_csv_run(
    session: Session,
    *,
    source_id: str,
    file_name: str,
    text: str,
    actor: str,
    min_employees: int | None = None,
    retry_of_id: str | None = None,
) -> IngestionRun:
    min_emp = min_employees if min_employees is not None else get_settings().min_employees_default
    source = _registered_or_deny(session, source_id, actor)
    if source.connector_type in ("brreg", "prh"):
        raise ConnectorError(f"source '{source_id}' is a registry connector; start a discovery run instead")
    run = _new_run(
        session,
        source,
        source_id,
        kind="csv",
        actor=actor,
        min_employees=min_emp,
        file_name=file_name,
        retry_of_id=retry_of_id,
    )
    _gate_or_reject(session, run, source, live=False)
    assert source is not None
    connector = CsvConnector(source.field_mapping)
    run.input_hash = hashlib.sha256(text.encode()).hexdigest()
    # Raw input kept only for retry, and only until the source's retention period ends.
    run.input_text = text
    run.input_expires_at = run.started_at + timedelta(days=source.retention_days)
    snapshot = connector.fetch_text(file_name, text)
    return _execute(session, run, source, connector, [snapshot])


def start_discovery_run(
    session: Session,
    *,
    source_id: str,
    query: dict[str, Any],
    actor: str,
    min_employees: int | None = None,
    retry_of_id: str | None = None,
) -> IngestionRun:
    min_emp = min_employees if min_employees is not None else get_settings().min_employees_default
    source = _registered_or_deny(session, source_id, actor)
    if source.connector_type == "ee_ariregister":
        from app.services.ee_import import import_estonia

        return import_estonia(
            session,
            source_id=source_id,
            query=query,
            actor=actor,
            min_employees=min_emp,
            retry_of_id=retry_of_id,
        )
    run = _new_run(
        session,
        source,
        source_id,
        kind="discovery",
        actor=actor,
        min_employees=min_emp,
        query=query,
        retry_of_id=retry_of_id,
    )
    live = bool(source and (source.connector_config or {}).get("live"))
    _gate_or_reject(session, run, source, live=live)
    assert source is not None
    try:
        connector = build_connector(source)
        effective_query = query
        if source.connector_type == "website":
            effective_query = {**query, "targets": website_targets(session, source, query, min_emp)}
        refs: list[CandidateRef] = connector.discover(effective_query, source.region, min_emp)
    except ConnectorError as exc:
        return _fail(session, run, "discover", str(exc))
    if isinstance(connector, WebsiteConnector):
        snaps, errs = connector.fetch_many(refs)
        run.counts = {**run.counts, "discovered": len(refs)}
        run.errors = errs
        return _execute(session, run, source, connector, snaps)
    run.counts = {**run.counts, "discovered": len(refs)}
    snapshots: list[FetchedSnapshot] = []
    fetch_errors = []
    for ref in refs:
        try:
            snapshots.append(connector.fetch(ref))
        except ConnectorError as exc:
            fetch_errors.append({"stage": "fetch", "reference": ref.reference, "message": str(exc)})
    run.errors = fetch_errors
    return _execute(session, run, source, connector, snapshots)


def _fail(session: Session, run: IngestionRun, stage: str, message: str) -> IngestionRun:
    run.status = "FAILED"
    run.errors = [*run.errors, {"stage": stage, "message": message}]
    run.finished_at = utcnow()
    audit.record(
        session,
        actor=run.actor,
        action="ingestion.failed",
        entity_type="ingestion_run",
        entity_id=run.id,
        details={"stage": stage, "message": message},
    )
    session.commit()
    return run


def _execute(
    session: Session, run: IngestionRun, source: Source, connector, snapshots: list[FetchedSnapshot]
) -> IngestionRun:
    run.parser_version = connector.parser_version
    run.config_hash = _config_hash(source, connector.parser_version, run.min_employees)
    run.status = "FETCHED"
    session.flush()

    # PARSE
    parsed_rows: list[tuple[FetchedSnapshot, dict[str, Any]]] = []
    try:
        for snap in snapshots:
            for row in connector.parse(snap):
                parsed_rows.append((snap, row))
    except (ConnectorError, KeyError, ValueError) as exc:
        return _fail(session, run, "parse", str(exc))
    run.status = "PARSED"
    if run.kind == "csv":
        run.counts = {**run.counts, "discovered": len(parsed_rows)}

    # VALIDATE (no writes to company tables happen before this stage completes)
    records: list[tuple[FetchedSnapshot, ParsedRecord]] = []
    for i, (snap, row) in enumerate(parsed_rows, start=1):
        records.append(
            (
                snap,
                build_record(i, row, source_countries=source.countries, allowed_fields=source.allowed_fields),
            )
        )
    run.status = "VALIDATED"
    session.flush()

    # UPSERT
    counts = dict(run.counts)
    touched: set[str] = set()
    seen_keys: dict[str, int] = {}
    for snap, rec in records:
        outcome = _upsert_one(session, run, source, snap, rec, counts, seen_keys)
        if outcome and outcome.company_id:
            touched.add(outcome.company_id)
    counts["warnings"] = sum(len(r.warnings) for _, r in records)

    from app.services.quality import detect_duplicates  # local import: quality depends on resolution

    counts["duplicate_candidates"] += detect_duplicates(session, touched)
    run.counts = counts
    run.warnings = [
        {"row": r.row_number, "source_key": r.source_key, "message": w}
        for _, r in records
        for w in r.warnings
    ]
    run.status = "UPSERTED"
    run.finished_at = utcnow()
    audit.record(
        session,
        actor=run.actor,
        action="ingestion.completed",
        entity_type="ingestion_run",
        entity_id=run.id,
        details={
            "source_id": source.id,
            "counts": counts,
            "parser_version": run.parser_version,
            "config_hash": run.config_hash,
        },
    )
    session.commit()
    log.info("ingestion run completed", extra={"ingestion_run_id": run.id, "source_id": source.id})
    return run


def _upsert_one(
    session: Session,
    run: IngestionRun,
    source: Source,
    snap: FetchedSnapshot,
    rec: ParsedRecord,
    counts: dict[str, int],
    seen_keys: dict[str, int],
) -> IngestionRecord:
    out = IngestionRecord(
        run_id=run.id,
        row_number=rec.row_number,
        source_key=rec.source_key,
        outcome="rejected",
        warnings=list(rec.warnings),
        errors=list(rec.errors),
    )
    session.add(out)
    if not rec.ok:
        counts["rejected"] += 1
        return out

    dedupe_keys = [k for k in (rec.source_key, rec.registry_key) if k]
    first = next((seen_keys[k] for k in dedupe_keys if k in seen_keys), None)
    if first is not None:
        out.outcome = "duplicate"
        out.match_reasons = [f"same stable key as row {first} in this file"]
        counts["duplicates"] += 1
        return out
    for k in dedupe_keys:
        seen_keys[k] = rec.row_number

    observed_at: datetime = rec.observed_at or run.started_at
    session.flush()  # persist the outcome row outside the savepoint so a rollback cannot drop it
    nested = session.begin_nested()
    try:
        existing_snapshot = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.source_id == source.id,
                SourceSnapshot.source_key == rec.source_key,
                SourceSnapshot.content_hash == rec.content_hash,
            )
        )
        resolution, keys = resolve(session, rec, source)
        company = resolution.company
        if company is None and rec.enrichment_only:
            # Website data may enrich a registry-backed company but never create one.
            out.outcome = "rejected"
            out.errors = [
                *out.errors,
                "enrichment record matched no existing company"
                + (
                    " (" + "; ".join(r["detail"] for _, rs in resolution.duplicate_links for r in rs) + ")"
                    if resolution.duplicate_links
                    else ""
                ),
            ]
            counts["rejected"] += 1
            nested.commit()
            return out
        if existing_snapshot is not None and company is not None:
            # Same source record, same content: idempotent no-op (contacts still re-checked below).
            out.outcome = "unchanged"
            out.match_reasons = ["identical snapshot already ingested (source key + content hash)"]
            counts["unchanged"] += 1
        else:
            snapshot = existing_snapshot or SourceSnapshot(
                source_id=source.id,
                ingestion_run_id=run.id,
                source_key=rec.source_key or "",
                source_url=rec.source_url or snap.source_url,
                request_id=snap.request_id,
                content_hash=rec.content_hash,
                raw_payload=rec.raw,
                parser_version=run.parser_version,
                http_status=snap.http_status,
                retrieved_at=run.started_at,
                expires_at=run.started_at + timedelta(days=source.retention_days),
            )
            session.add(snapshot)
            session.flush()
            is_new = company is None
            if company is None:
                if rec.country not in active_countries():  # defence in depth; build_record rejects these first
                    raise ValueError(f"refusing to create a company in inactive country {rec.country}")
                company = Company(
                    legal_name=rec.facts["legal_name"],
                    normalized_name=normalize_name(rec.facts["legal_name"]),
                    country=rec.country,
                    region=rec.region,
                    first_seen_at=observed_at,
                )
                session.add(company)
                session.flush()
                out.match_reasons = ["no existing company matched any stable key; created new profile"]
            else:
                out.match_reasons = resolution.match_reasons
            ensure_identifiers(session, company, keys, source, resolution.blocked_keys)
            change = upsert_facts(
                session, company, rec, source, run_id=run.id, snapshot_id=snapshot.id, observed_at=observed_at
            )
            counts["facts_added"] += change.added
            counts["facts_changed"] += change.changed
            recompute_company(session, company)
            if is_new:
                out.outcome = "accepted"
                counts["accepted"] += 1
            elif change.added or change.changed:
                out.outcome = "updated"
                counts["updated"] += 1
            else:
                out.outcome = "unchanged"
                counts["unchanged"] += 1
            for other_id, reasons in resolution.duplicate_links:
                if link_duplicate(session, company.id, other_id, reasons, 70, "possible"):
                    counts["duplicate_candidates"] += 1
                    out.warnings.append("possible duplicate linked for review (conflicting stable keys)")

        out.company_id = company.id
        out.qualification = company.qualification_status
        counts[company.qualification_status] = counts.get(company.qualification_status, 0) + 1
        for contact in rec.contacts:
            _upsert_contact(session, run, source, rec, contact, company, observed_at, counts, out)
        nested.commit()
    except Exception as exc:  # isolate one bad record from the rest of the run
        nested.rollback()
        session.add(out)
        out.outcome = "rejected"
        out.company_id = None
        out.errors = [*out.errors, f"upsert failed: {type(exc).__name__}: {exc}"]
        counts["rejected"] += 1
        log.exception("upsert failed", extra={"ingestion_run_id": run.id, "source_id": source.id})
    return out


def _upsert_contact(
    session: Session,
    run: IngestionRun,
    source: Source,
    rec: ParsedRecord,
    c: ParsedContact,
    company: Company,
    observed_at: datetime,
    counts: dict[str, int],
    out: IngestionRecord,
) -> None:
    ident = contact_identity_hash(c.name, company)
    suppressed_hashes = {ident} | ({email_hash(c.email)} if c.email else set())
    if session.scalar(
        select(ContactSuppression).where(ContactSuppression.identity_hash.in_(suppressed_hashes))
    ):
        counts["contacts_suppressed"] += 1
        out.warnings.append("contact skipped: subject to a GDPR erasure suppression")
        return
    existing = session.scalar(
        select(Contact).where(Contact.company_id == company.id, Contact.identity_hash == ident)
    )
    if existing:
        # Only overwrite with values the source actually supplies; never fill gaps by inference.
        existing.role = c.role or existing.role
        existing.email = c.email or existing.email
        existing.phone = c.phone or existing.phone
        existing.profile_url = c.profile_url or existing.profile_url
        existing.last_verified_at = max(existing.last_verified_at or observed_at, observed_at)
        return
    session.add(
        Contact(
            company_id=company.id,
            name=c.name,
            role=c.role,
            email=c.email,
            phone=c.phone,
            profile_url=c.profile_url,
            country=company.country,
            language="de" if company.region == "dach" else "en",
            source_id=source.id,
            source_key=rec.source_key,
            source_url=rec.source_url,
            ingestion_run_id=run.id,
            confidence=source.base_confidence,
            contact_basis=c.contact_basis,
            usage_policy=source.usage_policy,
            identity_hash=ident,
            last_verified_at=observed_at,
        )
    )
    counts["contacts_added"] += 1


def retry_run(session: Session, run_id: str, actor: str) -> IngestionRun:
    run = session.get(IngestionRun, run_id)
    if run is None:
        raise LookupError(run_id)
    audit.record(
        session,
        actor=actor,
        action="ingestion.retry_requested",
        entity_type="ingestion_run",
        entity_id=run.id,
    )
    if run.kind == "csv":
        if not run.input_text:
            raise ConnectorError(
                "raw input for this run is not available (rejected before storage or expired by "
                "retention); re-upload the file"
            )
        return start_csv_run(
            session,
            source_id=run.source_id,
            file_name=run.file_name or "retry.csv",
            text=run.input_text,
            actor=actor,
            min_employees=run.min_employees,
            retry_of_id=run.id,
        )
    return start_discovery_run(
        session,
        source_id=run.source_id,
        query=run.query,
        actor=actor,
        min_employees=run.min_employees,
        retry_of_id=run.id,
    )
