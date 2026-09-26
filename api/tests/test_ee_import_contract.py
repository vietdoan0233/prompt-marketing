"""Financial import rules for the official Estonia indicator files."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import ConnectorError
from app.connectors.ee_ariregister import DatasetFile, EeAriregisterFiles
from app.domain.records import ParsedRecord, build_record
from app.models import Company, CompanyFinancial, CompanyIdentifier, IngestionRun, Source
from app.services.ee_import import (
    IndicatorGroup,
    Report,
    _financial_payload,
    _legal_form,
    _row_snapshot,
    _upsert_financial,
)
from app.services.permissions import load_config
from app.services.resolution import resolve
from app.services.source_registry import sync_sources


def _group() -> IndicatorGroup:
    report = Report(
        report_id="report-123",
        registry_code="12345678",
        legal_form="ou",
        status="registered",
        fiscal_year=2024,
        consolidated=False,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        raw={"report_id": "report-123", "registrikood": "12345678"},
    )
    file = DatasetFile(
        kind="indicators",
        name="4.2024_aruannete_elemendid_kuni_31082026.zip",
        url="https://avaandmed.ariregister.rik.ee/sites/default/files/4.2024_aruannete_elemendid_kuni_31082026.zip",
        path="unused.zip",
        sha256="a" * 64,
        size=1,
        last_modified="2026-09-03T11:04:00+00:00",
        year=2024,
    )
    group = IndicatorGroup(report=report, scope="standalone", source_file=file)
    for line, (metric, element, value) in enumerate(
        (
            ("revenue", "Revenue", "1000"),
            ("net_income", "TotalAnnualPeriodProfitLoss", "75"),
            ("depreciation_and_impairment", "DepreciationAndImpairmentLossReversal", "20"),
            ("operating_profit", "TotalProfitLoss", "50"),
        ),
        start=1,
    ):
        group.elements.append(
            {
                "metric": metric,
                "element_name": element,
                "label": element,
                "table": "Kasumiaruanne",
                "value": value,
                "document_id": "report-123",
                "line_number": line,
            }
        )
    return group


def test_reported_lines_map_directly_and_deferred_values_stay_null(session: Session) -> None:
    sync_sources(session, actor="test", config=load_config())
    source = session.get(Source, "ee-ariregister")
    assert source is not None
    company = Company(legal_name="Financial test", normalized_name="financial test", country="EE")
    session.add(company)
    session.flush()
    group = _group()
    values, source_values, warnings = _financial_payload(group)
    assert values == {
        "revenue": Decimal("1000"),
        "net_income": Decimal("75"),
        "depreciation_and_impairment": Decimal("20"),
        "operating_profit": Decimal("50"),
    }
    assert not warnings
    assert {item["element_name"] for item in source_values} == {
        "Revenue",
        "TotalAnnualPeriodProfitLoss",
        "DepreciationAndImpairmentLossReversal",
        "TotalProfitLoss",
    }

    def import_group(hour: int) -> dict[str, int]:
        run = IngestionRun(
            source_id=source.id,
            kind="discovery",
            parser_version="test",
            config_hash="x",
            actor="test",
            started_at=datetime(2026, 9, 26, hour, tzinfo=UTC),
        )
        session.add(run)
        session.flush()
        snapshot = _row_snapshot(
            session,
            run,
            source,
            source_key="report:report-123:standalone",
            raw={"general_info": group.report.raw, "elements": source_values},
            file=group.source_file,
        )
        counts: dict[str, int] = {
            "financial_added": 0,
            "financial_changed": 0,
            "financial_unchanged": 0,
            "reports_imported": 0,
        }
        _upsert_financial(session, run, source, company, group, snapshot, counts)
        session.flush()
        return counts

    first_counts = import_group(10)
    assert first_counts["financial_added"] == 1
    assert first_counts.get("missing_currency", 0) == 0
    assert first_counts.get("missing_unit", 0) == 0
    financial = session.scalar(select(CompanyFinancial).where(CompanyFinancial.company_id == company.id))
    assert financial is not None
    assert financial.revenue == Decimal("1000")
    assert financial.net_income == Decimal("75")
    assert financial.depreciation_and_impairment == Decimal("20")
    assert financial.depreciation is None
    assert financial.ebitda == Decimal("70")
    assert financial.dividends is None
    assert financial.capex is None
    assert financial.currency == "EUR" and financial.unit == "EUR"
    assert financial.calculation_formula == "operating_profit + depreciation_and_impairment"
    assert financial.source_id == source.id
    assert financial.source_url == group.source_file.url
    assert financial.source_file == group.source_file.name
    assert financial.snapshot_id and financial.ingestion_run_id
    assert financial.observed_at == group.source_file.published_at
    assert financial.value_type == "derived"
    assert financial.statement_scope == "standalone"
    assert import_group(11)["financial_unchanged"] == 1
    assert session.query(CompanyFinancial).filter_by(company_id=company.id).count() == 1


def test_non_estonian_company_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr("app.domain.records.active_countries", lambda: {"EE"})
    record = build_record(
        1,
        {"source_key": "FI:123", "legal_name": "Foreign company", "country": "FI"},
        source_countries=["EE"],
        allowed_fields=["legal_name"],
    )
    assert not record.ok
    assert any("not an active country" in error for error in record.errors)


def test_indicator_completeness_requires_each_target_year() -> None:
    files = [
        DatasetFile(
            kind=kind,
            name=f"{kind}.zip",
            url="https://avaandmed.ariregister.rik.ee/sites/default/files/test.zip",
            path="unused.zip",
            sha256="a" * 64,
            size=1,
            last_modified="2026-09-03T11:04:00+00:00",
        )
        for kind in ("basic", "reports", "activity")
    ]
    files.append(
        DatasetFile(
            kind="indicators",
            name="4.2024.zip",
            url="https://avaandmed.ariregister.rik.ee/sites/default/files/4.2024.zip",
            path="unused.zip",
            sha256="a" * 64,
            size=1,
            last_modified="2026-09-03T11:04:00+00:00",
            year=2024,
        )
    )
    with pytest.raises(ConnectorError, match="2025"):
        EeAriregisterFiles._check_complete(files, [2024, 2025])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Osaühing", "ou"),
        ("Aktsiaselts", "as"),
        ("Usaldusühing", "uu"),
        ("Täisühing", "tu"),
        ("Tulundusühistu", "tuh"),
        ("Euroopa äriühing", "se"),
    ],
)
def test_legal_forms_use_accent_normalized_canonical_keys(raw: str, expected: str) -> None:
    assert _legal_form(raw) == expected


def test_only_official_estonia_source_is_enabled() -> None:
    configured = load_config()["sources"]
    assert [source["id"] for source in configured if source["enabled"]] == ["ee-ariregister"]
    assert all(source["countries"] == ["EE"] for source in configured)


def test_shared_vat_does_not_merge_distinct_estonian_registry_codes(session: Session) -> None:
    sync_sources(session, actor="test", config=load_config())
    source = session.get(Source, "ee-ariregister")
    assert source is not None
    company = Company(legal_name="First company", normalized_name="first company", country="EE")
    session.add(company)
    session.flush()
    session.add_all(
        [
            CompanyIdentifier(
                company_id=company.id, kind="registry_id", value="EE:10065762", source_id=source.id
            ),
            CompanyIdentifier(company_id=company.id, kind="vat_id", value="EE100058428", source_id=source.id),
        ]
    )
    session.flush()
    record = ParsedRecord(row_number=1, source_key="EE:10068482", raw={}, country="EE")
    record.registry_key = "EE:10068482"
    record.vat_key = "EE100058428"
    resolution, _ = resolve(session, record, source)
    assert resolution.company is None
    assert ("vat_id", "EE100058428") in resolution.blocked_keys
