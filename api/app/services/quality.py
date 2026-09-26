"""Duplicate candidate detection/review and the data-quality report."""

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.identity import Profile, score_pair
from app.models import (
    Company,
    CompanyFact,
    CompanyIdentifier,
    Contact,
    DuplicateCandidate,
    IngestionRecord,
    IngestionRun,
    SourceSnapshot,
    utcnow,
)
from app.services import audit
from app.services.resolution import link_duplicate, recompute_company


def _profiles(session: Session) -> dict[str, Profile]:
    companies = session.scalars(select(Company).where(Company.merged_into_id.is_(None))).all()
    idents: dict[str, list[CompanyIdentifier]] = defaultdict(list)
    for ident in session.scalars(select(CompanyIdentifier)):
        idents[ident.company_id].append(ident)
    out = {}
    for c in companies:
        ids = idents.get(c.id, [])
        out[c.id] = Profile(
            id=c.id,
            normalized_name=c.normalized_name,
            country=c.country,
            city=c.city,
            registry_keys={i.value for i in ids if i.kind == "registry_id"},
            vat_keys={i.value for i in ids if i.kind == "vat_id" and not i.derived},
            domains={i.value for i in ids if i.kind == "domain"},
            industry_code=c.industry_codes[0] if c.industry_codes else None,
        )
    return out


def detect_duplicates(session: Session, company_ids: set[str] | None = None) -> int:
    """Score pairs within the same country (blocking key) and store possible/likely candidates.
    Deterministic; existing (including dismissed) pairs are never re-opened."""
    profiles = _profiles(session)
    by_country: dict[str, list[Profile]] = defaultdict(list)
    for p in profiles.values():
        by_country[p.country].append(p)
    targets = [profiles[i] for i in sorted(company_ids or profiles.keys()) if i in profiles]
    created = 0
    for a in targets:
        for b in sorted(by_country[a.country], key=lambda p: p.id):
            if a.id == b.id or (company_ids and b.id in company_ids and b.id < a.id):
                continue
            first_tokens = set(a.normalized_name.split()[:1]) & set(b.normalized_name.split()[:1])
            if not (first_tokens or a.domains & b.domains or a.vat_keys & b.vat_keys):
                continue
            result = score_pair(a, b)
            if result.band == "none":
                continue
            if link_duplicate(session, a.id, b.id, result.reasons, result.score, result.band):
                created += 1
    session.flush()
    return created


def merge(
    session: Session, candidate: DuplicateCandidate, survivor_id: str, actor: str, reason: str
) -> Company:
    if candidate.status != "open":
        raise ValueError(f"candidate is already {candidate.status}")
    if survivor_id not in (candidate.company_a_id, candidate.company_b_id):
        raise ValueError("survivor must be one of the candidate companies")
    other_id = candidate.company_b_id if survivor_id == candidate.company_a_id else candidate.company_a_id
    survivor = session.get(Company, survivor_id)
    other = session.get(Company, other_id)
    assert survivor and other
    if survivor.country != other.country:
        raise ValueError("companies in different countries cannot be merged")
    regs = [
        set(
            session.scalars(
                select(CompanyIdentifier.value).where(
                    CompanyIdentifier.company_id == cid,
                    CompanyIdentifier.kind == "registry_id",
                    CompanyIdentifier.derived.is_(False),
                )
            )
        )
        for cid in (survivor.id, other.id)
    ]
    if regs[0] and regs[1] and not regs[0] & regs[1]:
        raise ValueError("different national registry IDs mean different legal entities; link them instead")

    moved: dict[str, list[str]] = {"identifiers": [], "facts": [], "contacts": []}
    for ident in session.scalars(select(CompanyIdentifier).where(CompanyIdentifier.company_id == other.id)):
        ident.company_id = survivor.id
        moved["identifiers"].append(ident.id)
    for fact in session.scalars(select(CompanyFact).where(CompanyFact.company_id == other.id)):
        fact.company_id = survivor.id
        moved["facts"].append(fact.id)
    for contact in session.scalars(select(Contact).where(Contact.company_id == other.id)):
        contact.company_id = survivor.id
        moved["contacts"].append(contact.id)
    other.merged_into_id = survivor.id
    session.flush()
    recompute_company(session, survivor)
    candidate.status = "merged"
    candidate.resolved_by = actor
    candidate.resolved_at = utcnow()
    candidate.resolution_reason = reason
    audit.record(
        session,
        actor=actor,
        action="identity.merged",
        entity_type="company",
        entity_id=survivor.id,
        company_id=survivor.id,
        details={
            "merged_company_id": other.id,
            "candidate_id": candidate.id,
            "reason": reason,
            "match_reasons": candidate.reasons,
            "moved": moved,
        },
    )
    audit.record(
        session,
        actor=actor,
        action="identity.merged_into",
        entity_type="company",
        entity_id=other.id,
        company_id=other.id,
        details={"survivor_id": survivor.id, "candidate_id": candidate.id},
    )
    session.commit()
    return survivor


def resolve_candidate(
    session: Session, candidate: DuplicateCandidate, status: str, actor: str, reason: str
) -> None:
    """status: dismissed (not the same company) | linked (related entities, e.g. same group)."""
    if candidate.status != "open":
        raise ValueError(f"candidate is already {candidate.status}")
    candidate.status = status
    candidate.resolved_by = actor
    candidate.resolved_at = utcnow()
    candidate.resolution_reason = reason
    for cid in (candidate.company_a_id, candidate.company_b_id):
        audit.record(
            session,
            actor=actor,
            action=f"identity.{status}",
            entity_type="duplicate_candidate",
            entity_id=candidate.id,
            company_id=cid,
            details={"reason": reason, "pair": [candidate.company_a_id, candidate.company_b_id]},
        )
    session.commit()


