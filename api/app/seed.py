"""Populate the database from LIVE approved sources (no synthetic data).

    python -m app.seed               # sync source registry; run live ingestion once (skips if runs exist)
    python -m app.seed --reimport    # re-run the same live queries (idempotent reruns)
    python -m app.seed --reset       # drop + recreate all tables first (SQLite/local only)
    python -m app.seed --sources-only

Requires LIVE_CONNECTORS_ENABLED=true. Credential-gated sources (Zefix, CVR) run too: without credentials
they fail closed and the run is recorded as FAILED with the reason, so the blocker is visible in the UI.
"""

import argparse
import sys

from sqlalchemy import func, select

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import IngestionRun
from app.services import ingestion
from app.services.permissions import PermissionDenied
from app.services.source_registry import sync_sources

ACTOR = "system:seed"

# Registry discovery (A sources). Brreg filters >= min_employees at source; PRH has no headcount.
REGISTRY_RUNS: list[tuple[str, dict]] = [
    ("no-brreg", {"naeringskode": "62", "max_records": 300}),  # IT services / software
    ("no-brreg", {"naeringskode": "28", "max_records": 100}),  # machinery & equipment
    ("no-brreg", {"naeringskode": "71", "max_records": 100}),  # engineering & technical consultancy
    (
        "fi-prh-ytj",
        {"mainBusinessLine": "62100", "companyForm": "OY", "location": "Tampere", "max_records": 120},
    ),
    ("fi-prh-ytj", {"mainBusinessLine": "62100", "companyForm": "OY", "location": "Oulu", "max_records": 80}),
    ("ch-zefix", {"name": "Informatik", "max_records": 50}),
    ("dk-cvr", {"industry_code": "62", "max_records": 200}),
]
# Website enrichment (C sources) of registry-published domains.
WEBSITE_RUNS: list[tuple[str, dict]] = [
    ("web-company-nordics", {"countries": ["NO"], "qualified_only": True, "max_records": 60}),
    ("web-company-nordics", {"countries": ["FI"], "qualified_only": False, "max_records": 30}),
    ("web-company-dach", {"countries": ["DE", "AT", "CH"], "qualified_only": True, "max_records": 60}),
]


def _fmt(counts: dict[str, int]) -> str:
    keys = [
        "discovered",
        "accepted",
        "updated",
        "unchanged",
        "rejected",
        "qualified",
        "unknown_headcount",
        "contacts_added",
    ]
    return " ".join(f"{k}={counts.get(k, 0)}" for k in keys)


def run_live(session) -> None:
    for source_id, query in REGISTRY_RUNS + WEBSITE_RUNS:
        try:
            run = ingestion.start_discovery_run(session, source_id=source_id, query=query, actor=ACTOR)
        except PermissionDenied as exc:
            print(f"  {source_id:<22} REJECTED  {'; '.join(exc.reasons)}")
            continue
        err = f"  ! {run.errors[0]['message']}" if run.errors and run.status != "UPSERTED" else ""
        print(f"  {source_id:<22} {run.status:<9} {_fmt(run.counts)}{err}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reimport", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--sources-only", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if args.reset:
        if not settings.database_url.startswith("sqlite"):
            sys.exit(
                "--reset is only allowed for local SQLite databases; use `alembic downgrade base` on Postgres"
            )
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    with SessionLocal() as session:
        changed = sync_sources(session, actor=ACTOR)
        session.commit()
        print(f"source registry synced ({len(changed)} changed)")
        if args.sources_only:
            return
        if not settings.live_connectors_enabled:
            sys.exit(
                "LIVE_CONNECTORS_ENABLED is false: no live ingestion performed (no synthetic data is seeded)."
            )
        existing = session.scalar(select(func.count()).select_from(IngestionRun))
        if existing and not args.reimport:
            print(f"{existing} ingestion runs already exist; skipping (use --reimport)")
            return
        run_live(session)


if __name__ == "__main__":
    main()
