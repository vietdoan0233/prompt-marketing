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

The importer uses the official basic-data, annual-report metadata, EMTAK activity, and annual indicator CSV ZIPs, plus two optional JSON ZIPs for general company data (current share capital) and shareholders (osanikud). Company facts retain source and snapshot provenance. The official CSV currently stores registry status as codes (`R` registered, `L` in liquidation, `N` bankrupt, `K` deleted); the original value is kept in the first-class `registry_status` fact and included in the company summary and detail view. The seller-prospect funnel (see "API and review" below) does not read this fact at all: it builds its stages, its Cash Harvesting signal and its peer index from financial and sector data only, so registry status is neither a gate nor an exclusion there.

`registered_addresses` represents the official registered seat (`asukoht`), not an inferred operating location. Address parts are nullable when the source is incomplete or ambiguous. A changed mapped address closes the previous version and adds a current one; unchanged addresses do not add a version.

`company_shareholders` represents the current shareholder set, versioned the same way as a registered address. A person shareholder is stored by name, role, and holding only: the register's national ID code, its one-way hash, birth date, and home address are never read by the importer. A legal-entity shareholder also keeps its registry (or foreign) code. The beneficial-owner (kasusaajad) file and board-event history are not ingested.

`company_financials` stores financial observations by company, filing, fiscal period, and statement scope. Estonian monetary values use `currency="EUR"` and `unit="EUR"`. Reported values retain the source lines and provenance. Reported EBITDA takes precedence. Otherwise, when operating profit and depreciation/impairment are present, EBITDA is derived as `operating_profit - depreciation_and_impairment`. The official statement preserves expenses as negative numbers, so subtracting the signed expense adds it back. Such a row is marked `value_type="derived"` with the applied formula in `calculation_formula`. Missing measures remain null.

Financial rows expose the inclusive `period_days` and a `period_length_class` (`short`, `standard_12_month`, `long`, or `invalid`). Missing period dates leave these fields unknown. Amounts are not annualized, so compare rows using their actual filing periods. EMTAK industry-code facts preserve the official `emtak_version` label as taxonomy metadata; the importer does not infer a code version from fiscal year.

Unmatched indicator report IDs are rejected and included in the ingestion run's outcome log; their values are not imported. Raw snapshot payloads follow the configured retention policy.

## API and review

- `GET /companies` lists companies and supports headcount, qualification, and review filters.
- `GET /companies/{id}` returns the summary (including registry status), source-backed facts and history (including share capital), registered address, current shareholders, annual financials, identifiers, warnings, and review information.
- `GET /companies/{id}/registered-address` returns the current registered-address version.
- `GET /companies/{id}/shareholders` returns the current shareholder set.
- `GET /companies/{id}/financials` returns the financial time series.
- `GET /ingestion-runs/{id}` exposes accepted, unchanged, rejected, orphan, and warning records.
- `POST /companies/{id}/signals/digital-decay` runs the gated website check for one company.
- `GET /seller-prospects` powers the read-only seller prospect funnel in `/seller-prospects`. It accepts `min_revenue_eur`, `max_revenue_eur`, `sector`, and `limit`. The default €5m–€50m annual-revenue band is a provisional Nordic size proxy until Mergero confirms which size metric it uses.

The funnel applies an explainable sequence to the imported companies: annual revenue band, three consecutive comparable standalone EUR fiscal years, operating-profit persistence, and a peer-relative financial-profile index. It uses only reported revenue, operating profit, assets, and equity. The index combines robust Z-scores for median operating margin (70%) and equity/assets (30%) inside two-digit EMTAK groups with at least eight comparable peers. Smaller groups show no index. Report IDs, official source links, and data gaps remain visible. A shortlist is for advisor review, not automatic outreach. Buyer fit and owner intent are not assessed by these files. The dataset does not directly provide dividends or capex; the funnel does not use them or treat derived EBITDA as a reported value.

