"""Official register registered-address mapping and provenance."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.ee_ariregister import DatasetFile
from app.domain.ee_address import ADDRESS_SOURCE_COLUMNS, parse_registered_address
from app.models import Company, IngestionRun, RegisteredAddress, Source, SourceSnapshot
from app.services.ee_import import _row_snapshot, _upsert_address
from app.services.permissions import load_config
from app.services.retention import expire_raw_data
from app.services.source_registry import sync_sources


def address_row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in ADDRESS_SOURCE_COLUMNS}
    row.update(
        {
            "ettevotja_aadress": "",
            "asukoht_ettevotja_aadressis": "Regati pst 12",
            "asukoha_ehak_kood": "0596",
            "asukoha_ehak_tekstina": "Pirita linnaosa, Tallinn, Harju maakond",
            "indeks_ettevotja_aadressis": "11911",
            "ads_adr_id": "123",
            "ads_ads_oid": "456",
            "ads_normaliseeritud_taisaadress": "Harju maakond, Tallinn, Pirita linnaosa, Regati pst 12",
            "teabesysteemi_link": "https://example.invalid/evidence",
        }
    )
    return {**row, **overrides}


def test_every_registered_address_field_maps_from_its_source_column() -> None:
    parts = parse_registered_address(address_row())
    assert parts.as_dict() == {
        "address_line": "Regati pst 12",
        "postal_code": "11911",
        "city": "Tallinn",
        "municipality": "Tallinn",
        "county": "Harju maakond",
        "ehak_code": "0596",
        "country": "EE",
    }
    assert not parts.warnings
    tartu = parse_registered_address(
        address_row(asukoha_ehak_tekstina="Tartu linn, Tartu linn, Tartu maakond")
    )
    assert tartu.city == "Tartu"
    assert tartu.municipality == "Tartu linn"


def test_city_is_null_when_administrative_units_are_ambiguous() -> None:
    parts = parse_registered_address(address_row(asukoha_ehak_tekstina="Tallinn, Tartu linn, Harju maakond"))
    assert parts.city is None
    assert parts.municipality is None
    assert parts.county == "Harju maakond"
    assert any("ambiguous" in warning for warning in parts.warnings)
    village = parse_registered_address(
        address_row(asukoha_ehak_tekstina="Pihva küla, Tartu linn, Tartu maakond")
    )
    assert village.city is None
    assert village.municipality == "Tartu linn"
    joined_village = parse_registered_address(
        address_row(asukoha_ehak_tekstina="Pildiküla, Harku vald, Harju maakond")
    )
    assert joined_village.city is None
    assert joined_village.municipality == "Harku vald"
    assert not joined_village.warnings
    bilingual_village = parse_registered_address(
        address_row(asukoha_ehak_tekstina="Elbiku küla / Ölbäck, Lääne-Nigula vald, Lääne maakond")
    )
    assert bilingual_village.city is None
    assert bilingual_village.municipality == "Lääne-Nigula vald"
    assert not bilingual_village.warnings


def test_missing_and_unmapped_source_address_are_warned_not_invented() -> None:
    missing = parse_registered_address(
        address_row(
            asukoht_ettevotja_aadressis="",
            asukoha_ehak_tekstina="",
            asukoha_ehak_kood="",
            indeks_ettevotja_aadressis="",
        )
    )
    assert missing.address_line is None
    assert missing.city is None
    assert missing.municipality is None
    assert missing.county is None
    assert any("missing" in warning for warning in missing.warnings)
    populated = parse_registered_address(address_row(ettevotja_aadress="a composite address"))
    assert populated.address_line == "Regati pst 12"
    assert any("ettevotja_aadress is populated" in warning for warning in populated.warnings)


def test_snapshot_preserves_original_columns_and_address_reimport_is_idempotent(
    session: Session, client: TestClient
) -> None:
    sync_sources(session, actor="test", config=load_config())
    source = session.get(Source, "ee-ariregister")
    assert source is not None
    company = Company(legal_name="Address test", normalized_name="address test", country="EE")
    session.add(company)
    session.flush()
    file = DatasetFile(
        kind="basic",
        name="ettevotja_rekvisiidid__lihtandmed.csv.zip",
        url="https://avaandmed.ariregister.rik.ee/sites/default/files/avaandmed/ettevotja_rekvisiidid__lihtandmed.csv.zip",
        path=str(Path("unused.zip")),
        sha256="a" * 64,
        size=1,
        last_modified="2026-09-26T08:37:00+00:00",
    )
    raw = address_row()

    def import_address(row: dict[str, str], run_number: int) -> tuple[IngestionRun, dict[str, int]]:
        run = IngestionRun(
            source_id=source.id,
            kind="discovery",
            parser_version="test",
            config_hash="x",
            actor="test",
            started_at=datetime(2026, 9, 26, run_number, tzinfo=UTC),
        )
        session.add(run)
        session.flush()
        snapshot = _row_snapshot(
            session,
            run,
            source,
            source_key="company:12345678",
            raw={"provenance": {column: row[column] for column in ADDRESS_SOURCE_COLUMNS}},
            file=file,
        )
        counts = {"address_added": 0, "address_changed": 0, "address_unchanged": 0}
        _upsert_address(session, run, source, company, parse_registered_address(row), file, counts, snapshot)
        session.flush()
        return run, counts

    first_run, first_counts = import_address(raw, 10)
    assert first_counts["address_added"] == 1
    first = session.scalar(select(RegisteredAddress).where(RegisteredAddress.company_id == company.id))
    assert first is not None
    assert first.snapshot_id is not None
    assert first.ingestion_run_id == first_run.id
    assert first.observed_at == file.published_at
    assert first.source_url == file.url
    assert first.source_file == file.name
    response = client.get(f"/companies/{company.id}/registered-address")
    assert response.status_code == 200
    assert {key: response.json()[key] for key in parse_registered_address(raw).as_dict()} == (
        parse_registered_address(raw).as_dict()
    )
    snapshot = session.get(SourceSnapshot, first.snapshot_id)
    assert snapshot is not None and snapshot.raw_payload is not None
    provenance = snapshot.raw_payload["provenance"]
    assert {column: provenance[column] for column in ADDRESS_SOURCE_COLUMNS} == raw
    assert provenance["source_file"] == file.name
    assert provenance["source_csv_file"] == "ettevotja_rekvisiidid__lihtandmed.csv"
    assert provenance["download_url"] == file.url
    assert provenance["file_date"] == "2026-09-26"
    assert provenance["observed_at"] == file.published_at.isoformat()
    assert provenance["source_id"] == source.id
    assert provenance["ingestion_run_id"] == first_run.id

    _, repeat_counts = import_address({**raw, "ads_adr_id": "changed metadata"}, 11)
    assert repeat_counts["address_unchanged"] == 1
    assert session.query(RegisteredAddress).filter_by(company_id=company.id).count() == 1
    session.refresh(first)
    assert first.snapshot_id != snapshot.id

    changed = {**raw, "asukoht_ettevotja_aadressis": "Regati pst 14"}
    _, changed_counts = import_address(changed, 12)
    assert changed_counts["address_changed"] == 1
    _, reverted_counts = import_address(raw, 13)
    assert reverted_counts["address_changed"] == 1
    versions = session.scalars(
        select(RegisteredAddress).where(RegisteredAddress.company_id == company.id)
    ).all()
    assert len(versions) == 3
    assert [item.address_line for item in versions if item.valid_to is None] == ["Regati pst 12"]

    snapshot.expires_at = datetime(2026, 9, 25, tzinfo=UTC)
    session.flush()
    expire_raw_data(session, actor="test", now=datetime(2026, 10, 1, tzinfo=UTC))
    session.refresh(snapshot)
    assert snapshot.raw_payload is not None


def test_company_ui_uses_registered_address_label() -> None:
    page = (
        Path(__file__).resolve().parents[2] / "web" / "app" / "companies" / "[id]" / "page.tsx"
    ).read_text(encoding="utf-8")
    assert "Registered address" in page
    assert "headquarters" not in page.casefold()
