# Mergero Estonia Company Database — Architecture

## Purpose and production scope

This internal application imports and presents source-backed company information for Estonia. Production configuration is Estonia-only (`ACTIVE_COUNTRIES=EE`) in the `baltics` region. The only enabled production ingestion source is the official [Estonian e-Business Register open-data portal](https://avaandmed.ariregister.rik.ee/et/avaandmete-allalaadimine), connector ID `ee-ariregister`.

The importer uses the portal's basic company, annual-report metadata, EMTAK activity, and annual indicator CSV ZIPs. It does not scrape company websites, download annual-report PDFs, or ingest shareholder, beneficial-owner, or personal-register datasets. Source permission and allowed fields are checked before an import.

## System shape

```text
Official RIK bulk files or verified local cache
                    |
           ee-ariregister resolver
                    |
          Estonia import service
          /         |          \
 company facts  address versions  annual financials
          \         |          /
            FastAPI + SQLAlchemy
                    |
        Next.js company database UI
```

The web app uses Next.js and TypeScript. The API and import process use FastAPI, SQLAlchemy, and Alembic. SQLite is the local default; deployment may configure another SQLAlchemy-supported database.

## Core tables

### `companies`

Stores the consolidated company profile, identity, headcount range, review state, and current qualification state. Stable registry identifiers are stored in `company_identifiers`. Previously known companies are refreshed when a newer 2024/2025 filing shows FTE below the import threshold, so their qualification state and employee fact reflect the latest evidence.

### `company_facts`

Stores versioned, source-backed claims including `registry_status`, legal name, registry ID, headcount, and industry code. Each fact retains its source, source key and URL, observed time, confidence, usage policy, review state, and link to an import run and source snapshot. `GET /companies/{id}` returns the active facts and superseded history.

### `registered_addresses`

Stores the official registered seat (`asukoht`) separately from the consolidated profile. Address rows are versioned: an unchanged mapped address does not add a row, a changed address closes the active version, and a later reversion creates a new version. Address parts may be null when the source is incomplete or ambiguous. This table does not claim to represent an operating location.

### `company_financials`

Stores one financial observation per company, filing, period, and statement scope. It retains reported values, source lines, currency/unit, source file, snapshot, parser version, confidence, usage policy, ingestion run, and review state. Estonian monetary values are recorded in EUR. If EBITDA is absent but both operating profit and depreciation plus impairment are reported, EBITDA is stored as derived with formula `operating_profit + depreciation_and_impairment`; the row is marked `value_type="derived"`. Other absent measures remain null.

### Provenance and operations

`sources` holds connector permission and allowed-field configuration. `source_snapshots` records file and row hashes, source URLs, parser versions, and retention metadata. `ingestion_runs` and `ingestion_records` keep outcome counts, warnings, errors, and rejected orphan indicators. `audit_events` records import completion and review actions.

## Import flow and safety

The Estonia import performs these stages:

1. Apply the source permission gate and resolve requested official datasets.
2. Require every requested indicator year and both qualification years (2024 and 2025). Missing files fail before company upserts. The seed command also resolves and validates all required files before a `--replace` backup and purge.
3. Snapshot file metadata and load annual-report metadata.
4. Select companies using non-consolidated reported FTE from 2024/2025, apply the legal-form allowlist, and refresh already-known companies that have fallen below the threshold.
5. Upsert source-backed company facts and status, then version the registered address.
6. Join indicator rows to annual reports, reject and log orphan 2024 report IDs, and upsert financial rows by stable source key and content hash.
7. Record counts and warnings, mark the run complete, and write an audit event.

Imports are incremental and idempotent for unchanged source content. Cache mode accepts only files whose URL, size, and SHA-256 match the local manifest. `api/data/` is git-ignored; a clean checkout therefore needs one live download with `LIVE_CONNECTORS_ENABLED=true` or a copy of a teammate's verified `api/data/ee_ariregister/` directory before `--from-cache` can work.

## API surface

```text
GET    /health
GET    /sources
GET    /ingestion-runs
POST   /ingestion-runs
GET    /ingestion-runs/{id}
POST   /ingestion-runs/{id}/retry
GET    /companies
GET    /companies/{id}
GET    /companies/{id}/financials
GET    /companies/{id}/registered-address
POST   /companies/{id}/facts/corrections
GET    /companies/{id}/audit-events
GET    /audit-events
GET    /quality/report
GET    /quality/duplicates
```

`GET /companies/{id}` includes `company.registry_status`, the `registry_status` fact and provenance, current registered address, financial rows, identifiers, warnings, and review history. `GET /companies` defaults to at least 20 employees and supports qualification and review filters.

## Local development and checks

Use Python 3.12 or newer, as required by `api/pyproject.toml`. Follow `README.md` for Windows PowerShell setup and the first live download or verified-cache copy. The documented checks are pytest, Ruff format/lint, mypy, Alembic schema consistency, TypeScript type checking, and the Next.js production build.
