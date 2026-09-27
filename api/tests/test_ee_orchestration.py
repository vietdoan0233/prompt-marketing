"""End-to-end contract tests for the Estonia bulk-file importer."""

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.ee_ariregister import PORTAL
from app.models import (
    AuditEvent,
    Company,
    CompanyFact,
    CompanyFinancial,
    IngestionRecord,
    RegisteredAddress,
    SourceSnapshot,
)
from app.services.ee_import import import_estonia

REGISTRY_CODE = "10065762"
PUBLISHED = "2026-09-03T11:04:00+00:00"
# `cache_dir` (a throwaway local-manifest cache directory) is a shared fixture in conftest.py.


def _csv(headers: list[str], rows: list[list[str]]) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue()


def _write_zip(cache: Path, name: str, content: str, *, kind: str, year: int | None = None) -> None:
    path = cache / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("official.csv", content.encode("utf-8-sig"))
    entry = {
        "kind": kind,
        "name": name,
        "url": f"{PORTAL}/sites/default/files/{name}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
        "last_modified": PUBLISHED,
        "year": year,
    }
    manifest_path = cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest[name] = entry
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _datasets(
    cache: Path,
    *,
    address: str = "Regati pst 12",
    employees_2024: str = "24",
    employees_2025: str = "22",
    period_2025_start: str = "01.01.2025",
    period_2025_end: str = "31.12.2025",
    emtak_version: str = "EMTAK 2025",
) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    basic = _csv(
        [
            "ariregistri_kood",
            "nimi",
            "ettevotja_oiguslik_vorm",
            "kmkr_nr",
            "ettevotja_staatus",
            "ettevotja_aadress",
            "asukoht_ettevotja_aadressis",
            "asukoha_ehak_kood",
            "asukoha_ehak_tekstina",
            "indeks_ettevotja_aadressis",
            "ads_adr_id",
            "ads_ads_oid",
            "ads_normaliseeritud_taisaadress",
            "teabesysteemi_link",
        ],
        [
            [
                REGISTRY_CODE,
                "Example OÜ",
                "Osaühing",
                "",
                "In Liquidation",
                "",
                address,
                "0596",
                "Pirita linnaosa, Tallinn, Harju maakond",
                "11911",
                "",
                "",
                "",
                "",
            ]
        ],
    )
    _write_zip(cache, "ettevotja_rekvisiidid__lihtandmed.csv.zip", basic, kind="basic")

    reports = _csv(
        [
            "report_id",
            "registrikood",
            "õiguslik vorm",
            "aruandeaasta",
            "staatus",
            "kas konsolideeritud?",
            "period_start",
            "period_end",
            "taidetud_aruanne_report_id",
        ],
        [
            [
                "R2024",
                REGISTRY_CODE,
                "Osaühing",
                "2024",
                "accepted",
                "Ei",
                "01.01.2024",
                "31.12.2024",
                "D2024",
            ],
            [
                "R2025",
                REGISTRY_CODE,
                "Osaühing",
                "2025",
                "accepted",
                "Ei",
                period_2025_start,
                period_2025_end,
                "D2025",
            ],
        ],
    )
    _write_zip(cache, "1.aruannete_yldandmed_kuni_31082026.zip", reports, kind="reports")

    activity = _csv(
        ["report_id", "emtak", "Jaotatud müügitulu", "põhitegevusala", "emtak_version"],
        [["D2025", "62011", "1000", "Jah", emtak_version]],
    )
    _write_zip(cache, "2.EMTAK_myygitulu_kuni_31082026.zip", activity, kind="activity")

    indicator_headers = ["report_id", "elemendi_nimetus", "tabel", "vaartus", "elemendi_label"]
    indicators_2024 = _csv(
        indicator_headers,
        [
            [
                "R2024",
                "AverageNumberOfEmployeesInFullTimeEquivalentUnits",
                "Bilanss",
                employees_2024,
                "FTE",
            ],
            ["UNKNOWN-REPORT", "Revenue", "Kasumiaruanne", "999", "Revenue"],
        ],
    )
    _write_zip(
        cache, "4.2024_aruannete_elemendid_kuni_31082026.zip", indicators_2024, kind="indicators", year=2024
    )

    indicators_2025 = _csv(
        indicator_headers,
        [
            [
                "R2025",
                "AverageNumberOfEmployeesInFullTimeEquivalentUnits",
                "Bilanss",
                employees_2025,
                "FTE",
            ],
            ["R2025", "Revenue", "Kasumiaruanne", "1000", "Revenue"],
            ["R2025", "TotalProfitLoss", "Kasumiaruanne", "100", "Operating profit"],
            [
                "R2025",
                "DepreciationAndImpairmentLossReversal",
                "Kasumiaruanne",
                "-20",
                "Depreciation and impairment",
            ],
        ],
    )
    _write_zip(
        cache, "4.2025_aruannete_elemendid_kuni_31082026.zip", indicators_2025, kind="indicators", year=2025
    )


