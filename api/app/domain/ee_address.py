"""Registered address (asukoht) parsing for the Estonian e-Business Register basic-data file.

The register publishes the *registered seat*, not an operating location, so the concept is always called
"registered address".

`asukoha_ehak_tekstina` lists administrative units from smallest to largest with a variable number of
parts, e.g.:

    "Pirita linnaosa, Tallinn, Harju maakond"      district, city/municipality, county
    "Tartu linn, Tartu linn, Tartu maakond"         town (settlement), town (municipality), county
    "Võhma linn, Põhja-Sakala vald, Viljandi maakond"
    "Äigrumäe küla, Viimsi vald, Harju maakond"     village, rural municipality, county
    "Rakvere linn, Lääne-Viru maakond"

Each part is classified by what it is (its unit suffix), never by its position. A field that cannot be
identified unambiguously stays None and a warning is returned; nothing is guessed.
"""

from dataclasses import dataclass, field

ADDRESS_PARSER_VERSION = "ee-address-2026.09.4"

# Source columns preserved verbatim in provenance.
ADDRESS_SOURCE_COLUMNS = (
    "ettevotja_aadress",
    "asukoht_ettevotja_aadressis",
    "asukoha_ehak_kood",
    "asukoha_ehak_tekstina",
    "indeks_ettevotja_aadressis",
    "ads_adr_id",
    "ads_ads_oid",
    "ads_normaliseeritud_taisaadress",
    "teabesysteemi_link",
)


@dataclass
class RegisteredAddressParts:
    address_line: str | None = None
    postal_code: str | None = None
    city: str | None = None
    municipality: str | None = None
    county: str | None = None
    ehak_code: str | None = None
    country: str | None = "EE"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "address_line": self.address_line,
            "postal_code": self.postal_code,
            "city": self.city,
            "municipality": self.municipality,
            "county": self.county,
            "ehak_code": self.ehak_code,
            "country": self.country,
        }


def classify(part: str) -> str:
    """Unit type of one EHAK text part."""
    if part.endswith(" maakond"):
        return "county"
    if part.endswith(" vald"):
        return "rural_municipality"
    if part == "Tallinn":
        return "tallinn"  # Tallinn is both a city and a municipality
    if part.endswith(" linnaosa"):
        return "city_district"
    if part.endswith(" linn"):
        return "town"
    if part.endswith((" alevik", " alev")):
        return "borough"
    if part.split(" / ", 1)[0].endswith("küla"):
        return "village"
    return "unknown"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def parse_registered_address(row: dict[str, str]) -> RegisteredAddressParts:
    out = RegisteredAddressParts(
        address_line=_clean(row.get("asukoht_ettevotja_aadressis")),
        postal_code=_clean(row.get("indeks_ettevotja_aadressis")),
        ehak_code=_clean(row.get("asukoha_ehak_kood")),
    )
    if _clean(row.get("ettevotja_aadress")):
        # This field is empty in the current official file. It may contain a composite address in a future
        # file, so keep it as evidence but do not silently substitute it for the source's street-line field.
        out.warnings.append("ettevotja_aadress is populated; preserved verbatim for review")

    text = _clean(row.get("asukoha_ehak_tekstina"))
    if not text:
        if not out.address_line:
            out.warnings.append("registered address missing in source")
        else:
            out.warnings.append("EHAK administrative units missing; city/municipality/county left empty")
        return out

    parts = [p.strip() for p in text.split(",") if p.strip()]
    typed = [(classify(p), p) for p in parts]
    by_type: dict[str, list[str]] = {}
    for t, p in typed:
        by_type.setdefault(t, []).append(p)

    unknown = by_type.get("unknown", [])
    if unknown:
        out.warnings.append(f"unrecognised administrative unit(s) {unknown}; not mapped")

    counties = by_type.get("county", [])
    if len(counties) == 1:
        out.county = counties[0]
    elif len(counties) > 1:
        out.warnings.append(f"several counties {counties}; county left empty")
    else:
        out.warnings.append("no county in EHAK text; county left empty")

    rural = list(dict.fromkeys(by_type.get("rural_municipality", [])))
    towns = by_type.get("town", [])
    tallinn = list(dict.fromkeys(by_type.get("tallinn", [])))
    distinct_towns = list(dict.fromkeys(towns))

    # Municipality = the local-government unit.
    if len(rural) == 1 and not tallinn:
        out.municipality = rural[0]
    elif len(tallinn) == 1 and not rural and not distinct_towns:
        out.municipality = "Tallinn"
    elif not rural and not tallinn and len(distinct_towns) == 1:
        out.municipality = distinct_towns[0]  # a town that is its own municipality (e.g. "Rakvere linn")
    else:
        out.warnings.append(f"local-government unit ambiguous in '{text}'; municipality left empty")

    # City only when the address is unambiguously inside a town/city.
    settlement_types = {t for t, _ in typed if t in ("village", "borough")}
    if len(tallinn) == 1 and not rural and not distinct_towns and not settlement_types:
        out.city = "Tallinn"
    elif settlement_types:
        out.city = (
            None  # a village/borough, even when its municipality is a town (e.g. "Pihva küla, Tartu linn")
        )
    elif len(distinct_towns) == 1 and not tallinn:
        out.city = distinct_towns[0].removesuffix(" linn")
    elif tallinn or distinct_towns:
        out.warnings.append(f"city ambiguous in '{text}'; city left empty")
    if not out.address_line:
        out.warnings.append("street address missing in source")
    return out
