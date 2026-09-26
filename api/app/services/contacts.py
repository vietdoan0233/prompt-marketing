"""GDPR erasure for personal contact data."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, ContactSuppression, IngestionRun
from app.services import audit
from app.services.ingestion import contact_identity_hash, email_hash


def erase_contact(
    session: Session, contact: Contact, *, actor: str, request_reference: str | None, reason: str
) -> dict:
    """Hard-delete the contact, redact it from retained raw run inputs, and store one-way suppression
    hashes so future imports of the same person are skipped. The audit event holds no personal data."""
    company = contact.company
    hashes = {contact_identity_hash(contact.name, company)}
    if contact.email:
        hashes.add(email_hash(contact.email))
    for h in hashes:
        if not session.scalar(select(ContactSuppression).where(ContactSuppression.identity_hash == h)):
            session.add(ContactSuppression(identity_hash=h, request_reference=request_reference))

    # Snapshots never hold contact fields; raw CSV inputs retained for retry might.
    personal_values = [v for v in (contact.email, contact.phone, contact.name, contact.profile_url) if v]
    redacted_runs = 0
    for run in session.scalars(
        select(IngestionRun).where(
            IngestionRun.source_id == contact.source_id, IngestionRun.input_text.is_not(None)
        )
    ):
        text = run.input_text or ""
        new_text = text
        for v in personal_values:
            new_text = new_text.replace(v, "[erased]")
        if new_text != text:
            run.input_text = new_text
            redacted_runs += 1

    contact_id, company_id = contact.id, contact.company_id
    session.delete(contact)
    audit.record(
        session,
        actor=actor,
        action="contact.erased",
        entity_type="contact",
        entity_id=contact_id,
        company_id=company_id,
        details={
            "basis": "GDPR Art. 17 erasure request",
            "request_reference": request_reference,
            "reason": reason,
            "suppression_hashes": len(hashes),
            "redacted_run_inputs": redacted_runs,
        },
    )
    session.commit()
    return {
        "contact_id": contact_id,
        "erased": True,
        "suppressions": len(hashes),
        "redacted_run_inputs": redacted_runs,
    }
