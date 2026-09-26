"""Multi-source entity resolution and consolidated company profile computation.

Registry IDs, VAT IDs and website domains from every approved source are resolved into one company
profile using exact, stable keys. Anything ambiguous becomes a duplicate candidate for human review;
nothing is merged silently.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain import confidence as conf
from app.domain.normalize import normalize_name, registry_from_vat
from app.domain.qualification import qualify
from app.domain.records import WEBSITE_SIGNAL_FIELDS, ParsedRecord, stable_hash
from app.models import Company, CompanyFact, CompanyIdentifier, DuplicateCandidate, Source, utcnow

KEY_PRIORITY = {"source_key": 0, "registry_id": 1, "vat_id": 2, "domain": 3}


@dataclass
class IdentityKeys:
    keys: list[tuple[str, str, bool]]  # (kind, value, derived)

    def lookup_pairs(self) -> list[tuple[str, str]]:
        return [(k, v) for k, v, _ in self.keys]


@dataclass
class Resolution:
    company: Company | None
    match_reasons: list[str] = field(default_factory=list)
    duplicate_links: list[tuple[str, list[dict[str, Any]]]] = field(default_factory=list)
    blocked_keys: set[tuple[str, str]] = field(default_factory=set)


def identity_keys(rec: ParsedRecord, source: Source) -> IdentityKeys:
    keys: list[tuple[str, str, bool]] = [("source_key", f"{source.id}|{rec.source_key}", False)]
    if rec.registry_key:
        keys.append(("registry_id", rec.registry_key, False))
    if rec.vat_key:
        keys.append(("vat_id", rec.vat_key, False))
        if rec.country and not rec.registry_key:
            reg = registry_from_vat(rec.country, rec.vat_key)
            if reg:
                keys.append(("registry_id", reg, True))
    if rec.derived_vat_key and rec.derived_vat_key != rec.vat_key:
        keys.append(("vat_id", rec.derived_vat_key, True))
    if rec.domain:
        # A domain found by an enrichment (website) source is a derived key; register-published ones are not.
        keys.append(("domain", rec.domain, rec.enrichment_only))
    return IdentityKeys(keys)


def _canonical(session: Session, company: Company) -> Company:
    seen = set()
    while company.merged_into_id and company.id not in seen:
        seen.add(company.id)
        company = session.get(Company, company.merged_into_id)  # type: ignore[assignment]
    return company


def _registry_keys(session: Session, company_id: str) -> set[str]:
    rows = session.scalars(
        select(CompanyIdentifier.value).where(
            CompanyIdentifier.company_id == company_id, CompanyIdentifier.kind == "registry_id"
        )
    )
    return set(rows)


def resolve(session: Session, rec: ParsedRecord, source: Source) -> tuple[Resolution, IdentityKeys]:
    keys = identity_keys(rec, source)
    rows = (
        session.execute(
            select(CompanyIdentifier).where(
                tuple_(CompanyIdentifier.kind, CompanyIdentifier.value).in_(keys.lookup_pairs())
            )
        )
        .scalars()
        .all()
    )
    matches: list[tuple[int, CompanyIdentifier, Company]] = []
    for ident in rows:
        matches.append((KEY_PRIORITY[ident.kind], ident, _canonical(session, ident.company)))
    matches.sort(key=lambda m: m[0])

    res = Resolution(company=None)
    for _, ident, company in matches:
        label = f"{ident.kind} {ident.value}" + (" (derived)" if ident.derived else "")
        if rec.registry_key:
            other_regs = {
                registry
                for registry in _registry_keys(session, company.id)
                if registry.startswith(f"{rec.country}:")
            }
            if other_regs and rec.registry_key not in other_regs:
                # A VAT registration may cover several legal entities. The official registry code identifies
                # each entity, so a VAT or domain match cannot collapse different register entries.
                res.duplicate_links.append(
                    (
                        company.id,
                        [
                            {"signal": ident.kind, "effect": "for", "detail": f"shared {label}"},
                            {
                                "signal": "registry_id",
                                "effect": "against",
                                "detail": (
                                    f"different registry IDs {rec.registry_key} vs {sorted(other_regs)}"
                                ),
                            },
                        ],
                    )
                )
                res.blocked_keys.add((ident.kind, ident.value))
                continue
        if res.company is None:
            res.company = company
            res.match_reasons.append(f"matched existing company on exact {label}")
        elif company.id == res.company.id:
            res.match_reasons.append(f"confirmed by exact {label}")
        else:
            # Record carries strong keys belonging to two different companies: never auto-merge.
            res.duplicate_links.append(
                (
                    company.id,
                    [
                        {
                            "signal": ident.kind,
                            "effect": "for",
                            "detail": f"record {rec.source_key} from {source.id} carries {label} "
                            "which belongs to this company",
                        }
                    ],
                )
            )
            res.blocked_keys.add((ident.kind, ident.value))
    return res, keys


def ensure_identifiers(
    session: Session, company: Company, keys: IdentityKeys, source: Source, blocked: set[tuple[str, str]]
) -> None:
    existing = {
        (i.kind, i.value)
        for i in session.scalars(
            select(CompanyIdentifier).where(
                tuple_(CompanyIdentifier.kind, CompanyIdentifier.value).in_(keys.lookup_pairs())
            )
        )
    }
    for kind, value, derived in keys.keys:
        if (kind, value) in existing or (kind, value) in blocked:
            continue
        session.add(
            CompanyIdentifier(
                company_id=company.id, kind=kind, value=value, source_id=source.id, derived=derived
            )
        )
        existing.add((kind, value))
    session.flush()


def link_duplicate(
    session: Session, a_id: str, b_id: str, reasons: list[dict[str, Any]], score: int, band: str
) -> bool:
    lo, hi = sorted((a_id, b_id))
    if lo == hi:
        return False
    existing = session.scalar(
        select(DuplicateCandidate).where(
            DuplicateCandidate.company_a_id == lo, DuplicateCandidate.company_b_id == hi
        )
    )
    if existing:
        return False
    session.add(DuplicateCandidate(company_a_id=lo, company_b_id=hi, score=score, band=band, reasons=reasons))
    return True


@dataclass
class FactChange:
    added: int = 0
    changed: int = 0
    unchanged: int = 0


def upsert_facts(
    session: Session,
    company: Company,
    rec: ParsedRecord,
    source: Source,
    *,
    run_id: str | None,
    snapshot_id: str | None,
    observed_at: datetime,
) -> FactChange:
    """Versioned upsert: the same source asserting a new value supersedes (valid_to) its old fact."""
    change = FactChange()
    active = {
        f.field_name: f
        for f in session.scalars(
            select(CompanyFact).where(
                CompanyFact.company_id == company.id,
                CompanyFact.source_id == source.id,
                CompanyFact.valid_to.is_(None),
                CompanyFact.is_correction.is_(False),
            )
        )
    }
    now = utcnow()
    for field_name, value in rec.facts.items():
        fact_metadata = rec.fact_metadata.get(field_name, {})
        value_hash = stable_hash({"value": value, **fact_metadata}) if fact_metadata else stable_hash(value)
        current = active.get(field_name)
        if current and current.value_hash == value_hash:
            if observed_at > current.observed_at:
                current.observed_at = observed_at  # re-confirmed by a newer observation
            change.unchanged += 1
            continue
        if current:
            current.valid_to = now
            current.review_status = "superseded"
            change.changed += 1
        else:
            change.added += 1
        session.add(
            CompanyFact(
                company_id=company.id,
                field_name=field_name,
                value_json=value,
                original_value=rec.originals.get(field_name),
                code_system=fact_metadata.get("code_system"),
                code_version=fact_metadata.get("code_version"),
                value_hash=value_hash,
                source_id=source.id,
                source_key=rec.source_key,
                source_url=rec.field_urls.get(field_name) or rec.source_url,
                ingestion_run_id=run_id,
                snapshot_id=snapshot_id,
                observed_at=observed_at,
                valid_from=observed_at,
                base_confidence=rec.fact_confidence.get(field_name, source.base_confidence),
                confidence=rec.fact_confidence.get(field_name, source.base_confidence),
                usage_policy=source.usage_policy,
            )
        )
    session.flush()
    return change


def recompute_company(
    session: Session, company: Company, now: datetime | None = None
) -> dict[str, conf.Resolution]:
    """Derive display fields + per-fact confidence from active facts. Deterministic for the same facts."""
    settings = get_settings()
    now = now or utcnow()
    facts = session.scalars(
        select(CompanyFact).where(CompanyFact.company_id == company.id, CompanyFact.valid_to.is_(None))
    ).all()
    trust = {s.id: s.trust_rank for s in session.scalars(select(Source))}
    by_field: dict[str, list[CompanyFact]] = {}
    for f in facts:
        by_field.setdefault(f.field_name, []).append(f)

    resolutions: dict[str, conf.Resolution] = {}
    for field_name, group in by_field.items():
        evidence = [
            conf.Evidence(
                fact_id=f.id,
                source_id=f.source_id,
                value=f.value_json,
                base_confidence=f.base_confidence,
                observed_at=f.observed_at,
                trust_rank=trust.get(f.source_id, 99),
                is_correction=f.is_correction,
                corrected_at=f.created_at,
            )
            for f in group
        ]
        res = conf.resolve_field(field_name, evidence, now, settings.stale_after_days)
        resolutions[field_name] = res
        for f in group:
            f.confidence = res.fact_confidence.get(f.id, f.base_confidence)

    def val(name: str) -> Any:
        r = resolutions.get(name)
        return r.value if r else None

    company.legal_name = val("legal_name") or company.legal_name
    company.normalized_name = normalize_name(company.legal_name)
    company.trading_name = val("trading_name")
    company.city = val("city")
    company.website = val("website")
    company.sector = val("sector")
    company.description = val("description")
    code = val("industry_code")
    company.industry_codes = [code] if code else []
    emp = val("employees")
    company.estimated_employee_min = emp.get("min") if emp else None
    company.estimated_employee_max = emp.get("max") if emp else None
    rev = val("revenue")
    company.revenue_min = rev.get("min") if rev else None
    company.revenue_max = rev.get("max") if rev else None
    company.currency = rev.get("currency") if rev else None
    company.ownership_type = val("ownership_type") or "unknown"
    company.qualification_status = qualify(
        company.estimated_employee_min,
        company.estimated_employee_max,
        settings.min_employees_default,
        settings.sub_scale_max_employees,
    ).value
    # Website activity signals are not source verification of the company record itself.
    source_obs = [
        f.observed_at
        for f in facts
        if not f.is_correction
        and not (f.source_id.startswith("web-") and f.field_name in WEBSITE_SIGNAL_FIELDS)
    ]
    company.last_verified_at = max(source_obs) if source_obs else None
    session.flush()
    return resolutions