def test_import_estonia_full_lifecycle_and_idempotency(
    cache_dir: Path, monkeypatch, session: Session, client
) -> None:
    cache = cache_dir
    _datasets(cache)
    settings = get_settings().model_copy(update={"ee_cache_dir": cache})
    monkeypatch.setattr("app.services.ee_import.get_settings", lambda: settings)

    first = import_estonia(session, query={"years": [2024, 2025]}, min_employees=20, live_override=False)
    assert first.status == "UPSERTED"
    assert first.counts["accepted"] == 1
    assert first.counts["address_added"] == 1
    assert first.counts["financial_added"] == 2
    assert first.counts["orphan_reports"] == 1

    company = session.scalar(select(Company).where(Company.country == "EE"))
    assert company is not None
    detail = client.get(f"/companies/{company.id}").json()
    assert detail["company"]["registry_status"] == "In Liquidation"
    fields = {item["field_name"]: item for item in detail["fields"]}
    assert fields["registry_status"]["value"] == "In Liquidation"
    assert detail["registered_address"]["address_line"] == "Regati pst 12"
    assert len(detail["financials"]) == 2
    derived = next(row for row in detail["financials"] if row["fiscal_year"] == 2025)
    assert derived["ebitda"] == "120.00"
    assert derived["period_days"] == 365 and derived["period_length_class"] == "standard_12_month"
    leap_year = next(row for row in detail["financials"] if row["fiscal_year"] == 2024)
    assert leap_year["period_days"] == 366 and leap_year["period_length_class"] == "standard_12_month"
    assert derived["currency"] == "EUR" and derived["unit"] == "EUR"
    assert derived["value_type"] == "derived"
    assert derived["calculation_formula"] == "operating_profit - depreciation_and_impairment"
    assert detail["company"]["industry_code_details"] == [
        {"code": "62011", "code_system": "EMTAK", "code_version": "2025"}
    ]
    activity_fact = next(f for f in detail["facts"] if f["field_name"] == "industry_code")
    assert activity_fact["code_system"] == "EMTAK" and activity_fact["code_version"] == "2025"
    orphan = session.scalar(
        select(IngestionRecord).where(
            IngestionRecord.run_id == first.id, IngestionRecord.source_key.like("orphan:%")
        )
    )
    assert orphan is not None and orphan.outcome == "rejected"
    assert any(event.action == "ingestion.completed" for event in session.scalars(select(AuditEvent)))
    assert (
        session.scalar(select(SourceSnapshot).where(SourceSnapshot.source_id == "ee-ariregister")) is not None
    )

    second = import_estonia(session, query={"years": [2024, 2025]}, min_employees=20, live_override=False)
    assert second.status == "UPSERTED"
    assert second.counts["unchanged"] == 1
    assert second.counts["address_unchanged"] == 1
    assert second.counts["financial_unchanged"] == 2
    assert session.query(Company).filter_by(country="EE").count() == 1
    assert session.query(CompanyFinancial).filter_by(company_id=company.id).count() == 2
    assert session.query(RegisteredAddress).filter_by(company_id=company.id).count() == 1

    _datasets(cache, address="Pärnu mnt 1", employees_2025="15")
    third = import_estonia(session, query={"years": [2024, 2025]}, min_employees=20, live_override=False)
    addresses = session.scalars(
        select(RegisteredAddress)
        .where(RegisteredAddress.company_id == company.id)
        .order_by(RegisteredAddress.valid_from)
    ).all()
    assert third.counts["address_changed"] == 1
    assert len(addresses) == 2
    assert addresses[0].valid_to is not None
    assert addresses[1].valid_to is None and addresses[1].address_line == "Pärnu mnt 1"
    detail = client.get(f"/companies/{company.id}").json()
    assert detail["company"]["qualification_status"] == "below_threshold"
    assert detail["company"]["estimated_employee_min"] == 15


