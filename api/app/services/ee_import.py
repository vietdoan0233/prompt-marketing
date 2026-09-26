"""Import the official Estonian e-Business Register bulk datasets.

The importer deliberately keeps the bulk-file resolver separate from the normal connector protocol. The
source consists of several related files and needs two passes over the indicator data before a company can be
qualified. Company facts still use the normal resolution/versioning services; financials, registered
addresses and shareholders are stored in their dedicated append-only tables. Share capital is a versioned
company fact, like registry status or industry code.

The general (yldandmed) and shareholders (osanikud) files are optional: their absence — an offline
`--from-cache` manifest predating them, or a portal that temporarily drops one — only skips that section
with a run warning. A person shareholder's national ID code, its one-way hash, birth date and home address
are never read from the shareholders file, so they can never reach a company_shareholders row or a source
snapshot; only their name, role and holding are used.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.base import ConnectorError
from app.connectors.ee_ariregister import (
    EE_PARSER_VERSION,
    QUALIFICATION_YEARS,
    DatasetFile,
    EeAriregisterFiles,
    iter_json_records,
    iter_rows,
)
from app.domain.ee_address import (
    ADDRESS_PARSER_VERSION,
    ADDRESS_SOURCE_COLUMNS,
    RegisteredAddressParts,
    parse_registered_address,
)
from app.domain.normalize import normalize_name
from app.domain.records import build_record, stable_hash
from app.domain.reporting_period import reporting_period_metadata
from app.models import (
    Company,
    CompanyFinancial,
    CompanyIdentifier,
    CompanyShareholder,
    IngestionRecord,
    IngestionRun,
    RegisteredAddress,
    Source,
    SourceSnapshot,
    utcnow,
)
from app.services import audit
from app.services.resolution import ensure_identifiers, recompute_company, resolve, upsert_facts

ALLOWED_LEGAL_FORMS = {"ou", "as", "uu", "tu", "tuh", "se"}
DEFAULT_YEARS = tuple(range(2019, 2026))
FINANCIAL_FIELDS = (
    "revenue",
    "ebitda",
    "net_income",
    "dividends",
    "capex",
    "depreciation",
    "depreciation_and_impairment",
    "operating_profit",
    "profit_before_tax",
    "total_assets",
    "equity",
    "labour_cost",
    "employees_fte",
)
ELEMENT_TO_METRIC = {
    "Revenue": "revenue",
    "EBITDA": "ebitda",
    "TotalAnnualPeriodProfitLoss": "net_income",
    "TotalProfitLoss": "operating_profit",
    "TotalProfitLossBeforeTax": "profit_before_tax",
    "EmployeeExpense": "labour_cost",
    "DepreciationAndImpairmentLossReversal": "depreciation_and_impairment",
    "Assets": "total_assets",
    "Equity": "equity",
    "AverageNumberOfEmployeesInFullTimeEquivalentUnits": "employees_fte",
}
COUNT_FIELDS = (
    "financial_added",
    "financial_changed",
    "financial_unchanged",
    "address_added",
    "address_changed",
    "address_unchanged",
    "shareholders_added",
    "shareholders_changed",
    "shareholders_unchanged",
    "orphan_reports",
    "reports_imported",
    "missing_revenue",
    "missing_ebitda",
    "missing_net_income",
    "missing_dividends",
    "missing_capex",
    "missing_depreciation",
    "missing_currency",
    "missing_unit",
)


@dataclass
class Report:
    report_id: str
    registry_code: str
    legal_form: str
    status: str
    fiscal_year: int
    consolidated: bool | None
    period_start: date | None
    period_end: date | None
    raw: dict[str, str]
    document_id: str | None = None


@dataclass
class IndicatorGroup:
    report: Report
    scope: str | None
    source_file: DatasetFile
    elements: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _legal_form(value: Any) -> str:
    value = _strip_accents(_text(value)).casefold()
    compact = "".join(c for c in value if c.isalnum())
    return {
        "osauhing": "ou",
        "aktsiaselts": "as",
        "usaldusuhing": "uu",
        "taisuhing": "tu",
        "tulundusuhistu": "tuh",
        "euroopaariuhing": "se",
        "se": "se",
    }.get(compact, compact)


def _parse_date(value: Any) -> date | None:
    raw = _text(value)
    if not raw:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _parse_decimal(value: Any) -> Decimal | None:
    raw = _text(value).replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parse_bool(value: Any) -> bool | None:
    raw = _strip_accents(_text(value)).casefold()
    if raw in {"jah", "yes", "true", "1"}:
        return True
    if raw in {"ei", "no", "false", "0"}:
        return False
    return None


def _report_from_row(row: dict[str, str], allowed: set[str]) -> Report | None:
    code = _text(row.get("registrikood"))
    form = _legal_form(row.get("õiguslik vorm"))
    try:
        year = int(_text(row.get("aruandeaasta")))
    except ValueError:
        return None
    report_id = _text(row.get("report_id"))
    if not report_id or not code or form not in allowed:
        return None
    return Report(
        report_id=report_id,
        registry_code=code,
        legal_form=form,
        status=_text(row.get("staatus")),
        fiscal_year=year,
        consolidated=_parse_bool(row.get("kas konsolideeritud?")),
        period_start=_parse_date(row.get("period_start")),
        period_end=_parse_date(row.get("period_end")),
        raw=dict(row),
        document_id=_text(row.get("taidetud_aruanne_report_id")) or None,
    )


def _statement_scope(report: Report, table: str, element_name: str) -> tuple[str | None, str | None]:
    table_norm = _strip_accents(_text(table)).casefold()
    if table_norm.startswith("konsolideerimata"):
        return "standalone", None
    if table_norm.startswith("konsolideeritud") or element_name.endswith("Consolidated"):
        return "consolidated", None
    if report.consolidated is False:
        return "standalone", None
    return None, "statement scope is unproven for a consolidated report"


def _ignored_table(table: str) -> bool:
    norm = _strip_accents(_text(table)).casefold()
    return any(
        marker in norm
        for marker in (
            "likvideerimise",
            "loppbilanss",
            "rahavoog",
            "rahavoogude",
            "kasumi jaot",
            "kasumijaot",
            "pdf",
        )
    )


def _table_priority(table: str) -> tuple[int, int, str]:
    norm = _strip_accents(_text(table)).casefold()
    return (1 if "detail" in norm else 0, len(norm), norm)


def _years(query: dict[str, Any]) -> list[int]:
    wanted = query.get("years")
    if not wanted:
        return list(DEFAULT_YEARS)
    values = sorted({int(y) for y in wanted})
    invalid = [y for y in values if y not in DEFAULT_YEARS]
    if invalid:
        raise ConnectorError(f"indicator years must be between 2019 and 2025: {invalid}")
    return values


def _file_snapshot(session: Session, run: IngestionRun, source: Source, file: DatasetFile) -> SourceSnapshot:
    existing = session.scalar(
        select(SourceSnapshot).where(
            SourceSnapshot.source_id == source.id,
            SourceSnapshot.source_key == f"file:{file.name}",
            SourceSnapshot.content_hash == file.sha256,
        )
    )
    if existing:
        return existing
    snapshot = SourceSnapshot(
        source_id=source.id,
        ingestion_run_id=run.id,
        source_key=f"file:{file.name}",
        source_url=file.url,
        content_hash=file.sha256,
        raw_payload={"dataset": file.meta()},
        parser_version=EE_PARSER_VERSION,
        http_status=200,
        retrieved_at=run.started_at,
        expires_at=run.started_at + timedelta(days=source.retention_days),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _row_snapshot(
    session: Session,
    run: IngestionRun,
    source: Source,
    *,
    source_key: str,
    raw: dict[str, Any],
    file: DatasetFile,
) -> SourceSnapshot:
    content_hash = stable_hash(raw)
    existing = session.scalar(
        select(SourceSnapshot).where(
            SourceSnapshot.source_id == source.id,
            SourceSnapshot.source_key == source_key,
            SourceSnapshot.content_hash == content_hash,
        )
    )
    run_id = existing.ingestion_run_id if existing else run.id
    payload = dict(raw)
    if isinstance(payload.get("provenance"), dict):
        payload["provenance"] = {
            **payload["provenance"],
            "source_file": file.name,
            "source_csv_file": file.name.removesuffix(".zip"),
            "download_url": file.url,
            "file_date": file.published_at.date().isoformat(),
            "observed_at": file.published_at.isoformat(),
            "source_id": source.id,
            "ingestion_run_id": run_id,
        }
    if existing:
        if existing.raw_payload is not None and existing.raw_payload != payload:
            existing.raw_payload = payload
        return existing
    snapshot = SourceSnapshot(
        source_id=source.id,
        ingestion_run_id=run.id,
        source_key=source_key,
        source_url=file.url,
        content_hash=content_hash,
        raw_payload=payload,
        parser_version=EE_PARSER_VERSION,
        http_status=200,
        retrieved_at=run.started_at,
        expires_at=run.started_at + timedelta(days=source.retention_days),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _new_record(run: IngestionRun, row_number: int, source_key: str | None, **kwargs: Any) -> IngestionRecord:
    return IngestionRecord(run_id=run.id, row_number=row_number, source_key=source_key, **kwargs)


def _upsert_companies(
    session: Session,
    run: IngestionRun,
    source: Source,
    basic_file: DatasetFile,
    rows: list[dict[str, Any]],
    counts: dict[str, int],
) -> dict[str, Company]:
    companies: dict[str, Company] = {}
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        rec = build_record(
            row_number,
            row,
            source_countries=source.countries,
            allowed_fields=source.allowed_fields,
        )
        outcome = _new_record(
            run,
            row_number,
            rec.source_key,
            outcome="rejected",
            warnings=list(rec.warnings),
            errors=list(rec.errors),
        )
        session.add(outcome)
        if not rec.ok:
            counts["rejected"] += 1
            continue
        if rec.source_key in seen:
            outcome.outcome = "duplicate"
            outcome.match_reasons = ["same stable key repeated in the basic-data file"]
            counts["duplicates"] += 1
            continue
        seen.add(rec.source_key or "")
        nested = session.begin_nested()
        try:
            resolution, keys = resolve(session, rec, source)
            company = resolution.company
            is_new = company is None
            if company is None:
                company = Company(
                    legal_name=rec.facts["legal_name"],
                    normalized_name=normalize_name(rec.facts["legal_name"]),
                    country=rec.country or "EE",
                    region=rec.region,
                    first_seen_at=rec.observed_at or run.started_at,
                )
                session.add(company)
                session.flush()
                outcome.match_reasons = ["no existing company matched an exact Estonia key; created profile"]
            else:
                outcome.match_reasons = resolution.match_reasons
            snapshot = _row_snapshot(
                session,
                run,
                source,
                source_key=f"company:{row['registry_id']}",
                raw=rec.raw,
                file=basic_file,
            )
            ensure_identifiers(session, company, keys, source, resolution.blocked_keys)
            change = upsert_facts(
                session,
                company,
                rec,
                source,
                run_id=run.id,
                snapshot_id=snapshot.id,
                observed_at=rec.observed_at or run.started_at,
            )
            recompute_company(session, company)
            if is_new:
                outcome.outcome = "accepted"
                counts["accepted"] += 1
            elif change.added or change.changed:
                outcome.outcome = "updated"
                counts["updated"] += 1
            else:
                outcome.outcome = "unchanged"
                counts["unchanged"] += 1
            counts["facts_added"] += change.added
            counts["facts_changed"] += change.changed
            outcome.company_id = company.id
            outcome.qualification = company.qualification_status
            counts[company.qualification_status] = counts.get(company.qualification_status, 0) + 1
            companies[str(row["registry_id"])] = company
            nested.commit()
        except Exception as exc:  # isolate one malformed row from the rest of the official file
            nested.rollback()
            counts["rejected"] += 1
            outcome.outcome = "rejected"
            outcome.errors = [*outcome.errors, f"upsert failed: {type(exc).__name__}: {exc}"]
    session.flush()
    return companies


def _upsert_address(
    session: Session,
    run: IngestionRun,
    source: Source,
    company: Company,
    parts: RegisteredAddressParts,
    file: DatasetFile,
    counts: dict[str, int],
    snapshot: SourceSnapshot,
) -> None:
    # The original address columns and ADS identifiers are evidence, but only the mapped address determines
    # whether a new address version is needed.
    content_hash = stable_hash(parts.as_dict())
    current = session.scalar(
        select(RegisteredAddress).where(
            RegisteredAddress.company_id == company.id,
            RegisteredAddress.source_id == source.id,
            RegisteredAddress.valid_to.is_(None),
        )
    )
    if current and current.content_hash == content_hash:
        if current.snapshot_id != snapshot.id:
            current.snapshot_id = snapshot.id
            current.ingestion_run_id = run.id
            current.observed_at = file.published_at
            current.source_url = file.url
            current.source_file = file.name
            current.parser_version = ADDRESS_PARSER_VERSION
            current.warnings = parts.warnings
        counts["address_unchanged"] += 1
        return
    now = utcnow()
    for old in session.scalars(
        select(RegisteredAddress).where(
            RegisteredAddress.company_id == company.id,
            RegisteredAddress.source_id == source.id,
            RegisteredAddress.valid_to.is_(None),
        )
    ):
        old.valid_to = now
    session.add(
        RegisteredAddress(
            company_id=company.id,
            **parts.as_dict(),
            source_id=source.id,
            source_url=file.url,
            source_file=file.name,
            snapshot_id=snapshot.id,
            ingestion_run_id=run.id,
            observed_at=file.published_at,
            parser_version=ADDRESS_PARSER_VERSION,
            content_hash=content_hash,
            warnings=parts.warnings,
            valid_from=now,
        )
    )
    counts["address_changed" if current is not None else "address_added"] += 1


def _current_capital(record: dict[str, Any]) -> tuple[Decimal, str | None] | None:
    """The current share-capital entry (kapitalid) from one company's general-data record, if any."""
    entries = (record.get("yldandmed") or {}).get("kapitalid") or []
    current = [e for e in entries if not e.get("lopp_kpv")]
    if not current:
        return None
    latest = max(current, key=lambda e: e.get("kande_nr") or 0)
    value = _parse_decimal(latest.get("kapitali_suurus"))
    if value is None:
        return None
    return value, _text(latest.get("kapitali_valuuta")) or None


