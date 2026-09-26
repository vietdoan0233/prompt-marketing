"""Multi-source entity resolution, duplicate review and confidence scoring."""

from sqlalchemy import func, select

from app.models import AuditEvent, Company, CompanyFact, CompanyIdentifier, DuplicateCandidate
from app.services import quality
from tests.conftest import MERGERO_HEADER, import_csv, import_fixture, seed_all


def _company(session, name):
    return session.scalars(
        select(Company).where(Company.legal_name == name, Company.merged_into_id.is_(None))
    ).one()


def test_registry_and_vat_resolve_to_one_profile(session):
    seed_all(session)
    revontuli = _company(session, "Revontuli Systems Oy")
    sources = set(
        session.scalars(select(CompanyFact.source_id).where(CompanyFact.company_id == revontuli.id))
    )
    # PRH (registry ID) + firmographics (VAT FI30188227 only) + Mergero CSV consolidated into one record.
    assert sources == {"fi-prh-ytj", "licensed-firmographics", "mergero-csv"}
    assert (
        session.scalar(
            select(func.count()).select_from(Company).where(Company.normalized_name == "revontuli systems")
        )
        == 1
    )


def test_domain_resolves_across_sources_without_registry_id(session):
    import_fixture(session, "de-handelsregister", "handelsregister_export.csv")
    import_fixture(session, "web-impressum", "impressum_extract.csv")
    run = import_fixture(session, "licensed-firmographics", "licensed_firmographics.csv")
    isar = _company(session, "Isar Robotics GmbH")
    rec = next(r for r in run.records if r.company_id == isar.id)
    assert any("domain isar-robotics.example" in m for m in rec.match_reasons)
    emp = session.scalars(
        select(CompanyFact).where(CompanyFact.company_id == isar.id, CompanyFact.field_name == "employees")
    ).all()
    assert {f.confidence for f in emp} == {"multi-source"}  # registry 20-49 and feed 38 overlap


def test_shared_domain_with_different_registry_ids_is_never_merged(session):
    import_fixture(session, "de-handelsregister", "handelsregister_export.csv")
    a = _company(session, "Rheinwerk Automation GmbH")
    b = _company(session, "Rheinwerk Automation Service GmbH")
    assert a.id != b.id
    cand = session.scalars(select(DuplicateCandidate)).one()
    assert {cand.company_a_id, cand.company_b_id} == {a.id, b.id}
    details = " ".join(r["detail"] for r in cand.reasons)
    assert "shared domain" in details and "different registry IDs" in details


def test_name_only_record_becomes_explainable_candidate(session):
    import_fixture(session, "se-bolagsverket-allabolag", "allabolag_export.csv")
    import_fixture(session, "mergero-csv", "mergero_companies.csv")
    cands = session.scalars(select(DuplicateCandidate)).all()
    vanern = [c for c in cands if any("vanern industri" in r["detail"] for r in c.reasons)]
    assert len(vanern) == 1
    assert vanern[0].band == "possible" and vanern[0].status == "open"
    signals = {r["signal"] for r in vanern[0].reasons}
    assert {"name", "city"} <= signals


def test_merge_moves_evidence_and_is_audited(session):
    import_fixture(session, "se-bolagsverket-allabolag", "allabolag_export.csv")
    import_fixture(session, "mergero-csv", "mergero_companies.csv")
    survivor = _company(session, "Vänern Industri AB")
    other = _company(session, "Vänern Industri")
    cand = session.scalars(select(DuplicateCandidate)).one()
    quality.merge(session, cand, survivor.id, "reviewer", "same org, Mergero list lacked org nr")
    session.refresh(other)
    assert other.merged_into_id == survivor.id
    sources = set(session.scalars(select(CompanyFact.source_id).where(CompanyFact.company_id == survivor.id)))
    assert sources == {"se-bolagsverket-allabolag", "mergero-csv"}
    assert (
        session.scalars(select(CompanyIdentifier).where(CompanyIdentifier.company_id == other.id)).all() == []
    )
    event = session.scalars(select(AuditEvent).where(AuditEvent.action == "identity.merged")).one()
    assert event.details["merged_company_id"] == other.id and event.details["match_reasons"]
    # Re-importing the merged record resolves to the survivor, not a new company.
    run = import_fixture(session, "mergero-csv", "mergero_companies.csv")
    assert all(r.company_id != other.id for r in run.records)


def test_dismissed_candidates_are_not_reopened(session):
    import_fixture(session, "de-handelsregister", "handelsregister_export.csv")
    cand = session.scalars(select(DuplicateCandidate)).one()
    quality.resolve_candidate(session, cand, "linked", "reviewer", "group subsidiary, separate legal entity")
    assert quality.detect_duplicates(session) == 0
    assert session.scalars(select(DuplicateCandidate)).one().status == "linked"


def test_multi_source_and_conflict_labels(session):
    seed_all(session)
    kuusisto = _company(session, "Kuusisto Analytics Oy")
    emp = session.scalars(
        select(CompanyFact).where(
            CompanyFact.company_id == kuusisto.id,
            CompanyFact.field_name == "employees",
            CompanyFact.valid_to.is_(None),
        )
    ).all()
    assert {f.confidence for f in emp} == {"multi-source"}

    revontuli = _company(session, "Revontuli Systems Oy")
    emp = session.scalars(
        select(CompanyFact).where(
            CompanyFact.company_id == revontuli.id,
            CompanyFact.field_name == "employees",
            CompanyFact.valid_to.is_(None),
        )
    ).all()
    assert {f.confidence for f in emp} == {"conflicting"}
    assert {(f.value_json["min"], f.value_json["max"]) for f in emp} == {
        (20, 49),
        (12, 12),
    }  # both values retained


def test_single_estimated_source_is_not_upgraded(session):
    import_csv(
        session,
        "licensed-firmographics",
        "legal_name,country,registry_id,employees\nSolo Oy,FI,2931457-2,50\n",
    )
    fact = session.scalars(select(CompanyFact).where(CompanyFact.field_name == "employees")).one()
    assert fact.confidence == "estimated"


def test_old_evidence_is_marked_old(session):
    seed_all(session)
    pohjola = _company(session, "Pohjola Metallityö Oy")
    confs = set(session.scalars(select(CompanyFact.confidence).where(CompanyFact.company_id == pohjola.id)))
    assert confs == {"old"}


def test_key_conflict_inside_one_record_links_instead_of_merging(session):
    import_csv(session, "mergero-csv", MERGERO_HEADER + "A,Alpha Oy,FI,,2931457-2,,,30,,,,\n")
    import_csv(session, "mergero-csv", MERGERO_HEADER + "B,Beta Oy,FI,,3018822-7,,beta.example,30,,,,\n")
    # A record carrying Alpha's registry ID and Beta's domain must not silently merge the two.
    import_csv(
        session,
        "licensed-firmographics",
        "legal_name,country,registry_id,website\nAlpha,FI,2931457-2,beta.example\n",
    )
    assert session.scalar(select(func.count()).select_from(Company)) == 2
    assert session.scalar(select(func.count()).select_from(DuplicateCandidate)) >= 1


def test_merge_refused_for_different_registry_ids(session):
    import_fixture(session, "de-handelsregister", "handelsregister_export.csv")
    cand = session.scalars(select(DuplicateCandidate)).one()
    try:
        quality.merge(session, cand, cand.company_a_id, "reviewer", "looks the same")
        raise AssertionError("merged two legal entities with different registry IDs")
    except ValueError as exc:
        assert "link them instead" in str(exc)
    session.rollback()
    assert session.get(DuplicateCandidate, cand.id).status == "open"
