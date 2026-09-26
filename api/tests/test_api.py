"""API contract smoke tests + migration check."""

from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import API_ROOT
from tests.conftest import FIXTURES, seed_all


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_sources_show_permission_and_gate(client):
    sources = {s["id"]: s for s in client.get("/sources").json()}
    assert sources["no-brreg"]["ingestible"] is True
    assert sources["linkedin-scrape"]["ingestible"] is False and sources["linkedin-scrape"]["gate_reasons"]
    assert {"nordics", "dach", "internal"} == {s["region"] for s in sources.values()}


def test_csv_upload_and_run_detail(client):
    data = (FIXTURES / "firmenbuch_export.csv").read_bytes()
    r = client.post(
        "/ingestion-runs",
        data={"source_id": "at-firmenbuch"},
        files={"file": ("firmenbuch_export.csv", data, "text/csv")},
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "UPSERTED" and run["counts"]["accepted"] == 3 and run["input_retained"]
    detail = client.get(f"/ingestion-runs/{run['id']}").json()
    assert len(detail["records"]) == 3
    retry = client.post(f"/ingestion-runs/{run['id']}/retry").json()
    assert retry["retry_of_id"] == run["id"] and retry["counts"]["unchanged"] == 3
    assert len(client.get("/ingestion-runs").json()) == 2


def test_discovery_via_json(client):
    r = client.post("/ingestion-runs", json={"source_id": "fi-prh-ytj", "query": {"industry_code": "62"}})
    assert r.status_code == 201, r.text
    assert r.json()["counts"]["discovered"] == 2


def test_company_filters_and_detail(client, session):
    seed_all(session)
    r = client.get(
        "/companies", params={"country": "DE,AT", "min_employees": 0, "sort": "employees", "order": "desc"}
    ).json()
    assert {i["country"] for i in r["items"]} == {"DE", "AT"}
    emps = [i["estimated_employee_min"] for i in r["items"] if i["estimated_employee_min"] is not None]
    assert emps == sorted(emps, reverse=True)
    assert (
        client.get("/companies", params={"sector": "62", "min_employees": 0}).json()["total"] >= 5
    )  # NACE 62
    assert client.get("/companies", params={"review_status": "reviewed"}).json()["total"] == 0

    kuusisto = client.get("/companies", params={"q": "kuusisto"}).json()["items"][0]
    assert kuusisto["source_count"] == 3 and kuusisto["freshness"] == "fresh"
    detail = client.get(f"/companies/{kuusisto['id']}").json()
    fields = {f["field_name"]: f for f in detail["fields"]}
    assert fields["employees"]["label"] == "multi-source"
    assert fields["registry_id"]["value"] == "2931457-2"
    assert detail["contacts"] and detail["identifiers"] and detail["timeline"]
    for fact in detail["facts"]:
        assert fact["source_id"] and fact["observed_at"] and fact["confidence"]

    r = client.post(f"/companies/{kuusisto['id']}/review", json={"review_status": "reviewed", "note": "ok"})
    assert r.json()["company"]["review_status"] == "reviewed"
    assert (
        client.get(f"/companies/{kuusisto['id']}/audit-events").json()[0]["action"]
        == "company.review_status_changed"
    )


def test_nordic_filter_with_blank_form_fields(client, session):
    # Exactly what the browser's GET filter form submits: every empty input is sent as `key=`.
    seed_all(session)
    r = client.get(
        "/companies?q=&country=FI%2CSE%2CNO%2CDK%2CIS&sector=&min_employees=0&max_employees="
        "&qualification=&review_status=&freshness=&sort=&order="
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items and {i["country"] for i in items} <= {"FI", "SE", "NO", "DK", "IS"}


def test_quality_report_and_duplicate_review(client, session):
    seed_all(session)
    report = client.get("/quality/report").json()
    assert report["totals"]["open_duplicate_candidates"] == 2
    assert report["totals"]["unresolved_conflicts"] >= 1
    assert report["missing_fields"]["employees"]["count"] == 1  # Saimaa Logistics
    assert report["stale_records"]
    dupes = client.get("/quality/duplicates").json()
    rheinwerk = next(d for d in dupes if "Rheinwerk" in (d["company_a_name"] or ""))
    r = client.post(f"/quality/duplicates/{rheinwerk['id']}/link", json={"reason": "group subsidiary"})
    assert r.json()["status"] == "linked"
    assert (
        client.post(f"/quality/duplicates/{rheinwerk['id']}/dismiss", json={"reason": "x" * 5}).status_code
        == 409
    )


def test_audit_events_filter(client, session):
    seed_all(session)
    events = client.get("/audit-events", params={"action": "ingestion."}).json()
    assert events and all(e["action"].startswith("ingestion.") for e in events)


def test_migrations_match_models():
    path = API_ROOT / f".migration-check-{uuid4().hex}.db"
    url = f"sqlite:///{path.as_posix()}"
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    try:
        command.upgrade(cfg, "head")
        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        assert {
            "companies",
            "company_facts",
            "company_financials",
            "registered_addresses",
            "contacts",
            "sources",
            "source_snapshots",
            "ingestion_runs",
            "audit_events",
            "company_identifiers",
            "duplicate_candidates",
        } <= tables
        command.check(cfg)  # raises if models drifted from migrations
    finally:
        path.unlink(missing_ok=True)
