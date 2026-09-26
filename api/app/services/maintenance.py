"""Database maintenance used by the Estonia replacement seed.

Replacement is intentionally explicit: make a recoverable backup first, remove source-derived data, keep the
audit trail and GDPR suppression tombstones, then let source sync/import rebuild the active catalog.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Company,
    CompanyFact,
    CompanyFinancial,
    CompanyIdentifier,
    Contact,
    DuplicateCandidate,
    IngestionRecord,
    IngestionRun,
    RegisteredAddress,
    Source,
    SourceSnapshot,
)
from app.services import audit


def backup_database() -> Path:
    """Create a timestamped SQLite backup or a PostgreSQL dump; fail closed if pg_dump is unavailable."""
    settings = get_settings()
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        database = url.database
        if not database or database == ":memory:":
            raise RuntimeError("cannot make a replacement backup for an in-memory SQLite database")
        source_path = Path(database)
        if not source_path.exists():
            raise RuntimeError(f"SQLite database does not exist: {source_path}")
        destination = settings.backup_dir / f"mergero_dev_{stamp}.db"
        with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return destination

    if url.get_backend_name() == "postgresql":
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise RuntimeError("pg_dump is required for a PostgreSQL replacement backup but is not installed")
        destination = settings.backup_dir / f"mergero_dev_{stamp}.dump"
        dump_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        subprocess.run(
            [pg_dump, "--format=custom", "--file", str(destination), dump_url],
            check=True,
            capture_output=True,
            text=True,
        )
        return destination
    raise RuntimeError(f"unsupported database backend for replacement backup: {url.get_backend_name()}")


def purge_source_data(session: Session, *, configured_source_ids: set[str], actor: str) -> dict[str, int]:
    """Delete all imported company/source data while retaining audit events and suppression tombstones."""
    counts: dict[str, int] = {}

    # Clear the self-reference before deleting companies, which also makes this safe on strict FK databases.
    session.execute(update(Company).values(merged_into_id=None))
    deletion_order = (
        (CompanyFinancial, "company_financials"),
        (RegisteredAddress, "registered_addresses"),
        (Contact, "contacts"),
        (CompanyFact, "company_facts"),
        (CompanyIdentifier, "company_identifiers"),
        (DuplicateCandidate, "duplicate_candidates"),
        (IngestionRecord, "ingestion_records"),
        (SourceSnapshot, "source_snapshots"),
        (IngestionRun, "ingestion_runs"),
        (Company, "companies"),
    )
    for model, label in deletion_order:
        result = session.query(model).delete(synchronize_session=False)
        counts[label] = int(result)

    stale_sources = list(session.scalars(select(Source).where(~Source.id.in_(configured_source_ids))))
    for source in stale_sources:
        session.delete(source)
    counts["sources"] = len(stale_sources)
    session.flush()

    audit.record(
        session,
        actor=actor,
        action="maintenance.purge",
        entity_type="database",
        details={"counts": counts, "kept": ["audit_events", "contact_suppressions"]},
    )
    session.commit()
    return counts