def _json_safe(value: Any) -> Any:
    """Recursively convert Decimal/date leaves so a parsed row can go straight into a JSON snapshot column."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _shareholder_from_record(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Map one official `osanikud[]` entry to the columns this database stores.

    This is an allow-list, not a filter: every field this function does not explicitly read — the national
    ID code, its one-way hash, birth date, home address and control-basis metadata the source also carries
    — is dropped by construction and can never reach `CompanyShareholder` or a source snapshot, regardless
    of what the official file contains.
    """
    holder_type_code = _text(entry.get("isiku_tyyp"))
    if holder_type_code == "F":  # füüsiline isik — a person
        holder_type = "person"
        name = " ".join(
            part for part in (_text(entry.get("eesnimi")), _text(entry.get("nimi_arinimi"))) if part
        )
        holder_registry_code = None
        holder_country = None
    elif holder_type_code == "J":  # juriidiline isik — a company or other legal entity
        holder_type = "legal_entity"
        name = _text(entry.get("nimi_arinimi"))
        holder_registry_code = (
            _text(entry.get("isikukood_registrikood")) or _text(entry.get("valis_kood")) or None
        )
        holder_country = _text(entry.get("valis_kood_riik")) or None
    else:
        holder_type, name, holder_registry_code, holder_country = (
            "unknown",
            _text(entry.get("nimi_arinimi")),
            None,
            None,
        )
    if not name:
        return None
    return {
        "holder_type": holder_type,
        "holder_name": name,
        "holder_registry_code": holder_registry_code,
        "holder_country": holder_country,
        "role": _text(entry.get("isiku_roll_tekstina")) or None,
        "holding_amount": _parse_decimal(entry.get("osaluse_suurus")),
        "holding_currency": _text(entry.get("osaluse_valuuta")) or None,
        "holding_percent": _parse_decimal(entry.get("osaluse_protsent")),
        "holding_type": _text(entry.get("osaluse_omandiliik_tekstina")) or None,
        "effective_from": _parse_date(entry.get("algus_kpv")),
        "effective_to": _parse_date(entry.get("lopp_kpv")),
    }


