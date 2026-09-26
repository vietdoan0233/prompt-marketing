"""Read-only seller prospect funnel over imported Estonian financial observations."""

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.domain.seller_signals import AnnualFinancial, financial_signal, robust_reference, robust_z
from app.models import Company, CompanyFact, CompanyFinancial, CompanyIdentifier


def _registered(status: str | None) -> bool:
    # The official Estonian export uses "Registrisse kantud"; older fixtures also use English.
    return status is not None and status.strip().casefold() in {
        "registrisse kantud",
        "registered",
    }


def _inactive(status: str | None) -> bool:
    return status is not None and status.strip().casefold() in {
        "likvideerimisel",
        "in liquidation",
        "pankrotis",
        "bankrupt",
        "kustutatud",
        "deleted",
    }


def _peer_group(codes: list[str]) -> str | None:
    for code in codes or []:
        digits = re.search(r"\d{2}", code)
        if digits:
            return digits.group()
    return None


def _next_action(status: str | None, focus: str, quality: str, evidence: str) -> str:
    if _inactive(status):
        return "exclude"
    if focus == "core" and quality == "core" and evidence == "complete" and _registered(status):
        return "advisor_review"
    if focus == "adjacent":
        return "outside_size_band"
    return "research"


def seller_funnel(
    session: Session,
    *,
    min_revenue_eur: int,
    max_revenue_eur: int,
    sector: str | None,
    limit: int,
) -> schemas.SellerFunnelOut:
    companies = list(
        session.scalars(select(Company).where(Company.country == "EE", Company.merged_into_id.is_(None)))
    )
    if not companies:
        return schemas.SellerFunnelOut(
            total_companies=0,
            core_size=0,
            three_year_profitable=0,
            advisor_review=0,
            items=[],
            methodology="No imported Estonian companies yet. Load the official register files first.",
        )

    financials: dict[str, list[AnnualFinancial]] = defaultdict(list)
    for row in session.scalars(
        select(CompanyFinancial).join(Company).where(
            Company.country == "EE",
            Company.merged_into_id.is_(None),
            CompanyFinancial.review_status != "superseded",
        )
    ):
        financials[row.company_id].append(
            AnnualFinancial(
                fiscal_year=row.fiscal_year,
                period_start=row.period_start,
                period_end=row.period_end,
                currency=row.currency,
                unit=row.unit,
                statement_scope=row.statement_scope,
                revenue=row.revenue,
                operating_profit=row.operating_profit,
                total_assets=row.total_assets,
                equity=row.equity,
                filing_id=row.filing_id,
                source_url=row.source_url,
                source_file=row.source_file,
                review_status=row.review_status,
            )
        )

    statuses: dict[str, str] = {}
    for fact in session.scalars(
        select(CompanyFact).join(Company).where(
            Company.country == "EE",
            Company.merged_into_id.is_(None),
            CompanyFact.field_name == "registry_status",
            CompanyFact.valid_to.is_(None),
        )
    ):
        value = fact.value_json
        if isinstance(value, str) and value:
            statuses[fact.company_id] = value

    registry_ids: dict[str, str] = {}
    for identifier in session.scalars(
        select(CompanyIdentifier).join(Company).where(
            Company.country == "EE",
            Company.merged_into_id.is_(None),
            CompanyIdentifier.kind == "registry_id",
            CompanyIdentifier.derived.is_(False),
        )
    ):
        registry_ids[identifier.company_id] = identifier.value

    all_items: list[schemas.SellerProspectOut] = []
    for company in companies:
        signal = financial_signal(
            financials.get(company.id, []),
            min_revenue_eur=min_revenue_eur,
            max_revenue_eur=max_revenue_eur,
        )
        status = statuses.get(company.id)
        peer_group = _peer_group(company.industry_codes)
        issues = list(signal.issues)
        if status is None:
            issues.append("Current registry status unavailable")
        all_items.append(
            schemas.SellerProspectOut(
                company_id=company.id,
                legal_name=company.legal_name,
                registry_id=registry_ids.get(company.id),
                registry_status=status,
                sector=company.sector,
                peer_group=peer_group,
                focus_band=signal.focus_band,
                quality_band=signal.quality_band,
                evidence_status=signal.evidence_status,
                next_action=_next_action(
                    status, signal.focus_band, signal.quality_band, signal.evidence_status
                ),
                latest_year=signal.latest_year,
                latest_revenue_eur=signal.latest_revenue_eur,
                latest_operating_margin=signal.latest_operating_margin,
                three_year_median_margin=signal.three_year_median_margin,
                three_year_revenue_cagr=signal.three_year_revenue_cagr,
                stable_revenue=signal.stable_revenue,
                positive_profit_years=signal.positive_profit_years,
                latest_equity_ratio=signal.latest_equity_ratio,
                filing_ids=signal.filing_ids,
                source_urls=signal.source_urls,
                issues=issues,
            )
        )

    peers: dict[str, list[schemas.SellerProspectOut]] = defaultdict(list)
    for item in all_items:
        if (
            item.peer_group is not None
            and item.focus_band == "core"
            and item.evidence_status == "complete"
            and item.three_year_median_margin is not None
            and item.latest_equity_ratio is not None
            and _registered(item.registry_status)
        ):
            peers[item.peer_group].append(item)

    for group in peers.values():
        if len(group) < 8:
            continue
        margin_ref = robust_reference(
            [
                item.three_year_median_margin
                for item in group
                if item.three_year_median_margin is not None
            ]
        )
        equity_ref = robust_reference(
            [item.latest_equity_ratio for item in group if item.latest_equity_ratio is not None]
        )
        for item in group:
            item.peer_count = len(group)
            assert item.three_year_median_margin is not None
            assert item.latest_equity_ratio is not None
            item.margin_peer_z = robust_z(item.three_year_median_margin, margin_ref)
            item.equity_peer_z = robust_z(item.latest_equity_ratio, equity_ref)
            if item.margin_peer_z is not None and item.equity_peer_z is not None:
                item.financial_profile_index = round(
                    0.7 * item.margin_peer_z + 0.3 * item.equity_peer_z, 2
                )

    if sector:
        needle = sector.casefold()
        all_items = [
            item
            for item in all_items
            if needle in (item.sector or "").casefold() or needle in (item.peer_group or "")
        ]

    next_action_order = {"advisor_review": 0, "research": 1, "outside_size_band": 2, "exclude": 3}
    all_items.sort(
        key=lambda item: (
            next_action_order[item.next_action],
            -(item.financial_profile_index if item.financial_profile_index is not None else -99),
            item.legal_name.casefold(),
        )
    )
    return schemas.SellerFunnelOut(
        total_companies=len(all_items),
        core_size=sum(item.focus_band == "core" for item in all_items),
        three_year_profitable=sum(
            item.focus_band == "core"
            and item.positive_profit_years == 3
            and item.evidence_status == "complete"
            for item in all_items
        ),
        advisor_review=sum(item.next_action == "advisor_review" for item in all_items),
        items=all_items[:limit],
        methodology=(
            "Annual revenue is a provisional size proxy. The funnel requires three consecutive "
            "standalone EUR fiscal years for complete financial evidence. The peer index uses "
            "median/MAD Z-scores within two-digit EMTAK groups with at least eight peers: "
            "70% operating margin and 30% equity/assets. It describes financial profile only; "
            "owner intent, buyer fit and mandate likelihood are not assessed."
        ),
    )
