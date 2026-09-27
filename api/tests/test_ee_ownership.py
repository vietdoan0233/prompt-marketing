"""Contract tests for the Estonia share-capital and shareholders import.

Reuses the basic/reports/activity/indicators fixture builder from test_ee_orchestration so each test here
only has to add the two optional JSON files (general data and shareholders) on top of it.
"""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.base import ConnectorError
from app.connectors.ee_ariregister import PORTAL, DatasetFile, discover_links, iter_json_records
from app.models import Company, CompanyFact, CompanyShareholder, SourceSnapshot
from app.services.ee_import import import_estonia
from tests.test_ee_orchestration import PUBLISHED, REGISTRY_CODE, _datasets

GENERAL_NAME = "ettevotja_rekvisiidid__yldandmed.json.zip"
SHAREHOLDERS_NAME = "ettevotja_rekvisiidid__osanikud.json.zip"

# Deliberately adversarial: a real "F" (person) row from the portal never carries these, but the importer
# must never read them into storage even if a row somehow did.
LEAKED_ID_CODE = "39001010000"
LEAKED_ID_HASH = "77a468f2-248a-5e41-8356-c975af9613a5"
LEAKED_BIRTHDATE = "1990-01-01"
LEAKED_ADDRESS = "Secret Street 1"


def _write_json_zip(cache: Path, name: str, records: list[dict], *, kind: str) -> None:
    path = cache / name
    member = name.removesuffix(".zip")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, json.dumps(records).encode("utf-8"))
    entry = {
        "kind": kind,
        "name": name,
        "url": f"{PORTAL}/sites/default/files/avaandmed/{name}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
        "last_modified": PUBLISHED,
        "year": None,
    }
    manifest_path = cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest[name] = entry
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _general(cache: Path, *, amount: str = "2500.00", currency: str = "EUR", kande_nr: int = 1) -> None:
    _write_json_zip(
        cache,
        GENERAL_NAME,
        [
            {
                "ariregistri_kood": int(REGISTRY_CODE),
                "nimi": "Example OÜ",
                "yldandmed": {
                    "kapitalid": [
                        {
                            "kirje_id": 1,
                            "kande_nr": kande_nr,
                            "kapitali_suurus": amount,
                            "kapitali_valuuta": currency,
                            "algus_kpv": "05.06.2023",
                            "lopp_kpv": None,
                        }
                    ]
                },
            }
        ],
        kind="general",
    )


def _shareholders(cache: Path, *, person_percent: str = "60.00") -> None:
    _write_json_zip(
        cache,
        SHAREHOLDERS_NAME,
        [
            {
                "ariregistri_kood": int(REGISTRY_CODE),
                "nimi": "Example OÜ",
                "osanikud": [
                    {
                        "kirje_id": 1,
                        "isiku_tyyp": "F",
                        "isiku_roll": "OSAN",
                        "isiku_roll_tekstina": "Osanik",
                        "eesnimi": "Jane",
                        "nimi_arinimi": "Doe",
                        "isikukood_registrikood": LEAKED_ID_CODE,
                        "synniaeg": LEAKED_BIRTHDATE,
                        "aadress_tanav_maja_korter": LEAKED_ADDRESS,
                        "osaluse_protsent": person_percent,
                        "osaluse_suurus": "1500.00",
                        "osaluse_valuuta": "EUR",
                        "osaluse_omandiliik": "L",
                        "osaluse_omandiliik_tekstina": "Ainuomand",
                        "algus_kpv": "05.06.2023",
                        "lopp_kpv": None,
                        "isikukood_hash": LEAKED_ID_HASH,
                    },
                    {
                        "kirje_id": 2,
                        "isiku_tyyp": "J",
                        "isiku_roll": "OSAN",
                        "isiku_roll_tekstina": "Osanik",
                        "eesnimi": None,
                        "nimi_arinimi": "Holdco OÜ",
                        "isikukood_registrikood": "12345678",
                        "osaluse_protsent": "40.00",
                        "osaluse_suurus": "1000.00",
                        "osaluse_valuuta": "EUR",
                        "osaluse_omandiliik": "L",
                        "osaluse_omandiliik_tekstina": "Ainuomand",
                        "algus_kpv": "05.06.2023",
                        "lopp_kpv": None,
                    },
                ],
            }
        ],
        kind="shareholders",
    )