def _upsert_shareholders(
    session: Session,
    run: IngestionRun,
    source: Source,
    company: Company,
    holders: list[dict[str, Any]],
    file: DatasetFile,
    counts: dict[str, int],
    snapshot: SourceSnapshot,
) -> None:
    """Versioned like the registered address, but as one group: the reported set is what the source
    asserts as current, so an unchanged set is confirmed in place and a changed set is replaced together.
    """
    current_rows = session.scalars(
        select(CompanyShareholder).where(
            CompanyShareholder.company_id == company.id,
            CompanyShareholder.source_id == source.id,
            CompanyShareholder.valid_to.is_(None),
        )
    ).all()
    if not current_rows and not holders:
        counts["shareholders_unchanged"] += 1
        return
    content_hash = stable_hash(holders)
    if current_rows and current_rows[0].content_hash == content_hash:
        for row in current_rows:
            if row.snapshot_id != snapshot.id:
                row.snapshot_id = snapshot.id
                row.ingestion_run_id = run.id
                row.observed_at = file.published_at
                row.source_url = file.url
                row.source_file = file.name
        counts["shareholders_unchanged"] += 1
        return
    now = utcnow()
    for row in current_rows:
        row.valid_to = now
    for holder in holders:
        session.add(
            CompanyShareholder(
                company_id=company.id,
                **holder,
                source_id=source.id,
                source_url=file.url,
                source_file=file.name,
                snapshot_id=snapshot.id,
                ingestion_run_id=run.id,
                observed_at=file.published_at,
                parser_version=EE_PARSER_VERSION,
                content_hash=content_hash,
                valid_from=now,
            )
        )
    counts["shareholders_changed" if current_rows else "shareholders_added"] += 1


