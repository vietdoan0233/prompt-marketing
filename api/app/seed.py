"""Synchronize the Estonia catalog and import the official register data."""

import argparse
import sys

from app.config import get_settings
from app.connectors.base import ConnectorError
from app.connectors.ee_ariregister import EeAriregisterFiles
from app.db import SessionLocal
from app.services import ee_import, maintenance
from app.services.permissions import PermissionDenied, load_config
from app.services.source_registry import sync_sources

ACTOR = "system:seed"
YEARS = list(range(2019, 2026))


def _fmt(counts: dict[str, int]) -> str:
    keys = (
        "discovered",
        "accepted",
        "updated",
        "unchanged",
        "financial_added",
        "financial_changed",
        "address_added",
        "orphan_reports",
        "rejected",
    )
    return " ".join(f"{key}={counts.get(key, 0)}" for key in keys)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--replace", action="store_true", help="backup and purge imported data before re-seeding"
    )
    parser.add_argument("--reset", action="store_true", help="deprecated alias for --replace")
    parser.add_argument("--sources-only", action="store_true")
    parser.add_argument("--reimport", action="store_true", help="re-run the import (kept for compatibility)")
    parser.add_argument(
        "--from-cache", action="store_true", help="use verified, previously downloaded official ZIPs"
    )
    args = parser.parse_args()
    settings = get_settings()
    replace = args.replace or args.reset

    # The production source is live by policy. Refuse before a destructive replace if live access is disabled.
    if not args.from_cache and not settings.live_connectors_enabled and not args.sources_only:
        sys.exit("LIVE_CONNECTORS_ENABLED is false: set it to true before running the Estonia import.")

    config = load_config()
    configured_ids = {entry["id"] for entry in config["sources"]}
    if replace:
        # Check every required official file before removing the current dataset. The import may then reuse
        # verified local copies even if the portal becomes unreachable later in the run.
        try:
            EeAriregisterFiles(settings.ee_cache_dir, live=not args.from_cache).resolve(YEARS)
        except (ConnectorError, OSError, ValueError) as exc:
            sys.exit(f"Estonia files unavailable; database was not purged: {exc}")
        backup = maintenance.backup_database()
        print(f"database backed up to {backup}")
        with SessionLocal() as session:
            counts = maintenance.purge_source_data(session, configured_source_ids=configured_ids, actor=ACTOR)
            print(f"purged {_fmt(counts)}")

    with SessionLocal() as session:
        changed = sync_sources(session, actor=ACTOR)
        session.commit()
        print(f"source registry synced ({len(changed)} changed)")
        if args.sources_only:
            return
        try:
            run = ee_import.import_estonia(
                session,
                source_id="ee-ariregister",
                query={"years": YEARS},
                actor=ACTOR,
                min_employees=settings.min_employees_default,
                live_override=False if args.from_cache or replace else None,
            )
        except PermissionDenied as exc:
            sys.exit(f"Estonia import rejected: {'; '.join(exc.reasons)}")
        error = f" error={run.errors[0]['message']}" if run.errors else ""
        print(f"ee-ariregister {run.status} {_fmt(run.counts)}{error}")


if __name__ == "__main__":
    main()
