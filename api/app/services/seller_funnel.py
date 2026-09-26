"""Read-only seller prospect funnel over imported Estonian financial observations."""

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.domain.seller_signals import (
    AnnualFinancial,
    consolidated_revenue,
    files_consolidated,
    financial_signal,
    is_holding_activity,
    robust_reference,
    robust_z,
)
from app.models import Company, CompanyFact, CompanyFinancial, CompanyIdentifier

REGISTER_COMPANY_URL = "https://ariregister.rik.ee/eng/company/{code}"
MIN_PEERS = 8


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


def _registry_url(registry_id: str | None) -> str | None:
    code = (registry_id or "").removeprefix("EE:")
    return REGISTER_COMPANY_URL.format(code=code) if re.fullmatch(r"\d{8}", code) else None


def _next_action(status: str | None, focus: str, quality: str, evidence: str, holding: bool) -> str:
    if _inactive(status):
        return "exclude"
    if focus == "adjacent":
        return "outside_size_band"
    if (
        focus == "core"
        and quality == "core"
        and evidence == "complete"
        and _registered(status)
        and not holding
    ):
        return "advisor_review"
    return "research"


def _millions(value: float) -> str:
    return f"€{value / 1_000_000:.1f}m"


def _brief(item: schemas.SellerProspectOut, min_revenue_eur: int, max_revenue_eur: int) -> None:
    """Fill review_reasons and open_questions from the item's own reported figures. No inference."""
    year = item.latest_year
    reasons: list[str] = []
    if item.latest_revenue_eur is not None and year is not None:
        band = (
            f", inside the provisional {_millions(min_revenue_eur)}–{_millions(max_revenue_eur)} band"
            if item.focus_band == "core"
            else ", outside the provisional size band"
        )
        reasons.append(f"Revenue {_millions(item.latest_revenue_eur)} in FY{year}{band}")
    if item.positive_profit_years is not None and year is not None:
        reasons.append(
            f"Operating profit positive in {item.positive_profit_years} of 3 years (FY{year - 2}–FY{year})"
        )
    if item.three_year_median_margin is not None:
        reasons.append(f"Three-year median operating margin {item.three_year_median_margin:.1%}")
    if item.three_year_revenue_cagr is not None and year is not None:
        reasons.append(f"Revenue growth FY{year - 2}–FY{year}: {item.three_year_revenue_cagr:+.1%} a year")
    if (
        item.financial_profile_index is not None
        and item.margin_peer_z is not None
        and item.equity_peer_z is not None
    ):
        reasons.append(
            f"Peer index {item.financial_profile_index:+.2f} among {item.peer_count} companies in "
            f"EMTAK {item.peer_group} (margin {item.margin_peer_z:+.1f}, equity ratio "
            f"{item.equity_peer_z:+.1f} robust SD from the group median)"
        )
    if item.latest_equity_ratio is not None and year is not None:
        reasons.append(f"Equity {item.latest_equity_ratio:.0%} of total assets in FY{year}")
    if item.latest_employees_fte is not None and year is not None:
        reasons.append(
            f"{item.latest_employees_fte:.0f} full-time equivalent employees reported for FY{year}"
        )
    if "group_parent" in item.flags:
        group = (
            f": group revenue {_millions(item.consolidated_revenue_eur)}"
            if item.consolidated_revenue_eur is not None
            else ""
        )
        reasons.append(f"Also filed consolidated accounts for FY{year}{group}")

    questions = [
        "Is the owner open to a conversation? Unknown until an advisor asks.",
        "Do Mergero's buyers want this profile? Needs MGX buyer criteria.",
        "Who owns it: founder, family, a group or a fund? Not in the imported files; "
        "check the register card.",
    ]
    if "group_parent" in item.flags:
        questions.append(
            "Group parent: the sellable unit is probably the group; standalone figures may understate it."
        )
    if "holding_activity" in item.flags:
        questions.append(
            "Registered as a holding or head-office activity: find the operating business before "
            "comparing margins."
        )
    if (
        item.focus_band == "core"
        and item.evidence_status == "complete"
        and item.financial_profile_index is None
        and "holding_activity" not in item.flags
    ):
        questions.append(
            f"No peer index: fewer than {MIN_PEERS} comparable companies in EMTAK {item.peer_group or '—'}."
        )
    item.review_reasons = reasons
    item.open_questions = questions


