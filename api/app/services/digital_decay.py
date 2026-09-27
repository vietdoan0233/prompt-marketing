"""Digital Decay: opt-in website activity signal for Estonian companies already in the database.

Runs the `website_decay` connector through the standard ingestion pipeline (permission gate, allowed-field
enforcement, provenance, idempotent snapshots). The connector may only enrich registry-backed companies;
it never creates one. Scoring is deterministic and lives in the connector/domain layer; this module only
selects targets, builds their registry context (registry code, registered address, reported revenue) and
wires the run.
"""

import json
import re
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.base import CandidateRef, ConnectorError, FetchedSnapshot
from app.domain.normalize import normalize_domain, normalize_name
from app.models import (
    Company,
    CompanyFact,
    CompanyFinancial,
    CompanyIdentifier,
    IngestionRun,
    RegisteredAddress,
    Source,
)
from app.services import audit, ingestion
from app.services.permissions import PermissionDenied, enable_gate, ingestion_gate

SOURCE_ID = "web-digital-decay"
REGISTER_SOURCE_ID = "ee-ariregister"
_REGISTER_DOMAIN_FIELDS = ("registry_domains", "website", "email_domain")
SIGNAL_FIELD = "digital_decay_signal"
REGISTERED_STATUSES = {"R", "Registered"}  # e-Business Register status code "R" = entered in the register
_SCOPE_RANK = {"standalone": 0, None: 1, "consolidated": 2}


def thresholds() -> Any:
    from app.domain.digital_decay import Thresholds

    s = get_settings()
    return Thresholds(
        copyright_stale_years=s.decay_copyright_stale_years,
        news_stale_months=s.decay_news_stale_months,
        min_revenue_eur=s.decay_min_revenue_eur,
        news_min_posts=s.decay_news_min_posts,
        headcount_flat_pct=s.decay_headcount_flat_pct,
    )


# ------------------------------------------------------------------ registry context


def _revenue_candidates():
    return (
        CompanyFinancial.revenue.is_not(None),
        CompanyFinancial.review_status != "superseded",
        CompanyFinancial.currency == "EUR",
        CompanyFinancial.period_length_class == "standard_12_month",
    )


def latest_revenue(session: Session, company_id: str) -> dict[str, Any] | None:
    """Latest reported 12-month EUR revenue. Short/long periods are never used and never annualized."""
    rows = session.scalars(
        select(CompanyFinancial).where(CompanyFinancial.company_id == company_id, *_revenue_candidates())
    ).all()
    if not rows:
        return None
    year = max(r.fiscal_year for r in rows)
    in_year = [r for r in rows if r.fiscal_year == year]
    # Prefer standalone, then unscoped, then consolidated; then latest observation; then id (deterministic).
    in_year.sort(key=lambda r: (_SCOPE_RANK.get(r.statement_scope, 3), -r.observed_at.timestamp(), r.id))
    best = in_year[0]
    assert best.revenue is not None
    return {
        "amount": int(best.revenue),
        "currency": "EUR",
        "fiscal_year": best.fiscal_year,
        # Revenue is always a reported statement line; the row's value_type describes EBITDA derivation.
        "value_type": "reported",
    }