def _run(session: Session, monkeypatch, cache: Path):
    settings = get_settings().model_copy(update={"ee_cache_dir": cache})
    monkeypatch.setattr("app.services.ee_import.get_settings", lambda: settings)
    return import_estonia(session, query={"years": [2024, 2025]}, min_employees=20, live_override=False)


def test_share_capital_is_imported_as_a_versioned_fact(
    cache_dir: Path, monkeypatch, session: Session, client
):
    _datasets(cache_dir)
    _general(cache_dir)

    run = _run(session, monkeypatch, cache_dir)

    assert run.status == "UPSERTED"
    company = session.scalar(select(Company).where(Company.country == "EE"))
    assert company is not None
    detail = client.get(f"/companies/{company.id}").json()
    fields = {item["field_name"]: item for item in detail["fields"]}
    assert fields["share_capital"]["value"] == {"amount": "2500.00", "currency": "EUR"}
    assert fields["share_capital"]["status"] == "verified"

    # Re-importing the same capital value is idempotent: no second fact version is created.
    run2 = _run(session, monkeypatch, cache_dir)
    assert run2.status == "UPSERTED"
    facts = session.scalars(
        select(CompanyFact).where(
            CompanyFact.company_id == company.id, CompanyFact.field_name == "share_capital"
        )
    ).all()
    assert len(facts) == 1 and facts[0].valid_to is None

    # A changed capital value versions the fact rather than overwriting it.
    _general(cache_dir, amount="5000.00", kande_nr=2)
    _run(session, monkeypatch, cache_dir)
    facts = session.scalars(
        select(CompanyFact)
        .where(CompanyFact.company_id == company.id, CompanyFact.field_name == "share_capital")
        .order_by(CompanyFact.valid_from)
    ).all()
    assert [f.value_json for f in facts] == [
        {"amount": "2500.00", "currency": "EUR"},
        {"amount": "5000.00", "currency": "EUR"},
    ]
    assert facts[0].valid_to is not None and facts[1].valid_to is None


def test_shareholders_are_stored_without_the_persons_id_code_or_other_personal_fields(
    cache_dir: Path, monkeypatch, session: Session, client
):
    _datasets(cache_dir)
    _shareholders(cache_dir)

    run = _run(session, monkeypatch, cache_dir)

    assert run.status == "UPSERTED"
    company = session.scalar(select(Company).where(Company.country == "EE"))
    assert company is not None
    rows = session.scalars(
        select(CompanyShareholder).where(
            CompanyShareholder.company_id == company.id, CompanyShareholder.valid_to.is_(None)
        )
    ).all()
    assert len(rows) == 2
    person = next(r for r in rows if r.holder_type == "person")
    legal_entity = next(r for r in rows if r.holder_type == "legal_entity")
    assert person.holder_name == "Jane Doe"
    assert person.holder_registry_code is None
    assert legal_entity.holder_name == "Holdco OÜ"
    assert legal_entity.holder_registry_code == "12345678"

    # None of the leaked personal fields reached the shareholder table, the source snapshot, or the API.
    leaked = [LEAKED_ID_CODE, LEAKED_ID_HASH, LEAKED_BIRTHDATE, LEAKED_ADDRESS]
    row_text = json.dumps(
        [{c.name: str(getattr(r, c.name)) for c in r.__table__.columns} for r in rows], default=str
    )
    for value in leaked:
        assert value not in row_text

    snapshot = session.scalar(
        select(SourceSnapshot).where(SourceSnapshot.source_key == f"shareholders:{REGISTRY_CODE}")
    )
    assert snapshot is not None
    snapshot_text = json.dumps(snapshot.raw_payload, default=str)
    for value in leaked:
        assert value not in snapshot_text

    api_text = client.get(f"/companies/{company.id}/shareholders").text
    for value in leaked:
        assert value not in api_text