def _financial_payload(group: IndicatorGroup) -> tuple[dict[str, Decimal], list[dict[str, Any]], list[str]]:
    warnings = list(group.warnings)
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for element in group.elements:
        metric = element.get("metric")
        if not metric:
            continue
        candidates[metric].append(element)
    values: dict[str, Decimal] = {}
    chosen: dict[str, dict[str, Any]] = {}
    for metric, elements in candidates.items():
        ordered = sorted(elements, key=lambda e: (_table_priority(e["table"]), e["line_number"]))
        first = ordered[0]
        parsed = _parse_decimal(first["value"])
        if parsed is not None:
            values[metric] = parsed
            chosen[metric] = first
        for other in ordered[1:]:
            other_value = _parse_decimal(other["value"])
            if parsed is not None and other_value is not None and other_value != parsed:
                warnings.append(
                    f"duplicate {metric} values differ; selected table '{first['table']}' "
                    f"over '{other['table']}'"
                )
    source_values = [
        {
            "metric": element["metric"],
            "element_name": element["element_name"],
            "label": element["label"],
            "table": element["table"],
            "value": element["value"],
            "document_id": element["document_id"],
            "selected": chosen.get(element["metric"]) is element,
        }
        for element in group.elements
    ]
    return values, source_values, warnings


