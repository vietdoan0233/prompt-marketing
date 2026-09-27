# CLAUDE.md — Mergero Estonia Company Database

## Mission and scope

Maintain an internal, provenance-linked company database for Estonia. The production deployment is Estonia-only (`ACTIVE_COUNTRIES=EE`, region `baltics`) and its enabled ingestion source is the official Estonian e-Business Register bulk-data portal (`ee-ariregister`). The application imports company identity and registry status, registered-address versions, annual-report facts, financial rows, current share capital, and current shareholders. The official importer does not scrape company pages or annual-report PDFs, and it does not import the beneficial-owner (kasusaajad) file. A separate, opt-in website-activity signal may be used only through its own approved, disabled-by-default source and safe domain validation.

Read `ARCHITECTURE.md` before making implementation decisions. The source catalog and this file must stay aligned with the Estonia-only production scope.

## Product rules

1. Ingest only Mergero-approved public, licensed, or Mergero-supplied data. Disabled or unapproved sources must fail closed.
2. Respect the official portal's terms, access controls, rate limits, allowed fields, and retention requirements.
3. Keep `unknown`, `estimated`, `conflicting`, `derived`, and `verified` distinct. Never turn a missing value into a guessed value.
4. Keep source, collection time, confidence, usage policy, review status, and provenance for every material fact.
5. Preserve official legal status as a source-backed `registry_status` fact; do not infer it from company names or other fields.
6. Make imports idempotent using stable source keys and content hashes. Version address and fact changes rather than silently overwriting evidence.
7. Keep the minimum evidence needed to reproduce a record and expire raw snapshots according to configuration.
8. Treat any contact fields as personal data. Keep them source-backed; never create or enrich personal details from naming conventions. A person shareholder's name and holding are personal data too: store them as source-backed, but never read the register's national ID code, its one-way hash, birth date, or home address for a person shareholder into any table, snapshot, or log, regardless of what the source file contains.
9. Use reported FTE from the 2024 or 2025 indicator datasets as the default ≥20 employee scope filter. Keep previously known companies visible with an updated qualification status when newer reported FTE falls below the threshold.
10. The official Estonian e-Business Register bulk-data portal remains the only enabled bulk-import source. Any website activity check must be a separate approved, opt-in, disabled-by-default enrichment; it must use rate limits, verify the company/domain relationship, block private or reserved network targets on every request and redirect, and never create a company. Do not add Nordic, DACH, or social-network connectors.
11. Seller-prospect signals are evidence for human review, not proof of owner intent, willingness to sell, buyer fit, or mandate likelihood. Keep `owner_intent` unknown and `buyer_fit` not assessed unless separately supported by reviewed evidence.
12. Derive sector groups from source-backed EMTAK codes. Keep the original code and taxonomy metadata unchanged; low-count and unmapped companies may be grouped into a display-only `Others` bucket.
13. Do not trigger cash-harvesting flags when any required input is missing or incomparable. Missing dividends, capex, depreciation, or financial years remain unknown, never zero.

## Data model and API