REQUIRED_FIELDS = {
    "registry_id": "registry ID",
    "employees": "headcount",
    "sector": "sector",
    "industry_code": "industry code",
    "website": "website",
    "city": "city",
}


def company_completeness(present_fields: set[str]) -> int:
    return round(100 * len(present_fields & REQUIRED_FIELDS.keys()) / len(REQUIRED_FIELDS))


def active_fact_fields(session: Session) -> dict[str, set[str]]:
    rows = session.execute(
        select(CompanyFact.company_id, CompanyFact.field_name)
        .where(CompanyFact.valid_to.is_(None))
        .distinct()
    ).all()
    out: dict[str, set[str]] = defaultdict(set)
    for cid, field_name in rows:
        out[cid].add(field_name)
    return out


def report(session: Session) -> dict[str, Any]:
    settings = get_settings()
    now = utcnow()
    companies = session.scalars(select(Company).where(Company.merged_into_id.is_(None))).all()
    fields = active_fact_fields(session)
    name = {c.id: c.legal_name for c in companies}

    missing: dict[str, list[dict[str, str]]] = {k: [] for k in REQUIRED_FIELDS}
    for c in companies:
        for f in REQUIRED_FIELDS:
            if f not in fields.get(c.id, set()):
                missing[f].append({"company_id": c.id, "legal_name": c.legal_name})

    stale_cutoff = now - timedelta(days=settings.stale_after_days)
    stale = [
        {
            "company_id": c.id,
            "legal_name": c.legal_name,
            "last_verified_at": c.last_verified_at.isoformat() if c.last_verified_at else None,
        }
        for c in companies
        if c.last_verified_at is None or c.last_verified_at < stale_cutoff
    ]

    conflicts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in session.scalars(
        select(CompanyFact).where(CompanyFact.valid_to.is_(None), CompanyFact.confidence == "conflicting")
    ):
        if fact.company_id in name:
            conflicts[(fact.company_id, fact.field_name)].append(
                {
                    "fact_id": fact.id,
                    "value": fact.value_json,
                    "source_id": fact.source_id,
                    "observed_at": fact.observed_at.isoformat(),
                }
            )
    corrected = {
        (f.company_id, f.field_name)
        for f in session.scalars(
            select(CompanyFact).where(CompanyFact.valid_to.is_(None), CompanyFact.is_correction.is_(True))
        )
    }

    rejected_rows = session.execute(
        select(IngestionRecord, IngestionRun.source_id, IngestionRun.started_at)
        .join(IngestionRun, IngestionRecord.run_id == IngestionRun.id)
        .where(IngestionRecord.outcome == "rejected")
        .order_by(IngestionRun.started_at.desc(), IngestionRecord.row_number)
        .limit(100)
    ).all()
    warning_counter: Counter[str] = Counter()
    for (w,) in session.execute(select(IngestionRecord.warnings)).all():
        for msg in w or []:
            warning_counter[msg.split("'")[0].strip()] += 1

    open_dupes = session.scalar(
        select(func.count()).select_from(DuplicateCandidate).where(DuplicateCandidate.status == "open")
    )
    expired = session.scalar(
        select(func.count()).select_from(SourceSnapshot).where(SourceSnapshot.expired_at.is_not(None))
    )
    due = session.scalar(
        select(func.count())
        .select_from(SourceSnapshot)
        .where(SourceSnapshot.expired_at.is_(None), SourceSnapshot.expires_at <= now)
    )
    completeness = [company_completeness(fields.get(c.id, set())) for c in companies]

    return {
        "generated_at": now.isoformat(),
        "totals": {
            "companies": len(companies),
            "qualification": dict(Counter(c.qualification_status for c in companies)),
            "review_status": dict(Counter(c.review_status for c in companies)),
            "average_completeness": round(sum(completeness) / len(completeness)) if completeness else 0,
            "open_duplicate_candidates": open_dupes,
            "unresolved_conflicts": sum(1 for k in conflicts if k not in corrected),
            "stale_records": len(stale),
            "validation_failures": len(rejected_rows),
            "expired_snapshots": expired,
            "snapshots_due_for_expiry": due,
        },
        "missing_fields": {
            f: {"label": REQUIRED_FIELDS[f], "count": len(rows), "companies": rows[:25]}
            for f, rows in missing.items()
        },
        "stale_records": stale[:50],
        "conflicts": [
            {
                "company_id": cid,
                "legal_name": name[cid],
                "field_name": fname,
                "values": vals,
                "resolved_by_correction": (cid, fname) in corrected,
            }
            for (cid, fname), vals in sorted(conflicts.items(), key=lambda kv: name[kv[0][0]])
        ],
        "validation_failures": [
            {
                "run_id": r.run_id,
                "source_id": src,
                "row": r.row_number,
                "source_key": r.source_key,
                "errors": r.errors,
                "run_started_at": started.isoformat(),
            }
            for r, src, started in rejected_rows
        ],
        "top_warnings": [{"message": m, "count": n} for m, n in warning_counter.most_common(15)],
    }
