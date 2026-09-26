# Mergero Estonia Company Database — Architecture

## Purpose and production scope

This internal application imports and presents source-backed company information for Estonia. Production configuration is Estonia-only (`ACTIVE_COUNTRIES=EE`) in the `baltics` region. The only enabled production ingestion source is the official [Estonian e-Business Register open-data portal](https://avaandmed.ariregister.rik.ee/et/avaandmete-allalaadimine), connector ID `ee-ariregister`.

The importer uses the portal's basic company, annual-report metadata, EMTAK activity, and annual indicator CSV ZIPs. It does not download annual-report PDFs, or ingest shareholder, beneficial-owner, or personal-register datasets. If event data is added later, legal effective dates, registry-entry dates, and ingestion dates must remain distinct; the 1 September 2023 ownership-register change is a structural break and its bulk of resulting entries must not be presented as ordinary acquisitions without corroboration. Source permission and allowed fields are checked before an import.

An opt-in, secondary source `web-digital-decay` (tier C, `connector_type: website_decay`) computes an operational-stagnation signal from a company's own website. It is approved but disabled by default and fails closed through the normal permission gate plus `LIVE_CONNECTORS_ENABLED`. It never creates companies: targets come from existing registry companies (legal name, registry code, current registered address, latest reported revenue). The website is taken first from the domains the company declared to the register (active WWW entries, then the domain part of active EMAIL entries, imported once from the official general-data file `ettevotja_rekvisiidid__yldandmed.json.zip` by `python -m app.decay --sync-register-domains`; mailbox local-parts and phone numbers are never read, and register-declared domains are not identity keys because group companies share them). Only when no declared domain is reachable is a domain guessed from the legal name, and a guessed domain is used only after the site shows the registry code, or the legal name together with the registered postal code or street; an unverified guess is never used. The crawler reads robots.txt, the home page, sitemap.xml (or sitemap_index.xml / wp-sitemap.xml, plus up to two post/news child sitemaps), a news/press page, and a careers page (at most 8 pages per domain, rate-limited). Snapshots store only extracted evidence (years, dates, job links, URLs, the Last-Modified header) with 14-day retention. The news check measures publishing cadence: one date per distinct post (translations counted once at their earliest sitemap lastmod, a lastmod shared by ≥4 posts treated as a bulk re-save and ignored, an on-page publication date beating lastmod, the news listing page's dates only when there is no post sitemap); it is stale when the newest post is ≥18 months old or fewer than `DECAY_NEWS_MIN_POSTS` (3) posts fall in the last 18 months. The register headcount trend (filed FTE from standard 12-month annual reports, latest year vs three years earlier, flat within ±`DECAY_HEADCOUNT_FLAT_PCT` %) is added as official-data context; it never counts as a website check. Verdicts, first match wins: `insufficient_evidence` (unverified domain or fewer than 2 determinable checks), `coasting` (revenue ≥ €5M, ≥2 stale, zero roles), `decaying` (≥2 stale), `watch` (revenue ≥ €5M, zero roles, flat or shrinking register headcount), `active`. Heuristics are deterministic for the same snapshot and date; no LLM is used. LinkedIn and other social networks are out of scope.

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

Stores versioned, source-backed claims including `registry_status`, legal name, registry ID, headcount, and industry code. Each fact retains its source, source key and URL, observed time, confidence, usage policy, review state, and link to an import run and source snapshot. Industry-code facts also retain `code_system` and `code_version` from the official EMTAK `emtak_version` column; a version is never inferred from report year. The company list and `GET /companies/{id}` expose versioned industry-code details. `GET /companies/{id}` returns active facts and superseded history.

### `registered_addresses`

Stores the official registered seat (`asukoht`) separately from the consolidated profile. Address rows are versioned: an unchanged mapped address does not add a row, a changed address closes the active version, and a later reversion creates a new version. Address parts may be null when the source is incomplete or ambiguous. This table does not claim to represent an operating location.

### `company_financials`

Stores one financial observation per company, filing, period, and statement scope. It retains reported values, source lines, currency/unit, source file, snapshot, parser version, confidence, usage policy, ingestion run, and review state. `period_days` is the inclusive day count between `period_start` and `period_end`; `period_length_class` is `short`, `standard_12_month` (365 or 366 days), `long`, or `invalid`. Missing dates leave both fields null. These values are returned by the financial API and shown in the company detail table. Financial amounts are never annualized. Estonian monetary values are recorded in EUR. Reported EBITDA takes precedence. If EBITDA is absent but operating profit and depreciation/impairment are both present, the importer derives it using `operating_profit - depreciation_and_impairment`: the official statement preserves expenses as negative values, so subtracting that signed expense adds it back. Derived rows use `value_type="derived"`. Other absent measures remain null.

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
POST   /companies/{id}/signals/digital-decay
GET    /companies/{id}/audit-events
GET    /audit-events
GET    /quality/report
GET    /quality/duplicates
```

- `POST /companies/{id}/signals/digital-decay` — run the gated website check for one company (403 when the source is unapproved, disabled, or live access is off).

`GET /companies/{id}` includes `company.registry_status`, the `registry_status` fact and provenance, current registered address, financial rows, identifiers, warnings, and review history. `GET /companies` defaults to at least 20 employees and supports qualification and review filters.

## Local development and checks

Use Python 3.12 or newer, as required by `api/pyproject.toml`. Follow `README.md` for Windows PowerShell setup and the first live download or verified-cache copy. The documented checks are pytest, Ruff format/lint, mypy, Alembic schema consistency, TypeScript type checking, and the Next.js production build.
