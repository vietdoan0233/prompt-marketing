"""End-to-end contract tests for the Estonia bulk-file importer."""

import csv
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.ee_ariregister import PORTAL
from app.models import (
    AuditEvent,
    Company,
    CompanyFinancial,
    IngestionRecord,
    RegisteredAddress,
    SourceSnapshot,
)
from app.services.ee_import import import_estonia

REGISTRY_CODE = "10065762"
PUBLISHED = "2026-09-03T11:04:00+00:00"


@pytest.fixture
def cache_dir():
    path = Path(__file__).parent / f".ee-import-cache-{uuid4().hex}"
    path.mkdir()
    yield path
    shutil.rmtree(path)


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
                "01.01.2025",
                "31.12.2025",
                "D2025",
            ],
        ],
    )
    _write_zip(cache, "1.aruannete_yldandmed_kuni_31082026.zip", reports, kind="reports")

    activity = _csv(
        ["report_id", "põhitegevusala", "emtak"],
        [["D2025", "Jah", "62011"]],
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
                "20",
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
    assert derived["currency"] == "EUR" and derived["unit"] == "EUR"
    assert derived["value_type"] == "derived"
    assert derived["calculation_formula"] == "operating_profit + depreciation_and_impairment"
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
