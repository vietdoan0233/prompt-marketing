"""Personal contact data: source-backed only, never guessed, erasable (GDPR Art. 17)."""

import json

from sqlalchemy import select

from app.models import AuditEvent, Contact, ContactSuppression, IngestionRun
from tests.conftest import import_fixture


def _liisa(session):
    return session.scalars(select(Contact).where(Contact.name == "Liisa Kuusisto")).one()


def test_contacts_are_source_backed_and_emails_never_guessed(session):
    import_fixture(session, "mergero-csv", "mergero_companies.csv")
    import_fixture(session, "de-handelsregister", "handelsregister_export.csv")
    mikko = session.scalars(select(Contact).where(Contact.name == "Mikko Laine")).one()
    assert mikko.email is None and mikko.phone is None  # source supplied none; nothing is constructed
    markus = session.scalars(select(Contact).where(Contact.name == "Markus Brenner")).one()
    assert markus.email is None
    for c in session.scalars(select(Contact)):
        assert c.source_id and c.contact_basis and c.usage_policy and c.last_verified_at


def test_gdpr_erasure(client, session):
    import_fixture(session, "mergero-csv", "mergero_companies.csv")
    liisa = _liisa(session)
    contact_id, company_id = liisa.id, liisa.company_id

    r = client.delete(f"/contacts/{contact_id}", params={"request_reference": "DSR-2026-001"})
    assert r.status_code == 200 and r.json()["erased"] is True
    assert session.get(Contact, contact_id) is None
    assert client.delete(f"/contacts/{contact_id}").status_code == 404

    event = session.scalars(select(AuditEvent).where(AuditEvent.action == "contact.erased")).one()
    blob = json.dumps(event.details)
    assert "Liisa" not in blob and "kuusisto-analytics" not in blob and "4567" not in blob
    assert event.details["request_reference"] == "DSR-2026-001" and event.company_id == company_id

    # Retained raw input for retry is redacted.
    run = session.scalars(select(IngestionRun).where(IngestionRun.source_id == "mergero-csv")).one()
    assert "Liisa Kuusisto" not in run.input_text and "liisa.kuusisto@" not in run.input_text

    # Re-importing the original file does not resurrect the person.
    rerun = import_fixture(session, "mergero-csv", "mergero_companies.csv")
    assert rerun.counts["contacts_suppressed"] == 1
    assert session.scalars(select(Contact).where(Contact.name == "Liisa Kuusisto")).all() == []
    assert session.scalars(select(ContactSuppression)).all()


def test_erasure_does_not_touch_company_facts(client, session):
    import_fixture(session, "mergero-csv", "mergero_companies.csv")
    liisa = _liisa(session)
    before = client.get(f"/companies/{liisa.company_id}").json()["facts"]
    client.delete(f"/contacts/{liisa.id}")
    after = client.get(f"/companies/{liisa.company_id}").json()
    assert after["facts"] == before and after["contacts"] == []
