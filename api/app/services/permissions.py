"""Permission gate. Fails closed: anything not explicitly approved + enabled + in policy is denied."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.models import Source

APPROVED = "approved"
PERMISSION_STATUSES = {"approved", "pending", "revoked", "unapproved"}
SOURCE_MODES = {"public-approved", "licensed", "mergero-supplied"}
INGESTIBLE_CONNECTORS = {"csv", "brreg", "prh", "zefix", "cvr", "website"}


class PermissionDenied(Exception):
    def __init__(self, reasons: list[str], run_id: str | None = None) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.run_id = run_id


@lru_cache
def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or get_settings().source_config_path
    with open(cfg_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def region_policy() -> dict[str, dict[str, Any]]:
    return load_config()["regions"]


def ingestion_gate(source: Source | None, *, live: bool = False) -> list[str]:
    """Return the reasons ingestion is denied. Empty list == allowed."""
    if source is None:
        return ["source is not registered in the source registry"]
    reasons: list[str] = []
    if source.permission_status != APPROVED:
        reasons.append(f"source permission status is '{source.permission_status}', not 'approved'")
    if not source.enabled:
        reasons.append("source connector is disabled")
    if source.connector_type not in INGESTIBLE_CONNECTORS:
        reasons.append(f"connector type '{source.connector_type}' cannot run ingestion jobs")
    if source.source_mode not in SOURCE_MODES:
        reasons.append(f"source mode '{source.source_mode}' is not an approved mode")

    policies = region_policy()
    if source.region == "internal":
        allowed_countries = {c for p in policies.values() for c in p["countries"]}
        allowed_modes = {"mergero-supplied", "licensed"}
    elif source.region in policies:
        allowed_countries = set(policies[source.region]["countries"])
        allowed_modes = set(policies[source.region]["source_modes"])
    else:
        return reasons + [f"region '{source.region}' has no regional policy"]
    if source.source_mode not in allowed_modes:
        reasons.append(f"source mode '{source.source_mode}' is not allowed in region '{source.region}'")
    outside = sorted(set(source.countries) - allowed_countries)
    if outside:
        reasons.append(f"source coverage {outside} is outside region '{source.region}' policy")
    if live and not get_settings().live_connectors_enabled:
        reasons.append("live network connectors are disabled (LIVE_CONNECTORS_ENABLED=false)")
    return reasons


def enable_gate(source: Source) -> list[str]:
    reasons = []
    if source.permission_status != APPROVED:
        reasons.append(f"cannot enable: permission status is '{source.permission_status}'")
    if not source.allowed_fields:
        reasons.append("cannot enable: no allowed fields are configured")
    return reasons