def _fte_number(value: Any) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def headcount_trend(session: Session, company_id: str) -> dict[str, Any] | None:
    """Register FTE trend: latest fiscal year vs three years earlier, from filed 12-month annual reports
    (standalone statement preferred per year). None when no FTE figure was filed at all; state `unknown`
    when the series is too short or the base year is zero."""
    rows = session.scalars(
        select(CompanyFinancial).where(
            CompanyFinancial.company_id == company_id,
            CompanyFinancial.employees_fte.is_not(None),
            CompanyFinancial.period_length_class == "standard_12_month",
            CompanyFinancial.review_status != "superseded",
        )
    ).all()
    if not rows:
        return None
    by_year: dict[int, list[CompanyFinancial]] = {}
    for r in rows:
        by_year.setdefault(r.fiscal_year, []).append(r)
    fte: dict[int, int | float] = {}
    for year, group in by_year.items():
        group.sort(key=lambda r: (_SCOPE_RANK.get(r.statement_scope, 3), -r.observed_at.timestamp(), r.id))
        fte[year] = _fte_number(group[0].employees_fte)
    years = sorted(fte)
    latest = years[-1]
    window = [y for y in years if latest - 3 <= y <= latest]  # at most the last 4 fiscal years
    series = [[y, fte[y]] for y in window]
    unknown: dict[str, Any] = {
        "state": "unknown",
        "change_pct": None,
        "from_year": None,
        "to_year": None,
        "series": series,
    }
    if latest - 3 in fte:
        base_year = latest - 3
    elif len(window) >= 3:
        base_year = window[0]
    else:
        return unknown
    base = fte[base_year]
    if base <= 0:
        return unknown
    change = round((fte[latest] - base) / base * 100, 1)
    flat = get_settings().decay_headcount_flat_pct
    state = "growing" if change > flat else "shrinking" if change < -flat else "flat"
    return {
        "state": state,
        "change_pct": change,
        "from_year": base_year,
        "to_year": latest,
        "series": series,
    }


def registry_code(session: Session, company_id: str) -> str | None:
    value = session.scalar(
        select(CompanyIdentifier.value)
        .where(
            CompanyIdentifier.company_id == company_id,
            CompanyIdentifier.kind == "registry_id",
            CompanyIdentifier.derived.is_(False),
            CompanyIdentifier.value.like("EE:%"),
        )
        .order_by(CompanyIdentifier.created_at)
    )
    return value.split(":", 1)[1] if value else None


def current_address(session: Session, company_id: str) -> dict[str, str | None] | None:
    row = session.scalar(
        select(RegisteredAddress)
        .where(RegisteredAddress.company_id == company_id, RegisteredAddress.valid_to.is_(None))
        .order_by(RegisteredAddress.observed_at.desc(), RegisteredAddress.id)
    )
    if row is None:
        return None
    return {"address_line": row.address_line, "postal_code": row.postal_code, "city": row.city}


def _one_line(addr: dict[str, str | None] | None) -> str | None:
    if not addr:
        return None
    tail = " ".join(p for p in (addr.get("postal_code"), addr.get("city")) if p)
    text = ", ".join(p for p in (addr.get("address_line"), tail) if p)
    return text or None


def _domains_from_facts(facts: list[CompanyFact]) -> dict[str, list[str]]:
    """{"www": [...], "email": [...]} from one company's active register facts (registry_domains preferred,
    else the website / email_domain facts)."""
    by_field: dict[str, CompanyFact] = {}
    for f in sorted(facts, key=lambda f: (f.observed_at, f.id), reverse=True):
        by_field.setdefault(f.field_name, f)
    structured = by_field.get("registry_domains")
    if structured is not None and isinstance(structured.value_json, dict):
        value = structured.value_json
        return {
            k: [d for d in (normalize_domain(str(x)) for x in value.get(k) or []) if d]
            for k in ("www", "email")
        }
    out: dict[str, list[str]] = {"www": [], "email": []}
    for field_name, kind in (("website", "www"), ("email_domain", "email")):
        fact = by_field.get(field_name)
        domain = normalize_domain(str(fact.value_json)) if fact is not None and fact.value_json else None
        if domain:
            out[kind].append(domain)
    return out


def _register_facts(session: Session, company_id: str | None = None) -> list[CompanyFact]:
    stmt = select(CompanyFact).where(
        CompanyFact.source_id == REGISTER_SOURCE_ID,
        CompanyFact.field_name.in_(_REGISTER_DOMAIN_FIELDS),
        CompanyFact.valid_to.is_(None),
        CompanyFact.is_correction.is_(False),
    )
    if company_id is not None:
        stmt = stmt.where(CompanyFact.company_id == company_id)
    return list(session.scalars(stmt))


def register_domain_counts(session: Session) -> Counter[str]:
    """How many companies declare each domain to the register (group domains appear more than once)."""
    grouped: dict[str, list[CompanyFact]] = {}
    for fact in _register_facts(session):
        grouped.setdefault(fact.company_id, []).append(fact)
    counts: Counter[str] = Counter()
    for facts in grouped.values():
        domains = _domains_from_facts(facts)
        counts.update(set(domains["www"]) | set(domains["email"]))
    return counts


