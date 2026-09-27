"""Read-only seller prospect funnel over imported Estonian financial observations."""

import re
from collections import defaultdict
from datetime import datetime
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.domain.sectors import MIN_COMPANIES_PER_SECTOR, industry_division, sector_options
from app.domain.sectors import OTHERS_CODE as OTHERS_SECTOR_CODE
from app.domain.seller_signals import (
    CASH_HARVESTING_CAGR_MAX,
    CASH_HARVESTING_CAGR_MIN,
    CASH_HARVESTING_EBITDA_MARGIN_MIN,
    CASH_HARVESTING_EXPLANATION,
    CASH_HARVESTING_LABEL,
    AnnualFinancial,
    cash_harvesting_candidate,
    consolidated_revenue,
    files_consolidated,
    financial_signal,
    is_holding_activity,
    robust_reference,
    robust_z,
)
from app.models import Company, CompanyFact, CompanyFinancial, CompanyIdentifier
from app.services.digital_decay import SIGNAL_FIELD as DECAY_SIGNAL_FIELD

REGISTER_COMPANY_URL = "https://ariregister.rik.ee/eng/company/{code}"
MIN_PEERS = 8
# Lower sorts first, ahead of the peer index, inside the advisor-review queue only. A missing check
# (None) is deliberately equal to "active"/"insufficient_evidence": absence of a check is not evidence.
DecayVerdict = Literal["coasting", "decaying", "watch", "active", "insufficient_evidence"]
VALID_DECAY_VERDICTS: frozenset[str] = frozenset(
    ("coasting", "decaying", "watch", "active", "insufficient_evidence")
)
DECAY_PRIORITY: dict[str, int] = {"coasting": 0, "decaying": 1, "watch": 2}
DECAY_DEFAULT_PRIORITY = 3


def is_registered_status(status: str | None) -> bool:
    # The official CSV uses R; other inputs may carry the Estonian or English label.
    return status is not None and status.strip().casefold() in {
        "r",
        "registrisse kantud",
        "registered",
    }


def is_inactive_status(status: str | None) -> bool:
    return status is not None and status.strip().casefold() in {
        "l",
        "likvideerimisel",
        "in liquidation",
        "n",
        "pankrotis",
        "bankrupt",
        "k",
        "kustutatud",
        "deleted",
    }


def _registry_url(registry_id: str | None) -> str | None:
    code = (registry_id or "").removeprefix("EE:")
    return REGISTER_COMPANY_URL.format(code=code) if re.fullmatch(r"\d{8}", code) else None


