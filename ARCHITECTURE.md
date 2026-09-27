# Mergero Estonia Company Database — Architecture

## Purpose and production scope

This internal application imports and presents source-backed company information for Estonia. Production configuration is Estonia-only (`ACTIVE_COUNTRIES=EE`) in the `baltics` region. The only enabled bulk-import source is the official [Estonian e-Business Register open-data portal](https://avaandmed.ariregister.rik.ee/et/avaandmete-allalaadimine), connector ID `ee-ariregister`. A separate approved website-activity enrichment may run only when explicitly enabled and opted into; it must never create a company.

The importer uses the portal's basic company, annual-report metadata, EMTAK activity, and annual indicator CSV ZIPs, plus two optional JSON ZIPs: general company data (current share capital) and shareholders (osanikud). It does not scrape company websites beyond the opt-in signal below, download annual-report PDFs, or ingest the beneficial-owner (kasusaajad) or personal-register datasets. A person shareholder is stored by name, role, and holding only: the official file's national ID code, its one-way hash, birth date, and home address are never read by the importer, so they can never reach storage. If event data is added later, legal effective dates, registry-entry dates, and ingestion dates must remain distinct; the 1 September 2023 ownership-register change is a structural break and its bulk of resulting entries must not be presented as ordinary acquisitions without corroboration. Source permission and allowed fields are checked before an import.

An opt-in, secondary source `web-digital-decay` (tier C, `connector_type: website_decay`) computes an operational-stagnation signal from a company's own website. It is approved but disabled by default and fails closed through the normal permission gate plus `LIVE_CONNECTORS_ENABLED`. It never creates companies: targets come from existing registry companies (legal name, registry code, current registered address, latest reported revenue). The website is taken first from the domains the company declared to the register (active WWW entries, then the domain part of active EMAIL entries, imported once from the official general-data file `ettevotja_rekvisiidid__yldandmed.json.zip` by `python -m app.decay --sync-register-domains`; mailbox local-parts and phone numbers are never read, and register-declared domains are not identity keys because group companies share them). Only when no declared domain is reachable is a domain guessed from the legal name, and a guessed domain is used only after the site shows the registry code, or the legal name together with the registered postal code or street; an unverified guess is never used. Every caller-supplied or guessed host is validated before the first network access and on every redirect hop, after DNS resolution: private, reserved, loopback, link-local, and cloud metadata-service IP ranges are blocked outright (defeating DNS rebinding, since the check is against the resolved address actually being connected to, not the hostname), and a domain is marked verified only after the company-identity match above succeeds — an unverified guess is never used and never marked verified regardless of how the check result comes out. The crawler reads robots.txt, the home page, sitemap.xml (or sitemap_index.xml / wp-sitemap.xml, plus up to two post/news child sitemaps), a news/press page, and a careers page (at most 8 pages per domain, rate-limited). Snapshots store only extracted evidence (years, dates, job links, URLs, the Last-Modified header) with 14-day retention. The news check measures publishing cadence: one date per distinct post (translations counted once at their earliest sitemap lastmod, a lastmod shared by ≥4 posts treated as a bulk re-save and ignored, an on-page publication date beating lastmod, the news listing page's dates only when there is no post sitemap); it is stale when the newest post is ≥18 months old or fewer than `DECAY_NEWS_MIN_POSTS` (3) posts fall in the last 18 months. The register headcount trend (filed FTE from standard 12-month annual reports, latest year vs three years earlier, flat within ±`DECAY_HEADCOUNT_FLAT_PCT` %) is added as official-data context; it never counts as a website check. Verdicts, first match wins: `insufficient_evidence` (unverified domain or fewer than 2 determinable checks), `coasting` (revenue ≥ €5M, ≥2 stale, zero roles), `decaying` (≥2 stale), `watch` (revenue ≥ €5M, zero roles, flat or shrinking register headcount), `active`. Heuristics are deterministic for the same snapshot and date; no LLM is used. LinkedIn and other social networks are out of scope.

## System shape