def registry_domains(
    session: Session, company_id: str, counts: Counter[str] | None = None
) -> dict[str, list[str]]:
    """Register-declared website/email domains plus `shared` (also declared by another company)."""
    domains = _domains_from_facts(_register_facts(session, company_id))
    own = list(dict.fromkeys(domains["www"] + domains["email"]))
    if own and counts is None:
        counts = register_domain_counts(session)
    shared = [d for d in own if counts is not None and counts[d] > 1]
    return {"www": domains["www"], "email": domains["email"], "shared": shared}


def build_target(
    session: Session,
    company: Company,
    domain: str | None = None,
    domain_counts: Counter[str] | None = None,
) -> dict[str, Any]:
    """Flat connector target. Contains registry context only; no personal data (email *domains* only)."""
    addr = current_address(session, company.id)
    return {
        "company_id": company.id,
        "legal_name": company.legal_name,
        "registry_code": registry_code(session, company.id),
        "postal_code": addr.get("postal_code") if addr else None,
        "street": addr.get("address_line") if addr else None,
        "address": _one_line(addr),
        "domain": normalize_domain(domain) if domain else None,
        "registry_domains": registry_domains(session, company.id, domain_counts),
        "revenue": latest_revenue(session, company.id),
        "headcount": headcount_trend(session, company.id),
    }


def _registry_status(session: Session, company_id: str) -> str | None:
    fact = session.scalar(
        select(CompanyFact)
        .where(
            CompanyFact.company_id == company_id,
            CompanyFact.field_name == "registry_status",
            CompanyFact.valid_to.is_(None),
        )
        .order_by(CompanyFact.is_correction.desc(), CompanyFact.observed_at.desc(), CompanyFact.id)
    )
    return None if fact is None or fact.value_json is None else str(fact.value_json)


# ------------------------------------------------------------------ target selection


def _live_ee():
    return (Company.country == "EE", Company.merged_into_id.is_(None))


def find_companies(session: Session, name: str, address: str | None) -> list[Company]:
    key = normalize_name(name)
    if not key:
        return []
    found = list(session.scalars(select(Company).where(Company.normalized_name == key, *_live_ee())))
    if not found:
        found = list(
            session.scalars(
                select(Company)
                .where(Company.normalized_name.like(f"%{key}%"), *_live_ee())
                .order_by(Company.legal_name, Company.id)
                .limit(25)
            )
        )
    if len(found) <= 1 or not address:
        return sorted(found, key=lambda c: (c.legal_name, c.id))

    text = address.casefold()
    postal = re.search(r"\b\d{5}\b", address)
    scored: list[tuple[int, Company]] = []
    for company in found:
        addr = current_address(session, company.id) or {}
        score = 0
        if postal and addr.get("postal_code") == postal.group(0):
            score += 2
        for token in re.findall(r"\w+", (addr.get("address_line") or "").casefold()):
            if len(token) >= 3 and token in text:
                score += 1
        city = (addr.get("city") or "").casefold()
        if city and city in text:
            score += 1
        scored.append((score, company))
    scored.sort(key=lambda sc: (-sc[0], sc[1].legal_name, sc[1].id))
    top = scored[0][0]
    if top > 0 and sum(1 for s, _ in scored if s == top) == 1:
        return [scored[0][1]]
    return [c for _, c in scored]