def _next_action(status: str | None, focus: str, quality: str, evidence: str, holding: bool) -> str:
    if is_inactive_status(status):
        return "exclude"
    if focus == "adjacent":
        return "outside_size_band"
    if (
        focus == "core"
        and quality == "core"
        and evidence == "complete"
        and is_registered_status(status)
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
    if item.digital_decay_verdict is not None:
        checked = (
            f" (checked {item.digital_decay_observed_at:%Y-%m-%d})"
            if item.digital_decay_observed_at is not None
            else ""
        )
        reasons.append(f"Website signal: {item.digital_decay_verdict}{checked}")
    if item.cash_harvesting_candidate:
        reasons.append(
            f"{CASH_HARVESTING_LABEL}: revenue CAGR "
            f"{item.cash_harvesting_revenue_cagr:+.1%} and EBITDA margin "
            f"{item.latest_ebitda_margin:.1%} in FY{year}. {CASH_HARVESTING_EXPLANATION}"
        )

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
    if item.next_action == "advisor_review" and item.digital_decay_verdict is None:
        questions.append(
            "No website check run yet (opt-in POST .../signals/digital-decay): a coasting or decaying "
            "verdict would be a stronger reason to call now than financials alone."
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
            lambda i: is_registered_status(i.registry_status),
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
        (
            "decay_flagged",
            "Also shows a website timing signal",
            "Digital-decay verdict is coasting or decaying (informational: most of the queue has no "
            "check run yet)",
            lambda i: i.digital_decay_verdict in DECAY_PRIORITY,
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
                ebitda=row.ebitda,
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

    # Read-only: the latest persisted digital-decay verdict, if a check has ever been run for this company.
    # This never triggers a new website check; it just reads what app.decay / the API already wrote.
    decay: dict[str, tuple[DecayVerdict, datetime]] = {}
    for fact in session.scalars(
        select(CompanyFact)
        .join(Company)
        .where(
            Company.country == "EE",
            Company.merged_into_id.is_(None),
            CompanyFact.field_name == DECAY_SIGNAL_FIELD,
            CompanyFact.valid_to.is_(None),
        )
    ):
        value = fact.value_json
        verdict = value.get("verdict") if isinstance(value, dict) else None
        if isinstance(verdict, str) and verdict in VALID_DECAY_VERDICTS:
            decay[fact.company_id] = (cast(DecayVerdict, verdict), fact.observed_at)

    all_items: list[schemas.SellerProspectOut] = []
    for company in companies:
        rows = financials.get(company.id, [])
        signal = financial_signal(rows, min_revenue_eur=min_revenue_eur, max_revenue_eur=max_revenue_eur)
        harvesting = cash_harvesting_candidate(rows)
        status = statuses.get(company.id)
        peer_group = industry_division(company.industry_codes)
        holding = is_holding_activity(company.industry_codes)
        flags: list = []
        group_revenue = None
        if signal.latest_year is not None and files_consolidated(rows, signal.latest_year):
            flags.append("group_parent")
            reported = consolidated_revenue(rows, signal.latest_year)
            group_revenue = float(reported) if reported is not None else None
        if holding:
            flags.append("holding_activity")
        if harvesting.triggered:
            flags.append("cash_harvesting_candidate")
        issues = list(signal.issues)
        if status is None:
            issues.append("Current registry status unavailable")
        decay_verdict, decay_observed_at = decay.get(company.id, (None, None))
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
                digital_decay_verdict=decay_verdict,
                digital_decay_observed_at=decay_observed_at,
                filing_ids=signal.filing_ids,
                source_urls=signal.source_urls,
                issues=issues,
                cash_harvesting_candidate=harvesting.triggered,
                cash_harvesting_evidence_status=harvesting.evidence_status,
                latest_ebitda_margin=harvesting.latest_ebitda_margin,
                cash_harvesting_revenue_cagr=harvesting.three_year_revenue_cagr,
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
            and is_registered_status(item.registry_status)
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

    # Computed over every imported company before the sector filter narrows `all_items`, so the option
    # list and its counts describe the whole database, not just the currently selected sector.
    sector_opts = [
        schemas.SectorOptionOut(code=opt.code, label=opt.label, count=opt.count)
        for opt in sector_options([c.industry_codes for c in companies])
    ]

    if sector:
        key = sector.strip().casefold()
        if key == OTHERS_SECTOR_CODE:
            named_divisions = {opt.code for opt in sector_opts if opt.code != OTHERS_SECTOR_CODE}
            all_items = [item for item in all_items if (item.peer_group or None) not in named_divisions]
            peer_groups = []
        else:
            all_items = [item for item in all_items if item.peer_group == key]
            peer_groups = [group for group in peer_groups if group.group == key]

    def decay_priority(verdict: str | None) -> int:
        return (
            DECAY_PRIORITY.get(verdict, DECAY_DEFAULT_PRIORITY)
            if verdict is not None
            else DECAY_DEFAULT_PRIORITY
        )

    next_action_order = {"advisor_review": 0, "research": 1, "outside_size_band": 2, "exclude": 3}
    all_items.sort(
        key=lambda item: (
            next_action_order[item.next_action],
            decay_priority(item.digital_decay_verdict),
            -(item.financial_profile_index if item.financial_profile_index is not None else -99),
            item.legal_name.casefold(),
        )
    )
    review_queue = [item for item in all_items if item.next_action == "advisor_review"]
    return schemas.SellerFunnelOut(
        total_companies=len(all_items),
        core_size=sum(item.focus_band == "core" for item in all_items),
        three_year_profitable=sum(
            item.focus_band == "core"
            and item.positive_profit_years == 3
            and item.evidence_status == "complete"
            for item in all_items
        ),
        advisor_review=len(review_queue),
        advisor_review_decay_checked=sum(item.digital_decay_verdict is not None for item in review_queue),
        advisor_review_decay_flagged=sum(
            item.digital_decay_verdict in DECAY_PRIORITY for item in review_queue
        ),
        stages=_stages(all_items, min_revenue_eur, max_revenue_eur),
        peer_groups=peer_groups,
        sector_options=sector_opts,
        items=all_items[:limit],
        methodology=(
            "Annual revenue is a provisional size proxy. The funnel requires three consecutive "
            "standalone EUR fiscal years for complete financial evidence. The peer index uses "
            f"median/MAD Z-scores within two-digit EMTAK groups with at least {MIN_PEERS} peers: "
            "70% operating margin and 30% equity/assets. Holding and head-office activity codes are "
            "flagged, not ranked. Within the advisor-review queue, a company already showing a "
            "coasting or decaying digital-decay verdict (opt-in, read here, never triggered here) is "
            "listed ahead of the peer-index ranking; a missing check is never treated as a negative "
            "signal. It describes financial profile only; owner intent, buyer fit and mandate "
            f"likelihood are not assessed. The sector filter groups companies by their source-backed "
            f"two-digit EMTAK division, requiring at least {MIN_COMPANIES_PER_SECTOR} companies per "
            "listed division; smaller divisions and companies without a usable code fall into the "
            f'display-only "{OTHERS_SECTOR_CODE}" option, computed fresh on every request. '
            f"{CASH_HARVESTING_LABEL} is a separate signal from stable revenue (CAGR between "
            f"{CASH_HARVESTING_CAGR_MIN:+.0%} and {CASH_HARVESTING_CAGR_MAX:+.0%} over three years) and "
            f"a high EBITDA margin (above {CASH_HARVESTING_EBITDA_MARGIN_MIN:.0%} in the latest year); "
            "it never uses dividends or capex, which are null for every current row in this database, "
            "and a missing or incomparable input is reported as insufficient evidence rather than "
            f"guessed. {CASH_HARVESTING_EXPLANATION}"
        ),
    )
