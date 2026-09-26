"""Explainable duplicate scoring between two consolidated company profiles.

Strong keys (registry ID, VAT, domain) are resolved exactly at ingestion time. This module scores the
remaining *possible* duplicates so a reviewer can decide. It never merges anything by itself.
Three outcomes, Senzing-style: likely (>= 80), possible (>= 55), no-match (< 55, not stored).
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

LIKELY_THRESHOLD = 80
POSSIBLE_THRESHOLD = 55


@dataclass
class Profile:
    id: str
    normalized_name: str
    country: str
    city: str | None
    registry_keys: set[str]
    vat_keys: set[str]
    domains: set[str]
    industry_code: str | None


@dataclass
class MatchResult:
    score: int
    band: str  # likely | possible | none
    reasons: list[dict]  # {"signal", "effect", "detail"} — both why and why-not


def name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return max(SequenceMatcher(None, a, b).ratio(), jaccard)


def score_pair(a: Profile, b: Profile) -> MatchResult:
    reasons: list[dict] = []
    score = 0

    if a.country != b.country:
        return MatchResult(
            0, "none", [{"signal": "country", "effect": "block", "detail": "different countries"}]
        )

    reg_a = {k for k in a.registry_keys if k.startswith(a.country)}
    reg_b = {k for k in b.registry_keys if k.startswith(b.country)}
    if reg_a and reg_b and not (reg_a & reg_b):
        # Different national registry IDs = different legal entities (maybe a group). Link, don't merge.
        reasons.append(
            {
                "signal": "registry_id",
                "effect": "against",
                "detail": f"different registry IDs {sorted(reg_a)} vs {sorted(reg_b)}",
            }
        )
        score -= 60
    if a.vat_keys & b.vat_keys:
        reasons.append(
            {
                "signal": "vat_id",
                "effect": "for",
                "detail": f"shared VAT ID {sorted(a.vat_keys & b.vat_keys)}",
            }
        )
        score += 60
    shared_domains = a.domains & b.domains
    if shared_domains:
        reasons.append(
            {"signal": "domain", "effect": "for", "detail": f"shared website domain {sorted(shared_domains)}"}
        )
        score += 45

    sim = name_similarity(a.normalized_name, b.normalized_name)
    if sim >= 0.999:
        reasons.append(
            {"signal": "name", "effect": "for", "detail": f"identical normalized name '{a.normalized_name}'"}
        )
        score += 50
    elif sim >= 0.85:
        reasons.append(
            {
                "signal": "name",
                "effect": "for",
                "detail": f"similar names ({sim:.2f}) '{a.normalized_name}' ~ '{b.normalized_name}'",
            }
        )
        score += 35
    else:
        reasons.append({"signal": "name", "effect": "neutral", "detail": f"name similarity {sim:.2f}"})

    if a.city and b.city:
        if a.city.casefold() == b.city.casefold():
            reasons.append({"signal": "city", "effect": "for", "detail": f"same city {a.city}"})
            score += 15
        else:
            reasons.append(
                {"signal": "city", "effect": "against", "detail": f"different cities {a.city} vs {b.city}"}
            )
            score -= 10
    if a.industry_code and b.industry_code and a.industry_code[:2] == b.industry_code[:2]:
        reasons.append(
            {"signal": "industry", "effect": "for", "detail": f"same industry division {a.industry_code[:2]}"}
        )
        score += 5

    score = max(0, min(100, score))
    band = "likely" if score >= LIKELY_THRESHOLD else "possible" if score >= POSSIBLE_THRESHOLD else "none"
    return MatchResult(score, band, reasons)
