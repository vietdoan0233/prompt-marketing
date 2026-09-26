"""Sync the reviewed source registry (config/sources.yaml) into the database."""

from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Source
from app.services import audit
from app.services.permissions import APPROVED, load_config

VALID_COUNTRIES = {"FI", "SE", "NO", "DK", "IS", "DE", "AT", "CH"}
_SYNCED_FIELDS = [
    "name",
    "tier",
    "provider",
    "region",
    "countries",
    "source_type",
    "source_mode",
    "terms_url",
    "connector_type",
    "connector_config",
    "allowed_fields",
    "field_mapping",
    "base_confidence",
    "usage_policy",
    "retention_days",
    "rate_limit_per_minute",
    "trust_rank",
    "notes",
    "approval_reference",
]


def sync_sources(
    session: Session, actor: str = "system:seed", config: dict[str, Any] | None = None
) -> list[str]:
    """Idempotent. New sources are created as configured. For existing sources, metadata is refreshed;
    a config downgrade (revoked/unapproved/pending) always wins and disables the source (fail closed),
    but config never silently re-enables a source an operator disabled."""
    cfg = config or load_config()
    changed: list[str] = []
    for entry in cfg["sources"]:
        countries = entry.get("countries", [])
        bad = [c for c in countries if not isinstance(c, str) or c not in VALID_COUNTRIES]
        if bad:
            # YAML 1.1 parses an unquoted NO (Norway) as boolean false; refuse rather than mis-scope a source.
            raise ValueError(f'source {entry["id"]}: invalid country codes {bad!r} (quote "NO" in YAML)')
        data = {k: entry.get(k) for k in _SYNCED_FIELDS if k in entry}
        data.setdefault("retention_days", get_settings().default_retention_days)
        source = session.get(Source, entry["id"])
        if source is None:
            source = Source(
                id=entry["id"],
                permission_status=entry.get("permission_status", "pending"),
                enabled=bool(entry.get("enabled", False)) and entry.get("permission_status") == APPROVED,
                **data,
            )
            session.add(source)
            audit.record(
                session,
                actor=actor,
                action="source.registered",
                entity_type="source",
                entity_id=source.id,
                details={"permission_status": source.permission_status, "enabled": source.enabled},
            )
            changed.append(source.id)
            continue
        for k, v in data.items():
            setattr(source, k, v)
        cfg_status = entry.get("permission_status", "pending")
        if cfg_status != APPROVED and (source.permission_status != cfg_status or source.enabled):
            source.permission_status = cfg_status
            source.enabled = False
            audit.record(
                session,
                actor=actor,
                action="source.permission_changed",
                entity_type="source",
                entity_id=source.id,
                details={"permission_status": cfg_status, "enabled": False, "via": "config"},
            )
            changed.append(source.id)
    session.flush()
    return changed