```text
Official RIK bulk files or verified local cache
                    |
           ee-ariregister resolver
                    |
                Estonia import service
          /            |            |            \
 company facts     address       annual       shareholders
 (+ share capital)  versions    financials
          \            |            |            /
                  FastAPI + SQLAlchemy
                    |
        Next.js company database UI
```

The seller-prospect analysis reads stored financials and registry facts without triggering ingestion. A separate, opt-in Digital Decay connector reads a verified company's public website and writes evidence as a provenance-linked estimated fact; it remains disabled by default.

The web app uses Next.js and TypeScript. The API and import process use FastAPI, SQLAlchemy, and Alembic. SQLite is the local default; deployment may configure another SQLAlchemy-supported database.

## Core tables

### `companies`

Stores the consolidated company profile, identity, headcount range, review state, and current qualification state. Stable registry identifiers are stored in `company_identifiers`. Previously known companies are refreshed when a newer 2024/2025 filing shows FTE below the import threshold, so their qualification state and employee fact reflect the latest evidence.

### `company_facts`

Stores versioned, source-backed claims including `registry_status`, legal name, registry ID, headcount, industry code, and share capital. Each fact retains its source, source key and URL, observed time, confidence, usage policy, review state, and link to an import run and source snapshot. Industry-code facts also retain `code_system` and `code_version` from the official EMTAK `emtak_version` column; a version is never inferred from report year. Share-capital facts hold `{amount, currency}` from the general-data file's current `kapitalid` entry (the source's own currency; not necessarily EUR, though it always is under Estonian company law today). The company list and `GET /companies/{id}` expose versioned industry-code details. `GET /companies/{id}` returns active facts and superseded history.

### `company_shareholders`

