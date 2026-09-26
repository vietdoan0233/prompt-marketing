"""Idempotent imports, versioned facts, provenance completeness, retry, retention."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.connectors.base import ConnectorError
from app.models import Company, CompanyFact, IngestionRun, SourceSnapshot, utcnow
from app.services import ingestion
from app.services.retention import expire_raw_data
from tests.conftest import MERGERO_HEADER, import_csv, import_fixture, seed_all


def _state(session):
    companies = session.scalar(select(func.count()).select_from(Company))
    facts = session.execute(
        select(
            CompanyFact.company_id,
            CompanyFact.field_name,
            CompanyFact.source_id,
            CompanyFact.value_hash,
            CompanyFact.confidence,
            CompanyFact.valid_to,
        ).order_by(
            CompanyFact.company_id, CompanyFact.field_name, CompanyFact.source_id, CompanyFact.value_hash
        )
    ).all()
    snapshots = session.scalar(select(func.count()).select_from(SourceSnapshot))
    return companies, facts, snapshots


def test_reimport_is_idempotent(session):
    first = import_fixture(session, "de-handelsregister", "handelsregister_export.csv")
    state1 = _state(session)
    second = import_fixture(session, "de-handelsregister", "handelsregister_export.csv")
    assert _state(session) == state1
    assert first.counts["accepted"] == 6
    assert second.counts["accepted"] == 0 and second.counts["updated"] == 0
    assert second.counts["unchanged"] == 6
    assert first.input_hash == second.input_hash and first.config_hash == second.config_hash


def test_full_seed_rerun_is_idempotent(session):
    seed_all(session)
    state1 = _state(session)
    seed_all(session)
    assert _state(session) == state1


def test_discovery_rerun_is_idempotent_and_filters_headcount_at_source(session):
    run1 = ingestion.start_discovery_run(session, source_id="no-brreg", query={}, actor="test")
    run2 = ingestion.start_discovery_run(session, source_id="no-brreg", query={}, actor="test")
    assert run1.counts["discovered"] == 3  # 18- and 1-employee entities filtered by fraAntallAnsatte=20
    assert run2.counts["unchanged"] == 3 and run2.counts["accepted"] == 0


def test_changed_value_supersedes_but_keeps_history(session):
    import_csv(session, "mergero-csv", MERGERO_HEADER + "M-1,Alpha Oy,FI,Espoo,2931457-2,,,30,,,,\n")
    import_csv(session, "mergero-csv", MERGERO_HEADER + "M-1,Alpha Oy,FI,Espoo,2931457-2,,,45,,,,\n")
    facts = session.scalars(select(CompanyFact).where(CompanyFact.field_name == "employees")).all()
    assert len(facts) == 2
    old = next(f for f in facts if f.valid_to is not None)
    new = next(f for f in facts if f.valid_to is None)
    assert old.value_json == {"min": 30, "max": 30} and old.review_status == "superseded"
    assert new.value_json == {"min": 45, "max": 45}
    company = session.scalars(select(Company)).one()
    assert company.estimated_employee_min == 45


def test_in_file_duplicates_are_reported_not_double_written(session):
    run = import_fixture(session, "se-bolagsverket-allabolag", "allabolag_export.csv")
    dupes = [r for r in run.records if r.outcome == "duplicate"]
    assert len(dupes) == 1 and "same stable key as row 3" in dupes[0].match_reasons[0]
    assert (
        session.scalar(
            select(func.count()).select_from(Company).where(Company.legal_name == "Vänern Industri AB")
        )
        == 1
    )


def test_every_fact_carries_full_provenance(session):
    seed_all(session)
    facts = session.scalars(select(CompanyFact)).all()
    assert facts
    for f in facts:
        assert f.source_id and f.observed_at and f.confidence and f.usage_policy and f.review_status
        assert f.ingestion_run_id and f.snapshot_id and f.source_key
        assert f.confidence in {"verified", "multi-source", "estimated", "old", "conflicting", "unknown"}


def test_invalid_rows_rejected_with_reasons(session):
    run = import_fixture(session, "se-bolagsverket-allabolag", "allabolag_export.csv")
    rejected = {r.row_number: r.errors for r in run.records if r.outcome == "rejected"}
    assert "missing required field 'legal_name'" in rejected[7]
    assert "outside this source's approved coverage" in rejected[8][0]
    bad_id = next(r for r in run.records if r.row_number == 5)
    assert bad_id.outcome == "accepted"
    assert any("invalid Swedish organisationsnummer" in w for w in bad_id.warnings)


def test_snapshots_hold_no_personal_contact_fields(session):
    import_fixture(session, "mergero-csv", "mergero_companies.csv")
    for snap in session.scalars(select(SourceSnapshot)):
        assert not any(k.startswith("contact_") for k in (snap.raw_payload or {}))


def test_retry_reuses_retained_input(session):
    run = import_fixture(session, "at-firmenbuch", "firmenbuch_export.csv")
    retried = ingestion.retry_run(session, run.id, "test")
    assert retried.retry_of_id == run.id
    assert retried.counts["unchanged"] == 3


def test_retention_expires_raw_data_and_blocks_retry(session):
    run = import_fixture(session, "at-firmenbuch", "firmenbuch_export.csv")
    result = expire_raw_data(session, actor="test", now=utcnow() + timedelta(days=31))
    assert result == {"snapshots_expired": 3, "run_inputs_expired": 1}
    for snap in session.scalars(select(SourceSnapshot)):
        assert snap.raw_payload is None and snap.expired_at is not None and snap.content_hash
    # Facts (the minimum evidence) survive; only raw payloads expire.
    assert session.scalar(select(func.count()).select_from(CompanyFact)) > 0
    with pytest.raises(ConnectorError):
        ingestion.retry_run(session, run.id, "test")


def test_retention_is_per_source(session):
    import_fixture(session, "web-impressum", "impressum_extract.csv")  # 14 days
    import_fixture(session, "at-firmenbuch", "firmenbuch_export.csv")  # 30 days
    result = expire_raw_data(session, actor="test", now=utcnow() + timedelta(days=15))
    assert result["snapshots_expired"] == 3
    runs = {r.source_id: r for r in session.scalars(select(IngestionRun))}
    assert runs["web-impressum"].input_text is None and runs["at-firmenbuch"].input_text is not None
