"""EMTAK sector filter: two-digit division grouping, the 10-company minimum, and the display-only
"Others" bucket. See app.domain.sectors; companies.sector is blank in the real database, so the filter is
built entirely from source-backed industry codes instead."""

from app.domain.sectors import MIN_COMPANIES_PER_SECTOR, OTHERS_CODE, industry_division, sector_options


def test_industry_division_takes_the_first_two_digit_code() -> None:
    assert industry_division(["62011"]) == "62"
    assert industry_division(["6201"]) == "62"
    assert industry_division(["invalid", "47110"]) == "47"
    assert industry_division([]) is None
    assert industry_division(None) is None
    assert industry_division(["x"]) is None


def test_exactly_at_the_minimum_is_its_own_option() -> None:
    assert MIN_COMPANIES_PER_SECTOR == 10
    codes = [["62011"]] * MIN_COMPANIES_PER_SECTOR
    options = {opt.code: opt for opt in sector_options(codes)}
    assert options["62"].count == MIN_COMPANIES_PER_SECTOR
    assert options[OTHERS_CODE].count == 0


def test_one_below_the_minimum_folds_into_others() -> None:
    codes = [["62011"]] * (MIN_COMPANIES_PER_SECTOR - 1)
    options = {opt.code: opt for opt in sector_options(codes)}
    assert "62" not in options
    assert options[OTHERS_CODE].count == MIN_COMPANIES_PER_SECTOR - 1


def test_others_aggregates_small_divisions_and_unmapped_companies() -> None:
    codes = (
        [["62011"]] * 12  # a real, selectable division
        + [["64201"]] * 3  # a small division: folds into Others
        + [["70101"]] * 4  # another small division: folds into Others
        + [None] * 2  # no usable code at all: folds into Others
    )
    options = {opt.code: opt for opt in sector_options(codes)}
    assert set(options) == {"62", OTHERS_CODE}
    assert options["62"].count == 12
    assert options[OTHERS_CODE].count == 3 + 4 + 2

    # Original codes are read only, never rewritten: the input list is untouched.
    assert codes[12] == ["64201"]
    assert codes[-1] is None


def test_options_are_sorted_by_code_with_others_last() -> None:
    codes = [["47110"]] * 10 + [["62011"]] * 10 + [None] * 5
    options = sector_options(codes)
    assert [opt.code for opt in options] == ["47", "62", OTHERS_CODE]


def test_counts_are_recomputed_every_call_not_cached() -> None:
    codes = [["62011"]] * 9
    assert sector_options(codes)[0].code == OTHERS_CODE  # below threshold
    codes.append(["62011"])
    assert sector_options(codes)[0].code == "62"  # now at threshold: a fresh call sees the new count
