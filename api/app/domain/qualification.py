"""Headcount-based qualification. Headcount is the primary viability proxy because timely
financials are rarely public for Estonian private companies."""

from enum import StrEnum


class Qualification(StrEnum):
    QUALIFIED = "qualified"  # lower bound >= threshold (default 20)
    BORDERLINE = "borderline"  # range straddles the threshold, e.g. 10-49
    BELOW_THRESHOLD = "below_threshold"  # 3..threshold-1
    SUB_SCALE = "sub_scale"  # 1-2 person micro-entity: low priority
    UNKNOWN = "unknown_headcount"  # no approved headcount evidence; never guessed


def qualify(
    employee_min: int | None, employee_max: int | None, min_employees: int = 20, sub_scale_max: int = 2
) -> Qualification:
    if employee_min is None:
        return Qualification.UNKNOWN
    if employee_min >= min_employees:
        return Qualification.QUALIFIED
    upper = employee_max if employee_max is not None else employee_min
    if employee_max is None or upper >= min_employees:
        return Qualification.BORDERLINE
    if upper <= sub_scale_max:
        return Qualification.SUB_SCALE
    return Qualification.BELOW_THRESHOLD
