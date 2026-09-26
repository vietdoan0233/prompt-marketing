# CLAUDE.md — Mergero Estonia Company Database

## Mission and scope

Maintain an internal, provenance-linked company database for Estonia. The production deployment is Estonia-only (`ACTIVE_COUNTRIES=EE`, region `baltics`) and has one enabled ingestion source: the official Estonian e-Business Register bulk-data portal (`ee-ariregister`). The application imports company identity and registry status, registered-address versions, annual-report facts, and financial rows. It does not download annual-report PDFs. The only company-website access is the opt-in `web-digital-decay` signal described in rule 10, which is disabled by default.

Read `ARCHITECTURE.md` before making implementation decisions. The source catalog and this file must stay aligned with the Estonia-only production scope.

## Product rules

1. Ingest only Mergero-approved public, licensed, or Mergero-supplied data. Disabled or unapproved sources must fail closed.
2. Respect the official portal's terms, access controls, rate limits, allowed fields, and retention requirements.
3. Keep `unknown`, `estimated`, `conflicting`, `derived`, and `verified` distinct. Never turn a missing value into a guessed value.
4. Keep source, collection time, confidence, usage policy, review status, and provenance for every material fact.
5. Preserve official legal status as a source-backed `registry_status` fact; do not infer it from company names or other fields.
6. Make imports idempotent using stable source keys and content hashes. Version address and fact changes rather than silently overwriting evidence.
7. Keep the minimum evidence needed to reproduce a record and expire raw snapshots according to configuration.
8. Treat any contact fields as personal data. Keep them source-backed; never create or enrich personal details from naming conventions.
9. Use reported FTE from the 2024 or 2025 indicator datasets as the default ≥20 employee scope filter. Keep previously known companies visible with an updated qualification status when newer reported FTE falls below the threshold.
10. The approved production source is the official Estonian e-Business Register bulk-data portal. Do not wire Nordic, DACH, or unapproved social-network (including LinkedIn) connectors into the Estonia production workflow. The single company-website exception is the opt-in `web-digital-decay` source: it is approved but `enabled: false` in `sources.yaml`, must be enabled by an operator (`python -m app.decay --approve-and-enable`) and needs `LIVE_CONNECTORS_ENABLED=true`; it only enriches existing companies, uses the website or company email domain the company declared to the register, otherwise verifies a guessed domain against the registry code or legal name plus registered address before use, respects robots.txt and the source rate limit, stores extracted evidence (never raw HTML), and records all outputs as `estimated` facts.

## Data model and API

- `companies` stores the consolidated company profile and qualification status.
- `company_facts` stores versioned facts such as `registry_status`, headcount, registry ID, and industry code with source provenance.
- Digital-decay outputs are `company_facts` from `web-digital-decay`: `website`, `footer_copyright_year`, `latest_news_date`, `open_positions`, and `digital_decay_signal` (JSON verdict with per-check evidence URLs). A verified domain is also stored as a derived `company_identifiers` row of kind `domain`. Unknown checks never count as stale, and a missing revenue value prevents a `coasting` or `watch` verdict. The news check is a publishing-cadence check (stale at no post for 18 months or fewer than 3 distinct posts in 18 months; translations counted once, bulk re-save lastmod dates ignored, on-page dates beat sitemap lastmod). The signal also carries `checks.headcount`, the register FTE trend (source-backed filed figures, not a website check), used only for the `watch` verdict (revenue ≥ €5M, zero open roles, flat or shrinking register headcount). Verdict order: `insufficient_evidence`, `coasting`, `decaying`, `watch`, `active`. Settings: `DECAY_NEWS_MIN_POSTS`, `DECAY_HEADCOUNT_FLAT_PCT`.
- `registered_addresses` stores versioned official registered seats (`asukoht`), not operating locations.
- `company_financials` stores annual report values by filing, statement scope, and explicit period length. Estonia rows use EUR. Period days and the short/standard/long class are exposed; values are never annualized. Reported EBITDA takes precedence, otherwise it is derived only when operating profit and depreciation plus impairment are both present; the formula and `value_type="derived"` are recorded.
- EMTAK code facts retain the official `emtak_version` metadata. Never infer taxonomy version from fiscal year.
- `source_snapshots`, `ingestion_runs`, `ingestion_records`, and `audit_events` preserve dataset evidence and import outcomes.

Company detail is served by `GET /companies/{id}`. It includes the company summary and registry status, source-backed facts, registered address, financials, identifiers, warnings, and review history. Dedicated time-series endpoints are `GET /companies/{id}/financials` and `GET /companies/{id}/registered-address`. Other relevant routes include `GET /companies`, `GET /ingestion-runs`, and `GET /ingestion-runs/{id}`. `POST /companies/{id}/signals/digital-decay` runs the gated website check; the detail response includes `digital_decay`.

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

Run the requested formatter, linter, type checker, migration consistency check, and tests. Verify the Estonia import lifecycle, cache integrity and missing-year handling, idempotency, address versioning, financial derivation and currency, status exposure, orphan rejection, permissions, and provenance. Keep setup instructions usable from a clean checkout; explain that the ignored `api/data/` cache must be downloaded once or copied from a verified cache before offline `--from-cache` use.
