"""Cash Harvesting candidate: an explainable financial review signal from populated, comparable revenue
CAGR and EBITDA margin only. Never derived from dividends or capex, never a substitute for operating-profit
margin, and never triggered from a missing or incomparable measure."""

from datetime import date
from decimal import Decimal

from app.domain.seller_signals import (
    CASH_HARVESTING_CAGR_MAX,
    CASH_HARVESTING_CAGR_MIN,
    CASH_HARVESTING_LABEL,
    AnnualFinancial,
    cash_harvesting_candidate,
)

YEARS = (2021, 2022, 2023)


def _row(
    year: int, revenue: str, ebitda: str | None, operating_profit: str | None = None, **kw
) -> AnnualFinancial:
    values: dict = {
        "fiscal_year": year,
        "period_start": date(year, 1, 1),
        "period_end": date(year, 12, 31),
        "currency": "EUR",
        "unit": "EUR",
        "statement_scope": "standalone",
        "revenue": Decimal(revenue),
        "operating_profit": Decimal(operating_profit) if operating_profit is not None else None,
        "total_assets": None,
        "equity": None,
        "filing_id": f"R{year}",
        "source_url": "https://avaandmed.ariregister.rik.ee/sites/default/files/4.x.zip",
        "source_file": "4.x.zip",
        "review_status": "unreviewed",
        "ebitda": Decimal(ebitda) if ebitda is not None else None,
    }
    values.update(kw)
    return AnnualFinancial(**values)


def _flat_rows(ebitda_margin: float, cagr: float) -> list[AnnualFinancial]:
    """Three consecutive years growing at exactly `cagr` a year, with EBITDA at `ebitda_margin` of
    revenue in the latest year only (the other years' EBITDA is irrelevant to the signal)."""
    base = 10_000_000.0
    revenues = [base, base * (1 + cagr), base * (1 + cagr) ** 2]
    rows = []
    for i, year in enumerate(YEARS):
        ebitda = str(round(revenues[i] * ebitda_margin)) if i == len(YEARS) - 1 else None
        rows.append(_row(year, str(round(revenues[i])), ebitda))
    return rows


def test_triggers_when_both_conditions_are_met() -> None:
    signal = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.20, cagr=0.0))
    assert signal.triggered is True
    assert signal.evidence_status == "evaluated"
    assert signal.latest_year == YEARS[-1]
    assert signal.issues == []


def test_ebitda_margin_boundary_is_exclusive() -> None:
    exactly_15 = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.15, cagr=0.0))
    assert exactly_15.evidence_status == "evaluated"
    assert exactly_15.triggered is False  # must be strictly above 15%, not >=

    just_above = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.150001, cagr=0.0))
    assert just_above.triggered is True


def test_cagr_boundaries_are_inclusive() -> None:
    assert CASH_HARVESTING_CAGR_MIN == -0.02
    assert CASH_HARVESTING_CAGR_MAX == 0.03

    # Just inside each bound (a hair off the literal float boundary, which is not itself a stable target
    # once run back through the CAGR formula's square root): both must trigger.
    just_inside_low = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.20, cagr=-0.0199))
    assert just_inside_low.three_year_revenue_cagr is not None
    assert just_inside_low.three_year_revenue_cagr >= CASH_HARVESTING_CAGR_MIN
    assert just_inside_low.triggered is True

    just_inside_high = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.20, cagr=0.0299))
    assert just_inside_high.three_year_revenue_cagr is not None
    assert just_inside_high.three_year_revenue_cagr <= CASH_HARVESTING_CAGR_MAX
    assert just_inside_high.triggered is True

    # Clearly outside each bound: neither triggers.
    below_low = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.20, cagr=-0.03))
    assert below_low.triggered is False

    above_high = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.20, cagr=0.05))
    assert above_high.triggered is False


def test_high_growth_does_not_trigger_even_with_a_strong_margin() -> None:
    signal = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.30, cagr=0.15))
    assert signal.evidence_status == "evaluated"
    assert signal.triggered is False


def test_missing_ebitda_is_insufficient_evidence_not_a_trigger_or_a_zero() -> None:
    rows = [_row(year, "10000000", None) for year in YEARS]
    signal = cash_harvesting_candidate(rows)
    assert signal.triggered is False
    assert signal.evidence_status == "insufficient_evidence"
    assert signal.latest_ebitda_margin is None
    assert any("EBITDA is not available" in issue for issue in signal.issues)
    # Revenue CAGR is still reported when it can be computed, even though the signal cannot evaluate.
    assert signal.three_year_revenue_cagr == 0.0


def test_operating_profit_margin_is_never_substituted_for_ebitda_margin() -> None:
    """A row with a strong operating margin but no EBITDA must not trigger the signal."""
    rows = [_row(year, "10000000", None, operating_profit="3000000") for year in YEARS]
    signal = cash_harvesting_candidate(rows)
    assert signal.triggered is False
    assert signal.evidence_status == "insufficient_evidence"
    assert signal.latest_ebitda_margin is None


def test_missing_middle_year_blocks_the_cagr_even_with_good_ebitda() -> None:
    rows = [_row(YEARS[0], "10000000", "2000000"), _row(YEARS[2], "10000000", "2000000")]
    signal = cash_harvesting_candidate(rows)
    assert signal.triggered is False
    assert signal.evidence_status == "insufficient_evidence"
    assert any("Three consecutive comparable fiscal years" in issue for issue in signal.issues)


def test_non_eur_or_non_standalone_rows_are_not_comparable() -> None:
    rows = [_row(year, "10000000", "2000000", statement_scope="consolidated") for year in YEARS]
    signal = cash_harvesting_candidate(rows)
    assert signal.evidence_status == "insufficient_evidence"
    assert signal.latest_year is None


def test_no_current_rows_is_insufficient_evidence() -> None:
    signal = cash_harvesting_candidate([])
    assert signal.triggered is False
    assert signal.evidence_status == "insufficient_evidence"
    assert signal.issues == ["No current annual financial rows"]


def test_signal_never_reads_dividends_or_capex() -> None:
    """AnnualFinancial and CashHarvestingSignal carry no dividends/capex field at all: the signal is
    structurally unable to read either one, regardless of what a row's underlying database row holds."""
    assert not hasattr(AnnualFinancial, "dividends")
    assert not hasattr(AnnualFinancial, "capex")
    signal = cash_harvesting_candidate(_flat_rows(ebitda_margin=0.20, cagr=0.0))
    assert not hasattr(signal, "dividends")
    assert not hasattr(signal, "capex")
    assert not hasattr(signal, "payout_ratio")


def test_label_text_is_stable() -> None:
    assert CASH_HARVESTING_LABEL == "Cash Harvesting candidate"