Each prospect also carries a separate **Cash Harvesting candidate** flag: revenue CAGR between -2% and +3% across the same three consecutive comparable years, and an EBITDA margin above 15% (strictly) in the latest one, both required and both read only from populated, comparable data. EBITDA comes from `company_financials.ebitda` (reported, or the importer's operating-profit-minus-depreciation/impairment derivation); operating-profit margin is never substituted for it. The flag never reads dividends or capex, in any form — no payout ratio, no dividend-history or capex comparison — because no imported row has either populated; a missing or incomparable input reports `insufficient_evidence` instead of guessing. The label is descriptive only: stable, high-margin, low-growth financials, not evidence of cash extraction or of an owner's intent to sell.

Sector filtering (`GET /sectors`, and the `sector` parameter on `/companies` and `/seller-prospects`) is built from source-backed two-digit EMTAK division codes, not the always-blank `companies.sector` field: each option needs at least 10 companies, and every smaller or unmapped group folds into a display-only `others` option, preserving each company's original industry code. Counts are recomputed from the live database on every request.

The response also reports the funnel stage by stage (imported → size band → three comparable years → three profitable years → advisor review), the EMTAK groups that carry a peer index, and two flags. The funnel does not check registry status (registered, in liquidation, bankrupt or deleted) at any stage; that fact is still imported and shown on the company's own profile, just not used here. `group_parent` means the company also filed consolidated accounts for its latest year, so the standalone figures may understate the sellable group; the reported group revenue is shown when available. `holding_activity` marks EMTAK 64.2x or 70.10 activity codes; these are sent to research rather than ranked, because a holding's margins are not comparable with operating peers. Each company has a deterministic brief: what the filings show, each line tied to a fiscal year, and what they cannot show. It also links to the official e-Business Register company page built from the registry code.

To see what the imported data actually covers, run the read-only coverage report from `api/`:

```powershell
.\.venv\Scripts\python.exe -m app.analysis.seller_coverage            # Markdown
.\.venv\Scripts\python.exe -m app.analysis.seller_coverage --json     # JSON
```

It lists field coverage by fiscal year and statement scope, fiscal-period lengths, the latest comparable revenue distribution, why in-band companies lack complete evidence, peer-group sizes, and flag counts.

## Digital decay signal (opt-in)

The `web-digital-decay` source flags operational stagnation from a company's own website:
- footer copyright ≥2 years old;
- news, press, or blog publishing cadence: no post for ≥18 months, or fewer than 3 distinct posts in the
  last 18 months. Posts come from the post/news sitemap (one date per post: translations such as `/en/blog/x/`
  and `/ru/blog/x/` count once, at their earliest lastmod; a lastmod shared by ≥4 posts is a bulk re-save and
  is ignored) and an on-page publication date beats the sitemap lastmod. Without a post sitemap, each distinct
  date on the news listing page counts as one post;
- zero open roles on the careers page.

The result is read against reported revenue and the register headcount trend (filed FTE, latest 12-month
fiscal year vs three years earlier; within ±10% is `flat`). Register FTE is official data shown as context,
not a website check.

It is disabled by default and requires `LIVE_CONNECTORS_ENABLED=true`. The check does the following:
- It only enriches companies already imported from the register.
- It picks the website in this order: the website the company declared to the register, then the company
  email domain declared to the register, then a domain guessed from the legal name. A guessed domain is used
  only after the site shows the registry code, or the legal name and registered address. A mail domain that
  several register entries share (group domain) is used only when the site itself names the company.
- Only the email *domain* is read from the register; mailbox names, phone and fax numbers are never stored.
- It uses the source rate limit.
- It stores extracted evidence only (no raw HTML) for 14 days.
- It does not use LinkedIn.

### How to run it

All commands run from `api/`. PowerShell is shown; on macOS/Linux use `.venv/bin/python` and set the variable
inline (`LIVE_CONNECTORS_ENABLED=true .venv/bin/python -m app.decay ...`).

1. **Load register data first** (see *First-time setup*). The check only enriches imported companies.
2. **Import the register-declared domains** (one time, idempotent). This reads the official general-data
   file (`ettevotja_rekvisiidid__yldandmed.json.zip`, about 230 MB, cached in `api/data/ee_ariregister_general/`)
   as a normal `ee-ariregister` ingestion run:

   ```powershell
   .\.venv\Scripts\python.exe -m app.decay --sync-register-domains --from-cache   # or without --from-cache to download
   ```

3. **Enable the source** (operator step, audited; once per database):

   ```powershell
   .\.venv\Scripts\python.exe -m app.decay --approve-and-enable
   .\.venv\Scripts\python.exe -m app.decay --status        # shows whether the gate is open and why not
   ```

4. **Turn on live access for the run.** Either add `LIVE_CONNECTORS_ENABLED=true` to `api/.env`, or set it
   only for the current shell:

   ```powershell
   $env:LIVE_CONNECTORS_ENABLED = "true"
   ```

5. **Run checks.** The usual run is the Cash Harvesting candidates that feed Seller Prospects:

   ```powershell
   # every Cash Harvesting candidate not checked in the last 30 days (max 200 per run)
   .\.venv\Scripts\python.exe -m app.decay --cash-harvesting --limit 100
   # re-check everything regardless of when it was last checked
   .\.venv\Scripts\python.exe -m app.decay --cash-harvesting --limit 100 --recheck-after-days 0

   # other targets
   .\.venv\Scripts\python.exe -m app.decay --registry-code 12345678 [--domain example.ee]
   .\.venv\Scripts\python.exe -m app.decay --name "Example OÜ" --address "Pärnu mnt 10, Tallinn"
   .\.venv\Scripts\python.exe -m app.decay --revenue-min 4000000 --revenue-max 6000000 --limit 15 [--json]
   ```

   A single company can also be checked from its detail page (**Run check**) or with
   `POST /companies/{id}/signals/digital-decay`.

6. **See the result.** Refresh **Seller Prospects**: coasting, decaying and watch companies move to the top,
   unchecked ones follow, and `active` ones go last (or tick *Hide companies whose website check is active*).

Practical notes:
- **Timing:** each site is limited to 20 requests a minute and needs roughly 6–10 fetches, so expect about
  30–40 seconds per company (≈25 minutes for 45 companies). Results are printed and saved when the run ends.
- **One run at a time:** SQLite allows one writer and a run holds it until it finishes, so parallel runs fail
  with `database is locked`. Run batches one after another.
- **Many results are `insufficient_evidence`:** small company sites often have no news page, careers page or
  footer year. That is expected; unknown checks are never counted as stale.

#### Sharing results without re-crawling

The database is git-ignored, so crawl results travel as a snapshot file keyed by registry code:

```powershell
.\.venv\Scripts\python.exe -m app.decay_snapshot --export seeds\digital_decay_YYYY-MM-DD.json
.\.venv\Scripts\python.exe -m app.decay_snapshot --import seeds\digital_decay_2026-09-27.json
```

`api/seeds/digital_decay_2026-09-27.json` holds the results for 45 Cash Harvesting candidates (7 flagged
coasting or watch). The import needs the register data loaded first, is idempotent, skips companies missing
from your database, and versions rather than overwrites existing decay facts.

Verdicts:
- `coasting`: revenue ≥ €5M, at least 2 checks stale, and zero roles.
- `decaying`: at least 2 checks stale.
- `watch`: revenue ≥ €5M, zero roles and flat or shrinking register headcount; the website is otherwise
  maintained.
- `active`
- `insufficient_evidence`: fewer than 2 determinable checks, or the domain is unverified.

Unknown checks never count as stale. Thresholds are configurable with `DECAY_COPYRIGHT_STALE_YEARS`, `DECAY_NEWS_STALE_MONTHS`, `DECAY_NEWS_MIN_POSTS` (default 3), `DECAY_HEADCOUNT_FLAT_PCT` (default 10) and `DECAY_MIN_REVENUE_EUR`. The company detail page shows the latest result with evidence links and a **Run check** button.

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