def test_shareholder_set_is_versioned_like_the_registered_address(
    cache_dir: Path, monkeypatch, session: Session
):
    _datasets(cache_dir)
    _shareholders(cache_dir, person_percent="60.00")
    run = _run(session, monkeypatch, cache_dir)
    assert run.counts["shareholders_added"] == 1
    company = session.scalar(select(Company).where(Company.country == "EE"))
    assert company is not None

    run2 = _run(session, monkeypatch, cache_dir)
    assert run2.counts["shareholders_unchanged"] == 1
    assert run2.counts.get("shareholders_changed", 0) == 0
    current = session.scalars(
        select(CompanyShareholder).where(
            CompanyShareholder.company_id == company.id, CompanyShareholder.valid_to.is_(None)
        )
    ).all()
    assert len(current) == 2

    _shareholders(cache_dir, person_percent="70.00")
    run3 = _run(session, monkeypatch, cache_dir)
    assert run3.counts["shareholders_changed"] == 1
    all_rows = session.scalars(
        select(CompanyShareholder).where(CompanyShareholder.company_id == company.id)
    ).all()
    current = [r for r in all_rows if r.valid_to is None]
    historical = [r for r in all_rows if r.valid_to is not None]
    assert len(current) == 2 and len(historical) == 2
    current_person = next(r for r in current if r.holder_type == "person")
    assert current_person.holding_percent == pytest.approx(70.00)


def test_missing_general_and_shareholders_files_warn_instead_of_failing(
    cache_dir: Path, monkeypatch, session: Session, client
):
    _datasets(cache_dir)  # no general/shareholders file added

    run = _run(session, monkeypatch, cache_dir)

    assert run.status == "UPSERTED"
    messages = " ".join(w["message"] for w in run.warnings if "message" in w)
    assert "general" in messages and "shareholders" in messages
    company = session.scalar(select(Company).where(Company.country == "EE"))
    assert company is not None
    detail = client.get(f"/companies/{company.id}").json()
    assert detail["shareholders"] == []
    fields = {item["field_name"]: item for item in detail["fields"]}
    assert fields["share_capital"]["status"] == "unknown"


def test_discover_links_finds_the_general_and_shareholders_files():
    html = (
        '<a href="/sites/default/files/avaandmed/ettevotja_rekvisiidid__osanikud.json.zip">osanikud</a>'
        '<a href="/sites/default/files/avaandmed/ettevotja_rekvisiidid__yldandmed.json.zip">yldandmed</a>'
    )
    found = discover_links(html)
    assert found["shareholders"] == [
        (f"{PORTAL}/sites/default/files/avaandmed/ettevotja_rekvisiidid__osanikud.json.zip", None)
    ]
    assert found["general"] == [
        (f"{PORTAL}/sites/default/files/avaandmed/ettevotja_rekvisiidid__yldandmed.json.zip", None)
    ]


def test_iter_json_records_streams_the_top_level_array(tmp_path: Path):
    records = [{"ariregistri_kood": 1, "nimi": "A"}, {"ariregistri_kood": 2, "nimi": "B õ"}]
    path = tmp_path / "sample.json.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("sample.json", json.dumps(records).encode("utf-8"))
    file = DatasetFile(
        kind="general",
        name="sample.json.zip",
        url="https://example.invalid/sample.json.zip",
        path=str(path),
        sha256="a" * 64,
        size=path.stat().st_size,
        last_modified=PUBLISHED,
    )
    assert list(iter_json_records(file)) == records


def test_iter_json_records_rejects_an_empty_array(tmp_path: Path):
    path = tmp_path / "empty.json.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("empty.json", b"[]")
    file = DatasetFile(
        kind="general",
        name="empty.json.zip",
        url="https://example.invalid/empty.json.zip",
        path=str(path),
        sha256="a" * 64,
        size=path.stat().st_size,
        last_modified=PUBLISHED,
    )
    assert list(iter_json_records(file)) == []


def test_iter_json_records_rejects_truncated_json(tmp_path: Path):
    path = tmp_path / "truncated.json.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("truncated.json", b'[{"ariregistri_kood": 1, "nimi": "A"')
    file = DatasetFile(
        kind="general",
        name="truncated.json.zip",
        url="https://example.invalid/truncated.json.zip",
        path=str(path),
        sha256="a" * 64,
        size=path.stat().st_size,
        last_modified=PUBLISHED,
    )
    with pytest.raises(ConnectorError, match="truncated"):
        list(iter_json_records(file))
