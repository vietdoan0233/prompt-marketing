"""Merge retention: what a duplicate merge does and does not carry to the surviving company.

`quality.merge` moves identifiers, facts and contacts to the survivor. Annual financial rows,
registered-address versions and shareholders are NOT moved: they stay attached to the merged-away
company. This file pins that: the evidence is retained (never deleted, still readable from the
merged-away record), and the expected-but-missing survivor view is recorded as a strict xfail until
source/version/conflict semantics for moving those versioned tables are designed (a domain decision,
not a UI change).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Company, CompanyFinancial, DuplicateCandidate, utcnow
from app.services import quality
from tests.conftest import import_fixture


def _company(session, name):
    return session.scalars(
        select(Company).where(Company.legal_name == name, Company.merged_into_id.is_(None))
    ).one()


def _merge_with_financial_on_other(session) -> tuple[Company, Company, CompanyFinancial]:
    import_fixture(session, "se-bolagsverket-allabolag", "allabolag_export.csv")
    import_fixture(session, "mergero-csv", "mergero_companies.csv")
    survivor = _company(session, "Vänern Industri AB")
    other = _company(session, "Vänern Industri")
    row = CompanyFinancial(
        company_id=other.id,
        fiscal_year=2024,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        currency="EUR",
        unit="EUR",
        statement_scope="standalone",
        revenue=Decimal(12_000_000),
        source_id="mergero-csv",
        source_url="https://example.invalid/filing",
        observed_at=utcnow(),
        confidence="verified",
        usage_policy="internal-only",
        parser_version="test",
        filing_id="F-2024",
        registry_code="test",
        source_key="merge-retention|2024",
        content_hash="merge-retention-2024",
    )
    session.add(row)
    session.flush()
    cand = session.scalars(select(DuplicateCandidate)).one()
    quality.merge(session, cand, survivor.id, "reviewer", "same organisation")
    session.commit()
    return survivor, other, row


def test_merge_retains_unmoved_financial_evidence_on_the_merged_away_record(client, session) -> None:
    survivor, other, row = _merge_with_financial_on_other(session)
    session.refresh(row)
    assert row.company_id == other.id  # retained, not deleted and not silently re-parented

    detail = client.get(f"/companies/{other.id}").json()
    assert detail["merged_into_id"] == survivor.id
    assert [f["filing_id"] for f in detail["financials"]] == ["F-2024"]


@pytest.mark.xfail(
    strict=True,
    reason="Unresolved: merge does not carry financials/addresses/shareholders to the survivor; "
    "needs a designed source/version/conflict policy before changing quality.merge.",
)
def test_survivor_view_includes_merged_financial_evidence(client, session) -> None:
    survivor, _other, _row = _merge_with_financial_on_other(session)
    detail = client.get(f"/companies/{survivor.id}").json()
    assert "F-2024" in [f["filing_id"] for f in detail["financials"]]