Stores current shareholders (osanikud) from the official register, one row per holder. A person shareholder is stored by `holder_name` and `holding_*` only; a legal-entity shareholder also keeps `holder_registry_code` (its own registry code, or a foreign entity's foreign code with `holder_country`). The register's national ID code, its one-way hash, birth date, and home address are personal data belonging to a person shareholder and are never read by the importer, so they can never reach this table or a source snapshot. The whole reported shareholder set for a company is versioned together, like a registered address: an unchanged set is confirmed in place, a changed set closes the current rows (`valid_to`) and inserts the new set. `GET /companies/{id}` and `GET /companies/{id}/shareholders` return the current set.

### `registered_addresses`

Stores the official registered seat (`asukoht`) separately from the consolidated profile. Address rows are versioned: an unchanged mapped address does not add a row, a changed address closes the active version, and a later reversion creates a new version. Address parts may be null when the source is incomplete or ambiguous. This table does not claim to represent an operating location.

### `company_financials`

Stores one financial observation per company, filing, period, and statement scope. It retains reported values, source lines, currency/unit, source file, snapshot, parser version, confidence, usage policy, ingestion run, and review state. `period_days` is the inclusive day count between `period_start` and `period_end`; `period_length_class` is `short`, `standard_12_month` (365 or 366 days), `long`, or `invalid`. Missing dates leave both fields null. These values are returned by the financial API and shown in the company detail table. Financial amounts are never annualized. Estonian monetary values are recorded in EUR. Reported EBITDA takes precedence. If EBITDA is absent but operating profit and depreciation/impairment are both present, the importer derives it using `operating_profit - depreciation_and_impairment`: the official statement preserves expenses as negative values, so subtracting that signed expense adds it back. Derived rows use `value_type="derived"`. Other absent measures remain null.

### Seller-prospect signals (planned integration)

The audited integration candidate is `origin/codex/seller-funnel`, which contains the Digital Decay branch and current `main`; the standalone `digital-decay-signal` branch is behind `main`. Seller-prospect calculations are read-only and explainable. Financial screens require comparable standalone EUR statements and preserve `unknown` when periods or measures are missing. They must not infer owner intent, buyer fit, or mandate probability.

The planned Cash Harvesting flag uses three or four consecutive comparable years and requires EBITDA margin above 15%, revenue CAGR between -2% and +3%, and dividends/net income above 70%. Payout-ratio changes over prior years support the description of a dividend spike. Capex below depreciation is supporting evidence only when both values are source-backed; it is not a substitute for the three core conditions. Keep the signal separate from Digital Decay and from the seller's intent field.

Local database audit on 2026-09-27: `api/mergero_dev.db` contains 3,159 companies and 20,805 financial rows. About 2,517 companies have three consecutive comparable standalone EUR years with revenue, EBITDA, and net income. Dividends and capex are null in all current rows; depreciation/impairment is populated for some rows. Until source mapping provides dividends, the payout ratio is not evaluable and a positive Cash Harvesting flag must not be emitted.

### Sector grouping (planned)

The current local `companies.sector` values are blank, so using that field alone would place every company in `Others`. Derive filter groups from the existing source-backed EMTAK codes, using the same two-digit division level as the seller-funnel peer groups. Require at least 10 companies per displayed group and aggregate smaller or unmapped groups into a display-only `Others` bucket without changing original industry codes or taxonomy metadata. The local snapshot has 57 groups meeting the threshold (3,067 companies), 90 companies across 23 smaller groups, and 2 companies without a usable code; calculate live counts rather than hard-coding these values.

### Digital Decay website signal (planned integration)

The website check is a separate opt-in enrichment, disabled by default, not a bulk importer. Enforce per-host rate limits, page budgets, and approved field/retention rules. Validate caller-supplied domains before network access; prevent private, reserved, loopback, link-local, and metadata-service access both before requests and across redirects; verify that the public page belongs to the company before storing a verified domain or signal. Missing or inconclusive website evidence remains `insufficient_evidence`.

### Provenance and operations

`sources` holds connector permission and allowed-field configuration. `source_snapshots` records file and row hashes, source URLs, parser versions, and retention metadata. `ingestion_runs` and `ingestion_records` keep outcome counts, warnings, errors, and rejected orphan indicators. `audit_events` records import completion and review actions.

## Import flow and safety

The Estonia import performs these stages:

1. Apply the source permission gate and resolve requested official datasets.
2. Require every requested indicator year and both qualification years (2024 and 2025). Missing files fail before company upserts. The seed command also resolves and validates all required files before a `--replace` backup and purge. The general-data and shareholders files are optional: either being missing (a portal outage, or an offline cache predating them) only adds a run warning and skips that section.
3. Snapshot file metadata and load annual-report metadata.
4. Select companies using non-consolidated reported FTE from 2024/2025, apply the legal-form allowlist, and refresh already-known companies that have fallen below the threshold.
5. Upsert source-backed company facts and status (including current share capital, when the general-data file is present), then version the registered address.
6. Join indicator rows to annual reports, reject and log orphan 2024 report IDs, and upsert financial rows by stable source key and content hash.
7. When the shareholders file is present, version each in-scope company's shareholder set (see `company_shareholders` above).
8. Record counts and warnings, mark the run complete, and write an audit event.

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
GET    /companies/{id}/shareholders
POST   /companies/{id}/facts/corrections
POST   /companies/{id}/signals/digital-decay
GET    /companies/{id}/audit-events
GET    /audit-events
GET    /quality/report
GET    /quality/duplicates
```

- `POST /companies/{id}/signals/digital-decay` — run the gated website check for one company (403 when the source is unapproved, disabled, or live access is off).

`GET /companies/{id}` includes `company.registry_status`, the `registry_status` fact and provenance, current registered address, current shareholders, financial rows, identifiers, warnings, review history, and the latest digital-decay signal when a check has been run. `GET /companies` defaults to at least 20 employees and supports qualification and review filters.

## Local development and checks

Use Python 3.12 or newer, as required by `api/pyproject.toml`. Follow `README.md` for Windows PowerShell setup and the first live download or verified-cache copy. The documented checks are pytest, Ruff format/lint, mypy, Alembic schema consistency, TypeScript type checking, and the Next.js production build.