def select_batch(
    session: Session, revenue_min: int, revenue_max: int, limit: int, min_employees: int
) -> list[Company]:
    """EE companies whose latest reported 12-month EUR revenue is within range, above the headcount floor,
    and still registered (not in liquidation, bankrupt or deleted)."""
    latest_year = (
        select(CompanyFinancial.company_id, func.max(CompanyFinancial.fiscal_year).label("fy"))
        .where(*_revenue_candidates())
        .group_by(CompanyFinancial.company_id)
        .subquery()
    )
    stmt = (
        select(Company)
        .join(latest_year, latest_year.c.company_id == Company.id)
        .join(
            CompanyFinancial,
            and_(
                CompanyFinancial.company_id == Company.id,
                CompanyFinancial.fiscal_year == latest_year.c.fy,
                *_revenue_candidates(),
                CompanyFinancial.revenue >= revenue_min,
                CompanyFinancial.revenue <= revenue_max,
            ),
        )
        .where(*_live_ee())
        .distinct()
    )
    if min_employees > 0:
        stmt = stmt.where(Company.estimated_employee_min >= min_employees)
    picked: list[tuple[int, int, str, Company]] = []
    for company in session.scalars(stmt):
        rev = latest_revenue(session, company.id)
        if rev is None or not (revenue_min <= rev["amount"] <= revenue_max):
            continue
        status = _registry_status(session, company.id)
        if status is not None and status not in REGISTERED_STATUSES:
            continue
        picked.append((-rev["fiscal_year"], -rev["amount"], company.id, company))
    picked.sort(key=lambda p: p[:3])
    cap = max(0, min(limit, get_settings().decay_batch_limit_max))
    return [p[3] for p in picked[:cap]]


def companies_from_query(session: Session, query: dict[str, Any]) -> list[Company]:
    """Accepts {"company_ids": [...]} and a stored run query {"targets": [{"company_id": ...}]} (retry)."""
    ids: list[str] = [str(i) for i in query.get("company_ids") or [] if i]
    ids += [
        str(t["company_id"])
        for t in query.get("targets") or []
        if isinstance(t, dict) and t.get("company_id")
    ]
    out: list[Company] = []
    seen: set[str] = set()
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        company = session.get(Company, cid)
        if company is not None and company.merged_into_id is None:
            out.append(company)
    return out


# ------------------------------------------------------------------ connector wiring


def _build_connector(source: Source, today: date | None) -> Any:
    from app.connectors.website_decay import WebsiteDecayConnector

    return WebsiteDecayConnector(source.rate_limit_per_minute, today=today, thresholds=thresholds())


def _signal_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


VERIFIED_DOMAIN_SOURCES = {
    "registry_code",
    "name_and_address",
    "registry_www",
    "registry_email",
}


