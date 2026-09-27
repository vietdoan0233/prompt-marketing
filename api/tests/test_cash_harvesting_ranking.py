"""Cash Harvesting's EBITDA-margin condition: sector-aware percentile ranking, the all-sectors flat
universe, the "others" absolute fallback, and the insufficient-evidence cases. Revenue CAGR is unchanged
and tested in test_cash_harvesting.py; this file covers app.services.seller_funnel's combination of the
two into the final candidate decision."""

from app.domain.seller_signals import (
    CASH_HARVESTING_EBITDA_MARGIN_MIN,
    CASH_HARVESTING_EBITDA_PERCENTILE,
    CASH_HARVESTING_MIN_PEERS,
    percentile,
)
from app.schemas import SellerProspectOut
from app.services.seller_funnel import _cash_harvesting_context, _resolve_cash_harvesting


def _item(
    *,
    peer_group: str | None,
    margin: float | None,
    cagr: float | None = 0.0,
    evidence_status: str = "evaluated",
) -> SellerProspectOut:
    return SellerProspectOut(
        company_id="c",
        legal_name="Test OÜ",
        registry_id=None,
        registry_status=None,
        sector=None,
        peer_group=peer_group,
        focus_band="core",
        quality_band="core",
        evidence_status="complete",
        next_action="advisor_review",
        latest_year=2024,
        latest_revenue_eur=10_000_000.0,
        latest_operating_margin=0.1,
        three_year_median_margin=0.1,
        three_year_revenue_cagr=0.0,
        stable_revenue=True,
        positive_profit_years=3,
        latest_equity_ratio=0.5,
        filing_ids=[],
        source_urls=[],
        issues=[],
        latest_ebitda_margin=margin,
        cash_harvesting_revenue_cagr=cagr,
        cash_harvesting_evidence_status=evidence_status,
    )


# ------------------------------------------------------------------ percentile()


def test_percentile_matches_hand_computed_values() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 1.0) == 10.0
    assert percentile(values, 0.5) == 5.5
    # Linear interpolation at the 75th percentile of 10 ranked points: rank = 0.75 * 9 = 6.75.
    assert percentile(values, 0.75) == 7.75


def test_percentile_single_value() -> None:
    assert percentile([42.0], 0.75) == 42.0


# ------------------------------------------------------------------ _cash_harvesting_context()


def test_context_is_others_with_no_pool_when_sector_is_others() -> None:
    items = [_item(peer_group="62", margin=0.2)]
    mode, pool = _cash_harvesting_context(items, "others")
    assert mode == "others"
    assert pool == []


def test_context_is_all_sectors_flat_pool_when_no_sector_selected() -> None:
    items = [
        _item(peer_group="62", margin=0.10),
        _item(peer_group="47", margin=0.30),
        _item(peer_group=None, margin=0.50),  # no sector info: excluded from the pool
        _item(peer_group="62", margin=None),  # no margin: excluded from the pool
    ]
    mode, pool = _cash_harvesting_context(items, None)
    assert mode == "all_sectors"
    assert pool == sorted([0.10, 0.30])


def test_context_is_division_scoped_when_a_sector_is_selected() -> None:
    items = [_item(peer_group="62", margin=0.10), _item(peer_group="62", margin=0.30)]
    mode, pool = _cash_harvesting_context(items, "62")
    assert mode == "division"
    assert pool == [0.10, 0.30]


# ------------------------------------------------------------------ _resolve_cash_harvesting()


def _pool_of(n: int, top_margin: float) -> list[float]:
    """`n` margins evenly spread from 0.01 up to `top_margin`, so the exact P75 value is predictable."""
    return sorted(top_margin * (i + 1) / n for i in range(n))


def test_division_mode_triggers_at_or_above_the_percentile_with_enough_peers() -> None:
    pool = _pool_of(CASH_HARVESTING_MIN_PEERS, 0.40)
    threshold = percentile(pool, CASH_HARVESTING_EBITDA_PERCENTILE)

    above = _item(peer_group="62", margin=threshold + 0.001)
    _resolve_cash_harvesting(above, mode="division", pool=pool)
    assert above.cash_harvesting_margin_basis == "division"
    assert above.cash_harvesting_margin_percentile == CASH_HARVESTING_EBITDA_PERCENTILE
    assert above.cash_harvesting_peer_count == len(pool)
    assert above.cash_harvesting_evidence_status == "evaluated"
    assert above.cash_harvesting_candidate is True
    assert "cash_harvesting_candidate" in above.flags

    at_threshold = _item(peer_group="62", margin=threshold)
    _resolve_cash_harvesting(at_threshold, mode="division", pool=pool)
    assert at_threshold.cash_harvesting_candidate is True  # inclusive: "at or above"

    below = _item(peer_group="62", margin=threshold - 0.001)
    _resolve_cash_harvesting(below, mode="division", pool=pool)
    assert below.cash_harvesting_candidate is False
    assert below.cash_harvesting_evidence_status == "evaluated"  # evaluated, just doesn't qualify


