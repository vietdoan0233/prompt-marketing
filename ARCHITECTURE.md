# Mergero Company Database Scraper — Architecture

## 1. Purpose

Build an internal, permission-gated data-ingestion pipeline that creates and maintains a reliable company database for Mergero. The first demonstrable workflow covers Nordic and DACH regions. The system discovers candidate company records, fetches approved source data, parses and normalizes it, validates the result, deduplicates it, and stores source-backed facts with provenance.

The product is an internal data pipeline and database browser. It is not a generic unrestricted web scraper, a public marketplace, or an autonomous system that takes action outside the database. Its scope ends at ingestion, review, correction, and data-quality reporting.

Only Mergero-approved public, licensed, or Mergero-supplied data may enter the pipeline. The UI must make source permission, provenance, freshness, confidence, and data-quality warnings visible.

## 2. MVP outcome

1. Register and enable an allowed source.
2. Run a region-, sector-, and headcount-scoped discovery/import job (targeting ≥20 employees as baseline viable scale), or load a CSV export.
3. Fetch source records within the source's access, rate, field, and retention rules.
4. Review normalized company facts with source links, collection dates, confidence, and warnings.
5. Resolve stable company identity and deduplicate repeated records without losing source history.
6. Correct a fact while preserving the original value, source, and audit trail.
7. Rerun the same import safely and produce the same result for the same snapshot and parser version.
8. Inspect ingestion-run metrics, rejected records, incomplete records, conflicts, and expired snapshots.

The prototype should work end-to-end with seeded data and Nordic/DACH connectors or CSV import. No external action is part of the MVP.

## 3. System context

```text
                         +----------------------+
                         | Mergero data         |
                         | browser              |
                         +----------+-----------+
                                    |
                            REST/typed API
                                    |
+------------------+      +--------v---------+      +------------------+
| Source registry  | ---> | Ingestion API    | ---> | PostgreSQL       |
| CSV / seed data  |      | parse + validate |      | companies + facts|
+------------------+      +--------+---------+      | + provenance    |
                                    |               +------------------+
                            ingestion jobs
                                    |
       +----------------------------+-----------------------------+
       |                            |                             |
+------v-------+             +------v-------+               +-------v--------+
| Nordic       |             | Mergero      |               | DACH            |
| public/      |             | approved    |               | public/         |
| approved     |             | CSV/API     |               | approved        |
| connectors   |             | connector   |               | connectors      |
+--------------+             +--------------+               +----------------+
```

Recommended implementation shape:

- **Web app:** Next.js + TypeScript for the internal database browser.
- **API and jobs:** Python FastAPI plus a worker process (Celery/RQ/Prefect is acceptable; keep the first version simple).
- **Database:** PostgreSQL. Add PostGIS only if geography queries require it. Use pgvector only when semantic search is demonstrably useful; do not make it a prerequisite for the MVP.
- **Local deployment:** Docker Compose with PostgreSQL and the app. Keep provider credentials in environment variables; include `.env.example`, never secrets.
- **Parsing boundary:** Keep provider-specific fetching and parsing behind connector interfaces. Persist parser version, source snapshot metadata, and normalized output so imports are reproducible.

The exact framework may change if the repository already establishes a different stack. Preserve the source, provenance, permission, and retention boundaries below.

## 4. Core data model

The canonical model is relational and event-friendly. Important facts should be append-only or versioned instead of silently overwritten.

### `companies`

- `id`, `legal_name`, `trading_name`, `country`, `region`, `city`
- `website`, `industry_codes[]`, `description`
- `estimated_employee_min`, `estimated_employee_max`
- `revenue_min`, `revenue_max`, `currency`
- `ownership_type` (founder-led, family-owned, PE-backed, corporate, unknown) when supplied by an approved source
- `first_seen_at`, `last_verified_at`

Stable source identifiers and normalized identity keys should be stored separately from display fields so that identity resolution can be rerun without destroying source values.

### `company_facts`

One row per material claim rather than one untraceable company blob:

- `id`, `company_id`, `field_name`, `value_json`
- `source_id`, `observed_at`, `valid_from`, `valid_to`
- `confidence` (verified, multi-source, estimated, old, conflicting, unknown)
- `usage_policy` (internal-only, restricted, prohibited, or another source-approved policy)
- `review_status`, `reviewed_by`, `reviewed_at`

Every displayed material value must be traceable to one or more source facts. Do not silently replace a conflicting value; retain the conflict and surface it for review.

