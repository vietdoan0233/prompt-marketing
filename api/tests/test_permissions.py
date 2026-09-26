"""Permission gating: unapproved, pending, disabled and out-of-policy sources fail closed."""

from sqlalchemy import func, select

from app.models import Company, CompanyFact, IngestionRun, SourceSnapshot
from app.services import ingestion
from app.services.permissions import PermissionDenied
from app.services.source_registry import sync_sources
from tests.conftest import import_csv, source

ROW = "legal_name,country,registry_id,employees\nTest Data ApS,DK,41234567,30\n"


def _counts(session):
    return tuple(
        session.scalar(select(func.count()).select_from(m)) for m in (Company, CompanyFact, SourceSnapshot)
    )


def _assert_rejected(session, source_id, text=ROW):
    before = _counts(session)
    try:
        import_csv(session, source_id, text)
    except PermissionDenied as exc:
        assert exc.reasons
        if exc.run_id:  # unregistered sources are denied before a run row can exist
            run = session.get(IngestionRun, exc.run_id)
            assert run.status == "REJECTED"
            assert run.input_text is None  # nothing from an unapproved source is retained
        assert _counts(session) == before  # no partial writes
        return exc
    raise AssertionError(f"{source_id} was not rejected")


def test_unapproved_source_fails_closed(session):
    exc = _assert_rejected(session, "linkedin-scrape")
    assert any("unapproved" in r for r in exc.reasons)


def test_pending_licence_fails_closed(session):
    exc = _assert_rejected(session, "de-northdata", "legal_name,country\nX GmbH,DE\n")
    assert any("pending" in r for r in exc.reasons)


def test_approved_but_disabled_connector_fails_closed(session):
    exc = _assert_rejected(session, "dk-cvr")
    assert exc.reasons == ["source connector is disabled"]


def test_unknown_source_fails_closed(session):
    exc = _assert_rejected(session, "does-not-exist")
    assert "not registered" in exc.reasons[0]


def test_manual_source_cannot_ingest(session):
    exc = _assert_rejected(session, "mergero-manual")
    assert any("cannot run ingestion" in r for r in exc.reasons)


def test_live_network_requires_feature_flag(session):
    src = source(session, "ee-ariregister")
    src.connector_config = {"live": True}
    session.commit()
    try:
        ingestion.start_discovery_run(session, source_id="ee-ariregister", query={}, actor="test")
        raise AssertionError("live connector ran without LIVE_CONNECTORS_ENABLED")
    except PermissionDenied as exc:
        assert any("LIVE_CONNECTORS_ENABLED" in r for r in exc.reasons)


def test_regional_policy_blocks_out_of_region_coverage(session):
    src = source(session, "at-firmenbuch")
    src.countries = ["AT", "FI"]  # a DACH source claiming Finnish coverage
    session.commit()
    exc = _assert_rejected(session, "at-firmenbuch", "legal_name,country\nX GmbH,AT\n")
    assert any("outside region 'dach'" in r for r in exc.reasons)


def test_record_outside_source_coverage_is_rejected(session):
    run = import_csv(
        session, "at-firmenbuch", "legal_name,country,employees\nNorsk AS,NO,30\nWien GmbH,AT,30\n"
    )
    outcomes = [r.outcome for r in run.records]
    assert outcomes == ["rejected", "accepted"]
    assert "outside this source's approved coverage" in run.records[0].errors[0]


def test_enable_requires_approval_and_revocation_disables(client, session):
    r = client.post("/sources/de-northdata/enable")
    assert r.status_code == 409
    assert "pending" in r.json()["detail"]["reasons"][0]

    r = client.post("/sources/dk-cvr/enable")
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["ingestible"] is True

    r = client.post(
        "/sources/dk-cvr/permission", json={"permission_status": "revoked", "reason": "terms changed"}
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["ingestible"] is False

    r = client.post(
        "/sources/dk-cvr/permission", json={"permission_status": "approved", "reason": "re-approved"}
    )
    assert r.status_code == 422  # approval needs a legal/licence reference


def test_api_returns_403_with_reasons(client):
    r = client.post(
        "/ingestion-runs",
        data={"source_id": "linkedin-scrape"},
        files={"file": ("x.csv", ROW.encode(), "text/csv")},
    )
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["run_id"] and body["reasons"]


def test_new_sources_start_pending_and_disabled(client):
    r = client.post(
        "/sources",
        json={
            "id": "ee-new-feed",
            "name": "New feed",
            "provider": "Vendor",
            "region": "baltics",
            "countries": ["EE"],
            "source_type": "licensed_feed",
            "source_mode": "licensed",
            "allowed_fields": ["legal_name"],
        },
    )
    assert r.status_code == 201
    assert r.json()["permission_status"] == "pending" and r.json()["enabled"] is False


def test_config_downgrade_wins_and_disables(session):
    cfg = {"sources": [{"id": "ee-ariregister", "permission_status": "revoked", "countries": ["EE"]}]}
    sync_sources(session, actor="test", config=cfg)
    src = source(session, "ee-ariregister")
    assert src.permission_status == "revoked" and src.enabled is False


def test_contacts_dropped_when_source_not_allowed_contacts(session):
    run = import_csv(
        session,
        "licensed-firmographics",
        "legal_name,country,registry_id,contact_name,contact_email\n"
        "Kuusisto Analytics,FI,2931457-2,Liisa K,l@k.example\n",
    )
    assert run.counts["contacts_added"] == 0
    assert any("not allowed" in w for w in run.records[0].warnings)
