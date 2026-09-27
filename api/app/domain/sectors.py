"""Two-digit EMTAK division grouping for the sector filter.

`companies.sector` is a free-text display field that the Estonia importer never populates, so a sector
filter built on it alone would put every company in one bucket. Instead, filter options are derived from
the source-backed EMTAK industry codes already stored on each company, grouped at the two-digit division
level (e.g. "62011" -> "62"). Original industry codes and taxonomy metadata are never changed by this
module; it only computes a read-only, always-recomputed display grouping.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

MIN_COMPANIES_PER_SECTOR = 10
OTHERS_CODE = "others"


def industry_division(industry_codes: list[str] | None) -> str | None:
    """The first two-digit EMTAK division found in `industry_codes` (e.g. "62011" -> "62"), or None when
    no code yields a usable two-digit division."""
    for code in industry_codes or []:
        digits = re.search(r"\d{2}", code)
        if digits:
            return digits.group()
    return None


@dataclass(frozen=True)
class SectorOption:
    code: str
    label: str
    count: int


def sector_options(industry_codes_by_company: Sequence[list[str] | None]) -> list[SectorOption]:
    """One option per EMTAK division with at least `MIN_COMPANIES_PER_SECTOR` companies (sorted by code),
    plus a trailing display-only "Others" option that aggregates every smaller division and every company
    without a usable code. Counts are computed fresh from `industry_codes_by_company` on every call."""
    counts: Counter[str] = Counter()
    unmapped = 0
    for codes in industry_codes_by_company:
        division = industry_division(codes)
        if division is None:
            unmapped += 1
        else:
            counts[division] += 1
    options = [
        SectorOption(code=code, label=f"EMTAK {code}", count=count)
        for code, count in sorted(counts.items())
        if count >= MIN_COMPANIES_PER_SECTOR
    ]
    others = unmapped + sum(count for count in counts.values() if count < MIN_COMPANIES_PER_SECTOR)
    options.append(SectorOption(code=OTHERS_CODE, label="Others", count=others))
    return options