### `sources` and `source_snapshots`

- `sources`: provider, country coverage, source type, terms URL, permission status, connector type, allowed fields, enabled flag.
- `source_snapshots`: retrieval timestamp, request identifier, content hash, source URL, raw payload location, parser version, HTTP status, retention expiry.

Do not store raw pages indefinitely by default. Store the minimum evidence needed for reproducibility and delete or expire snapshots according to the configured retention policy.

### `contacts` (optional source data)

If an approved source exposes business contact fields and those fields are in scope for the source, store them only as source-backed data:

- `id`, `company_id`, `name`, `role`, `email`, `phone`, `profile_url`
- `country`, `language`, `source_id`, `confidence`
- `contact_basis` (public-business, Mergero-supplied, partner-referral, unknown)
- `usage_policy`, `last_verified_at`

Treat contact data as personal data. Do not infer sensitive attributes or create personal details from naming conventions. If a source provides suppression or consent metadata, preserve it as source data and apply its retention and usage policy.

### `ingestion_runs` and `audit_events`

An ingestion run should record the source, query or file identity, region, start/end time, parser version, configuration hash, counts by outcome, and errors/warnings. Audit events record who or what changed a source permission, fact, correction, identity link, retention status, or parser configuration.

## 5. Ingestion and source policy

Every connector implements the same interface:

```text
discover(query, region, min_employees=20) -> candidate references
fetch(reference) -> source snapshot
parse(snapshot) -> normalized facts + optional contacts
validate(records) -> warnings/errors
upsert(records) -> ids + provenance links
```

Each stage must be observable and safe to retry. A failed permission check or validation step must not partially write an unapproved record.

### Company qualification and size filtering

Because timely public financial data is often unavailable for private companies, employee headcount is used as the primary proxy for company scale and M&A viability. By default, discovery and ingestion qualify companies with **≥20 employees** (e.g. verified through official register filings, reports, or approved headcount indicators), while tagging 1–2 person micro-entities as sub-scale/low-priority unless explicitly included.

### Regional policy configuration

```yaml
regions:
  nordics:
    countries: [FI, SE, NO, DK, IS]
    source_modes: [public-approved, mergero-supplied, licensed]
    default_language: en
  dach:
    countries: [DE, AT, CH]
    source_modes: [public-approved, mergero-supplied, licensed]
    default_language: de
```

### Target data sources and registries (A/L/C Taxonomy)

**Legend:**
- **A (Authoritative / Open Public / API)**: Prefer official open government APIs and registers.
- **L (Licensed / Commercial Feeds)**: Verified B2B directories and aggregators.
- **C (Company Domain Enrichment)**: Direct crawler targeting `/impressum`, `/about`, `/team`, and `/careers` in regional languages.

