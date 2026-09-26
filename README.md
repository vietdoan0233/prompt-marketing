# Estonia company register

This FastAPI and Next.js application stores company identities, official registry statuses, registered-address versions, and annual financial observations for Estonian entities. Production ingestion uses the official [Estonian e-Business Register open-data portal](https://avaandmed.ariregister.rik.ee/et/avaandmete-allalaadimine) bulk files. The active country is `EE`; the region is `baltics`.

## Prerequisites

- Python 3.12 or newer (`api/pyproject.toml` requires `>=3.12`). On Windows, install the Python launcher (`py`) as well.
- Node.js 20 or newer with npm for the web UI.
- Git.

The API uses SQLite by default. It creates `api/mergero_dev.db` when migrations run; no separate database server or `.env` file is required for local development. The database and downloaded register data are local and git-ignored.

## First-time setup

Run these commands from the repository root to create the API environment, apply the database schema, and install the web dependencies:

```powershell
cd api
py -3.12 -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\alembic.exe upgrade head
cd ..\web
npm ci
cd ..
```

The API and UI can be started without importing registry data, but the company list will be empty until you load data. Follow one of the cache/bootstrap options below to populate it.

The SQLite database and official dataset cache are git-ignored. A clean checkout has no `api/data/ee_ariregister/` cache, so `--from-cache` will fail until the cache is bootstrapped. Choose one of these options:

1. **Download from the official portal once.** Set `LIVE_CONNECTORS_ENABLED=true` in `api/.env`, then run:

   ```powershell
   .\.venv\Scripts\python.exe -m app.seed --replace
   ```

   This downloads and verifies the required official ZIPs into `api/data/ee_ariregister/`, then imports them. Keep `LIVE_CONNECTORS_ENABLED=true` for the live import.

2. **Use a teammate's verified cache.** Copy the complete `api/data/ee_ariregister/` directory, including `manifest.json`, from a trusted checkout, then run:

   ```powershell
   .\.venv\Scripts\python.exe -m app.seed --from-cache --replace
   ```

`--from-cache` intentionally does not access the network. It verifies each ZIP against the manifest's official URL, size, and SHA-256. Both cache resolution and required-year validation finish before `--replace` backs up and purges the existing imported data. The import requires files for every requested year plus indicator files for both 2024 and 2025. If either qualification-year file is missing, the import fails before company upserts and the replacement command exits before purge.

For later incremental imports, use `python -m app.seed --reimport` with live access, or add `--from-cache` to use the verified local files. `--replace` makes a timestamped database backup in `api/backups/` before purging imported data. Do not delete the database file by hand; that removes audit and correction history.

The default import covers fiscal years 2019–2025 and legal forms OÜ, AS, UÜ, TÜ, TÜH, and SE. Company qualification uses non-consolidated reported FTE from 2024 or 2025 and defaults to at least 20 employees. Previously imported companies with newer below-threshold FTE are refreshed and shown with their updated qualification status.

## Run the application

Start the API in one terminal from the repository root:

```powershell
cd api
.\.venv\Scripts\uvicorn.exe app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the web UI in a second terminal from the repository root:

```powershell
cd web
npm run dev
```

Open the UI at `http://localhost:3000` and the API documentation at `http://localhost:8000/docs`. Stop either process with `Ctrl+C`. For later runs, start both processes again; migrations and `npm ci` are only needed during setup or after dependency/schema changes.

## Data and provenance

The importer uses the official basic-data, annual-report metadata, EMTAK activity, and annual indicator CSV ZIPs. Company facts retain source and snapshot provenance. Legal status such as `Registered`, `In Liquidation`, `Bankrupt`, or `Deleted` is stored as the first-class `registry_status` fact and included in the company summary and detail view.

`registered_addresses` represents the official registered seat (`asukoht`), not an inferred operating location. Address parts are nullable when the source is incomplete or ambiguous. A changed mapped address closes the previous version and adds a current one; unchanged addresses do not add a version.

`company_financials` stores financial observations by company, filing, fiscal period, and statement scope. Estonian monetary values use `currency="EUR"` and `unit="EUR"`. Reported values retain the source lines and provenance. EBITDA is calculated only when it is not explicitly reported and both `operating_profit` and `depreciation_and_impairment` are present. Such a row is marked `value_type="derived"` with `calculation_formula="operating_profit + depreciation_and_impairment"`. Missing measures remain null.

Financial rows expose the inclusive `period_days` and a `period_length_class` (`short`, `standard_12_month`, `long`, or `invalid`). Missing period dates leave these fields unknown. Amounts are not annualized, so compare rows using their actual filing periods. EMTAK industry-code facts preserve the official `emtak_version` label as taxonomy metadata; the importer does not infer a code version from fiscal year. Shareholder and board event histories are not currently ingested.

Unmatched indicator report IDs are rejected and included in the ingestion run's outcome log; their values are not imported. Raw snapshot payloads follow the configured retention policy.

## API and review

- `GET /companies` lists companies and supports headcount, qualification, and review filters.
- `GET /companies/{id}` returns the summary (including registry status), source-backed facts and history, registered address, annual financials, identifiers, warnings, and review information.
- `GET /companies/{id}/registered-address` returns the current registered-address version.
- `GET /companies/{id}/financials` returns the financial time series.
- `GET /ingestion-runs/{id}` exposes accepted, unchanged, rejected, orphan, and warning records.

## Checks

Run from the repository root:

```powershell
cd api
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe format --check app tests migrations
.\.venv\Scripts\ruff.exe check app tests migrations
.\.venv\Scripts\python.exe -m mypy app
.\.venv\Scripts\alembic.exe check
cd ..\web
npx tsc --noEmit
npm run build
```

The Estonia orchestration tests build small ZIP fixtures and cover real manifest resolution, required indicator years, import snapshots, company and status facts, address versioning, financial derivation and EUR values, orphan rejection, completion logging, and idempotent reruns. Tests use isolated SQLite databases by default; `TEST_DATABASE_URL` may point to PostgreSQL.