def _upsert_financial(
    session: Session,
    run: IngestionRun,
    source: Source,
    company: Company,
    group: IndicatorGroup,
    snapshot: SourceSnapshot,
    counts: dict[str, int],
) -> None:
    values, source_values, warnings = _financial_payload(group)
    calculation_formula = None
    if (
        "ebitda" not in values
        and values.get("operating_profit") is not None
        and values.get("depreciation_and_impairment") is not None
    ):
        # The Estonian statement line preserves its reported sign: expenses are negative. Subtract
        # that signed expense from operating profit to add it back; a reported EBITDA still wins.
        values["ebitda"] = values["operating_profit"] - values["depreciation_and_impairment"]
        calculation_formula = "operating_profit - depreciation_and_impairment"
    report = group.report
    source_key = (
        f"EE:{report.registry_code}:{report.report_id}:{report.fiscal_year}:{group.scope or 'unknown'}"
    )
    content_hash = stable_hash(
        {
            "report": report.raw,
            "scope": group.scope,
            "values": source_values,
            "normalized_values": {field: values.get(field) for field in FINANCIAL_FIELDS},
            "currency": "EUR",
            "unit": "EUR",
            "calculation_formula": calculation_formula,
            "parser_version": EE_PARSER_VERSION,
            "warnings": warnings,
        }
    )
    existing = session.scalar(
        select(CompanyFinancial).where(
            CompanyFinancial.source_id == source.id,
            CompanyFinancial.source_key == source_key,
            CompanyFinancial.content_hash == content_hash,
        )
    )
    if existing:
        counts["financial_unchanged"] += 1
        return
    previous_rows = list(
        session.scalars(
            select(CompanyFinancial).where(
                CompanyFinancial.source_id == source.id,
                CompanyFinancial.source_key == source_key,
                CompanyFinancial.review_status != "superseded",
            )
        )
    )
    for old in previous_rows:
        old.review_status = "superseded"
    kwargs = {field: values.get(field) for field in FINANCIAL_FIELDS}
    period_days, period_length_class = reporting_period_metadata(report.period_start, report.period_end)
    session.add(
        CompanyFinancial(
            company_id=company.id,
            period_start=report.period_start,
            period_end=report.period_end,
            period_days=period_days,
            period_length_class=period_length_class,
            fiscal_year=report.fiscal_year,
            currency="EUR",
            **kwargs,
            source_id=source.id,
            source_url=group.source_file.url,
            source_file=group.source_file.name,
            snapshot_id=snapshot.id,
            observed_at=group.source_file.published_at,
            confidence=source.base_confidence,
            usage_policy=source.usage_policy,
            parser_version=EE_PARSER_VERSION,
            statement_scope=group.scope,
            value_type="derived" if calculation_formula else "reported",
            filing_id=report.report_id,
            document_id=source_values[0]["document_id"] if source_values else report.document_id,
            unit="EUR",
            restated=None,
            calculation_formula=calculation_formula,
            ingestion_run_id=run.id,
            review_status="unreviewed",
            registry_code=report.registry_code,
            source_key=source_key,
            content_hash=content_hash,
            source_values=source_values,
        )
    )
    counts["financial_changed" if previous_rows else "financial_added"] += 1
    for metric in FINANCIAL_FIELDS:
        if metric not in values:
            counts[f"missing_{metric}"] = counts.get(f"missing_{metric}", 0) + 1
    counts["reports_imported"] += 1