#### 🇩🇪 Germany
- **A**: [Handelsregister](https://www.handelsregister.de/) (legal entities, directors), [Unternehmensregister](https://www.unternehmensregister.de/) (filings, financial documents), [Bundesanzeiger](https://www.bundesanzeiger.de/) (official publications).
- **L**: [North Data](https://www.northdata.de/) (ownership, management networks), [OpenRegister](https://openregister.de/), [Handelsregister.ai](https://handelsregister.ai/), [Firmenwissen](https://www.firmenwissen.com/), [WLW](https://www.wlw.de/), [11880](https://www.11880.com/), [Gelbe Seiten](https://www.gelbeseiten.de/).
- **C**: Company domains: `/impressum`, `/ueber-uns`, `/team`, `/karriere`, `/kontakt`.

#### 🇦🇹 Austria
- **A**: [JustizOnline Firmenbuch](https://justizonline.gv.at/jop/web/firmenbuchabfrage), [Austrian Justice Firmenbuch](https://www.justiz.gv.at/), [WKO Firmen A-Z](https://firmen.wko.at/) (freely accessible trade registry).
- **L**: [FirmenABC](https://www.firmenabc.at/) (executives, ownership signals), [Compass / Firmeninfo](https://www.firmeninfo.at/), [Herold](https://www.herold.at/), [WLW Austria](https://www.wlw.at/), [North Data](https://www.northdata.com/).
- **C**: Company domains: `/impressum`, `/ueber-uns`, `/team`, `/karriere`, `/kontakt`.

#### 🇨🇭 Switzerland
- **A**: [Zefix](https://www.zefix.ch/) (central business name index REST API), [SHAB / Swiss Official Gazette of Commerce](https://www.shab.ch/), Cantonal registers (e.g. Zurich Handelsregister).
- **L**: [Moneyhouse](https://www.moneyhouse.ch/) (company & network API), [local.ch](https://www.local.ch/), [search.ch](https://search.ch/), [Dun & Bradstreet Switzerland](https://www.dnb.com/ch-de/), [North Data](https://www.northdata.com/).
- **C**: Company domains: `/impressum`, `/ueber-uns`, `/team`, `/jobs`, `/kontakt`.

#### 🇫🇮 Finland
- **A**: [YTJ company search](https://www.ytj.fi/), [PRH open data API](https://www.ytj.fi/index/avoindata.html) (free daily JSON feed), [Virre Information Service](https://virre.prh.fi/).
- **L**: [Finder](https://www.finder.fi/), [Kauppalehti Yrityshaku](https://www.kauppalehti.fi/yritykset/), [Asiakastieto](https://www.asiakastieto.fi/), [Fonecta](https://www.fonecta.fi/).
- **C**: Company domains: `/yritys`, `/tietoa-meista`, `/tiimi`, `/ura`, `/yhteystiedot`.

#### 🇸🇪 Sweden
- **A**: [Bolagsverket](https://bolagsverket.se/), [Verksamt](https://verksamt.se/).
- **L**: [Allabolag](https://www.allabolag.se/) (financials, headcount, roles), [UC](https://www.uc.se/), [Creditsafe Sweden](https://www.creditsafe.com/), [Hitta](https://www.hitta.se/), [Eniro](https://www.eniro.se/), [Ratsit företag](https://www.ratsit.se/foretag).
- **C**: Company domains: `/om-oss`, `/team`, `/karriar`, `/kontakt`.

#### 🇳🇴 Norway
- **A**: [Brønnøysundregistrene](https://www.brreg.no/), [Enhetsregisteret Open API](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html) (`antallAnsatte` headcount filter supported).
- **L**: [Proff Norway](https://www.proff.no/), [Purehelp](https://www.purehelp.no/), [1881](https://www.1881.no/), [Gule Sider](https://www.gulesider.no/), [Creditsafe Norway](https://www.creditsafe.com/no/).
- **C**: Company domains: `/om-oss`, `/team`, `/karriere`, `/kontakt`.

#### 🇩🇰 Denmark
- **A**: [CVR / Virk](https://datacvr.virk.dk/) (Danish Central Business Register open data API), [Danish FSA](https://virksomhedsregister.finanstilsynet.dk/).
- **L**: [Proff Denmark](https://www.proff.dk/), [CVRDB](https://www.cvrdb.dk/), [Virmo](https://virmo.dk/), [Krak](https://www.krak.dk/firma), [Ownr](https://ownr.dk/).
- **C**: Company domains: `/om-os`, `/team`, `/karriere`, `/kontakt`.

#### 🇮🇸 Iceland
- **A**: [Skatturinn Companies Register](https://www.skatturinn.is/fyrirtaekjaskra/), [Já National Registers API](https://gagnatorg.ja.is/docs/skra/v1/).
- **L**: [Creditinfo Iceland](https://www.creditinfo.is/), [Overit Iceland](https://overit.is/en/), [Keldan](https://keldan.is/).
- **C**: Company domains: `/um-okkur`, `/starfsfolk`, `/laus-storf`, `/hafdu-samband`.

The UI must show the source mode and permission status. Records from unapproved connectors must be rejected at ingestion, not merely marked with a warning later.

## 6. Normalization and data quality

Normalization must be deterministic and reversible where practical:

- Preserve the original source value alongside the normalized value.
- Normalize country, region, legal-form, industry-code, currency, URL, phone, and date formats using explicit versioned rules.
- Use stable source keys and content hashes for idempotent imports.
- Resolve likely duplicates using explainable keys and match reasons; never merge records silently.
- Keep conflicting source facts and show which source and timestamp support each value.
- Mark stale, incomplete, rejected, and manually corrected records explicitly.

The pipeline must report, at minimum:

- candidates discovered;
- records accepted, rejected, skipped, and duplicated;
- facts added, changed, conflicting, or missing;
- source and parser errors;
- records affected by retention expiry.

Do not use sensitive personal attributes for identity resolution or data-quality decisions. An LLM may summarize supplied warnings, but it must not invent, approve, or silently alter database values.

## 7. Review workflow

The state machine should be explicit:

```text
IMPORT_STARTED -> FETCHED -> PARSED -> VALIDATED -> UPSERTED -> REVIEWED
                                      |              |
                                      +-> REJECTED  +-> NEEDS_CORRECTION
```

Required actions:

- Start, pause, retry, and inspect an ingestion run.
- Review accepted, rejected, duplicate, incomplete, stale, and conflicting records.
- Add a correction without destroying the original fact.
- Link or unlink a duplicate with a recorded reason.
- Enable or disable a source subject to permission checks.
- Inspect and expire raw snapshots according to retention policy.

## 8. API surface

Keep the first API small and typed:

```text
GET    /health
GET    /sources
POST   /sources
POST   /sources/{id}/enable
POST   /sources/{id}/disable
GET    /ingestion-runs
POST   /ingestion-runs
GET    /ingestion-runs/{id}
POST   /ingestion-runs/{id}/retry
GET    /companies
GET    /companies/{id}
POST   /companies/{id}/facts/corrections
GET    /companies/{id}/audit-events
GET    /audit-events
```

Use pagination, filters for country, sector, min/max headcount (default min: 20 employees), source, freshness, completeness, and review status, and stable IDs. Return source links and warnings with each company profile, not in a separate hidden screen.

## 9. Database browser

Minimum screens:

1. **Source registry:** provider, coverage, permission status, allowed fields, connector state, and retention settings.
2. **Ingestion runs:** query/file identity, progress, counts, warnings, errors, and retry controls.
3. **Company database:** sortable and filterable normalized records (by country, sector, headcount range, review state) with freshness and provenance badges.
4. **Company detail:** source-backed facts, evidence timeline, conflicts, optional source contact fields, corrections, and audit history.
5. **Data-quality review:** duplicate candidates, missing fields, stale records, validation failures, and unresolved conflicts.

Make uncertainty visible. A clean but overconfident record is worse than an incomplete record with clear evidence gaps.

## 10. Security, privacy, and compliance guardrails

- Keep credentials and API keys server-side; redact them from logs.
- Apply least-privilege database access and separate raw-source storage from application tables.
- Encrypt data in transit and use encrypted managed storage in production.
- Minimize personal-data fields and define retention/expiry for raw snapshots and stale source records.
- Support deletion and export requests where required by the applicable policy.
- Keep source permission and usage policy attached to every imported record and fact.
- Add visible `generated`, `source-verified`, or `manually-corrected` labels to material claims.

## 11. Observability and metrics

Track the ingestion funnel by country, source, sector, and run:

- candidates discovered;
- records accepted/rejected by permission gate;
- fetch, parse, validation, and upsert error rates;
- verified or multi-source facts per company;
- duplicate, stale, incomplete, and conflicting record rates;
- review time per record;
- correction and retention-expiry rates;
- source freshness and parser-version coverage.

Add structured logs with `job_id`, `source_id`, `company_id`, `ingestion_run_id`, and `trace_id`. Never log full personal contact details or raw payloads by default.

## 12. Delivery plan

### Slice 1 — usable vertical demo

- Database migrations and seed data.
- Source registry and permission status.
- CSV import connector with provenance.
- Deterministic normalization and idempotent upsert.
- Company database and ingestion-run views.

### Slice 2 — source connector foundation

- Pluggable Nordic and DACH connector interfaces.
- Regional permission gate and allowed-field enforcement.
- Snapshot retention and parser-version tracking.
- Validation warnings, rejection reasons, and audit events.

### Slice 3 — data quality

- Stable identity resolution and duplicate review.
- Fact corrections that preserve history.
- Freshness, completeness, and conflict reporting.
- Source and ingestion metrics.

### Slice 4 — multi-region hardening and standardization

- Standardized Nordic and DACH connector types.
- Ingestion tests proving unapproved connector rejection across both regions.
- Regional localization for source and data-quality labels.
- Country- and region-specific source and retention configuration.

## 13. Definition of done

The MVP is complete when a reviewer can run the app locally and:

- register an approved source;
- import or seed Nordic and DACH companies;
- see source provenance, permission, freshness, and confidence badges;
- inspect normalized company facts and optional source contact fields;
- review validation warnings, duplicates, missing values, and conflicts;
- correct a fact without losing its original provenance;
- rerun an import idempotently;
- inspect the ingestion audit trail;
- run automated tests covering parsing, normalization, idempotent import, permission gating, retention, deduplication, correction history, and provenance.
