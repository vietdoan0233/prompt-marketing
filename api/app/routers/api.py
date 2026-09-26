"""HTTP API. Thin layer: validation + mapping domain errors to status codes."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import String, asc, cast, desc, func, or_, select, text
from sqlalchemy.orm import Session

from app import schemas
from app.config import get_settings
from app.connectors.base import ConnectorError
from app.connectors.csv_connector import decode_csv_bytes
from app.db import get_session
from app.domain.normalize import NORMALIZATION_VERSION
from app.domain.records import PARSER_VERSION
from app.models import (
    AuditEvent,
    Company,
    CompanyFact,
    CompanyFinancial,
    CompanyIdentifier,
    Contact,
    DuplicateCandidate,
    IngestionRun,
    RegisteredAddress,
    Source,
)
from app.services import audit, digital_decay, ingestion, quality, retention
from app.services.contacts import erase_contact
from app.services.corrections import CorrectionError, apply_correction
from app.services.permissions import PermissionDenied, enable_gate, ingestion_gate
from app.views import company_detail, digital_decay_out, duplicate_out, freshness, summaries

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def actor_dep(x_actor: Annotated[str | None, Header()] = None) -> str:
    # Prototype: no SSO yet. The actor is recorded on every audit event; production must derive it from auth.
    actor = (x_actor or get_settings().default_actor).strip()
    return actor[:200]


ActorDep = Annotated[str, Depends(actor_dep)]


def _source_out(source: Source) -> schemas.SourceOut:
    out = schemas.SourceOut.model_validate(source)
    out.gate_reasons = ingestion_gate(source, live=bool((source.connector_config or {}).get("live")))
    out.ingestible = not out.gate_reasons
    return out


def _run_out(run: IngestionRun, detail: bool = False) -> schemas.IngestionRunOut:
    model = schemas.IngestionRunDetail if detail else schemas.IngestionRunOut
    out = model.model_validate(run)
    out.input_retained = run.input_text is not None
    return out


# ------------------------------------------------------------------ health


@router.get("/health")
def health(session: SessionDep) -> dict:
    session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "ok",
        "normalization_version": NORMALIZATION_VERSION,
        "parser_version": PARSER_VERSION,
        "live_connectors_enabled": get_settings().live_connectors_enabled,
    }


# ------------------------------------------------------------------ sources


@router.get("/sources", response_model=list[schemas.SourceOut])
def list_sources(session: SessionDep, region: str | None = None) -> list[schemas.SourceOut]:
    stmt = select(Source).order_by(Source.region, Source.id)
    if region:
        stmt = stmt.where(Source.region == region)
    return [_source_out(s) for s in session.scalars(stmt)]


@router.get("/sources/{source_id}", response_model=schemas.SourceOut)
def get_source(source_id: str, session: SessionDep) -> schemas.SourceOut:
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(404, "source not found")
    return _source_out(source)


@router.post("/sources", response_model=schemas.SourceOut, status_code=201)
def create_source(body: schemas.SourceCreate, session: SessionDep, actor: ActorDep) -> schemas.SourceOut:
    if session.get(Source, body.id):
        raise HTTPException(409, "source id already exists")
    # New sources always start pending + disabled: registering is not approving.
    source = Source(**body.model_dump(), permission_status="pending", enabled=False)
    session.add(source)
    audit.record(
        session,
        actor=actor,
        action="source.registered",
        entity_type="source",
        entity_id=source.id,
        details={"permission_status": "pending", "enabled": False, "source_mode": body.source_mode},
    )
    session.commit()
    return _source_out(source)


@router.post("/sources/{source_id}/permission", response_model=schemas.SourceOut)
def change_permission(source_id: str, body: schemas.PermissionChange, session: SessionDep, actor: ActorDep):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(404, "source not found")
    if source.connector_type == "manual":
        raise HTTPException(409, "the manual-correction source is system-managed")
    if body.permission_status == "approved" and not body.approval_reference:
        raise HTTPException(422, "approving a source requires an approval_reference (legal/licence review)")
    before = source.permission_status
    source.permission_status = body.permission_status
    source.approval_reference = body.approval_reference or source.approval_reference
    if body.permission_status != "approved":
        source.enabled = False
    audit.record(
        session,
        actor=actor,
        action="source.permission_changed",
        entity_type="source",
        entity_id=source.id,
        details={
            "before": before,
            "after": body.permission_status,
            "reason": body.reason,
            "approval_reference": body.approval_reference,
            "enabled": source.enabled,
        },
    )
    session.commit()
    return _source_out(source)


@router.post("/sources/{source_id}/enable", response_model=schemas.SourceOut)
def enable_source(source_id: str, session: SessionDep, actor: ActorDep) -> schemas.SourceOut:
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(404, "source not found")
    reasons = enable_gate(source)
    if reasons:
        audit.record(
            session,
            actor=actor,
            action="source.enable_denied",
            entity_type="source",
            entity_id=source.id,
            details={"reasons": reasons},
        )
        session.commit()
        raise HTTPException(409, {"message": "source cannot be enabled", "reasons": reasons})
    source.enabled = True
    audit.record(session, actor=actor, action="source.enabled", entity_type="source", entity_id=source.id)
    session.commit()
    return _source_out(source)


@router.post("/sources/{source_id}/disable", response_model=schemas.SourceOut)
def disable_source(source_id: str, session: SessionDep, actor: ActorDep) -> schemas.SourceOut:
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(404, "source not found")
    source.enabled = False
    audit.record(session, actor=actor, action="source.disabled", entity_type="source", entity_id=source.id)
    session.commit()
    return _source_out(source)


# ------------------------------------------------------------------ ingestion runs


@router.get("/ingestion-runs", response_model=list[schemas.IngestionRunOut])
def list_runs(
    session: SessionDep,
    source_id: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[schemas.IngestionRunOut]:
    stmt = select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit)
    if source_id:
        stmt = stmt.where(IngestionRun.source_id == source_id)
    if status:
        stmt = stmt.where(IngestionRun.status == status)
    return [_run_out(r) for r in session.scalars(stmt)]


@router.post("/ingestion-runs", response_model=schemas.IngestionRunDetail, status_code=201)
async def create_run(request: Request, session: SessionDep, actor: ActorDep) -> schemas.IngestionRunOut:
    """multipart/form-data (source_id, file, min_employees?) imports a CSV;
    application/json {source_id, query, min_employees?} runs registry discovery."""
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            source_id = str(form.get("source_id") or "")
            upload = form.get("file")
            if not source_id or upload is None or isinstance(upload, str):
                raise HTTPException(422, "multipart import needs 'source_id' and a 'file'")
            data = await upload.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "CSV exceeds 10 MB limit")
            min_emp_raw = form.get("min_employees")
            min_emp = int(str(min_emp_raw)) if min_emp_raw not in (None, "") else None
            run = ingestion.start_csv_run(
                session,
                source_id=source_id,
                file_name=upload.filename or "upload.csv",
                text=decode_csv_bytes(data),
                actor=actor,
                min_employees=min_emp,
            )
        else:
            try:
                body = schemas.DiscoveryRunCreate.model_validate(json.loads(await request.body() or b"{}"))
            except (ValidationError, json.JSONDecodeError) as exc:
                raise HTTPException(422, f"invalid discovery request: {exc}") from exc
            source = session.get(Source, body.source_id)
            if source is not None and source.connector_type == "csv":
                raise HTTPException(
                    422, "this source imports CSV files; upload a file with multipart/form-data"
                )
            run = ingestion.start_discovery_run(
                session,
                source_id=body.source_id,
                query=body.query,
                actor=actor,
                min_employees=body.min_employees,
            )
    except PermissionDenied as exc:
        raise HTTPException(
            403, {"message": "rejected by permission gate", "reasons": exc.reasons, "run_id": exc.run_id}
        ) from exc
    except ConnectorError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _run_out(run, detail=True)


@router.get("/ingestion-runs/{run_id}", response_model=schemas.IngestionRunDetail)
def get_run(run_id: str, session: SessionDep) -> schemas.IngestionRunOut:
    run = session.get(IngestionRun, run_id)
    if not run:
        raise HTTPException(404, "ingestion run not found")
    return _run_out(run, detail=True)


@router.post("/ingestion-runs/{run_id}/retry", response_model=schemas.IngestionRunDetail, status_code=201)
def retry(run_id: str, session: SessionDep, actor: ActorDep) -> schemas.IngestionRunOut:
    try:
        run = ingestion.retry_run(session, run_id, actor)
    except LookupError as exc:
        raise HTTPException(404, "ingestion run not found") from exc
    except PermissionDenied as exc:
        raise HTTPException(
            403, {"message": "rejected by permission gate", "reasons": exc.reasons, "run_id": exc.run_id}
        ) from exc
    except ConnectorError as exc:
        session.rollback()
        raise HTTPException(409, str(exc)) from exc
    return _run_out(run, detail=True)


# ------------------------------------------------------------------ companies

SORTS = {
    "legal_name": Company.legal_name,
    "country": Company.country,
    "employees": Company.estimated_employee_min,
    "last_verified_at": Company.last_verified_at,
    "sector": Company.sector,
    "qualification": Company.qualification_status,
}


@router.get("/companies", response_model=schemas.CompanyPage)
def list_companies(
    session: SessionDep,
    country: str | None = Query(None, description="Comma-separated ISO codes, e.g. FI,SE"),
    region: str | None = None,
    sector: str | None = Query(None, description="Case-insensitive substring of sector or industry code"),
    min_employees: int = Query(
        20, ge=0, description="Lower headcount bound must be >= this. 0 = include all"
    ),
    max_employees: int | None = Query(None, ge=0),
    review_status: str | None = None,
    qualification: str | None = None,
    freshness_filter: str | None = Query(None, alias="freshness"),
    source_id: str | None = None,
    q: str | None = None,
    sort: str = "legal_name",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> schemas.CompanyPage:
    stmt = select(Company).where(Company.merged_into_id.is_(None))
    if country:
        stmt = stmt.where(Company.country.in_([c.strip().upper() for c in country.split(",") if c.strip()]))
    if region:
        stmt = stmt.where(Company.region == region)
    if sector:
        like = f"%{sector.lower()}%"
        stmt = stmt.where(
            or_(func.lower(Company.sector).like(like), cast(Company.industry_codes, String).like(like))
        )
    if min_employees > 0:
        stmt = stmt.where(Company.estimated_employee_min >= min_employees)
    if max_employees is not None:
        stmt = stmt.where(Company.estimated_employee_min <= max_employees)
    if review_status:
        stmt = stmt.where(Company.review_status == review_status)
    if qualification:
        stmt = stmt.where(Company.qualification_status.in_(qualification.split(",")))
    if q:
        stmt = stmt.where(
            or_(
                func.lower(Company.legal_name).like(f"%{q.lower()}%"),
                func.lower(Company.trading_name).like(f"%{q.lower()}%"),
            )
        )
    if source_id:
        stmt = stmt.where(
            Company.id.in_(select(CompanyFact.company_id).where(CompanyFact.source_id == source_id))
        )
    column = SORTS.get(sort, Company.legal_name)
    direction = desc if order == "desc" else asc
    rows = list(session.scalars(stmt.order_by(direction(column).nulls_last(), Company.legal_name)))
    if freshness_filter:
        rows = [c for c in rows if freshness(c.last_verified_at) == freshness_filter]
    total = len(rows)
    page_rows = rows[(page - 1) * page_size : page * page_size]
    items = summaries(session, page_rows)
    if sort == "completeness":
        items.sort(key=lambda i: i.completeness, reverse=order == "desc")
    return schemas.CompanyPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        filters={
            "country": country,
            "region": region,
            "sector": sector,
            "min_employees": min_employees,
            "max_employees": max_employees,
            "review_status": review_status,
            "qualification": qualification,
            "freshness": freshness_filter,
            "source_id": source_id,
            "q": q,
            "sort": sort,
            "order": order,
        },
    )


def _company_or_404(session: Session, company_id: str) -> Company:
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(404, "company not found")
    return company


@router.get("/companies/{company_id}", response_model=schemas.CompanyDetail)
def get_company(company_id: str, session: SessionDep) -> schemas.CompanyDetail:
    return company_detail(session, _company_or_404(session, company_id))


@router.get("/companies/{company_id}/financials", response_model=list[schemas.FinancialOut])
def company_financials(company_id: str, session: SessionDep) -> list[schemas.FinancialOut]:
    _company_or_404(session, company_id)
    source_names = {s.id: s.name for s in session.scalars(select(Source))}
    rows = session.scalars(
        select(CompanyFinancial)
        .where(CompanyFinancial.company_id == company_id, CompanyFinancial.review_status != "superseded")
        .order_by(CompanyFinancial.fiscal_year.desc(), CompanyFinancial.statement_scope.nulls_last())
    ).all()
    out: list[schemas.FinancialOut] = []
    for row in rows:
        item = schemas.FinancialOut.model_validate(row)
        item.source_name = source_names.get(row.source_id)
        out.append(item)
    return out


@router.get("/companies/{company_id}/registered-address", response_model=schemas.RegisteredAddressOut | None)
def company_registered_address(company_id: str, session: SessionDep) -> schemas.RegisteredAddressOut | None:
    _company_or_404(session, company_id)
    row = session.scalar(
        select(RegisteredAddress)
        .where(RegisteredAddress.company_id == company_id, RegisteredAddress.valid_to.is_(None))
        .order_by(RegisteredAddress.observed_at.desc())
    )
    if row is None:
        return None
    item = schemas.RegisteredAddressOut.model_validate(row)
    source = session.get(Source, row.source_id)
    item.source_name = source.name if source else None
    return item


@router.post(
    "/companies/{company_id}/signals/digital-decay",
    response_model=schemas.DigitalDecayRunOut,
    status_code=201,
)
def run_digital_decay(
    company_id: str,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.DigitalDecayRunCreate | None = None,
) -> schemas.DigitalDecayRunOut:
    """Opt-in website activity check for one registry-backed company (estimated, provenance-linked facts)."""
    company = _company_or_404(session, company_id)
    domains = {company.id: body.domain} if body and body.domain else None
    try:
        run = digital_decay.run_decay(session, [company], actor=actor, domains=domains)
    except PermissionDenied as exc:
        raise HTTPException(
            403, {"message": "rejected by permission gate", "reasons": exc.reasons, "run_id": exc.run_id}
        ) from exc
    except ConnectorError as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from exc
    source_names = {s.id: s.name for s in session.scalars(select(Source))}
    decay = digital_decay_out(session, company.id, source_names)
    run_detail = _run_out(run, detail=True)
    assert isinstance(run_detail, schemas.IngestionRunDetail)
    return schemas.DigitalDecayRunOut(
        run_id=run.id,
        status=run.status,
        # A failed fetch leaves an older signal active; only report it when this run produced evidence.
        signal=decay.signal if decay and not run.errors else None,
        run=run_detail,
        digital_decay=decay,
    )


@router.post(
    "/companies/{company_id}/facts/corrections", response_model=schemas.CompanyDetail, status_code=201
)
def correct_fact(company_id: str, body: schemas.CorrectionCreate, session: SessionDep, actor: ActorDep):
    company = _company_or_404(session, company_id)
    if company.merged_into_id:
        raise HTTPException(
            409, f"company was merged into {company.merged_into_id}; correct the surviving record"
        )
    try:
        apply_correction(
            session,
            company,
            field_name=body.field_name,
            value=body.value,
            reason=body.reason,
            actor=actor,
            corrects_fact_id=body.corrects_fact_id,
            evidence_url=body.evidence_url,
        )
    except CorrectionError as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from exc
    return company_detail(session, company)


@router.post("/companies/{company_id}/review", response_model=schemas.CompanyDetail)
def review_company(company_id: str, body: schemas.ReviewUpdate, session: SessionDep, actor: ActorDep):
    company = _company_or_404(session, company_id)
    before = company.review_status
    company.review_status = body.review_status
    audit.record(
        session,
        actor=actor,
        action="company.review_status_changed",
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        details={"before": before, "after": body.review_status, "note": body.note},
    )
    session.commit()
    return company_detail(session, company)


@router.get("/companies/{company_id}/audit-events", response_model=list[schemas.AuditEventOut])
def company_audit(company_id: str, session: SessionDep) -> list[AuditEvent]:
    _company_or_404(session, company_id)
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.company_id == company_id)
            .order_by(AuditEvent.occurred_at.desc())
        )
    )


# ------------------------------------------------------------------ contacts (personal data)


@router.delete("/contacts/{contact_id}")
def delete_contact(
    contact_id: str,
    session: SessionDep,
    actor: ActorDep,
    request_reference: str | None = Query(None, max_length=200),
    reason: str = Query("GDPR erasure request", max_length=1000),
) -> dict:
    contact = session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "contact not found (it may already have been erased)")
    return erase_contact(session, contact, actor=actor, request_reference=request_reference, reason=reason)


# ------------------------------------------------------------------ audit


@router.get("/audit-events", response_model=list[schemas.AuditEventOut])
def list_audit(
    session: SessionDep,
    company_id: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[AuditEvent]:
    stmt = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).offset(offset).limit(limit)
    if company_id:
        stmt = stmt.where(AuditEvent.company_id == company_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    return list(session.scalars(stmt))


# ------------------------------------------------------------------ data quality & retention


@router.get("/quality/report")
def quality_report(session: SessionDep) -> dict:
    return quality.report(session)


@router.get("/quality/duplicates", response_model=list[schemas.DuplicateOut])
def list_duplicates(session: SessionDep, status: str | None = "open") -> list[schemas.DuplicateOut]:
    stmt = select(DuplicateCandidate).order_by(DuplicateCandidate.score.desc(), DuplicateCandidate.created_at)
    if status and status != "all":
        stmt = stmt.where(DuplicateCandidate.status == status)
    return [duplicate_out(session, d) for d in session.scalars(stmt)]


@router.post("/quality/duplicates/detect")
def detect(session: SessionDep, actor: ActorDep) -> dict:
    created = quality.detect_duplicates(session)
    audit.record(
        session,
        actor=actor,
        action="identity.duplicate_scan",
        entity_type="duplicate_candidate",
        details={"created": created},
    )
    session.commit()
    return {"created": created}


def _candidate_or_404(session: Session, candidate_id: str) -> DuplicateCandidate:
    cand = session.get(DuplicateCandidate, candidate_id)
    if not cand:
        raise HTTPException(404, "duplicate candidate not found")
    return cand


@router.post("/quality/duplicates/{candidate_id}/merge", response_model=schemas.DuplicateOut)
def merge_duplicate(candidate_id: str, body: schemas.DuplicateMerge, session: SessionDep, actor: ActorDep):
    cand = _candidate_or_404(session, candidate_id)
    try:
        quality.merge(session, cand, body.survivor_id, actor, body.reason)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(409, str(exc)) from exc
    return duplicate_out(session, cand)


@router.post("/quality/duplicates/{candidate_id}/dismiss", response_model=schemas.DuplicateOut)
def dismiss_duplicate(
    candidate_id: str, body: schemas.DuplicateResolve, session: SessionDep, actor: ActorDep
):
    cand = _candidate_or_404(session, candidate_id)
    try:
        quality.resolve_candidate(session, cand, "dismissed", actor, body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return duplicate_out(session, cand)


@router.post("/quality/duplicates/{candidate_id}/link", response_model=schemas.DuplicateOut)
def link_duplicate(candidate_id: str, body: schemas.DuplicateResolve, session: SessionDep, actor: ActorDep):
    cand = _candidate_or_404(session, candidate_id)
    try:
        quality.resolve_candidate(session, cand, "linked", actor, body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return duplicate_out(session, cand)


@router.post("/retention/expire")
def expire(session: SessionDep, actor: ActorDep) -> dict:
    return retention.expire_raw_data(session, actor=actor)


@router.get("/identifiers/lookup")
def lookup_identifier(session: SessionDep, kind: str, value: str) -> dict:
    ident = session.scalar(
        select(CompanyIdentifier).where(CompanyIdentifier.kind == kind, CompanyIdentifier.value == value)
    )
    if not ident:
        raise HTTPException(404, "identifier not found")
    return {"company_id": ident.company_id, "kind": ident.kind, "value": ident.value}
