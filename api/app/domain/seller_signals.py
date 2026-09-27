"""Explainable financial indicators for an advisor's seller-prospect review.

These indicators describe reported companies. They do not infer owner intent,
confirm buyer interest, or assign a probability of winning a mandate.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Literal


@dataclass(frozen=True)
class AnnualFinancial:
    fiscal_year: int
    period_start: date | None
    period_end: date | None
    currency: str | None
    unit: str | None
    statement_scope: str | None
    revenue: Decimal | None
    operating_profit: Decimal | None
    total_assets: Decimal | None
    equity: Decimal | None
    filing_id: str
    source_url: str
    source_file: str | None
    review_status: str
    employees_fte: Decimal | None = None
    ebitda: Decimal | None = None


# EMTAK/NACE activity codes whose accounts describe an ownership vehicle rather than an operating business:
# 64.2x holding companies and financing conduits, 70.10 head offices. Their margins are not comparable with
# operating peers, so they are flagged instead of ranked.
HOLDING_ACTIVITY_PREFIXES = ("642", "7010")


def is_holding_activity(codes: list[str] | None) -> bool:
    for code in codes or []:
        digits = "".join(ch for ch in code if ch.isdigit())
        if digits.startswith(HOLDING_ACTIVITY_PREFIXES):
            return True
    return False


def consolidated_revenue(rows: list[AnnualFinancial], fiscal_year: int) -> Decimal | None:
    """Reported group revenue for one year, if the company filed exactly one consolidated EUR statement."""
    matches = [
        row
        for row in rows
        if row.review_status != "superseded"
        and row.fiscal_year == fiscal_year
        and row.statement_scope == "consolidated"
        and row.currency == "EUR"
        and row.unit == "EUR"
        and row.revenue is not None
    ]
    return matches[0].revenue if len(matches) == 1 else None


def files_consolidated(rows: list[AnnualFinancial], fiscal_year: int) -> bool:
    return any(
        row.review_status != "superseded"
        and row.fiscal_year == fiscal_year
        and row.statement_scope == "consolidated"
        for row in rows
    )


@dataclass
class FinancialSignal:
    focus_band: str = "unknown"
    quality_band: str = "unknown"
    evidence_status: str = "needs_data"
    latest_year: int | None = None
    latest_revenue_eur: float | None = None
    latest_operating_margin: float | None = None
    three_year_median_margin: float | None = None
    three_year_revenue_cagr: float | None = None
    positive_profit_years: int | None = None
    stable_revenue: bool | None = None
    latest_equity_ratio: float | None = None
    latest_employees_fte: float | None = None
    filing_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _comparable(row: AnnualFinancial) -> bool:
    if row.statement_scope != "standalone" or row.currency != "EUR" or row.unit != "EUR":
        return False
    if row.period_start is None or row.period_end is None:
        return False
    days = (row.period_end - row.period_start).days + 1
    return (
        330 <= days <= 400
        and row.revenue is not None
        and row.revenue > 0
        and row.operating_profit is not None
    )


def _margin(row: AnnualFinancial) -> float:
    assert row.revenue is not None and row.operating_profit is not None
    return float(row.operating_profit / row.revenue)


def financial_signal(
    rows: list[AnnualFinancial], *, min_revenue_eur: int, max_revenue_eur: int
) -> FinancialSignal:
    signal = FinancialSignal()
    current = [row for row in rows if row.review_status != "superseded"]
    if not current:
        signal.issues.append("No current annual financial rows")
        return signal

    latest_filing_year = max(row.fiscal_year for row in current)
    by_year: dict[int, list[AnnualFinancial]] = defaultdict(list)
    for row in current:
        if _comparable(row):
            by_year[row.fiscal_year].append(row)

    unique = {year: reports[0] for year, reports in by_year.items() if len(reports) == 1}
    if len(by_year.get(latest_filing_year, [])) != 1:
        signal.issues.append("Latest fiscal year lacks one unique comparable standalone EUR report")
    for year, reports in by_year.items():
        if year >= latest_filing_year - 2 and len(reports) > 1:
            signal.issues.append(f"{year} has multiple current comparable reports")

    if not unique:
        signal.issues.append("No comparable annual revenue and operating profit")
        return signal

    latest_year = max(unique)
    latest = unique[latest_year]
    signal.latest_year = latest_year
    signal.filing_ids = [f"{latest_year}:{latest.filing_id}"]
    signal.source_urls = [latest.source_url] if latest.source_url else []
    signal.latest_revenue_eur = float(latest.revenue) if latest.revenue is not None else None
    signal.latest_operating_margin = _margin(latest)
    signal.focus_band = (
        "core"
        if signal.latest_revenue_eur is not None
        and min_revenue_eur <= signal.latest_revenue_eur <= max_revenue_eur
        else "adjacent"
    )
    if latest.total_assets is not None and latest.total_assets > 0 and latest.equity is not None:
        signal.latest_equity_ratio = float(latest.equity / latest.total_assets)
    if latest.employees_fte is not None:
        signal.latest_employees_fte = float(latest.employees_fte)

    years = [latest_year - 2, latest_year - 1, latest_year]
    if not all(year in unique for year in years):
        signal.issues.append("Three consecutive comparable fiscal years unavailable")
        return signal

    recent = [unique[year] for year in years]
    signal.filing_ids = [f"{row.fiscal_year}:{row.filing_id}" for row in recent]
    signal.source_urls = sorted({row.source_url for row in recent if row.source_url})
    if any(not row.filing_id for row in recent):
        signal.issues.append("One or more report IDs unavailable")

    margins = [_margin(row) for row in recent]
    signal.three_year_median_margin = median(margins)
    first_revenue, last_revenue = recent[0].revenue, recent[-1].revenue
    assert first_revenue is not None and last_revenue is not None
    signal.three_year_revenue_cagr = float(last_revenue / first_revenue) ** 0.5 - 1
    signal.stable_revenue = -0.02 <= signal.three_year_revenue_cagr <= 0.03
    signal.positive_profit_years = sum(
        bool(row.operating_profit and row.operating_profit > 0) for row in recent
    )
    if signal.positive_profit_years == 3:
        signal.quality_band = "core"
    elif (
        signal.positive_profit_years == 2
        and latest.operating_profit is not None
        and latest.operating_profit > 0
    ):
        signal.quality_band = "borderline"
    else:
        signal.quality_band = "weak"

    if latest_year < date.today().year - 2:
        signal.issues.append("Latest comparable fiscal year is older than two years")
    if latest_year == latest_filing_year and not signal.issues:
        signal.evidence_status = "complete"
    return signal


def robust_reference(values: list[float]) -> tuple[float, float]:
    centre = median(values)
    spread = 1.4826 * median(abs(value - centre) for value in values)
    return centre, spread


def robust_z(value: float, reference: tuple[float, float]) -> float | None:
    centre, spread = reference
    if spread == 0:
        return None
    return max(-3.0, min(3.0, (value - centre) / spread))


# ------------------------------------------------------------------ Cash Harvesting candidate

CASH_HARVESTING_LABEL = "Cash Harvesting candidate"
CASH_HARVESTING_EXPLANATION = (
    "Stable, high-margin, low-growth financials over three consecutive comparable fiscal years. This "
    "does not establish that the owner is extracting cash, and it is not a claim about seller intent or "
    "preparation to sell: owner_intent stays unknown and buyer_fit stays not assessed."
)
CASH_HARVESTING_EBITDA_MARGIN_MIN = 0.15  # exclusive: EBITDA margin must be strictly above 15%
CASH_HARVESTING_CAGR_MIN = -0.02
CASH_HARVESTING_CAGR_MAX = 0.03


@dataclass
class CashHarvestingSignal:
    """A separate, explainable financial review signal. It never uses dividends or capex: those fields
    are null for every current row in this database, and even when populated they are excluded from this
    calculation by design (no payout ratio, no dividend-history comparison)."""

    triggered: bool = False
    evidence_status: Literal["insufficient_evidence", "evaluated"] = "insufficient_evidence"
    latest_year: int | None = None
    latest_ebitda_margin: float | None = None
    three_year_revenue_cagr: float | None = None
    filing_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _comparable_revenue_row(row: AnnualFinancial) -> bool:
    if row.statement_scope != "standalone" or row.currency != "EUR" or row.unit != "EUR":
        return False
    if row.period_start is None or row.period_end is None:
        return False
    days = (row.period_end - row.period_start).days + 1
    return 330 <= days <= 400 and row.revenue is not None and row.revenue > 0


def cash_harvesting_candidate(rows: list[AnnualFinancial]) -> CashHarvestingSignal:
    """Trigger only when both populated, comparable conditions hold: revenue CAGR in [-2%, +3%] across
    three consecutive comparable fiscal years, and EBITDA margin above 15% in the most recent of them.
    EBITDA comes only from the already-computed `ebitda` column (reported, or derived by the importer from
    operating profit and depreciation/impairment) -- operating-profit margin is never substituted for it.
    Any missing or incomparable input leaves the signal at "insufficient_evidence" with the reason
    recorded in `issues`; it never trigger from a missing value treated as zero."""
    signal = CashHarvestingSignal()
    current = [row for row in rows if row.review_status != "superseded"]
    if not current:
        signal.issues.append("No current annual financial rows")
        return signal

    by_year: dict[int, list[AnnualFinancial]] = defaultdict(list)
    for row in current:
        if _comparable_revenue_row(row):
            by_year[row.fiscal_year].append(row)
    unique = {year: reports[0] for year, reports in by_year.items() if len(reports) == 1}
    for year, reports in by_year.items():
        if len(reports) > 1:
            signal.issues.append(f"{year} has multiple current comparable reports")
    if not unique:
        signal.issues.append("No comparable standalone EUR revenue reported")
        return signal

    latest_year = max(unique)
    latest = unique[latest_year]
    signal.latest_year = latest_year
    if latest.ebitda is None:
        signal.issues.append(f"EBITDA is not available for FY{latest_year}, the latest comparable year")
    else:
        assert latest.revenue is not None  # guaranteed by _comparable_revenue_row
        signal.latest_ebitda_margin = float(latest.ebitda / latest.revenue)
        # Provenance for the margin alone; replaced by the three-year set below when CAGR is computable.
        signal.filing_ids = [f"{latest.fiscal_year}:{latest.filing_id}"]
        signal.source_urls = [latest.source_url] if latest.source_url else []

    years = [latest_year - 2, latest_year - 1, latest_year]
    if not all(year in unique for year in years):
        signal.issues.append("Three consecutive comparable fiscal years unavailable for revenue CAGR")
        return signal

    recent = [unique[year] for year in years]
    signal.filing_ids = [f"{row.fiscal_year}:{row.filing_id}" for row in recent]
    signal.source_urls = sorted({row.source_url for row in recent if row.source_url})
    first_revenue, last_revenue = recent[0].revenue, recent[-1].revenue
    assert first_revenue is not None and last_revenue is not None
    signal.three_year_revenue_cagr = float(last_revenue / first_revenue) ** 0.5 - 1

    if signal.latest_ebitda_margin is None:
        return signal  # insufficient_evidence: EBITDA missing, already recorded above

    signal.evidence_status = "evaluated"
    signal.triggered = (
        CASH_HARVESTING_CAGR_MIN <= signal.three_year_revenue_cagr <= CASH_HARVESTING_CAGR_MAX
        and signal.latest_ebitda_margin > CASH_HARVESTING_EBITDA_MARGIN_MIN
    )
    return signal
