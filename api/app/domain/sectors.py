"""Two-digit EMTAK division grouping for the sector filter.

`companies.sector` is a free-text display field that the Estonia importer never populates, so a sector
filter built on it alone would put every company in one bucket. Instead, filter options are derived from
the source-backed EMTAK industry codes already stored on each company, grouped at the two-digit division
level (e.g. "62011" -> "62"). Original industry codes and taxonomy metadata are never changed by this
module; it only computes a read-only, always-recomputed display grouping. Option labels come from
`DIVISION_NAMES`.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

MIN_COMPANIES_PER_SECTOR = 10
OTHERS_CODE = "others"

# Static taxonomy metadata: short English NACE Rev. 2.1 division titles (EMTAK 2025 is NACE Rev. 2.1 at
# division level). Display labels only; codes and counts are never derived from it. Division 45 exists
# only in EMTAK 2008 / NACE Rev. 2.
DIVISION_NAMES: dict[str, str] = {
    "01": "Crop & animal production",
    "02": "Forestry & logging",
    "03": "Fishing & aquaculture",
    "05": "Coal & lignite mining",
    "06": "Crude petroleum & natural gas",
    "07": "Metal ore mining",
    "08": "Other mining & quarrying",
    "09": "Mining support services",
    "10": "Food products",
    "11": "Beverages",
    "12": "Tobacco products",
    "13": "Textiles",
    "14": "Wearing apparel",
    "15": "Leather products",
    "16": "Wood & cork products",
    "17": "Paper products",
    "18": "Printing & recorded media",
    "19": "Coke & refined petroleum",
    "20": "Chemicals",
    "21": "Pharmaceuticals",
    "22": "Rubber & plastic products",
    "23": "Non-metallic mineral products",
    "24": "Basic metals",
    "25": "Fabricated metal products",
    "26": "Computer, electronic & optical products",
    "27": "Electrical equipment",
    "28": "Machinery & equipment",
    "29": "Motor vehicles & trailers",
    "30": "Other transport equipment",
    "31": "Furniture",
    "32": "Other manufacturing",
    "33": "Machinery repair & installation",
    "35": "Electricity, gas, steam & air conditioning",
    "36": "Water supply",
    "37": "Sewerage",
    "38": "Waste collection & recovery",
    "39": "Remediation",
    "41": "Construction of buildings",
    "42": "Civil engineering",
    "43": "Specialised construction",
    "45": "Motor vehicle trade & repair",
    "46": "Wholesale trade",
    "47": "Retail trade",
    "49": "Land transport",
    "50": "Water transport",
    "51": "Air transport",
    "52": "Warehousing & transport support",
    "53": "Postal & courier",
    "55": "Accommodation",
    "56": "Food & beverage service",
    "58": "Publishing",
    "59": "Film, video, TV & music production",
    "60": "Programming, broadcasting & news agencies",
    "61": "Telecommunications",
    "62": "Computer programming & consultancy",
    "63": "Computing infrastructure, hosting & information services",
    "64": "Financial services",
    "65": "Insurance & pension funding",
    "66": "Auxiliary financial services",
    "68": "Real estate",
    "69": "Legal & accounting",
    "70": "Head offices & management consultancy",
    "71": "Architecture & engineering",
    "72": "Scientific R&D",
    "73": "Advertising & market research",
    "74": "Other professional & technical services",
    "75": "Veterinary activities",
    "77": "Rental & leasing",
    "78": "Employment activities",
    "79": "Travel agencies & tour operators",
    "80": "Security & investigation",
    "81": "Building & landscape services",
    "82": "Office & business support",
    "84": "Public administration & defence",
    "85": "Education",
    "86": "Human health",
    "87": "Residential care",
    "88": "Social work",
    "90": "Arts & performing arts",
    "91": "Libraries, archives & museums",
    "92": "Gambling & betting",
    "93": "Sports, amusement & recreation",
    "94": "Membership organisations",
    "95": "Repair of computers, household goods & vehicles",
    "96": "Personal services",
    "97": "Households as employers",
    "98": "Households' own-use production",
    "99": "Extraterritorial organisations",
}


def industry_division(industry_codes: list[str] | None) -> str | None:
    """The first two-digit EMTAK division found in `industry_codes` (e.g. "62011" -> "62"), or None when
    no code yields a usable two-digit division."""
    for code in industry_codes or []:
        digits = re.search(r"\d{2}", code)
        if digits:
            return digits.group()
    return None


def division_label(division: str | None) -> str | None:
    """Display label for a two-digit EMTAK division, e.g. "62" -> "62 · Computer programming & consultancy";
    an unknown code falls back to "EMTAK 62". None stays None."""
    if division is None:
        return None
    name = DIVISION_NAMES.get(division)
    return f"{division} · {name}" if name else f"EMTAK {division}"


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
        SectorOption(code=code, label=division_label(code) or f"EMTAK {code}", count=count)
        for code, count in sorted(counts.items())
        if count >= MIN_COMPANIES_PER_SECTOR
    ]
    others = unmapped + sum(count for count in counts.values() if count < MIN_COMPANIES_PER_SECTOR)
    options.append(SectorOption(code=OTHERS_CODE, label="Others", count=others))
    return options
