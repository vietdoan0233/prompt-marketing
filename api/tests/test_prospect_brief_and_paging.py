"""Seller-prospect paging, rank-independent brief retrieval and Cash Harvesting provenance."""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.seller_signals import cash_harvesting_candidate, financial_signal
from app.models import CompanyFinancial
from app.services.seller_funnel import seller_funnel
from tests.test_seller_funnel import BAND, LATEST, _add_company, _annual, _seed_funnel


def test_offset_paging_is_a_window_over_the_same_sorted_list(session: Session) -> None:
    _seed_funnel(session)
    full = seller_funnel(session, sector=None, limit=500, **BAND)
    first = seller_funnel(session, sector=None, limit=4, offset=0, **BAND)
    second = seller_funnel(session, sector=None, limit=4, offset=4, **BAND)
    beyond = seller_funnel(session, sector=None, limit=4, offset=10_000, **BAND)

    assert first.total_items == second.total_items == full.total_items == len(full.items)
    assert [i.company_id for i in first.items + second.items] == [i.company_id for i in full.items[:8]]
    assert (second.offset, second.limit) == (4, 4)
    assert beyond.items == [] and beyond.total_items == full.total_items
    # Paging never changes the funnel counts.
    assert [s.count for s in first.stages] == [s.count for s in second.stages]


def test_brief_is_retrievable_regardless_of_rank(client, session: Session) -> None:
    _seed_funnel(session)
    full = client.get("/seller-prospects", params={"limit": 500, "view": "all"}).json()["items"]
    last = full[-1]

    brief = client.get(f"/seller-prospects/{last['company_id']}").json()
    assert brief["item"]["company_id"] == last["company_id"]
    assert brief["rank"] == len(full) and brief["total_items"] == len(full)
    assert brief["in_sector"] is True
    assert brief["item"]["owner_intent"] == "unknown" and brief["item"]["buyer_fit"] == "not_assessed"


def test_brief_distinguishes_absent_excluded_and_outside_band(client, session: Session) -> None:
    _seed_funnel(session)
    items = {
        i["legal_name"]: i
        for i in client.get("/seller-prospects", params={"limit": 500, "view": "all"}).json()["items"]
    }

    assert client.get("/seller-prospects/no-such-company").status_code == 404

    peer = items["Peer 0 OÜ"]  # EMTAK 62
    excluded = client.get(f"/seller-prospects/{peer['company_id']}", params={"sector": "64"}).json()
    assert excluded["in_sector"] is False and excluded["rank"] is None
    assert excluded["item"]["company_id"] == peer["company_id"]

    large = items["Large AS"]  # revenue above the band: classified, not excluded
    outside = client.get(f"/seller-prospects/{large['company_id']}").json()
    assert outside["item"]["next_action"] == "outside_size_band" and outside["rank"] is not None

    assert (
        client.get(
            f"/seller-prospects/{peer['company_id']}", params={"min_revenue_eur": 9, "max_revenue_eur": 1}
        ).status_code
        == 422
    )


def test_cash_signal_carries_its_own_year_and_filings_when_operating_profit_is_missing() -> None:
    rows = [
        _annual(LATEST - 3, "10000000", "900000", ebitda=Decimal("2000000")),
        _annual(LATEST - 2, "10100000", "900000", ebitda=Decimal("2100000")),
        _annual(LATEST - 1, "10200000", "900000", ebitda=Decimal("2200000")),
        # Latest year: reported EBITDA but no operating profit. The general signal cannot use this year;
        # the cash signal can, so their evidence covers different years.
        _annual(LATEST, "10300000", "0", operating_profit=None, ebitda=Decimal("2300000")),
    ]
    general = financial_signal(rows, min_revenue_eur=5_000_000, max_revenue_eur=50_000_000)
    cash = cash_harvesting_candidate(rows)

    assert general.latest_year == LATEST - 1
    assert cash.latest_year == LATEST
    assert cash.filing_ids == [f"{y}:R{y}" for y in (LATEST - 2, LATEST - 1, LATEST)]
    assert cash.filing_ids != general.filing_ids
    assert cash.evidence_status == "evaluated" and cash.issues == []


def test_cash_margin_alone_keeps_its_filing_and_reason() -> None:
    cash = cash_harvesting_candidate([_annual(LATEST, "10300000", "900000", ebitda=Decimal("2300000"))])
    assert cash.evidence_status == "insufficient_evidence" and cash.triggered is False
    assert cash.latest_ebitda_margin is not None and cash.three_year_revenue_cagr is None
    assert cash.filing_ids == [f"{LATEST}:R{LATEST}"]
    assert cash.issues == ["Three consecutive comparable fiscal years unavailable for revenue CAGR"]


def test_funnel_exposes_cash_provenance_separately(session: Session) -> None:
    company = _add_company(
        session, 40, "Split Years OÜ", revenue=(10_000_000, 10_100_000, 10_200_000), ebitda_margin=0.25
    )
    # Add a newer year whose operating profit is unreported but EBITDA is: only the cash signal can use it.
    code = company.identifiers[0].value.removeprefix("EE:") if company.identifiers else "x"
    session.add(
        CompanyFinancial(
            company_id=company.id,
            fiscal_year=LATEST + 1,
            period_start=date(LATEST + 1, 1, 1),
            period_end=date(LATEST + 1, 12, 31),
            currency="EUR",
            unit="EUR",
            statement_scope="standalone",
            revenue=Decimal(10_300_000),
            operating_profit=None,
            ebitda=Decimal(2_600_000),
            source_id="ee-ariregister",
            source_url="https://avaandmed.ariregister.rik.ee/sites/default/files/4.new.zip",
            observed_at=company.facts[0].observed_at if company.facts else date.today(),
            confidence="verified",
            usage_policy="internal-only",
            parser_version="test",
            filing_id=f"{code}-new",
            registry_code=code,
            source_key=f"{code}|new",
            content_hash=f"{code}-new",
        )
    )
    session.commit()

    item = next(
        i for i in seller_funnel(session, sector=None, limit=500, **BAND).items if i.company_id == company.id
    )
    assert item.cash_harvesting_latest_year == LATEST + 1
    assert item.cash_harvesting_latest_year != item.latest_year
    assert f"{LATEST + 1}:{code}-new" in item.cash_harvesting_filing_ids
    assert (
        "https://avaandmed.ariregister.rik.ee/sites/default/files/4.new.zip"
        in item.cash_harvesting_source_urls
    )
    assert item.cash_harvesting_filing_ids != item.filing_ids