def test_margin_must_be_positive_even_if_above_a_negative_percentile_threshold() -> None:
    pool = sorted([-0.30, -0.20, -0.10, -0.05, -0.01] * 2)  # 10 peers, all negative margins
    threshold = percentile(pool, CASH_HARVESTING_EBITDA_PERCENTILE)
    assert threshold < 0
    item = _item(peer_group="62", margin=-0.001)  # above threshold, but still negative
    _resolve_cash_harvesting(item, mode="division", pool=pool)
    assert item.cash_harvesting_margin_threshold == threshold
    assert item.cash_harvesting_candidate is False  # positive margin is a hard requirement


def test_all_sectors_mode_uses_the_shared_flat_pool() -> None:
    pool = _pool_of(20, 0.40)
    threshold = percentile(pool, CASH_HARVESTING_EBITDA_PERCENTILE)
    item = _item(peer_group="62", margin=threshold + 0.01)
    _resolve_cash_harvesting(item, mode="all_sectors", pool=pool)
    assert item.cash_harvesting_margin_basis == "all_sectors"
    assert item.cash_harvesting_peer_count == 20
    assert item.cash_harvesting_candidate is True


def test_others_mode_uses_the_fixed_absolute_threshold_strictly() -> None:
    at_threshold = _item(peer_group=None, margin=CASH_HARVESTING_EBITDA_MARGIN_MIN)
    _resolve_cash_harvesting(at_threshold, mode="others", pool=[])
    assert at_threshold.cash_harvesting_margin_basis == "absolute"
    assert at_threshold.cash_harvesting_margin_percentile is None
    assert at_threshold.cash_harvesting_peer_count is None
    assert at_threshold.cash_harvesting_candidate is False  # exclusive: must be strictly above

    above = _item(peer_group=None, margin=CASH_HARVESTING_EBITDA_MARGIN_MIN + 0.0001)
    _resolve_cash_harvesting(above, mode="others", pool=[])
    assert above.cash_harvesting_candidate is True


def test_others_mode_does_not_require_sector_information() -> None:
    """The "others" bucket exists precisely for companies without a reliable sector group."""
    item = _item(peer_group=None, margin=0.20)
    _resolve_cash_harvesting(item, mode="others", pool=[])
    assert item.cash_harvesting_margin_basis == "absolute"
    assert item.cash_harvesting_candidate is True


def test_missing_sector_information_is_insufficient_evidence_outside_others() -> None:
    item = _item(peer_group=None, margin=0.90)  # a very strong margin, but no sector to rank it against
    _resolve_cash_harvesting(item, mode="all_sectors", pool=[0.1] * 50)
    assert item.cash_harvesting_margin_basis == "insufficient_evidence"
    assert item.cash_harvesting_margin_threshold is None
    assert item.cash_harvesting_peer_count is None
    assert item.cash_harvesting_evidence_status == "insufficient_evidence"
    assert item.cash_harvesting_candidate is False


def test_too_few_peers_is_insufficient_evidence_but_reports_the_actual_count() -> None:
    small_pool = [0.10, 0.20, 0.30]  # fewer than CASH_HARVESTING_MIN_PEERS
    item = _item(peer_group="07", margin=0.90)
    _resolve_cash_harvesting(item, mode="division", pool=small_pool)
    assert item.cash_harvesting_margin_basis == "insufficient_evidence"
    assert item.cash_harvesting_margin_threshold is None
    assert item.cash_harvesting_margin_percentile is None
    assert item.cash_harvesting_peer_count == len(small_pool)  # visible, not hidden
    assert item.cash_harvesting_candidate is False


def test_missing_own_margin_or_cagr_out_of_range_never_triggers_even_with_a_qualifying_pool() -> None:
    pool = _pool_of(CASH_HARVESTING_MIN_PEERS, 0.40)

    no_margin = _item(peer_group="62", margin=None)
    _resolve_cash_harvesting(no_margin, mode="division", pool=pool)
    assert no_margin.cash_harvesting_candidate is False

    cagr_out_of_range = _item(peer_group="62", margin=0.39, cagr=0.10)
    _resolve_cash_harvesting(cagr_out_of_range, mode="division", pool=pool)
    assert cagr_out_of_range.cash_harvesting_candidate is False


def test_flags_are_kept_in_sync_with_the_final_decision() -> None:
    pool = _pool_of(CASH_HARVESTING_MIN_PEERS, 0.40)
    threshold = percentile(pool, CASH_HARVESTING_EBITDA_PERCENTILE)
    item = _item(peer_group="62", margin=threshold + 0.01)
    item.flags = ["holding_activity"]
    _resolve_cash_harvesting(item, mode="division", pool=pool)
    assert set(item.flags) == {"holding_activity", "cash_harvesting_candidate"}

    item.latest_ebitda_margin = threshold - 0.01  # now falls below the threshold
    _resolve_cash_harvesting(item, mode="division", pool=pool)
    assert item.flags == ["holding_activity"]