def _stages(items: list[schemas.SellerProspectOut], min_revenue_eur: int, max_revenue_eur: int):
    rules = [
        ("imported", "Imported companies", "Estonian companies in the database (≥20 FTE import scope)", None),
        (
            "registered",
            "Active in the register",
            "Official status is registered",
            lambda i: _registered(i.registry_status),
        ),
        (
            "in_size_band",
            "In the size band",
            f"Latest comparable revenue {_millions(min_revenue_eur)}–{_millions(max_revenue_eur)}",
            lambda i: i.focus_band == "core",
        ),
        (
            "complete_evidence",
            "Three comparable years",
            "Three consecutive standalone EUR fiscal years, latest filing included",
            lambda i: i.evidence_status == "complete",
        ),
        (
            "profitable",
            "Profitable all three years",
            "Positive operating profit in each of the three years",
            lambda i: i.positive_profit_years == 3,
        ),
        (
            "advisor_review",
            "Advisor review queue",
            "Excludes holding or head-office activity codes",
            lambda i: i.next_action == "advisor_review",
        ),
    ]
    remaining = list(items)
    stages: list[schemas.FunnelStageOut] = []
    for key, label, rule, keep in rules:
        if keep is not None:
            remaining = [item for item in remaining if keep(item)]
        stages.append(schemas.FunnelStageOut(key=key, label=label, count=len(remaining), rule=rule))
    return stages


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
        select(CompanyFinancial)
        .join(Company)
        .where(
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
                employees_fte=row.employees_fte,
            )
        )

    statuses: dict[str, str] = {}
    for fact in session.scalars(
        select(CompanyFact)
        .join(Company)
        .where(
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
        select(CompanyIdentifier)
        .join(Company)
        .where(
            Company.country == "EE",
            Company.merged_into_id.is_(None),
            CompanyIdentifier.kind == "registry_id",
            CompanyIdentifier.derived.is_(False),
        )
    ):
        registry_ids[identifier.company_id] = identifier.value

    all_items: list[schemas.SellerProspectOut] = []
    for company in companies:
        rows = financials.get(company.id, [])
        signal = financial_signal(rows, min_revenue_eur=min_revenue_eur, max_revenue_eur=max_revenue_eur)
        status = statuses.get(company.id)
        peer_group = _peer_group(company.industry_codes)
        holding = is_holding_activity(company.industry_codes)
        flags: list = []
        group_revenue = None
        if signal.latest_year is not None and files_consolidated(rows, signal.latest_year):
            flags.append("group_parent")
            reported = consolidated_revenue(rows, signal.latest_year)
            group_revenue = float(reported) if reported is not None else None
        if holding:
            flags.append("holding_activity")
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
                    status, signal.focus_band, signal.quality_band, signal.evidence_status, holding
                ),
                latest_year=signal.latest_year,
                latest_revenue_eur=signal.latest_revenue_eur,
                latest_operating_margin=signal.latest_operating_margin,
                three_year_median_margin=signal.three_year_median_margin,
                three_year_revenue_cagr=signal.three_year_revenue_cagr,
                stable_revenue=signal.stable_revenue,
                positive_profit_years=signal.positive_profit_years,
                latest_equity_ratio=signal.latest_equity_ratio,
                latest_employees_fte=signal.latest_employees_fte,
                consolidated_revenue_eur=group_revenue,
                flags=flags,
                registry_url=_registry_url(registry_ids.get(company.id)),
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
            and "holding_activity" not in item.flags
        ):
            peers[item.peer_group].append(item)

    peer_groups: list[schemas.PeerGroupOut] = []
    for code, group in peers.items():
        if len(group) < MIN_PEERS:
            continue
        margins = [
            item.three_year_median_margin for item in group if item.three_year_median_margin is not None
        ]
        equity_ratios = [item.latest_equity_ratio for item in group if item.latest_equity_ratio is not None]
        margin_ref = robust_reference(margins)
        equity_ref = robust_reference(equity_ratios)
        peer_groups.append(
            schemas.PeerGroupOut(
                group=code,
                peer_count=len(group),
                median_margin=margin_ref[0],
                median_equity_ratio=equity_ref[0],
            )
        )
        for item in group:
            item.peer_count = len(group)
            assert item.three_year_median_margin is not None
            assert item.latest_equity_ratio is not None
            item.margin_peer_z = robust_z(item.three_year_median_margin, margin_ref)
            item.equity_peer_z = robust_z(item.latest_equity_ratio, equity_ref)
            if item.margin_peer_z is not None and item.equity_peer_z is not None:
                item.financial_profile_index = round(0.7 * item.margin_peer_z + 0.3 * item.equity_peer_z, 2)
    peer_groups.sort(key=lambda group: (-group.peer_count, group.group))

    for item in all_items:
        _brief(item, min_revenue_eur, max_revenue_eur)

    if sector:
        needle = sector.casefold()
        all_items = [
            item
            for item in all_items
            if needle in (item.sector or "").casefold() or needle in (item.peer_group or "")
        ]
        peer_groups = [group for group in peer_groups if needle in group.group]

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
        stages=_stages(all_items, min_revenue_eur, max_revenue_eur),
        peer_groups=peer_groups,
        items=all_items[:limit],
        methodology=(
            "Annual revenue is a provisional size proxy. The funnel requires three consecutive "
            "standalone EUR fiscal years for complete financial evidence. The peer index uses "
            f"median/MAD Z-scores within two-digit EMTAK groups with at least {MIN_PEERS} peers: "
            "70% operating margin and 30% equity/assets. Holding and head-office activity codes are "
            "flagged, not ranked. It describes financial profile only; owner intent, buyer fit and "
            "mandate likelihood are not assessed."
        ),
    )
