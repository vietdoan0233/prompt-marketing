"""Multi-source fact confidence scoring. Pure: takes plain evidence tuples, returns labels."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.domain.normalize import normalize_name

VERIFIED = "verified"
MULTI_SOURCE = "multi-source"
ESTIMATED = "estimated"
OLD = "old"
CONFLICTING = "conflicting"
UNKNOWN = "unknown"
MANUALLY_CORRECTED = "manually-corrected"

RANGE_FIELDS = {"employees", "revenue"}
NAME_FIELDS = {"legal_name", "trading_name"}
# Free-text labels: different wording across sources is a variant, not a material conflict.
DESCRIPTIVE_FIELDS = {"sector", "description"}


@dataclass
class Evidence:
    fact_id: str
    source_id: str
    value: Any
    base_confidence: str
    observed_at: datetime
    trust_rank: int
    is_correction: bool = False
    corrected_at: datetime | None = None


@dataclass
class Resolution:
    value: Any
    status: str  # verified | multi-source | estimated | old | conflicting | unknown | manually-corrected
    fact_confidence: dict[str, str]  # fact_id -> confidence label
    supporting_fact_ids: list[str]
    conflicting_values: list[Any]


def values_agree(field: str, a: Any, b: Any) -> bool:
    """Ranges agree when they overlap: registry bracket 20-49 and a feed's exact 35 confirm each other."""
    if field in RANGE_FIELDS and isinstance(a, dict) and isinstance(b, dict):
        a_lo, a_hi = a.get("min"), a.get("max")
        b_lo, b_hi = b.get("min"), b.get("max")
        if a_lo is None or b_lo is None:
            return False
        a_hi = a_hi if a_hi is not None else 10**12
        b_hi = b_hi if b_hi is not None else 10**12
        return a_lo <= b_hi and b_lo <= a_hi
    if field in NAME_FIELDS and isinstance(a, str) and isinstance(b, str):
        return normalize_name(a) == normalize_name(b)
    if field == "industry_code" and isinstance(a, str) and isinstance(b, str):
        return a.replace(".", "") == b.replace(".", "")
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() == b.strip().casefold()
    return a == b


def _range_width(v: Any) -> int:
    if isinstance(v, dict) and v.get("min") is not None:
        return (v.get("max") if v.get("max") is not None else 10**12) - v["min"]
    return 0


def resolve_field(field: str, evidence: list[Evidence], now: datetime, stale_after_days: int) -> Resolution:
    """Pick the display value for one field and label every supporting fact.

    - An active manual correction wins, but source facts keep their own labels.
    - Current facts (not older than the stale window) are clustered by agreement.
    - One cluster confirmed by >= 2 distinct approved sources -> multi-source.
    - One cluster from one source -> that source's base confidence (verified / estimated).
    - Two or more disagreeing clusters -> conflicting; the most authoritative source is shown, flagged.
    - Only old facts -> old.
    """
    if not evidence:
        return Resolution(None, UNKNOWN, {}, [], [])

    stale_cutoff = now - timedelta(days=stale_after_days)
    labels: dict[str, str] = {}
    corrections = sorted(
        (e for e in evidence if e.is_correction), key=lambda e: e.corrected_at or e.observed_at, reverse=True
    )
    source_facts = [e for e in evidence if not e.is_correction]
    current = [e for e in source_facts if e.observed_at >= stale_cutoff]
    for e in source_facts:
        if e.observed_at < stale_cutoff:
            labels[e.fact_id] = OLD

    clusters: list[list[Evidence]] = []
    for e in sorted(current, key=lambda e: (e.trust_rank, _range_width(e.value), -e.observed_at.timestamp())):
        for cluster in clusters:
            if values_agree(field, cluster[0].value, e.value):
                cluster.append(e)
                break
        else:
            clusters.append([e])

    status: str
    chosen: list[Evidence]
    if len(clusters) > 1 and field in DESCRIPTIVE_FIELDS:
        chosen = clusters[0]
        status = MULTI_SOURCE if len({e.source_id for e in chosen}) >= 2 else chosen[0].base_confidence
        for cluster in clusters:
            for e in cluster:
                labels[e.fact_id] = (
                    MULTI_SOURCE if (cluster is chosen and status == MULTI_SOURCE) else e.base_confidence
                )
        clusters = [chosen]
    elif len(clusters) > 1:
        status = CONFLICTING
        for cluster in clusters:
            for e in cluster:
                labels[e.fact_id] = CONFLICTING
        chosen = clusters[0]
    elif len(clusters) == 1:
        chosen = clusters[0]
        sources = {e.source_id for e in chosen}
        if len(sources) >= 2:
            status = MULTI_SOURCE
        else:
            status = VERIFIED if chosen[0].base_confidence == VERIFIED else chosen[0].base_confidence
        for e in chosen:
            labels[e.fact_id] = MULTI_SOURCE if status == MULTI_SOURCE else e.base_confidence
    else:
        # Only old evidence: show the most authoritative source's latest value, labelled old.
        best_old = min(source_facts, key=lambda e: (e.trust_rank, -e.observed_at.timestamp()), default=None)
        chosen = [best_old] if best_old else []
        status = OLD if chosen else UNKNOWN

    conflicting_values = [c[0].value for c in clusters[1:]] if len(clusters) > 1 else []

    if corrections:
        top = corrections[0]
        labels[top.fact_id] = MANUALLY_CORRECTED
        for older in corrections[1:]:
            labels[older.fact_id] = OLD
        return Resolution(top.value, MANUALLY_CORRECTED, labels, [top.fact_id], conflicting_values)

    # For ranges, show the narrowest value within the winning cluster (most specific evidence).
    display = min(chosen, key=lambda e: (_range_width(e.value), e.trust_rank)) if chosen else None
    return Resolution(
        display.value if display else None,
        status,
        labels,
        [e.fact_id for e in chosen],
        conflicting_values,
    )