def _record_orphan(
    session: Session,
    run: IngestionRun,
    file: DatasetFile,
    report_id: str,
    row_number: int,
    counts: dict[str, int],
    seen: set[tuple[str, str]],
) -> None:
    key = (file.name, report_id)
    if key in seen:
        return
    seen.add(key)
    session.add(
        IngestionRecord(
            run_id=run.id,
            row_number=10_000_000 + row_number,
            source_key=f"orphan:{file.name}:{report_id}",
            outcome="rejected",
            warnings=[],
            errors=["indicator report has no matching general-info row; values were not imported"],
        )
    )
    counts["rejected"] += 1
    counts["orphan_reports"] += 1


def _load_reports(
    files: list[DatasetFile], years: list[int]
) -> tuple[dict[str, Report], dict[str, Report], dict[str, list[Report]]]:
    report_file = next(f for f in files if f.kind == "reports")
    reports: dict[str, Report] = {}
    documents: dict[str, Report] = {}
    by_code: dict[str, list[Report]] = defaultdict(list)
    for row in iter_rows(report_file):
        report = _report_from_row(row, ALLOWED_LEGAL_FORMS)
        if report is None or report.fiscal_year not in years:
            continue
        reports[report.report_id] = report
        if report.document_id:
            documents[report.document_id] = report
        by_code[report.registry_code].append(report)
    return reports, documents, by_code


def _resolve_report(
    report_id: str, reports: dict[str, Report], documents: dict[str, Report]
) -> Report | None:
    return reports.get(report_id) or documents.get(report_id)


