"""Manual fact corrections. The original fact, its value and its provenance are never modified."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import normalize as n
from app.domain.records import stable_hash
from app.models import Company, CompanyFact, utcnow
from app.services import audit
from app.services.resolution import recompute_company

MANUAL_SOURCE_ID = "mergero-manual"
CORRECTABLE_FIELDS = {
    "legal_name",
    "trading_name",
    "city",
    "website",
    "industry_code",
    "sector",
    "employees",
    "revenue",
    "ownership_type",
    "description",
}


class CorrectionError(ValueError):
    pass


def normalize_correction(field_name: str, value: Any) -> Any:
    if field_name not in CORRECTABLE_FIELDS:
        raise CorrectionError(f"field '{field_name}' cannot be corrected manually")
    if value is None or (isinstance(value, str) and not value.strip()):
        raise CorrectionError(
            "a correction needs a value; to flag a wrong value without a replacement, set review status"
        )
    if field_name == "employees":
        if isinstance(value, dict):
            lo, hi = value.get("min"), value.get("max")
            rng = (int(lo), int(hi) if hi is not None else None) if lo is not None else None
        else:
            rng = n.parse_employee_range(value)
        if rng is None or (rng[1] is not None and rng[0] > rng[1]):
            raise CorrectionError(f"invalid employee count/range: {value!r}")
        return {"min": rng[0], "max": rng[1]}
    if field_name == "revenue":
        if not isinstance(value, dict) or not value.get("currency") or value.get("min") is None:
            raise CorrectionError("revenue corrections need {min, max, currency}")
        return {
            "min": int(value["min"]),
            "max": int(value.get("max") or value["min"]),
            "currency": str(value["currency"]).upper(),
        }
    if field_name == "website":
        website = n.normalize_website(str(value))
        if not website:
            raise CorrectionError(f"invalid website: {value!r}")
        return website
    if field_name == "ownership_type":
        own = n.normalize_ownership(str(value))
        if not own:
            raise CorrectionError(f"ownership_type must be one of {sorted(n.OWNERSHIP_TYPES)}")
        return own
    return " ".join(str(value).split())


def apply_correction(
    session: Session,
    company: Company,
    *,
    field_name: str,
    value: Any,
    reason: str,
    actor: str,
    corrects_fact_id: str | None = None,
    evidence_url: str | None = None,
) -> CompanyFact:
    if not reason or not reason.strip():
        raise CorrectionError("a correction reason is required")
    normalized = normalize_correction(field_name, value)
    target = None
    if corrects_fact_id:
        target = session.get(CompanyFact, corrects_fact_id)
        if target is None or target.company_id != company.id or target.field_name != field_name:
            raise CorrectionError("corrects_fact_id does not reference a fact of this company and field")

    before = recompute_company(session, company).get(field_name)
    now = utcnow()
    for prev in session.scalars(
        select(CompanyFact).where(
            CompanyFact.company_id == company.id,
            CompanyFact.field_name == field_name,
            CompanyFact.is_correction.is_(True),
            CompanyFact.valid_to.is_(None),
        )
    ):
        prev.valid_to = now
        prev.review_status = "superseded"

    fact = CompanyFact(
        company_id=company.id,
        field_name=field_name,
        value_json=normalized,
        original_value=value if isinstance(value, str) else None,
        value_hash=stable_hash(normalized),
        source_id=MANUAL_SOURCE_ID,
        source_url=evidence_url,
        observed_at=now,
        valid_from=now,
        base_confidence="verified",
        confidence="manually-corrected",
        usage_policy="internal-only",
        review_status="accepted",
        reviewed_by=actor,
        reviewed_at=now,
        is_correction=True,
        corrects_fact_id=target.id if target else None,
        correction_reason=reason.strip(),
    )
    session.add(fact)
    # Source facts keep their value and provenance; only their review status records the correction.
    for f in session.scalars(
        select(CompanyFact).where(
            CompanyFact.company_id == company.id,
            CompanyFact.field_name == field_name,
            CompanyFact.is_correction.is_(False),
            CompanyFact.valid_to.is_(None),
        )
    ):
        if target is None or f.id == target.id:
            f.review_status = "corrected"
            f.reviewed_by, f.reviewed_at = actor, now
    session.flush()
    recompute_company(session, company)
    audit.record(
        session,
        actor=actor,
        action="fact.corrected",
        entity_type="company_fact",
        entity_id=fact.id,
        company_id=company.id,
        details={
            "field_name": field_name,
            "before": before.value if before else None,
            "before_status": before.status if before else "unknown",
            "after": normalized,
            "reason": reason.strip(),
            "corrects_fact_id": fact.corrects_fact_id,
            "evidence_url": evidence_url,
        },
    )
    session.commit()
    return fact
