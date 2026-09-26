# Mergero Company Database

An internal, permission-gated, provenance-linked company database for Nordic and DACH companies. Data flows through these stages:

```
approved registry / licensed feed / Mergero CSV
  → permission gate (fails closed)
  → fetch → parse → validate
  → multi-source entity resolution + dedup
  → headcount qualification (≥ 20 employees)
  → versioned, source-backed facts
  → database browser, review, corrections, data-quality report
```

Scope ends at ingestion, review and data-quality reporting. Nothing here sends messages, drafts outreach or acts outside the database. `CLAUDE.md` holds the product rules and `ARCHITECTURE.md` the design; this README covers how to run the project and what was built.

## Stack

The repository started as empty Next.js (`web/package.json`) and FastAPI (`api/.venv`) stubs. Those were kept:

| Part | Tech |
|---|---|
| API and ingestion jobs | Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2 |
| Database | PostgreSQL 16 (Docker). SQLite for local runs and tests. The same migrations and tests pass on both. |
| Database browser | Next.js 15 (App Router, Server Components), React 19, TypeScript, plain CSS |
| Local deployment | Docker Compose: `db`, `api` (migrates and seeds on start), `web` |

Ingestion runs synchronously inside the API request. That is fine at prototype scale. See [Known limitations](#known-limitations--blockers).

## Quick start

### Option A: Docker Compose (PostgreSQL)

```bash
cp .env.example .env
docker compose up --build
```

- Browser: http://localhost:3000
- API docs (OpenAPI): http://localhost:8000/docs

The `api` container runs `alembic upgrade head`, then `python -m app.seed`, then starts uvicorn.

### Option B: without Docker (SQLite, no credentials)

```bash
cd api
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt        # macOS/Linux: .venv/bin/pip
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m app.seed
.venv/Scripts/uvicorn app.main:app --port 8000
```

In a second terminal:

```bash
cd web
npm install
npm run dev
```

Then open http://localhost:3000. To use Postgres instead, set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db` before running `alembic` and the seed.

## Live data sources (no synthetic data)

`python -m app.seed` ingests **only live data** from approved sources (it refuses to run unless `LIVE_CONNECTORS_ENABLED=true`). Synthetic fixtures exist only under `api/tests/fixtures/` for the automated tests and are never loaded into the database.

The catalog in `api/config/sources.yaml` has 60 sources, classified as **A** (official registry / public API), **L** (licensed / commercial), **C** (company-website enrichment) or **I** (internal). Access status was verified from this environment on 2026-09-26:

| Country | A — official source | Status | Headcount at source |
|---|---|---|---|
| 🇳🇴 NO | Brreg Enhetsregisteret API | ✅ **live** (NLOD) | ✅ `antallAnsatte` (Aa-register); filtered ≥ 20 at source |
| 🇫🇮 FI | PRH / YTJ open data API v3 | ✅ **live** (CC BY 4.0); active companies only | ❌ none — stays *headcount unknown* |
| 🇨🇭 CH | Zefix PublicREST | 🔒 connector built; **needs free credentials** (HTTP 401) | ❌ none |
| 🇩🇰 DK | CVR system-to-system | 🔒 connector built; **needs free credentials** (HTTP 401) | ✅ annual employment |
| 🇸🇪 SE | Bolagsverket API | 🔒 OAuth client credentials required | ❌ |
| 🇩🇪 DE | Handelsregister / Unternehmensregister / Bundesanzeiger | 🚫 no API; terms/robots restrict automation | — |
| 🇦🇹 AT | JustizOnline Firmenbuch / WKO | 🚫 paid extracts / terms review | — |
| 🇮🇸 IS | Skatturinn / Já Gagnatorg | 🚫 no open API / paid key | — |

**C — company websites** (`web-company-nordics`, `web-company-dach`) crawl only domains that an approved registry published for a company already in the database. Website data enriches existing companies and never creates one; an unmatched site is rejected with a reason.

- The crawler honours `robots.txt`, fetches at most 1 request per second per host and at most 6 pages per domain, and never stores raw HTML.
- Pages are found through local-language links:
  - DE/AT/CH: `/impressum`, `/ueber-uns`, `/team`, `/karriere`
  - FI: `/tietoa-meista`, `/ura`
  - SE / NO / DK: `/om-oss`, `/om-os`, `/karriar`, `/karriere`
  - IS: `/um-okkur`, `/laus-storf`

What the crawler extracts:

| Extracted | Rule | Confidence |
|---|---|---|
| Registry ID / VAT stated on the site | Must pass the national checksum/format; if a page states several different IDs (group sites), none is stored | verified → **multi-source** when it matches the registry |
| DACH Impressum legal name, HRB/HRA + court, Geschäftsführer / Vorstand | Explicit label required; headings and truncated text are never taken as names; no emails or phones | verified; managing directors stored as `public-business` contacts |
| `open_positions` | Distinct job-posting links on the careers page; external ATS boards are reported, not crawled | estimated |
| `founder_signal`, `family_business_signal` | Self-description keywords in DE/EN/FI/SV/NO/DA/IS; booleans only, no names | estimated |

**L — licensed sources** (North Data, OpenRegister, Allabolag, Proff, Asiakastieto, Moneyhouse, Creditinfo, …) are registered as `pending`. Each needs a signed licence or API contract, recorded as its `approval_reference`, before it can be enabled.

### Current live dataset (seeded 2026-09-26)

| | Count |
|---|---|
| Norwegian companies (Brreg, NACE 62 / 28 / 71, all ≥ 20 registered employees) | 496 |
| Finnish companies (PRH, TOL 62100, Tampere + Oulu, headcount unknown) | 200 |
| Companies enriched from their own website | 42 |
| Duplicate candidates (all real group subsidiaries sharing one website with different org numbers → *link*, not merge) | 14 |
| Zefix / CVR runs | `FAILED` — credentials missing (fail closed, visible in **Ingestion runs**) |
| DACH website run | `FAILED` — no DACH companies yet, because there is no approved DACH discovery source |

## Demo walkthrough

| # | What to show | Where |
|---|---|---|
| 1 | The A/L/C catalog: live, credential-gated, pending-licence and unapproved sources, each with the reason it can or cannot ingest | **Source registry** |
| 2 | Live runs with counts; Zefix and CVR failing closed with "credentials missing" | **Ingestion runs** |
| 3 | Default ≥ 20 view: 496 real Norwegian companies with Brreg-registered headcount. Switch *Min employees* to "All" to see Finnish companies as *headcount unknown* | **Companies** |
| 4 | Multi-source identity: e.g. *HELSE VEST IKT AS*. Brreg org nr 987601787 is confirmed by the company's own website, so the registry ID is **multi-source** | **Company detail** |
| 5 | Hiring signal with evidence link: e.g. *BOUVET NORGE AS* shows 43 open positions from its careers page, labelled `estimated` | **Company detail** |
| 6 | Group structure: *Knowit* / *Itera* / *Framo* subsidiaries share one domain but have different org numbers, so they can only be linked, never merged | **Data quality** |
| 7 | Run live discovery or a website crawl from the UI (e.g. Brreg NACE 70 or PRH Helsinki) | **Ingestion runs** |
| 8 | Idempotency: `python -m app.seed --reimport` re-queries the live APIs; unchanged records stay `unchanged` | terminal |

### API demo (curl)

```bash
# health and source registry
curl localhost:8000/health
curl localhost:8000/sources

# CSV import (multipart). Rerun it: all rows become "unchanged"
curl -F source_id=mergero-csv -F file=@your_real_export.csv localhost:8000/ingestion-runs

# registry discovery (JSON). Brreg filters >= 20 employees at source
curl -H 'Content-Type: application/json' -d '{"source_id":"no-brreg","query":{"naeringskode":"70","max_records":50}}' localhost:8000/ingestion-runs

# website enrichment of registry-published domains (Nordic companies already in the DB)
curl -H 'Content-Type: application/json' -d '{"source_id":"web-company-nordics","query":{"countries":["NO"],"max_records":20}}' localhost:8000/ingestion-runs

# an unapproved source is rejected with 403 and reasons
curl -F source_id=linkedin-scrape -F file=@any.csv localhost:8000/ingestion-runs

# company list filters (min_employees defaults to 20; pass 0 to include everything)
curl 'localhost:8000/companies?country=DE,AT,CH&sector=62&min_employees=20&review_status=unreviewed'

# correction (preserves original facts)
curl -H 'Content-Type: application/json' -H 'X-Actor: analyst@mergero.local' \
  -d '{"field_name":"employees","value":"24","reason":"confirmed in annual report"}' \
  localhost:8000/companies/<company_id>/facts/corrections

# GDPR erasure of a personal contact
curl -X DELETE 'localhost:8000/contacts/<contact_id>?request_reference=DSR-2026-001'

# quality report, duplicate queue, audit log, retention expiry
curl localhost:8000/quality/report
curl localhost:8000/quality/duplicates
curl 'localhost:8000/audit-events?action=fact.'
curl -X POST localhost:8000/retention/expire
```

## API

Every endpoint the brief asked for, plus the review and quality endpoints the UI needs:

```
GET    /health
GET    /sources                         POST /sources   (always created pending + disabled)
POST   /sources/{id}/enable             POST /sources/{id}/disable
POST   /sources/{id}/permission         (approve/revoke; approving requires approval_reference)
GET    /ingestion-runs                  POST /ingestion-runs   (multipart CSV | JSON discovery)
GET    /ingestion-runs/{id}             POST /ingestion-runs/{id}/retry
GET    /companies                       ?country&region&sector&min_employees(=20)&max_employees
                                         &review_status&qualification&freshness&source_id&q&sort&order&page
GET    /companies/{id}                  (facts, conflicts, identifiers, contacts, timeline, audit, warnings)
POST   /companies/{id}/facts/corrections
POST   /companies/{id}/review
GET    /companies/{id}/audit-events
DELETE /contacts/{id}                   ?request_reference&reason   (GDPR erasure)
GET    /audit-events                    ?company_id&action(prefix)&entity_type
GET    /quality/report                  GET /quality/duplicates   POST /quality/duplicates/detect
POST   /quality/duplicates/{id}/merge | /link | /dismiss
POST   /retention/expire
```

Timestamps are stored in UTC and returned as ISO 8601. Mutating calls read the reviewer identity from the `X-Actor` header; the UI sends it from the "acting as" box.

## How it works

### Data model (`api/app/models.py`, `api/migrations/`)

- `sources`: the registry. Provider, region, countries, mode (`public-approved` / `licensed` / `mergero-supplied`), permission status and approval reference, connector type and config, allowed fields, column mapping, base confidence, usage policy, retention days, trust rank.
- `ingestion_runs` + `ingestion_records`: every run records its stage, input hash, parser version, config hash and counts. Every row records its outcome (accepted / updated / unchanged / duplicate / rejected) with match reasons, warnings and errors.
- `source_snapshots`: the minimum raw evidence. Unique on `(source, source_key, content_hash)`. **Contact fields are never stored here.** Payloads expire per source.
- `companies`: a derived display view, recomputed from facts.
- `company_identifiers`: stable identity keys (registry ID, VAT, domain, source key), stored separately so resolution can be rerun.
- `company_facts`: one row per claim, append-only and versioned. Each has source, reference URL, run, snapshot, `observed_at`, `valid_from` / `valid_to`, base confidence, computed confidence, usage policy, review status, and correction links.
- `contacts`: personal data, source-backed only. `contact_suppressions` holds one-way hashes after erasure.
- `duplicate_candidates`: score, band, the reasons for and against, status, and the resolution reason.
- `audit_events`: permission changes, runs, corrections, merges and links, review changes, retention, erasure.

### Entity resolution (Clarvo-style multi-source consolidation)

A research pass found that Clarvo AI (clarvo.ai) is a recruiting and outreach platform. It publishes no entity-resolution details beyond merging many sources into one profile. That consolidation idea is what this project borrows. The matching method follows documented patterns from Senzing (match / possible / no-match, with reasons) and OpenCorporates (provenance on every fact). Outreach is out of scope.

1. **Exact stable keys, applied at ingestion.** Keys are the national registry ID (validated per country: FI Y-tunnus mod-11, SE Luhn, NO mod-11, DK CVR, CH UID mod-11, AT FN, DE HRB plus court), VAT ID, normalized domain (shared hosts like gmail.com are ignored), and source key.
   - For FI, SE, NO, DK and CH, VAT and registry IDs are derived from each other deterministically. These derived keys are used for matching only and are flagged `derived`.
2. **Key conflicts are never merged.** A record whose keys point to two different companies, or a domain match where the registry IDs differ (e.g. group subsidiaries), becomes a duplicate candidate instead.
3. **Possible duplicates.** Names are normalized (legal forms and accents stripped), blocked by country, and scored on name, city, industry, VAT and domain. Pairs scoring 80 or more are `likely`, 55–79 `possible`, and anything lower is dropped. Every candidate keeps its reasons for and against.
4. **Review.** A reviewer can **merge**, which moves facts, identifiers and contacts and is fully audited. Merging is refused when registry IDs differ. Alternatively the reviewer can **link** (related entity) or **dismiss**. Resolved pairs never reopen.

### Confidence labels (`api/app/domain/confidence.py`)

| Label | Meaning |
|---|---|
| `source-verified` | One authoritative source (registry, Impressum) |
| `multi-source` | The same value, or overlapping ranges (e.g. 20–49 and 38), from 2 or more approved sources |
| `estimated` | Only from an estimate-grade source (licensed feed, Mergero CSV) |
| `conflicting` | Sources disagree. All values are kept and shown, with the most authoritative displayed first |
| `old` | Evidence older than `STALE_AFTER_DAYS` (default 365) |
| `manually-corrected` | A reviewer correction. The original facts are kept, with review status `corrected` |
| `unknown` | No evidence. Never guessed |

Free-text labels (sector, description) that differ across sources count as variants, not conflicts.

### Qualification

Headcount is the primary viability proxy, because private-company financials are rarely public.

| Status | Rule |
|---|---|
| `qualified` | Lower bound ≥ 20 |
| `borderline` | Range spans 20 (e.g. 10–49) |
| `below_threshold` | 3 to 19 |
| `sub_scale` | 1–2 people; low priority |
| `unknown_headcount` | No approved evidence |

`GET /companies` defaults to `min_employees=20`. Brreg discovery applies the threshold at the source. The thresholds are configurable (`MIN_EMPLOYEES_DEFAULT`).

### Compliance guardrails

- **Permission gate** (`services/permissions.py`). Ingestion requires all of the following:
  - the source is registered, `approved` and enabled;
  - it uses an ingestible connector;
  - its mode and countries are inside the regional policy (`config/sources.yaml`);
  - for live HTTP, `LIVE_CONNECTORS_ENABLED=true`.

  Denied runs are recorded as `REJECTED` and store no input. New sources start `pending` and disabled. Approving a source needs an approval reference. Revoking one disables it, and a downgrade in the config always wins.
- **Field-level enforcement.** Fields a source is not allowed to supply are dropped, with a warning. Records outside the source's countries are rejected.
- **Personal data.** Contacts are stored only when the source's allowed fields include `contacts`. Emails are validated, never constructed, and the code has no guessing functions. Logs redact emails and phone numbers.
- **GDPR erasure** does four things:
  - hard-deletes the contact;
  - redacts retained raw inputs;
  - writes suppression hashes so re-imports skip the person;
  - writes an audit event with the request reference and no personal data.
- **Retention.** Snapshot payloads and raw run inputs expire after each source's `retention_days`. Hashes, parser versions and facts are kept. Run it with `POST /retention/expire`, or schedule that call.
- **No AI valuation claims.** No LLM is used anywhere in the pipeline.

## Tests and quality checks

```bash
cd api
.venv/Scripts/python -m pytest                    # 112 tests, SQLite, no network
TEST_DATABASE_URL=postgresql+psycopg://... .venv/Scripts/python -m pytest   # same suite on Postgres
.venv/Scripts/ruff format --check app tests && .venv/Scripts/ruff check app tests
.venv/Scripts/python -m mypy app

cd ../web
npx tsc --noEmit
npm run build
```

What the tests cover:

| Test file | Covers |
|---|---|
| `test_ingestion.py` | Import idempotency (CSV, discovery, full reseed), superseded values keeping their history, in-file duplicates, rejection reasons, provenance on every fact, no personal data in snapshots, retry, retention per source |
| `test_permissions.py` | Unapproved, pending, disabled, unknown and manual sources fail closed with no partial writes; live-connector flag; regional policy; coverage per record; enable/revoke rules; config downgrade; contacts dropped when not allowed |
| `test_entity_resolution.py` | Registry ↔ VAT and domain resolution across sources; shared domain with different registry IDs never merges; name-only candidates with reasons; merge moves evidence and is audited; merge refused across registry IDs; resolved pairs stay closed; multi-source, conflicting, estimated and old labels |
| `test_qualification.py` | ≥ 20 / borderline / below / sub-scale / unknown; counts per run; API default filter; sub-scale warning |
| `test_contacts.py` | No guessed emails; GDPR erasure (deletion, audit without personal data, input redaction, suppression on re-import); erasure leaves company facts alone |
| `test_corrections.py` | Originals preserved; before/after audit; second correction supersedes the first; correction can change qualification; invalid corrections rejected; corrections survive re-import |
| `test_normalize.py`, `test_api.py` | Per-country ID validation, ranges, domains; API contract; migrations match the models (`alembic check`) |

## Configuration

See `.env.example`. The source registry and regional policy live in `api/config/sources.yaml`. Quote `"NO"` in YAML, because an unquoted `NO` parses as boolean false; the loader rejects it.

## Known limitations / blockers

- **Registry credentials and licences:**
  - CVR system-to-system access needs issued credentials, so `dk-cvr` stays disabled.
  - The North Data and Moneyhouse licences are `pending`.
  - LinkedIn scraping is `unapproved` by design; headcount comes from the licensed firmographic feed instead.
- **Credentials needed for live coverage:**
  - CH needs `ZEFIX_USERNAME`/`ZEFIX_PASSWORD`, requested free from zefix@bj.admin.ch.
  - DK needs `CVR_USERNAME`/`CVR_PASSWORD`, requested free from Erhvervsstyrelsen.
  - SE needs Bolagsverket OAuth credentials.
  - The Zefix and CVR response mappings follow the published documentation but have **not been exercised live** from here. Verify them on the first credentialed run.
- **No open DACH or Icelandic discovery source.** Germany, Austria and Iceland need an approved licensed register feed (e.g. OpenRegister, North Data, FirmenABC, Creditinfo) before DACH/IS companies can enter the database. The Impressum crawler is ready and was checked against real Impressum pages (e.g. Voith, WAGO, Plansee, Hilti), but nothing from those checks was stored.
- **Headcount gaps:** FI (PRH), CH (Zefix) and SE (Bolagsverket) publish no headcount. Those companies stay *headcount unknown* until a licensed source such as Asiakastieto, Finder, Allabolag or Moneyhouse is approved.
- **Website extraction is rule-based.** JavaScript-rendered pages yield nothing, and nothing is inferred from them. Hiring counts are link-based estimates.
- **No authentication yet.** The reviewer identity is a header. Production needs SSO and role-based access, e.g. only data owners may approve sources.
- **Synchronous runs.** There is no worker queue yet. Pause and resume are not implemented; retry is.
- **Docker Compose was not run in this environment**, because Docker is unavailable. Migrations 0001–0002 and all 112 tests were verified against a local PostgreSQL 16.2 instance; the live seed was run on SQLite.
- **Unmerge (split) is not implemented.** Every merge records the moved identifiers, facts and contacts in its audit event, so a split can be added later.