def import_estonia(
    session: Session,
    *,
    source_id: str = "ee-ariregister",
    query: dict[str, Any] | None = None,
    actor: str = "system:seed",
    min_employees: int | None = None,
    retry_of_id: str | None = None,
    live_override: bool | None = None,
) -> IngestionRun:
    """Run an Estonia import from the configured live portal or a local manifest cache."""
    from app.services.ingestion import _fail, _gate_or_reject, _new_run, _registered_or_deny

    query = dict(query or {})
    settings = get_settings()
    minimum = settings.min_employees_default if min_employees is None else min_employees
    source = _registered_or_deny(session, source_id, actor)
    run = _new_run(
        session,
        source,
        source_id,
        kind="discovery",
        actor=actor,
        min_employees=minimum,
        query=query,
        retry_of_id=retry_of_id,
    )
    live = bool((source.connector_config or {}).get("live")) if live_override is None else live_override
    _gate_or_reject(session, run, source, live=live)
    years = _years(query)
    run.parser_version = EE_PARSER_VERSION
    run.config_hash = stable_hash({"source": source.id, "years": years, "min_employees": minimum})
    try:
        resolver = EeAriregisterFiles(
            settings.ee_cache_dir, live=live, rate_limit_per_minute=source.rate_limit_per_minute
        )
        files = resolver.resolve(years)
    except (ConnectorError, OSError, ValueError) as exc:
        return _fail(session, run, "resolve", str(exc))

    run.status = "FETCHED"
    for file in files:
        _file_snapshot(session, run, source, file)
    run.status = "PARSED"
    reports, documents, reports_by_code = _load_reports(files, years)
    qualification_years = sorted(QUALIFICATION_YEARS)
    qualification_reports, qualification_documents, _ = _load_reports(files, qualification_years)
    run.counts = {**run.counts, **{field: 0 for field in COUNT_FIELDS}}

    # Pass A: determine the current qualified company set from 2024/2025 entity-level FTE lines.
    fte_by_code: dict[str, tuple[int, Decimal, DatasetFile]] = {}
    indicator_files = [f for f in files if f.kind == "indicators"]
    for file in indicator_files:
        if file.year not in QUALIFICATION_YEARS:
            continue
        for row in iter_rows(file):
            if row.get("elemendi_nimetus") != "AverageNumberOfEmployeesInFullTimeEquivalentUnits":
                continue
            report = _resolve_report(
                _text(row.get("report_id")), qualification_reports, qualification_documents
            )
            if report is None:
                continue
            scope, _ = _statement_scope(report, _text(row.get("tabel")), _text(row.get("elemendi_nimetus")))
            value = _parse_decimal(row.get("vaartus"))
            if scope == "consolidated" or value is None or report.fiscal_year not in QUALIFICATION_YEARS:
                continue
            previous = fte_by_code.get(report.registry_code)
            if previous is None or report.fiscal_year >= previous[0]:
                fte_by_code[report.registry_code] = (report.fiscal_year, value, file)

    in_scope = (
        set(reports_by_code)
        if minimum <= 0
        else {code for code, (_, value, _) in fte_by_code.items() if value >= Decimal(minimum)}
    )
    known_registry_codes = {
        value.removeprefix("EE:")
        for value in session.scalars(
            select(CompanyIdentifier.value)
            .join(Company, Company.id == CompanyIdentifier.company_id)
            .where(Company.country == "EE", CompanyIdentifier.kind == "registry_id")
        )
    }

    # Current basic data supplies the company profile and the registered address. Only eligible, in-scope
    # registry codes are materialised; the full 378k-row file is never retained in memory.
    basic_file = next(f for f in files if f.kind == "basic")
    activity_file = next(f for f in files if f.kind == "activity")
    latest_activity: dict[str, tuple[int, str, str | None]] = {}
    for row in iter_rows(activity_file):
        report = _resolve_report(_text(row.get("report_id")), reports, documents)
        if report is None:
            continue
        code = report.registry_code
        if code not in in_scope or _strip_accents(_text(row.get("põhitegevusala"))).casefold() not in {
            "jah",
            "yes",
        }:
            continue
        version_label = _text(row.get("emtak_version"))
        if version_label.casefold().startswith("emtak "):
            version = version_label[len("EMTAK ") :].strip() or None
        else:
            version = version_label or None
        candidate = (report.fiscal_year, _text(row.get("emtak")), version)
        if candidate[1] and (code not in latest_activity or candidate[0] >= latest_activity[code][0]):
            latest_activity[code] = candidate

    # General data (yldandmed) supplies the current share capital; it is optional (see module docstring).
    general_file = next((f for f in files if f.kind == "general"), None)
    capital_by_code: dict[str, tuple[Decimal, str | None]] = {}
    if general_file is not None:
        for record in iter_json_records(general_file):
            code = _text(record.get("ariregistri_kood"))
            if code not in in_scope and code not in known_registry_codes:
                continue
            capital = _current_capital(record)
            if capital is not None:
                capital_by_code[code] = capital
    else:
        run.warnings = [
            *run.warnings,
            {
                "message": "general (yldandmed) dataset file not found on the portal; share_capital was not "
                "imported this run"
            },
        ]

    company_rows: list[dict[str, Any]] = []
    address_parts: dict[str, RegisteredAddressParts] = {}
    for row in iter_rows(basic_file):
        code = _text(row.get("ariregistri_kood"))
        form = _legal_form(row.get("ettevotja_oiguslik_vorm"))
        if (
            not code
            or (code not in in_scope and code not in known_registry_codes)
            or form not in ALLOWED_LEGAL_FORMS
        ):
            continue
        parts = parse_registered_address(row)
        fte = fte_by_code.get(code)
        employees = None if fte is None else _text(fte[1])
        evidence: dict[str, str] = {}
        if fte:
            evidence["employees"] = fte[2].url
        report_candidates = reports_by_code.get(code, [])
        latest_report = max(report_candidates, key=lambda r: (r.fiscal_year, r.report_id), default=None)
        activity = latest_activity.get(code)
        if latest_report and code in latest_activity:
            evidence["industry_code"] = activity_file.url
        capital = capital_by_code.get(code)
        if capital is not None and general_file is not None:
            evidence["share_capital"] = general_file.url
        provenance = {column: row.get(column, "") for column in ADDRESS_SOURCE_COLUMNS}
        provenance.update(
            {
                "file_name": basic_file.name,
                "file_url": basic_file.url,
                "file_last_modified": basic_file.last_modified,
                "legal_form": row.get("ettevotja_oiguslik_vorm", ""),
                "status": row.get("ettevotja_staatus", ""),
            }
        )
        company_rows.append(
            {
                "source_key": f"EE:{code}",
                "source_url": basic_file.url,
                "observed_at": basic_file.published_at.isoformat(),
                "legal_name": row.get("nimi"),
                "registry_id": code,
                "vat_id": row.get("kmkr_nr"),
                "country": "EE",
                "city": parts.city,
                "industry_code": activity[1] if activity else None,
                "fact_metadata": {"industry_code": {"code_system": "EMTAK", "code_version": activity[2]}}
                if activity
                else {},
                "employees": employees,
                "share_capital": str(capital[0]) if capital else None,
                "currency": capital[1] if capital else None,
                "registry_status": row.get("ettevotja_staatus"),
                "warnings": parts.warnings,
                "evidence": evidence,
                "provenance": provenance,
            }
        )
        address_parts[code] = parts
    run.counts = {**run.counts, "discovered": len(company_rows)}
    run.status = "VALIDATED"
    companies = _upsert_companies(session, run, source, basic_file, company_rows, run.counts)
    run.warnings = [
        *run.warnings,
        *(
            {"row": index, "source_key": row["source_key"], "message": warning}
            for index, row in enumerate(company_rows, start=1)
            for warning in row.get("warnings", [])
        ),
    ]

    for code, company in companies.items():
        snapshot = session.scalar(
            select(SourceSnapshot)
            .where(SourceSnapshot.source_id == source.id, SourceSnapshot.source_key == f"company:{code}")
            .order_by(SourceSnapshot.retrieved_at.desc())
        )
        if snapshot is None:
            raise ConnectorError(f"company snapshot missing for Estonia registry code {code}")
        _upsert_address(
            session,
            run,
            source,
            company,
            address_parts[code],
            basic_file,
            run.counts,
            snapshot,
        )

    # Shareholders (osanikud) is another current-state file, like basic data; it is optional (see module
    # docstring). Its raw payload is the already-sanitized holder list, so no personal data beyond a
    # person's name, role and holding ever reaches a source snapshot either.
    shareholders_file = next((f for f in files if f.kind == "shareholders"), None)
    if shareholders_file is not None:
        for record in iter_json_records(shareholders_file):
            holder_code = _text(record.get("ariregistri_kood"))
            holder_company = companies.get(holder_code)
            if holder_company is None:
                continue
            holders = [
                holder
                for holder in (_shareholder_from_record(entry) for entry in record.get("osanikud") or [])
                if holder is not None
            ]
            snapshot = _row_snapshot(
                session,
                run,
                source,
                source_key=f"shareholders:{holder_code}",
                raw={"shareholders": _json_safe(holders)},
                file=shareholders_file,
            )
            _upsert_shareholders(
                session, run, source, holder_company, holders, shareholders_file, run.counts, snapshot
            )
    else:
        run.warnings = [
            *run.warnings,
            {
                "message": "shareholders (osanikud) dataset file not found on the portal; shareholders were "
                "not imported this run"
            },
        ]

    # Pass B: retain only mapped indicator rows for in-scope reports and build one report snapshot per scope.
    grouped: dict[tuple[str, str | None], IndicatorGroup] = {}
    orphan_seen: set[tuple[str, str]] = set()
    for file in indicator_files:
        for line_number, row in enumerate(iter_rows(file), start=1):
            document_id = _text(row.get("report_id"))
            report = _resolve_report(document_id, reports, documents)
            if report is None:
                if file.year == 2024:
                    _record_orphan(session, run, file, document_id, line_number, run.counts, orphan_seen)
                continue
            if report.registry_code not in in_scope:
                continue
            element_name = _text(row.get("elemendi_nimetus"))
            metric = ELEMENT_TO_METRIC.get(element_name)
            if metric is None or _ignored_table(_text(row.get("tabel"))):
                continue
            scope, warning = _statement_scope(report, _text(row.get("tabel")), element_name)
            key = (report.report_id, scope)
            group = grouped.setdefault(key, IndicatorGroup(report, scope, file))
            if warning and warning not in group.warnings:
                group.warnings.append(warning)
            group.elements.append(
                {
                    "metric": metric,
                    "element_name": element_name,
                    "label": _text(row.get("elemendi_label")),
                    "table": _text(row.get("tabel")),
                    "value": _text(row.get("vaartus")),
                    "document_id": document_id,
                    "line_number": line_number,
                }
            )

    for (report_id, scope), group in grouped.items():
        matched_company: Company | None = companies.get(group.report.registry_code)
        if matched_company is None:
            continue
        values, source_values, warnings = _financial_payload(group)
        report_raw = {
            "general_info": group.report.raw,
            "elements": source_values,
            "warnings": warnings,
            "scope": scope,
        }
        snapshot = _row_snapshot(
            session,
            run,
            source,
            source_key=f"report:{report_id}:{scope or 'unknown'}",
            raw=report_raw,
            file=group.source_file,
        )
        _upsert_financial(session, run, source, matched_company, group, snapshot, run.counts)
        if warnings:
            run.warnings = [
                *run.warnings,
                *(
                    {"source_key": f"EE:{report_id}:{scope or 'unknown'}", "message": warning}
                    for warning in warnings
                ),
            ]

    run.counts = {**run.counts, "warnings": len(run.warnings)}
    run.status = "UPSERTED"
    run.finished_at = utcnow()
    audit.record(
        session,
        actor=actor,
        action="ingestion.completed",
        entity_type="ingestion_run",
        entity_id=run.id,
        details={"source_id": source.id, "counts": run.counts, "parser_version": run.parser_version},
    )
    session.commit()
    return run
