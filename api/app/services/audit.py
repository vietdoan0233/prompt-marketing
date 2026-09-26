from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    company_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event. Callers must never put personal contact data into `details`."""
    event = AuditEvent(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        company_id=company_id,
        details=details or {},
    )
    session.add(event)
    return event
