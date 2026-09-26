"""Manual corrections preserve the original evidence and are fully audited."""

from sqlalchemy import select

from app.models import AuditEvent, Company, CompanyFact
from tests.conftest import seed_all


def _revontuli(session):
    return session.scalars(select(Company).where(Company.legal_name == "Revontuli Systems")).one()


def test_correction_preserves_original_facts(client, session):
    seed_all(session)
    company = _revontuli(session)
    originals = {
        f.id: (f.value_json, f.source_id, f.source_url, f.observed_at)
        for f in session.scalars(select(CompanyFact).where(CompanyFact.company_id == company.id))
    }
    conflict = next(
        f for f in client.get(f"/companies/{company.id}").json()["fields"] if f["field_name"] == "employees"
    )
    assert conflict["status"] == "conflicting"

    r = client.post(
        f"/companies/{company.id}/facts/corrections",
        json={
            "field_name": "employees",
            "value": "24",
            "reason": "Confirmed in 2025 annual report",
            "evidence_url": "https://example.test/annual-report",
        },
        headers={"X-Actor": "analyst@mergero.test"},
    )
    assert r.status_code == 201, r.text
    detail = r.json()
    field = next(f for f in detail["fields"] if f["field_name"] == "employees")
    assert field["value"] == {"min": 24, "max": 24}
    assert field["label"] == "manually-corrected"
    assert detail["company"]["estimated_employee_min"] == 24

    for f in session.scalars(select(CompanyFact).where(CompanyFact.id.in_(list(originals)))):
        assert (f.value_json, f.source_id, f.source_url, f.observed_at) == originals[f.id]
    corrected_sources = session.scalars(
        select(CompanyFact).where(
            CompanyFact.company_id == company.id,
            CompanyFact.field_name == "employees",
            CompanyFact.is_correction.is_(False),
        )
    ).all()
    assert {f.review_status for f in corrected_sources} == {"corrected"}

    event = session.scalars(select(AuditEvent).where(AuditEvent.action == "fact.corrected")).one()
    assert event.actor == "analyst@mergero.test"
    assert (
        event.details["before"] == {"min": 20, "max": 49} and event.details["before_status"] == "conflicting"
    )
    assert event.details["after"] == {"min": 24, "max": 24}
    assert event.details["reason"] == "Confirmed in 2025 annual report"


def test_second_correction_supersedes_first(client, session):
    seed_all(session)
    company = _revontuli(session)
    for value in ("24", "26"):
        client.post(
            f"/companies/{company.id}/facts/corrections",
            json={"field_name": "employees", "value": value, "reason": "update"},
        )
    corrections = session.scalars(
        select(CompanyFact).where(CompanyFact.company_id == company.id, CompanyFact.is_correction.is_(True))
    ).all()
    assert len(corrections) == 2
    assert sum(1 for c in corrections if c.valid_to is None) == 1
    assert session.get(Company, company.id).estimated_employee_min == 26


def test_correction_can_change_qualification(client, session):
    seed_all(session)
    company = session.scalars(select(Company).where(Company.legal_name == "Genfersee Medtech SA")).one()
    assert company.qualification_status == "borderline"
    client.post(
        f"/companies/{company.id}/facts/corrections",
        json={"field_name": "employees", "value": {"min": 31, "max": 31}, "reason": "phone-verified"},
    )
    session.refresh(company)
    assert company.qualification_status == "qualified"


def test_invalid_corrections_rejected(client, session):
    seed_all(session)
    company = _revontuli(session)
    url = f"/companies/{company.id}/facts/corrections"
    assert (
        client.post(url, json={"field_name": "registry_id", "value": "x", "reason": "nope"}).status_code
        == 422
    )
    assert (
        client.post(url, json={"field_name": "employees", "value": "lots", "reason": "nope"}).status_code
        == 422
    )
    assert (
        client.post(url, json={"field_name": "employees", "value": "", "reason": "nope"}).status_code == 422
    )
    assert (
        client.post(url, json={"field_name": "employees", "value": "24"}).status_code == 422
    )  # reason required


def test_corrections_survive_reimport(client, session):
    seed_all(session)
    company = _revontuli(session)
    client.post(
        f"/companies/{company.id}/facts/corrections",
        json={"field_name": "employees", "value": "24", "reason": "verified"},
    )
    seed_all(session)
    assert session.get(Company, company.id).estimated_employee_min == 24
