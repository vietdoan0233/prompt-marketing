# CLAUDE.md — Mergero Database Scraper

## Mission

Build the smallest credible internal prototype for Mergero's permission-gated company database scraper. Start with a Nordic and DACH workflow. The system discovers, fetches, parses, validates, normalizes, deduplicates, and stores company records and source-backed facts from approved data sources. Its output is a reliable, provenance-linked database for internal use; the scope ends after ingestion, review, and data-quality reporting.

Read `ARCHITECTURE.md` before making implementation decisions. The architecture is the source of truth for source permissions, provenance, normalization, retention, and regional data rules.

## Product rules

1. Ingest only Mergero-approved public, licensed, or Mergero-supplied data. Disabled or unapproved sources must fail closed.
2. Respect each source's terms, access controls, rate limits, allowed fields, and retention requirements. Do not bypass authentication or technical restrictions.
3. Keep `unknown`, `estimated`, `conflicting`, and `verified` distinct. Never turn a missing value into a guessed value.
4. Store source, collection time, confidence, usage policy, and review status for every material fact.
5. Never infer sensitive attributes or fabricate company, ownership, financial, employee, or contact data.
6. Make discovery and imports idempotent using stable source keys and content hashes.
7. Preserve the minimum evidence needed to reproduce a record, and expire raw snapshots according to configuration.
8. Treat scraped contact fields as personal data. Keep them source-backed and do not create or enrich personal details from naming conventions.
9. Filter by company size qualification: target a baseline of ≥20 employees as the primary proxy for viable scale (since public financial data is often unavailable). Flag sub-scale (1–2 person) micro-entities as out-of-scope or low priority.
10. Target approved discovery and enrichment tiers: official national registries (YTJ/PRH, Allabolag/Bolagsverket, CVR, Brreg in Nordics; Handelsregister, North Data, FirmenABC/Firmenbuch, Zefix in DACH), target company websites (extracting legal entity and managing directors via `/impressum`, founder signals via `/about` and `/team`, and growth/headcount via `/careers`), and approved B2B firmographic feeds.

## Working conventions

- Preserve existing repository conventions if they appear; otherwise use a typed, testable structure.
- Prefer small vertical ingestion slices over scaffolding a large platform.
- Keep domain logic independent of frameworks and external providers.
- Use migrations and seed data so a reviewer can run the demo without credentials.
- Put provider-specific behavior behind interfaces and configuration.
- Use UTC timestamps in storage and ISO 8601 at API boundaries.
- Validate all external input with typed schemas.
- Return provenance and warnings in API responses used by the database browser.
- Use feature flags or explicit configuration for connectors; disabled connectors must fail closed.
- Avoid adding dependencies unless they materially reduce implementation risk.

## Required implementation order

1. Inspect the repository and document any existing stack.
2. Add the domain schema, migrations, and seed data.
3. Implement the source registry, permission gate, provenance model, and CSV importer.
4. Implement the connector interface for discovery, fetching, parsing, validation, and upsert.
5. Implement normalization, stable identity resolution, deduplication, and idempotent reruns.
6. Implement the database browser and ingestion-run detail view with source links and warnings.
7. Add data-quality checks, correction history, retention handling, and audit events.
8. Add automated tests and a concise local runbook.

Do not move to the next slice while the previous slice cannot be demonstrated locally.

## Data-quality requirements

The ingestion pipeline must be deterministic for the same source snapshot, parser version, and configuration. It must explain accepted, rejected, skipped, duplicate, incomplete, stale, and conflicting records with source references. A missing field reduces completeness; it does not justify inventing a value. Corrections must preserve the original fact and its provenance.

If Claude or another LLM is used inside the application, limit it to schema mapping or human-readable summaries of supplied source facts and warnings. It must not decide permissions, invent values, silently rewrite source data, access unapproved sources, or trigger external side effects.

## Data and prompt safety

- Keep raw snapshots short-lived and configurable.
- Redact personal contact data and credentials from debug logs.
- Do not put unnecessary personal data or raw source payloads into LLM prompts.
- Persist parser/provider metadata and the source facts used for any generated data-quality summary.
- Mark generated summaries as generated until reviewed.
- Add tests that reject missing provenance, unauthorized sources, fabricated values, and duplicate records.

## Expected quality bar

Before declaring work complete:

- Run the formatter, linter, type checker, and tests.
- Test idempotent imports, regional permission enforcement, source retention, normalization, deduplication, correction history, and provenance.
- Exercise the happy path from an approved source query or CSV through a reviewed database record.
- Verify the app runs from a clean checkout using documented commands.
- Update documentation when behavior or setup changes.
- Report any provider credential, source permission, or legal/compliance dependency as a clear blocker; do not silently substitute an unapproved scraper.

## Scope boundary

Keep implementation limited to source discovery, fetching, parsing, validation, normalization, deduplication, provenance, database review, and data-quality reporting. Anything beyond that boundary requires a separate request.

Do not make AI valuation claims without verified financial data.
