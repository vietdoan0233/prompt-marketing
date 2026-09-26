# Estonia company register

This FastAPI and Next.js application holds company identities, official registered addresses, and annual financial observations for Estonian entities. The active country is `EE`. The [Estonian e-Business Register open-data portal](https://avaandmed.ariregister.rik.ee/et/avaandmete-allalaadimine) is the only enabled production ingestion source. Imports use its downloadable files, never company-page scraping or annual-report PDFs.

### First-time / Teammate database setup

The SQLite database file (`mergero_dev.db`) is git-ignored (`*.db`) to keep repository size lean and prevent merge collisions. New contributors can initialize their local database in seconds:

1. **Set up virtual environment & install dependencies** (from `api/`):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip.exe install -r requirements.txt
   ```
2. **Apply migrations and seed data**:
   ```powershell
   .\.venv\Scripts\alembic.exe upgrade head
   .\.venv\Scripts\python.exe -m app.seed --from-cache --replace
   ```

### Running seed and live imports

From `api/`:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m app.seed --sources-only
.\.venv\Scripts\python.exe -m app.seed --replace
```

Set `LIVE_CONNECTORS_ENABLED=true` in `api/.env` before a live import. `--replace` makes a timestamped backup in `api/backups/`, purges imported companies and their dependent records, retains audit history and contact suppression tombstones, syncs the Estonia source registry, and imports again. A normal `python -m app.seed --reimport` is incremental. Do not delete the database file by hand: that loses audit and correction history.

If the official files were downloaded earlier but the portal is temporarily unreachable, run `python -m app.seed --from-cache --reimport` or `python -m app.seed --from-cache --replace`. Cache mode verifies every required ZIP against the manifest's official URL, file size, and SHA-256 before importing. Replacement checks file availability before purging the database.

The importer caches official ZIPs and their SHA-256 hashes under `api/data/ee_ariregister/`. The cache manifest records each file's portal URL, name, size, hash, and HTTP Last-Modified date. The live seed fails closed on network errors.

The configured import covers fiscal years 2019–2025 and the legal forms OÜ, AS, UÜ, TÜ, TÜH, and SE. The default company qualification uses at least 20 reported full-time equivalent employees in a 2024 or 2025 filing. This is a scope filter, not a claim that every Estonian company is in the application.

Run the API with `.\.venv\Scripts\uvicorn.exe app.main:app --port 8000` and the web app with `npm run dev` from `web/`. The API documentation is at `http://localhost:8000/docs`; the UI is at `http://localhost:3000`.

## Official datasets and provenance

The importer uses these official CSV ZIPs linked from the portal:

| Dataset | Purpose |
|---|---|
| `ettevotja_rekvisiidid__lihtandmed.csv.zip` | Registry code, legal name, status, and registered address |
| `1.aruannete_yldandmed_kuni_*.zip` | Annual report ID, period, category, and filing metadata |
| `2.EMTAK_myygitulu_kuni_*.zip` | Main activity code |
| `4.<year>_aruannete_elemendid_kuni_*.zip` | Reported financial and employment indicators |

Report files are joined by `report_id`. `source_snapshots` keep raw company-address columns and financial source lines, a stable content hash, source URL, source ID, ingestion run ID, parser version, and retrieval time. Financial and address rows link to the snapshots and retain their own source URL, source file, observation time, and ingestion run ID. The official company/address snapshots are retained with their original columns for review; other raw snapshot payloads follow the configured retention policy. Stable hashes and relational facts remain after expiration.

### Registered address

`registered_addresses` represents the official registered seat (`asukoht`). It is displayed as **Registered address** on the company detail page and is not an assertion about where the company operates. All address parts are nullable; `country` is `EE` for entities in this register.

| Relational field | Basic-data column / rule |
|---|---|
| `address_line` | `asukoht_ettevotja_aadressis` |
| `postal_code` | `indeks_ettevotja_aadressis` |
| `city` | Unambiguous city or town identified from `asukoha_ehak_tekstina`; otherwise NULL |
| `municipality` | Identifiable local-government unit from `asukoha_ehak_tekstina`; otherwise NULL |
| `county` | The unique segment ending in `maakond`; otherwise NULL |
| `ehak_code` | `asukoha_ehak_kood` |
| `country` | `EE` |

EHAK parts vary in count and are classified by type, not fixed position. For example, `Pirita linnaosa, Tallinn, Harju maakond` identifies Tallinn and Harju maakond; `Tartu linn, Tartu linn, Tartu maakond` identifies Tartu as the city and `Tartu linn` as the municipality. Ambiguous or missing components stay NULL, with a row-level parser warning. The importer preserves all nine original address columns unchanged in each company source snapshot: `ettevotja_aadress`, `asukoht_ettevotja_aadressis`, `asukoha_ehak_kood`, `asukoha_ehak_tekstina`, `indeks_ettevotja_aadressis`, `ads_adr_id`, `ads_ads_oid`, `ads_normaliseeritud_taisaadress`, and `teabesysteemi_link`. The snapshot also records the downloadable ZIP name, inner CSV file name, download URL, file date, observed time, source ID, and ingestion run ID.

In the cached official basic-data file checked on 2026-09-26, `ettevotja_aadress` was blank in all 378,648 rows. If it becomes populated, the parser flags it for review and preserves it verbatim; it does not replace the explicit street-line field with a potentially composite address. In that file, 25,382 rows had neither a street line nor EHAK text. An unchanged mapped address for the same company and source does not create another address version, even if ADS metadata changes. A changed address closes the prior version; reverting to an earlier address creates a new version for the new validity interval.

### Financials

`company_financials` stores one source-backed row per company, report, fiscal period, and statement scope. Values are nullable. Revenue and annual profit/loss are mapped directly from supported indicator lines. `depreciation_and_impairment` is kept separately from pure `depreciation`; the latter stays NULL unless the source proves a pure depreciation value. EBITDA, dividends, and capex are NULL because these files do not supply a safe direct mapping. No such value is estimated or silently derived. The original label and value are kept in `source_values` and the source snapshot.

The financial row records period, scope, reported/derived type, filing and document IDs, currency and unit where supported, source URL/file, snapshot, observation time, parser version, confidence, usage policy, ingestion run, and review status. Reimporting the same report content is idempotent.

## API and review

- `GET /companies` lists Estonian companies; the default minimum is 20 employees.
- `GET /companies/{id}` includes `registered_address`, annual `financials`, identifiers, source-backed facts, warnings, and review history.
- `GET /companies/{id}/registered-address` returns the current registered-address version.
- `GET /companies/{id}/financials` returns the financial time series.
- `GET /ingestion-runs/{id}` exposes accepted, unchanged, rejected, and warning records for an import.

The existing correction, audit, source-permission, and data-quality mechanisms remain available. The importer rejects non-Estonian company rows and the active country policy is `EE`.

## Checks

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

Tests include address mapping, ambiguous city handling, source-column provenance, absence of a headquarters label in the company UI, idempotent reimport, financial mapping, country restrictions, and migration consistency. SQLite is used for the default test run; `TEST_DATABASE_URL` can point the suite at PostgreSQL.

## Source limitations

The portal publishes CSV snapshots and monthly report extracts, so `observed_at` reflects the official file date rather than a live company-page timestamp. The basic-data address is a registered seat only. Some source rows omit address components, and administrative text can be ambiguous or malformed. Indicator names may combine depreciation and impairment, so pure depreciation is left NULL unless an unambiguous source line appears. The 20-FTE filter excludes entities without qualifying recent filings.

### Verified local import, 2026-09-26

The local database was rebuilt from the cached official ZIPs after the portal request failed in this Python environment. Cache mode verified file URLs, sizes, and SHA-256 hashes. The latest run contains 3,159 `EE` companies, 3,159 current registered addresses, and 20,805 financial rows across fiscal years 2019–2025. No company has a second registry code, and no FI or NO company remains.

| Fiscal year | Financial rows |
|---|---:|
| 2019 | 2,827 |
| 2020 | 2,939 |
| 2021 | 2,998 |
| 2022 | 3,034 |
| 2023 | 3,079 |
| 2024 | 3,132 |
| 2025 | 2,796 |

All 20,805 rows have NULL EBITDA, dividends, capex, pure depreciation, currency, and unit. Revenue is NULL in 134 rows and net income in 7. The current registered addresses have 7 NULL street lines, 901 NULL cities, 4 NULL municipalities, and 4 NULL counties. Ten imported address rows have parser warnings; their missing or unresolved components remain NULL. The run also recorded 36,831 unmatched 2024 indicator report IDs as rejected, because they had no matching general-info report row; no values from those rows were imported. Every imported financial row has the required source and ingestion provenance.