def test_importer_fails_before_upserts_when_a_required_year_is_missing(
    cache_dir: Path, monkeypatch, session: Session
) -> None:
    cache = cache_dir
    _datasets(cache)
    manifest_path = cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["4.2025_aruannete_elemendid_kuni_31082026.zip"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    settings = get_settings().model_copy(update={"ee_cache_dir": cache})
    monkeypatch.setattr("app.services.ee_import.get_settings", lambda: settings)

    run = import_estonia(session, query={"years": [2024]}, min_employees=20, live_override=False)

    assert run.status == "FAILED"
    assert "2025" in run.errors[0]["message"]
    assert session.query(Company).filter_by(country="EE").count() == 0


def test_2025_qualification_file_is_used_for_single_year_import(
    cache_dir: Path, monkeypatch, session: Session
) -> None:
    _datasets(cache_dir, employees_2024="15", employees_2025="22")
    settings = get_settings().model_copy(update={"ee_cache_dir": cache_dir})
    monkeypatch.setattr("app.services.ee_import.get_settings", lambda: settings)

    run = import_estonia(session, query={"years": [2024]}, min_employees=20, live_override=False)

    assert run.status == "UPSERTED"
    assert run.counts["accepted"] == 1
    company = session.scalar(select(Company).where(Company.country == "EE"))
    assert company is not None
    assert company.estimated_employee_min == 22 and company.qualification_status == "qualified"


def test_short_reporting_period_is_exposed_without_annualizing_values(
    cache_dir: Path, monkeypatch, session: Session, client
) -> None:
    _datasets(cache_dir, period_2025_start="01.04.2025")
    settings = get_settings().model_copy(update={"ee_cache_dir": cache_dir})
    monkeypatch.setattr("app.services.ee_import.get_settings", lambda: settings)

    run = import_estonia(session, query={"years": [2024, 2025]}, min_employees=20, live_override=False)

    assert run.status == "UPSERTED"
    company = session.scalar(select(Company).where(Company.country == "EE"))
    assert company is not None
    detail = client.get(f"/companies/{company.id}").json()
    financial = next(row for row in detail["financials"] if row["fiscal_year"] == 2025)
    assert financial["period_start"] == "2025-04-01"
    assert financial["period_end"] == "2025-12-31"
    assert financial["period_days"] == 275
    assert financial["period_length_class"] == "short"
    assert financial["revenue"] == "1000.00"


def test_same_bare_activity_code_keeps_source_taxonomy_versions_distinct(
    cache_dir: Path, monkeypatch, session: Session, client
) -> None:
    settings = get_settings().model_copy(update={"ee_cache_dir": cache_dir})
    monkeypatch.setattr("app.services.ee_import.get_settings", lambda: settings)
    _datasets(cache_dir, emtak_version="EMTAK 2008")
    import_estonia(session, query={"years": [2024, 2025]}, min_employees=20, live_override=False)
    company = session.scalar(select(Company).where(Company.country == "EE"))
    assert company is not None

    _datasets(cache_dir, emtak_version="EMTAK 2025")
    import_estonia(session, query={"years": [2024, 2025]}, min_employees=20, live_override=False)

    facts = session.scalars(
        select(CompanyFact)
        .where(CompanyFact.company_id == company.id, CompanyFact.field_name == "industry_code")
        .order_by(CompanyFact.valid_from)
    ).all()
    assert len(facts) == 2
    assert [(fact.value_json, fact.code_system, fact.code_version) for fact in facts] == [
        ("62011", "EMTAK", "2008"),
        ("62011", "EMTAK", "2025"),
    ]
    assert facts[0].valid_to is not None and facts[1].valid_to is None

    detail = client.get(f"/companies/{company.id}").json()
    assert detail["company"]["industry_code_details"] == [
        {"code": "62011", "code_system": "EMTAK", "code_version": "2025"}
    ]
