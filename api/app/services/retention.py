"""Retention: expire raw snapshot payloads and raw run inputs per each source's retention policy."""

from datetime import datetime

from sqlalchemy import and_, not_, select
from sqlalchemy.orm import Session

from app.models import IngestionRun, SourceSnapshot, utcnow
from app.services import audit


def expire_raw_data(session: Session, *, actor: str, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    snapshots = session.scalars(
        select(SourceSnapshot).where(
            SourceSnapshot.expired_at.is_(None),
            SourceSnapshot.expires_at <= now,
            # The official company snapshot is the only copy of the nine original registered-address
            # columns. Keep that source evidence for as long as its relational address may be reviewed.
            not_(
                and_(
                    SourceSnapshot.source_id == "ee-ariregister",
                    SourceSnapshot.source_key.like("company:%"),
                )
            ),
        )
    ).all()
    for snap in snapshots:
        # Keep the reproducibility metadata (hash, parser version, URL); drop the raw payload.
        snap.raw_payload = None
        snap.expired_at = now
    runs = session.scalars(
        select(IngestionRun).where(IngestionRun.input_text.is_not(None), IngestionRun.input_expires_at <= now)
    ).all()
    for run in runs:
        run.input_text = None
    result = {"snapshots_expired": len(snapshots), "run_inputs_expired": len(runs)}
    audit.record(session, actor=actor, action="retention.expired", entity_type="retention", details=result)
    session.commit()
    return result
