"""Share Digital Decay results between local databases without re-crawling.

    python -m app.decay_snapshot --export FILE   write every current web-digital-decay fact and derived
                                                 domain identifier, keyed by registry code
    python -m app.decay_snapshot --import FILE   load such a file into this database

Only the extracted evidence already stored as `estimated` facts is exported (never raw HTML). Import
matches companies by registry code, skips facts whose value hash already matches the current fact, and
otherwise closes the current fact (valid_to) and adds the imported one with its original observed_at,
so existing evidence is versioned, not overwritten. Companies missing from this database are reported
and skipped; nothing is created.
"""

import argparse
import json
import sys
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import CompanyFact, CompanyIdentifier, utcnow
from app.services.digital_decay import SOURCE_ID

FORMAT_VERSION = 1
FACT_COLUMNS = (
    "field_name",
    "value_json",
    "original_value",
    "value_hash",
    "source_key",
    "source_url",
    "base_confidence",
    "confidence",
    "usage_policy",
    "review_status",
    "code_system",
    "code_version",
)


def _registry_codes(session: Session) -> dict[str, str]:
    rows = session.scalars(
        select(CompanyIdentifier).where(
            CompanyIdentifier.kind == "registry_id", CompanyIdentifier.derived.is_(False)
        )
    )
    return {row.company_id: row.value for row in rows}


def export(session: Session) -> dict[str, Any]:
    codes = _registry_codes(session)
    facts = session.scalars(
        select(CompanyFact).where(CompanyFact.source_id == SOURCE_ID, CompanyFact.valid_to.is_(None))
    )
    out_facts = []
    for fact in facts:
        code = codes.get(fact.company_id)
        if code is None:
            continue
        row = {col: getattr(fact, col) for col in FACT_COLUMNS}
        row["registry_id"] = code
        row["observed_at"] = fact.observed_at.isoformat()
        row["valid_from"] = fact.valid_from.isoformat()
        out_facts.append(row)
    domains = [
        {"registry_id": codes[i.company_id], "value": i.value}
        for i in session.scalars(
            select(CompanyIdentifier).where(
                CompanyIdentifier.kind == "domain", CompanyIdentifier.source_id == SOURCE_ID
            )
        )
        if i.company_id in codes
    ]
    out_facts.sort(key=lambda r: (r["registry_id"], r["field_name"]))
    domains.sort(key=lambda r: (r["registry_id"], r["value"]))
    return {"format_version": FORMAT_VERSION, "source_id": SOURCE_ID, "facts": out_facts, "domains": domains}


def import_(session: Session, data: dict[str, Any]) -> dict[str, int]:
    if data.get("format_version") != FORMAT_VERSION or data.get("source_id") != SOURCE_ID:
        raise ValueError("not a web-digital-decay snapshot of a supported format version")
    company_by_code = {code: company_id for company_id, code in _registry_codes(session).items()}
    counts = {"added": 0, "unchanged": 0, "missing_company": 0, "domains_added": 0}
    now = utcnow()
    for row in data["facts"]:
        company_id = company_by_code.get(row["registry_id"])
        if company_id is None:
            counts["missing_company"] += 1
            continue
        current = session.scalars(
            select(CompanyFact).where(
                CompanyFact.company_id == company_id,
                CompanyFact.source_id == SOURCE_ID,
                CompanyFact.field_name == row["field_name"],
                CompanyFact.valid_to.is_(None),
            )
        ).first()
        if current is not None and current.value_hash == row["value_hash"]:
            counts["unchanged"] += 1
            continue
        if current is not None:
            current.valid_to = now
        session.add(
            CompanyFact(
                id=str(uuid.uuid4()),
                company_id=company_id,
                source_id=SOURCE_ID,
                observed_at=datetime.fromisoformat(row["observed_at"]),
                valid_from=datetime.fromisoformat(row["valid_from"]),
                is_correction=False,
                created_at=now,
                **{col: row[col] for col in FACT_COLUMNS},
            )
        )
        counts["added"] += 1
    for row in data["domains"]:
        company_id = company_by_code.get(row["registry_id"])
        exists = session.scalars(
            select(CompanyIdentifier).where(
                CompanyIdentifier.kind == "domain", CompanyIdentifier.value == row["value"]
            )
        ).first()
        if company_id is None or exists is not None:
            continue
        session.add(
            CompanyIdentifier(
                id=str(uuid.uuid4()),
                company_id=company_id,
                kind="domain",
                value=row["value"],
                source_id=SOURCE_ID,
                derived=True,
                created_at=now,
            )
        )
        counts["domains_added"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.decay_snapshot", description=__doc__.split("\n\n")[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export", metavar="FILE")
    mode.add_argument("--import", dest="import_file", metavar="FILE")
    args = p.parse_args(argv)
    with SessionLocal() as session:
        if args.export:
            data = export(session)
            with open(args.export, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
            print(f"exported {len(data['facts'])} facts, {len(data['domains'])} domains to {args.export}")
        else:
            with open(args.import_file, encoding="utf-8") as fh:
                counts = import_(session, json.load(fh))
            session.commit()
            print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
