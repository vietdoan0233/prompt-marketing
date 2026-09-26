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

`company_financials` stores financial observations by company, filing, fiscal period, and statement scope. Estonian monetary values use `currency="EUR"` and `unit="EUR"`. Reported values retain the source lines and provenance. Reported EBITDA takes precedence. Otherwise, when operating profit and depreciation/impairment are present, EBITDA is derived as `operating_profit - depreciation_and_impairment`. The official statement preserves expenses as negative numbers, so subtracting the signed expense adds it back. Such a row is marked `value_type="derived"` with the applied formula in `calculation_formula`. Missing measures remain null.

Financial rows expose the inclusive `period_days` and a `period_length_class` (`short`, `standard_12_month`, `long`, or `invalid`). Missing period dates leave these fields unknown. Amounts are not annualized, so compare rows using their actual filing periods. EMTAK industry-code facts preserve the official `emtak_version` label as taxonomy metadata; the importer does not infer a code version from fiscal year. Shareholder and board event histories are not currently ingested.

Unmatched indicator report IDs are rejected and included in the ingestion run's outcome log; their values are not imported. Raw snapshot payloads follow the configured retention policy.

## API and review

- `GET /companies` lists companies and supports headcount, qualification, and review filters.
- `GET /companies/{id}` returns the summary (including registry status), source-backed facts and history, registered address, annual financials, identifiers, warnings, and review information.
- `GET /companies/{id}/registered-address` returns the current registered-address version.
- `GET /companies/{id}/financials` returns the financial time series.
- `GET /ingestion-runs/{id}` exposes accepted, unchanged, rejected, orphan, and warning records.
- `GET /seller-prospects` powers the read-only seller prospect funnel in `/seller-prospects`. It accepts `min_revenue_eur`, `max_revenue_eur`, `sector`, and `limit`. The default €5m–€50m annual-revenue band is a provisional Nordic size proxy until Mergero confirms which size metric it uses.

The funnel applies an explainable sequence to the imported companies: annual revenue band, three consecutive comparable standalone EUR fiscal years, operating-profit persistence, and a peer-relative financial-profile index. It uses only reported revenue, operating profit, assets, and equity. The index combines robust Z-scores for median operating margin (70%) and equity/assets (30%) inside two-digit EMTAK groups with at least eight comparable peers. Smaller groups show no index. Report IDs, official source links, and data gaps remain visible. A shortlist is for advisor review, not automatic outreach. Buyer fit and owner intent are not assessed by these files. The dataset does not directly provide dividends or capex; the funnel does not use them or treat derived EBITDA as a reported value.

The response also reports the funnel stage by stage (imported → active → size band → three comparable years → three profitable years → advisor review), the EMTAK groups that carry a peer index, and two flags. `group_parent` means the company also filed consolidated accounts for its latest year, so the standalone figures may understate the sellable group; the reported group revenue is shown when available. `holding_activity` marks EMTAK 64.2x or 70.10 activity codes; these are sent to research rather than ranked, because a holding's margins are not comparable with operating peers. Each company has a deterministic brief: what the filings show, each line tied to a fiscal year, and what they cannot show. It also links to the official e-Business Register company page built from the registry code.

To see what the imported data actually covers, run the read-only coverage report from `api/`:

```powershell
.\.venv\Scripts\python.exe -m app.analysis.seller_coverage            # Markdown
.\.venv\Scripts\python.exe -m app.analysis.seller_coverage --json     # JSON
```

It lists field coverage by fiscal year and statement scope, fiscal-period lengths, the latest comparable revenue distribution, why in-band companies lack complete evidence, peer-group sizes, and flag counts.

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
