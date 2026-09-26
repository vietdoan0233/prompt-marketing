"""Reporting-period metadata shared by financial ingestion and API consumers."""

from datetime import date


def reporting_period_metadata(
    period_start: date | None, period_end: date | None
) -> tuple[int | None, str | None]:
    """Return inclusive day count and a comparability class without annualizing values.

    A 365- or 366-day inclusive period is treated as a standard twelve-month filing period. This handles
    leap years while keeping short and long periods visible to consumers.
    """
    if period_start is None or period_end is None:
        return None, None
    days = (period_end - period_start).days + 1
    if days <= 0:
        return days, "invalid"
    if days < 365:
        return days, "short"
    if days <= 366:
        return days, "standard_12_month"
    return days, "long"