- `companies` stores the consolidated company profile and qualification status.
- `company_facts` stores versioned facts such as `registry_status`, headcount, registry ID, industry code, and share capital (`{amount, currency}`) with source provenance.
- `registered_addresses` stores versioned official registered seats (`asukoht`), not operating locations.
- `company_shareholders` stores the current shareholder set (osanikud), versioned as one group per company like a registered address. A person shareholder keeps only a name, role, and holding; a legal-entity shareholder also keeps its (or a foreign entity's) registry code.
- `company_financials` stores annual report values by filing, statement scope, and explicit period length. Estonia rows use EUR. Period days and the short/standard/long class are exposed; values are never annualized. Reported EBITDA takes precedence, otherwise it is derived only when operating profit and depreciation plus impairment are both present; the formula and `value_type="derived"` are recorded.
- EMTAK code facts retain the official `emtak_version` metadata. Never infer taxonomy version from fiscal year.
- `source_snapshots`, `ingestion_runs`, `ingestion_records`, and `audit_events` preserve dataset evidence and import outcomes.

Company detail is served by `GET /companies/{id}`. It includes the company summary and registry status, source-backed facts, registered address, current shareholders, financials, identifiers, warnings, and review history. Dedicated time-series endpoints are `GET /companies/{id}/financials`, `GET /companies/{id}/registered-address`, and `GET /companies/{id}/shareholders`. Other relevant routes include `GET /companies`, `GET /ingestion-runs`, and `GET /ingestion-runs/{id}`.

## Audited implementation scope (pending)

The local audit on 2026-09-27 found `origin/codex/seller-funnel` to be the integration candidate: it includes `digital-decay-signal` and the current `main` commit. The standalone `digital-decay-signal` branch is older than `main`. Use the seller-funnel branch as the base for integration and preserve a reviewable diff.

Before enabling its website crawler, fix the caller-supplied-domain path: do not fetch a supplied host before validating it, do not follow redirects to private/reserved targets, and do not mark a supplied domain verified unless the company match succeeds. The source must remain opt-in and disabled by default.

Add cash harvesting as an explainable financial review flag. Evaluate comparable standalone EUR annual reports over three or four consecutive years; require EBITDA margin above 15%, revenue CAGR from -2% through +3%, and dividends/net income above 70% for a valid signal. Compare payout ratios over prior years when describing a dividend spike. Treat capex below depreciation as supporting evidence only when both source-backed values are present. Never label the signal as confirmed seller intent.

The local database audit found 3,159 companies and 20,805 financial rows. About 2,517 companies have three consecutive comparable standalone EUR years with revenue, EBITDA, and net income, but no stored row has dividends or capex populated. `depreciation_and_impairment` exists for some rows; it does not replace missing capex. Extend and validate source mappings before allowing a positive cash-harvesting trigger; otherwise return insufficient evidence.

For sector filtering, the local `companies.sector` field is blank for all 3,159 companies. Use the existing two-digit EMTAK peer grouping instead of that empty field: 57 groups meet the 10-company threshold and contain 3,067 companies; 90 companies fall in 23 smaller groups and 2 lack a usable code. Put those 92 into a display-only `Others` bucket without overwriting their source codes. Keep sector counts dynamic rather than hard-coded to this local database snapshot.

## Working conventions

- Preserve repository conventions and prefer typed, testable vertical changes.
- Keep domain logic independent of frameworks and external providers.
- Use migrations for database schema changes and seed commands for source synchronization/import.
- Put provider behavior behind explicit configuration. Live access is off unless `LIVE_CONNECTORS_ENABLED=true`.
- Use UTC timestamps in storage and ISO 8601 at API boundaries.
- Validate external input with typed schemas and return provenance and warnings in the database browser.
- Do not add dependencies unless they materially reduce implementation risk.

## Data quality and safety

Imports must be deterministic for the same source snapshot, parser version, and configuration. Explain accepted, rejected, skipped, duplicate, incomplete, stale, and conflicting records with source references. A missing field reduces completeness; it does not justify inventing a value. Corrections preserve the original fact and provenance. Financial observations keep their source duration and must not be annualized. If ownership-event data enters scope, retain legal effective, registry-entry, and ingestion dates separately and flag the 2023-09-01 reporting change before interpreting clustered entries as acquisitions.

Keep raw snapshots short-lived and configurable. Redact personal contact data and credentials from debug logs. Do not put unnecessary personal data or raw source payloads into LLM prompts. Generated summaries must cite supplied facts and remain marked as generated until reviewed.

## Expected quality bar

Run the requested formatter, linter, type checker, migration consistency check, and tests. Verify the Estonia import lifecycle, cache integrity and missing-year handling, idempotency, address versioning, share-capital and shareholder versioning, that no person shareholder's ID code, ID hash, birth date, or home address ever reaches a table, snapshot, or API response, financial derivation and currency, status exposure, orphan rejection, permissions, and provenance. Add coverage for website-domain identity validation, private-network and redirect blocking, missing financial measures, cash-harvesting thresholds, and the 10-company sector/`Others` boundary. Keep setup instructions usable from a clean checkout; explain that the ignored `api/data/` cache must be downloaded once or copied from a verified cache before offline `--from-cache` use.
