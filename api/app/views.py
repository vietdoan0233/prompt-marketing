"""Read-side assembly: company summaries with provenance/freshness badges and the company detail view."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import schemas
from app.config import get_settings
from app.domain.confidence import MANUALLY_CORRECTED
from app.models import (
    AuditEvent,
    Company,
    CompanyFact,
    CompanyFinancial,
    CompanyIdentifier,
    CompanyShareholder,
    Contact,
    DuplicateCandidate,
    RegisteredAddress,
    Source,
    utcnow,
)
from app.services.digital_decay import latest_signal
from app.services.quality import company_completeness
from app.services.resolution import recompute_company

STATUS_LABEL = {
    "verified": "source-verified",
    "multi-source": "multi-source",
    "estimated": "estimated",
    "old": "old",
    "conflicting": "conflicting",
    "unknown": "unknown",
    MANUALLY_CORRECTED: "manually-corrected",
}
FIELD_ORDER = [
    "legal_name",
    "registry_status",
    "trading_name",
    "registry_id",
    "vat_id",
    "website",
    "city",
    "industry_code",
    "sector",
    "employees",
    "revenue",
    "ownership_type",
    "description",
    "share_capital",
    "open_positions",
    "footer_copyright_year",
    "latest_news_date",
    "founder_signal",
    "family_business_signal",
]


def freshness(last_verified_at: datetime | None, now: datetime | None = None) -> str:
    if last_verified_at is None:
        return "unknown"
    s = get_settings()
    age = (now or utcnow()) - last_verified_at
    if age > timedelta(days=s.stale_after_days):
        return "stale"
    if age > timedelta(days=s.aging_after_days):
        return "aging"
    return "fresh"


def summaries(session: Session, companies: list[Company]) -> list[schemas.CompanySummary]:
    ids = [c.id for c in companies]
    facts_by_company: dict[str, list[CompanyFact]] = defaultdict(list)
    regs: dict[str, str] = {}
    if ids:
        for f in session.scalars(
            select(CompanyFact).where(CompanyFact.company_id.in_(ids), CompanyFact.valid_to.is_(None))
        ):
            facts_by_company[f.company_id].append(f)
        for ident in session.scalars(
            select(CompanyIdentifier).where(
                CompanyIdentifier.company_id.in_(ids),
                CompanyIdentifier.kind == "registry_id",
                CompanyIdentifier.derived.is_(False),
            )
        ):
            regs.setdefault(ident.company_id, ident.value)
    now = utcnow()
    out = []
    for c in companies:
        facts = facts_by_company.get(c.id, [])
        sources = sorted({f.source_id for f in facts if not f.is_correction})
        fields_by_conf: dict[str, set[str]] = defaultdict(set)
        for f in facts:
            fields_by_conf[f.confidence].add(f.field_name)
        corrected = {f.field_name for f in facts if f.is_correction}
        emp_facts = [f for f in facts if f.field_name == "employees"]
        if not emp_facts:
            headcount_status = "unknown"
        elif "employees" in corrected:
            headcount_status = MANUALLY_CORRECTED
        else:
            confs = {f.confidence for f in emp_facts}
            headcount_status = next(
                (s for s in ("conflicting", "multi-source", "verified", "estimated", "old") if s in confs),
                "unknown",
            )
        summary = schemas.CompanySummary.model_validate(c)
        code_details = {
            (str(f.value_json), f.code_system, f.code_version)
            for f in facts
            if f.field_name == "industry_code" and not f.is_correction and f.value_json is not None
        }
        summary.industry_code_details = [
            schemas.IndustryCodeOut(code=code, code_system=system, code_version=version)
            for code, system, version in sorted(
                code_details, key=lambda item: (item[0], item[1] or "", item[2] or "")
            )
        ]
        summary.freshness = freshness(c.last_verified_at, now)
        summary.source_ids = sources
        summary.source_count = len(sources)
        summary.multi_source_fields = len(fields_by_conf.get("multi-source", set()))
        summary.conflict_fields = len(fields_by_conf.get("conflicting", set()) - corrected)
        summary.completeness = company_completeness({f.field_name for f in facts})
        summary.headcount_status = headcount_status
        summary.registry_id = regs.get(c.id)
        latest = {f.field_name: f.value_json for f in facts if not f.is_correction}
        summary.registry_status = latest.get("registry_status")
        summary.open_positions = latest.get("open_positions")
        summary.founder_signal = bool(latest.get("founder_signal"))
        summary.family_business_signal = bool(latest.get("family_business_signal"))
        summary.website_enriched = any(f.source_id.startswith("web-") for f in facts)
        out.append(summary)
    return out


def duplicate_out(session: Session, d: DuplicateCandidate) -> schemas.DuplicateOut:
    item = schemas.DuplicateOut.model_validate(d)
    a, b = session.get(Company, d.company_a_id), session.get(Company, d.company_b_id)
    item.company_a_name = a.legal_name if a else None
    item.company_b_name = b.legal_name if b else None
    return item


def digital_decay_out(
    session: Session, company_id: str, source_names: dict[str, str]
) -> schemas.DigitalDecayOut | None:
    fact = latest_signal(session, company_id)
    if fact is None or not isinstance(fact.value_json, dict):
        return None
    domain_identifier = session.scalar(
        select(CompanyIdentifier.value)
        .where(CompanyIdentifier.company_id == company_id, CompanyIdentifier.kind == "domain")
        .order_by(CompanyIdentifier.created_at, CompanyIdentifier.id)
    )
    return schemas.DigitalDecayOut(
        signal=fact.value_json,
        fact_id=fact.id,
        source_id=fact.source_id,
        source_name=source_names.get(fact.source_id),
        source_url=fact.source_url,
        ingestion_run_id=fact.ingestion_run_id,
        snapshot_id=fact.snapshot_id,
        observed_at=fact.observed_at,
        confidence=fact.confidence,
        review_status=fact.review_status,
        domain_identifier=domain_identifier,
    )


def decay_warning(decay: schemas.DigitalDecayOut | None) -> str | None:
    if decay is None:
        return None
    s = decay.signal
    review = "unreviewed" if decay.review_status == "unreviewed" else "reviewed"
    stale = s.get("stale_count")
    if s.get("verdict") == "coasting":
        min_rev = get_settings().decay_min_revenue_eur
        return (
            f"digital decay: coasting — {stale} of 3 website activity checks stale and no open roles "
            f"despite reported revenue ≥ €{min_rev / 1e6:.1f}M (estimated website signal, {review})"
        )
    if s.get("verdict") == "watch":
        min_rev = get_settings().decay_min_revenue_eur
        headcount = (s.get("checks") or {}).get("headcount") or {}
        pct = headcount.get("change_pct")
        change = f"{pct:+.1f}%" if isinstance(pct, int | float) else "change unknown"
        return (
            f"digital decay: watch — zero open roles, register headcount "
            f"{headcount.get('state') or 'flat/shrinking'} ({change}), revenue ≥ €{min_rev / 1e6:.1f}M "
            f"(estimated website signal, {review})"
        )
    if s.get("verdict") == "decaying":
        return (
            f"digital decay: {stale} of 3 website activity checks stale (estimated website signal, {review})"
        )
    return None


def company_detail(session: Session, company: Company) -> schemas.CompanyDetail:
    resolutions = recompute_company(session, company)
    session.commit()
    source_names = {s.id: s.name for s in session.scalars(select(Source))}
    all_facts = session.scalars(
        select(CompanyFact)
        .where(CompanyFact.company_id == company.id)
        .order_by(CompanyFact.observed_at.desc())
    ).all()

    def fact_out(f: CompanyFact) -> schemas.FactOut:
        item = schemas.FactOut.model_validate(f)
        item.source_name = source_names.get(f.source_id)
        return item

    active = [fact_out(f) for f in all_facts if f.valid_to is None]
    history = [fact_out(f) for f in all_facts if f.valid_to is not None]

    fields: list[schemas.FieldView] = []
    for name in FIELD_ORDER:
        res = resolutions.get(name)
        if res is None:
            fields.append(
                schemas.FieldView(
                    field_name=name,
                    value=None,
                    status="unknown",
                    label="unknown",
                    source_ids=[],
                    supporting_fact_ids=[],
                    conflicting_values=[],
                )
            )
            continue
        support = [f for f in all_facts if f.id in res.supporting_fact_ids]
        fields.append(
            schemas.FieldView(
                field_name=name,
                value=res.value,
                status=res.status,
                label=STATUS_LABEL.get(res.status, res.status),
                source_ids=sorted({f.source_id for f in support}),
                supporting_fact_ids=res.supporting_fact_ids,
                conflicting_values=res.conflicting_values,
            )
        )

    identifiers = session.scalars(
        select(CompanyIdentifier)
        .where(CompanyIdentifier.company_id == company.id)
        .order_by(CompanyIdentifier.kind)
    ).all()
    contacts = session.scalars(
        select(Contact).where(Contact.company_id == company.id).order_by(Contact.name)
    ).all()
    financial_rows = session.scalars(
        select(CompanyFinancial)
        .where(
            CompanyFinancial.company_id == company.id,
            CompanyFinancial.review_status != "superseded",
        )
        .order_by(CompanyFinancial.fiscal_year.desc(), CompanyFinancial.statement_scope.nulls_last())
    ).all()
    address = session.scalar(
        select(RegisteredAddress)
        .where(RegisteredAddress.company_id == company.id, RegisteredAddress.valid_to.is_(None))
        .order_by(RegisteredAddress.observed_at.desc())
    )
    shareholder_rows = session.scalars(
        select(CompanyShareholder)
        .where(CompanyShareholder.company_id == company.id, CompanyShareholder.valid_to.is_(None))
        .order_by(CompanyShareholder.holding_percent.desc().nulls_last(), CompanyShareholder.holder_name)
    ).all()
    dupes = session.scalars(
        select(DuplicateCandidate)
        .where(
            or_(DuplicateCandidate.company_a_id == company.id, DuplicateCandidate.company_b_id == company.id)
        )
        .order_by(DuplicateCandidate.score.desc())
    ).all()
    audits = session.scalars(
        select(AuditEvent).where(AuditEvent.company_id == company.id).order_by(AuditEvent.occurred_at.desc())
    ).all()

    timeline: list[schemas.TimelineEntry] = []
    for f in all_facts:
        timeline.append(
            schemas.TimelineEntry(
                at=f.observed_at if not f.is_correction else f.created_at,
                kind="correction" if f.is_correction else "fact",
                title=(f"{f.field_name} corrected" if f.is_correction else f"{f.field_name} observed")
                + (" (superseded)" if f.valid_to else ""),
                source_id=f.source_id,
                detail={
                    "value": f.value_json,
                    "confidence": f.confidence,
                    "source_url": f.source_url,
                    "reason": f.correction_reason,
                },
            )
        )
    for a in audits:
        timeline.append(
            schemas.TimelineEntry(at=a.occurred_at, kind="audit", title=a.action, detail=a.details)
        )
    timeline.sort(key=lambda t: t.at, reverse=True)

    summary = summaries(session, [company])[0]
    warnings: list[str] = []
    for fv in fields:
        if fv.status == "conflicting":
            warnings.append(
                f"{fv.field_name}: sources disagree ({len(fv.conflicting_values) + 1} values); review needed"
            )
        if fv.status == "old":
            warnings.append(
                f"{fv.field_name}: only evidence is older than {get_settings().stale_after_days} days"
            )
    if summary.qualification_status == "sub_scale":
        warnings.append("sub-scale micro-entity (1-2 employees): low priority")
    if summary.qualification_status == "unknown_headcount":
        warnings.append("no approved headcount evidence: qualification unknown (not guessed)")
    if summary.qualification_status == "borderline":
        warnings.append("headcount range straddles the 20-employee threshold")
    if summary.freshness == "stale":
        warnings.append("record is stale: no source observation within the freshness window")
    if not summary.registry_id:
        warnings.append("no validated national registry ID")
    open_dupes = [d for d in dupes if d.status == "open"]
    if open_dupes:
        warnings.append(f"{len(open_dupes)} open duplicate candidate(s) awaiting review")
    decay = digital_decay_out(session, company.id, source_names)
    decay_note = decay_warning(decay)
    if decay_note:
        warnings.append(decay_note)

    revenue = resolutions.get("revenue")
    financial_out = []
    for financial in financial_rows:
        item = schemas.FinancialOut.model_validate(financial)
        item.source_name = source_names.get(financial.source_id)
        financial_out.append(item)
    address_out = None
    if address:
        address_out = schemas.RegisteredAddressOut.model_validate(address)
        address_out.source_name = source_names.get(address.source_id)
    shareholder_out = []
    for row in shareholder_rows:
        shareholder_item = schemas.ShareholderOut.model_validate(row)
        shareholder_item.source_name = source_names.get(row.source_id)
        shareholder_out.append(shareholder_item)
    return schemas.CompanyDetail(
        company=summary,
        description=company.description,
        revenue=revenue.value if revenue else None,
        merged_into_id=company.merged_into_id,
        fields=fields,
        facts=active,
        history=history,
        identifiers=[schemas.IdentifierOut.model_validate(i) for i in identifiers],
        financials=financial_out,
        registered_address=address_out,
        shareholders=shareholder_out,
        contacts=[schemas.ContactOut.model_validate(c) for c in contacts],
        duplicates=[duplicate_out(session, d) for d in dupes],
        audit_events=[schemas.AuditEventOut.model_validate(a) for a in audits],
        timeline=timeline,
        warnings=warnings,
        digital_decay=decay,
    )