class _EnrichmentOnly:
    """Safety net around the website connector: every row is enrichment-only EE data, and `website` is kept
    only when the signal says the domain was verified against the register (registry_code,
    name_and_address, registry_www or registry_email). A caller-supplied domain earns one of these two
    identity-check outcomes the same way a guessed domain does; supplying a domain is never itself a
    verification source."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.parser_version: str = inner.parser_version

    def discover(self, query: dict[str, Any], region: str | None, min_employees: int) -> list[CandidateRef]:
        return list(self.inner.discover(query, region, min_employees))

    def fetch(self, ref: CandidateRef) -> FetchedSnapshot:
        return self.inner.fetch(ref)

    def fetch_many(self, refs: list[CandidateRef]) -> tuple[list[FetchedSnapshot], list[dict[str, Any]]]:
        if hasattr(self.inner, "fetch_many"):
            snapshots, errors = self.inner.fetch_many(refs)
            return list(snapshots), list(errors)
        snapshots, errors = [], []
        for ref in refs:
            try:
                snapshots.append(self.inner.fetch(ref))
            except ConnectorError as exc:
                errors.append({"stage": "fetch", "reference": ref.reference, "message": str(exc)})
        return snapshots, errors

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        rows = []
        for row in self.inner.parse(snapshot):
            row = dict(row)
            row["country"] = "EE"
            row["enrichment_only"] = True
            if "evidence" not in row and isinstance(row.get("field_urls"), dict):
                row["evidence"] = row.pop("field_urls")
            sig = _signal_dict(row.get(SIGNAL_FIELD))
            verified = bool(
                sig and sig.get("domain") and sig.get("domain_verification") in VERIFIED_DOMAIN_SOURCES
            )
            row["website_verified"] = verified
            if not verified:
                row.pop("website", None)
            rows.append(row)
        return rows


def run_decay(
    session: Session,
    companies: list[Company],
    *,
    actor: str,
    domains: dict[str, str] | None = None,
    today: date | None = None,
    retry_of_id: str | None = None,
) -> IngestionRun:
    source = ingestion._registered_or_deny(session, SOURCE_ID, actor)
    counts = register_domain_counts(session) if companies else None
    targets = [build_target(session, c, (domains or {}).get(c.id), counts) for c in companies]
    query = {"targets": targets}
    run = ingestion._new_run(
        session,
        source,
        SOURCE_ID,
        kind="discovery",
        actor=actor,
        min_employees=get_settings().min_employees_default,
        query=query,
        retry_of_id=retry_of_id,
    )
    ingestion._gate_or_reject(session, run, source, live=bool((source.connector_config or {}).get("live")))
    if not targets:
        return ingestion._fail(session, run, "discover", "no registered company to check")

    connector = _EnrichmentOnly(_build_connector(source, today))
    try:
        try:
            refs = connector.discover(query, source.region, run.min_employees)
        except ConnectorError as exc:
            return ingestion._fail(session, run, "discover", str(exc))
        run.counts = {**run.counts, "discovered": len(refs)}
        snapshots, errors = connector.fetch_many(refs)
    finally:
        connector.close()
    if errors:
        run.errors = [
            *run.errors,
            *(
                {
                    "stage": str(e.get("stage") or "fetch"),
                    "message": f"{e.get('reference')}: {e.get('message')}",
                }
                for e in errors
            ),
        ]
    return ingestion._execute(session, run, source, connector, snapshots)


def run_unregistered(
    session: Session, target: dict[str, Any], *, actor: str, today: date | None = None
) -> dict[str, Any]:
    """Check a company that is not in the database. The result is returned only; nothing is stored."""
    source = session.get(Source, SOURCE_ID)
    reasons = ingestion_gate(source, live=True)
    if reasons:
        audit.record(
            session,
            actor=actor,
            action="ingestion.rejected_by_permission_gate",
            entity_type="source",
            entity_id=SOURCE_ID,
            details={"reasons": reasons, "mode": "unregistered_target"},
        )
        session.commit()
        raise PermissionDenied(reasons)
    assert source is not None
    connector = _EnrichmentOnly(_build_connector(source, today))
    query = {"targets": [target]}
    try:
        refs = connector.discover(query, source.region, 0)
        snapshots, errors = connector.fetch_many(refs)
        rows = [row for snap in snapshots for row in connector.parse(snap)]
    finally:
        connector.close()
    signal = next((s for s in (_signal_dict(r.get(SIGNAL_FIELD)) for r in rows) if s), None)
    if signal is None:
        detail = "; ".join(str(e.get("message")) for e in errors) or "no signal produced"
        raise ConnectorError(f"Digital Decay check produced no signal: {detail}")
    audit.record(
        session,
        actor=actor,
        action="digital_decay.unregistered_check",
        entity_type="source",
        entity_id=SOURCE_ID,
        details={
            "legal_name": target.get("legal_name"),
            "domain": signal.get("domain"),
            "verdict": signal.get("verdict"),
        },
    )
    session.commit()
    return signal


def set_enabled(session: Session, *, enabled: bool, actor: str) -> list[str]:
    source = session.get(Source, SOURCE_ID)
    if source is None:
        return ["source is not registered in the source registry"]
    if enabled:
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
            return reasons
        source.enabled = True
        audit.record(session, actor=actor, action="source.enabled", entity_type="source", entity_id=source.id)
    else:
        source.enabled = False
        audit.record(
            session, actor=actor, action="source.disabled", entity_type="source", entity_id=source.id
        )
    session.commit()
    return []


def latest_signal(session: Session, company_id: str) -> CompanyFact | None:
    return session.scalar(
        select(CompanyFact)
        .where(
            CompanyFact.company_id == company_id,
            CompanyFact.field_name == SIGNAL_FIELD,
            CompanyFact.source_id == SOURCE_ID,
            CompanyFact.valid_to.is_(None),
            CompanyFact.is_correction.is_(False),
        )
        .order_by(CompanyFact.observed_at.desc(), CompanyFact.id)
    )
