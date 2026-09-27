"""Seller prospect funnel: evidence rules, peer index, flags, stages and the API contract."""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.normalize import _ee_check
from app.domain.seller_signals import (
    AnnualFinancial,
    financial_signal,
    is_holding_activity,
    robust_reference,
    robust_z,
)
from app.models import Company, CompanyFact, CompanyFinancial, CompanyIdentifier, Source, utcnow
from app.services.digital_decay import SIGNAL_FIELD as DECAY_SIGNAL_FIELD
from app.services.seller_funnel import seller_funnel

LATEST = date.today().year - 1
YEARS = (LATEST - 2, LATEST - 1, LATEST)
BAND = {"min_revenue_eur": 5_000_000, "max_revenue_eur": 50_000_000}


def _registry_code(seed: int) -> str:
    base = f"{1_000_000 + seed * 13:07d}"
    return f"{base}{_ee_check(base)}"


def _annual(year: int, revenue: str, profit: str, **kw) -> AnnualFinancial:
    values = {
        "fiscal_year": year,
        "period_start": date(year, 1, 1),
        "period_end": date(year, 12, 31),
        "currency": "EUR",
        "unit": "EUR",
        "statement_scope": "standalone",
        "revenue": Decimal(revenue),
        "operating_profit": Decimal(profit),
        "total_assets": Decimal("4000000"),
        "equity": Decimal("2000000"),
        "filing_id": f"R{year}",
        "source_url": "https://avaandmed.ariregister.rik.ee/sites/default/files/4.x.zip",
        "source_file": "4.x.zip",
        "review_status": "unreviewed",
    }
    values.update(kw)
    return AnnualFinancial(**values)


def _add_company(
    session: Session,
    seed: int,
    name: str,
    *,
    emtak: str = "62011",
    status: str = "Registrisse kantud",
    revenue: tuple[int, ...] = (10_000_000, 10_500_000, 11_000_000),
    margin: float = 0.10,
    equity_ratio: float = 0.5,
    years: tuple[int, ...] = YEARS,
    consolidated_revenue: int | None = None,
    decay_verdict: str | None = None,
    ebitda_margin: float | None = None,
) -> Company:
    code = _registry_code(seed)
    company = Company(legal_name=name, normalized_name=name.casefold(), country="EE", industry_codes=[emtak])
    session.add(company)
    session.flush()
    session.add(CompanyIdentifier(company_id=company.id, kind="registry_id", value=f"EE:{code}"))
    now = utcnow()
    session.add(
        CompanyFact(
            company_id=company.id,
            field_name="registry_status",
            value_json=status,
            value_hash=f"status-{seed}",
            source_id="ee-ariregister",
            observed_at=now,
            valid_from=now,
            base_confidence="verified",
            confidence="verified",
            usage_policy="internal-only",
        )
    )

    def financial(year: int, value: int, scope: str, ebitda: int | None = None) -> CompanyFinancial:
        return CompanyFinancial(
            company_id=company.id,
            fiscal_year=year,
            period_start=date(year, 1, 1),
            period_end=date(year, 12, 31),
            currency="EUR",
            unit="EUR",
            statement_scope=scope,
            revenue=Decimal(value),
            operating_profit=Decimal(round(value * margin)),
            ebitda=Decimal(ebitda) if ebitda is not None else None,
            total_assets=Decimal(value // 2),
            equity=Decimal(round(value // 2 * equity_ratio)),
            employees_fte=Decimal(40),
            source_id="ee-ariregister",
            source_url="https://avaandmed.ariregister.rik.ee/sites/default/files/4.x.zip",
            observed_at=now,
            confidence="verified",
            usage_policy="internal-only",
            parser_version="test",
            filing_id=f"{code}-{year}-{scope}",
            registry_code=code,
            source_key=f"{code}|{year}|{scope}",
            content_hash=f"{code}-{year}-{scope}",
        )

    for year, value in zip(years, revenue, strict=False):
        ebitda = round(value * ebitda_margin) if ebitda_margin is not None and year == years[-1] else None
        session.add(financial(year, value, "standalone", ebitda))
    if consolidated_revenue is not None:
        session.add(financial(years[-1], consolidated_revenue, "consolidated"))
    if decay_verdict is not None:
        if session.get(Source, "web-digital-decay") is None:
            # Not in the cut-down test source registry (tests/fixtures/sources_test.yaml); this is the
            # only test that exercises a persisted digital-decay fact, so register it minimally here.
            session.add(
                Source(
                    id="web-digital-decay",
                    name="Company website activity check (Digital Decay)",
                    provider="Company-published websites",
                    region="baltics",
                    countries=["EE"],
                    tier="C",
                    source_type="website",
                    source_mode="public-approved",
                    permission_status="approved",
                    connector_type="website_decay",
                    base_confidence="estimated",
                )
            )
            session.flush()
        session.add(
            CompanyFact(
                company_id=company.id,
                field_name=DECAY_SIGNAL_FIELD,
                value_json={"verdict": decay_verdict, "stale_count": 2, "determinable_count": 3},
                value_hash=f"decay-{seed}",
                source_id="web-digital-decay",
                observed_at=now,
                valid_from=now,
                base_confidence="estimated",
                confidence="estimated",
                usage_policy="internal-only",
            )
        )
    session.flush()
    return company


def _seed_funnel(session: Session) -> None:
    for seed in range(9):  # nine comparable operating peers in EMTAK 62 with spread in margin
        _add_company(
            session, seed, f"Peer {seed} OÜ", margin=0.04 + 0.02 * seed, equity_ratio=0.3 + 0.04 * seed
        )
    _add_company(session, 20, "Group Parent AS", margin=0.12, consolidated_revenue=60_000_000)
    _add_company(session, 21, "Holding OÜ", emtak="64201", margin=0.30)
    _add_company(session, 22, "Closing OÜ", status="Likvideerimisel")
    _add_company(session, 23, "Large AS", revenue=(80_000_000, 81_000_000, 82_000_000))
    _add_company(session, 24, "Gap OÜ", years=(LATEST - 2, LATEST), revenue=(9_000_000, 9_500_000))
    session.commit()


def test_financial_signal_requires_three_unique_comparable_years() -> None:
    rows = [_annual(year, "10000000", "1000000") for year in YEARS]
    signal = financial_signal(rows, **BAND)
    assert signal.evidence_status == "complete" and signal.quality_band == "core"
    assert signal.filing_ids == [f"{year}:R{year}" for year in YEARS]
    assert signal.three_year_median_margin == 0.1

    duplicated = [*rows, _annual(LATEST, "10000000", "900000", filing_id="R-dup")]
    signal = financial_signal(duplicated, **BAND)
    assert signal.evidence_status == "needs_data"
    assert any("lacks one unique comparable" in issue for issue in signal.issues)

    consolidated_only = [_annual(year, "10000000", "1", statement_scope="consolidated") for year in YEARS]
    signal = financial_signal(consolidated_only, **BAND)
    assert signal.issues[-1] == "No comparable annual revenue and operating profit"


def test_robust_scores_and_holding_codes() -> None:
    assert robust_z(0.5, robust_reference([0.1, 0.1, 0.1])) is None  # zero spread: no score
    assert robust_z(10.0, robust_reference([0.0, 0.1, 0.2, 0.3])) == 3.0  # clipped
    assert is_holding_activity(["64201"]) and is_holding_activity(["70101"])
    assert not is_holding_activity(["62011"]) and not is_holding_activity(["64191"])


def test_funnel_stages_flags_and_ranking(session: Session) -> None:
    _seed_funnel(session)
    funnel = seller_funnel(session, sector=None, limit=100, **BAND)

    stages = {stage.key: stage.count for stage in funnel.stages}
    assert stages == {
        "imported": 14,
        "registered": 13,
        "in_size_band": 12,
        "complete_evidence": 11,
        "profitable": 11,
        "advisor_review": 10,
        "decay_flagged": 0,  # no company here has ever had a digital-decay check run
    }
    assert funnel.advisor_review == 10
    assert funnel.advisor_review_decay_checked == 0
    assert funnel.advisor_review_decay_flagged == 0

    items = {item.legal_name: item for item in funnel.items}
    assert items["Holding OÜ"].next_action == "research"
    assert items["Holding OÜ"].flags == ["holding_activity"]
    assert items["Holding OÜ"].financial_profile_index is None
    assert items["Closing OÜ"].next_action == "exclude"
    assert items["Large AS"].next_action == "outside_size_band"
    assert items["Gap OÜ"].evidence_status == "needs_data"

    parent = items["Group Parent AS"]
    assert parent.flags == ["group_parent"] and parent.consolidated_revenue_eur == 60_000_000
    assert any("group revenue €60.0m" in reason for reason in parent.review_reasons)

    ranked = [item for item in funnel.items if item.next_action == "advisor_review"]
    assert ranked[0].legal_name == "Peer 8 OÜ"  # highest margin peer ranks first
    assert all(item.peer_count == 10 for item in ranked)
    assert funnel.peer_groups[0].group == "62" and funnel.peer_groups[0].peer_count == 10

    top = ranked[0]
    assert top.registry_url == f"https://ariregister.rik.ee/eng/company/{_registry_code(8)}"
    assert any(reason.startswith("Peer index") for reason in top.review_reasons)
    assert top.open_questions[0].startswith("Is the owner open to a conversation?")
    assert top.owner_intent == "unknown" and top.buyer_fit == "not_assessed"


def test_digital_decay_verdict_is_the_last_filter(session: Session) -> None:
    for seed in range(9):  # nine comparable operating peers; seed 8 has the strongest margin
        _add_company(
            session,
            seed,
            f"Decay Peer {seed} OÜ",
            emtak="47110",
            margin=0.04 + 0.02 * seed,
            equity_ratio=0.3 + 0.04 * seed,
            decay_verdict={0: "coasting", 1: "active"}.get(seed),
        )
    session.commit()

    funnel = seller_funnel(session, sector="47", limit=100, **BAND)
    items = {item.legal_name: item for item in funnel.items}
    weakest_margin, strongest_margin, untouched = (
        items["Decay Peer 0 OÜ"],
        items["Decay Peer 8 OÜ"],
        items["Decay Peer 2 OÜ"],
    )

    # Weakest financial profile, but flagged coasting: still ranks first in the advisor-review queue.
    ranked = [item for item in funnel.items if item.next_action == "advisor_review"]
    assert ranked[0].legal_name == "Decay Peer 0 OÜ"
    assert weakest_margin.digital_decay_verdict == "coasting"
    assert weakest_margin.digital_decay_observed_at is not None
    assert any(r.startswith("Website signal: coasting") for r in weakest_margin.review_reasons)
    assert not any("No website check run yet" in q for q in weakest_margin.open_questions)

    # A merely "active" verdict is not a positive signal: it does not jump the financial-index ranking,
    # and a still-unchecked company keeps its normal position too.
    assert items["Decay Peer 1 OÜ"].digital_decay_verdict == "active"
    assert strongest_margin.digital_decay_verdict is None
    assert ranked[1].legal_name == "Decay Peer 8 OÜ"  # highest financial-profile-index among the rest
    assert untouched.digital_decay_verdict is None
    assert any("No website check run yet" in q for q in untouched.open_questions)

    decay_stage = next(s for s in funnel.stages if s.key == "decay_flagged")
    assert decay_stage.count == 1  # only the coasting one; "active" and unchecked don't count
    assert funnel.advisor_review_decay_checked == 2  # coasting + active
    assert funnel.advisor_review_decay_flagged == 1


def test_sector_filter_keeps_peer_reference(session: Session) -> None:
    _seed_funnel(session)
    everything = {i.legal_name: i for i in seller_funnel(session, sector=None, limit=100, **BAND).items}
    filtered = seller_funnel(session, sector="62", limit=100, **BAND)
    assert {item.peer_group for item in filtered.items} == {"62"}
    for item in filtered.items:
        assert item.financial_profile_index == everything[item.legal_name].financial_profile_index


def test_sector_options_and_others_bucket(session: Session) -> None:
    _seed_funnel(session)  # 13 companies in EMTAK 62 (>= 10), 1 in EMTAK 64 (Holding OÜ, below threshold)
    funnel = seller_funnel(session, sector=None, limit=100, **BAND)
    options = {opt.code: opt.count for opt in funnel.sector_options}
    assert options == {"62": 13, "others": 1}

    others = seller_funnel(session, sector="others", limit=100, **BAND)
    assert {item.legal_name for item in others.items} == {"Holding OÜ"}
    assert others.peer_groups == []
    # The option list itself never changes with the currently selected sector.
    assert {opt.code: opt.count for opt in others.sector_options} == options


def test_cash_harvesting_candidate_flows_through_the_funnel(session: Session) -> None:
    _add_company(
        session,
        30,
        "Steady Cashco OÜ",
        revenue=(10_000_000, 10_000_000, 10_000_000),  # flat: 0% CAGR, inside [-2%, +3%]
        ebitda_margin=0.25,  # above the 15% threshold
    )
    _add_company(
        session, 31, "Growth Co OÜ", revenue=(10_000_000, 12_000_000, 14_000_000), ebitda_margin=0.25
    )
    session.commit()

    funnel = seller_funnel(session, sector=None, limit=100, **BAND)
    items = {item.legal_name: item for item in funnel.items}

    steady = items["Steady Cashco OÜ"]
    assert steady.cash_harvesting_candidate is True
    assert steady.cash_harvesting_evidence_status == "evaluated"
    assert steady.latest_ebitda_margin == 0.25
    assert "cash_harvesting_candidate" in steady.flags
    assert any("Cash Harvesting candidate" in reason for reason in steady.review_reasons)
    assert steady.owner_intent == "unknown" and steady.buyer_fit == "not_assessed"

    growth = items["Growth Co OÜ"]
    assert growth.cash_harvesting_candidate is False
    assert growth.cash_harvesting_evidence_status == "evaluated"
    assert "cash_harvesting_candidate" not in growth.flags


def test_seller_prospects_endpoint(client, session: Session) -> None:
    empty = client.get("/seller-prospects").json()
    assert empty["total_companies"] == 0 and empty["items"] == []

    _seed_funnel(session)
    body = client.get("/seller-prospects", params={"limit": 5, "view": "all"}).json()
    stage_counts = {s["key"]: s["count"] for s in body["stages"]}
    assert len(body["items"]) == 5 and stage_counts["advisor_review"] == 10
    assert body["items"][0]["next_action"] == "advisor_review"
    assert (
        client.get("/seller-prospects", params={"min_revenue_eur": 9, "max_revenue_eur": 1}).status_code
        == 422
    )


def test_coverage_report_summarises_fields_and_peer_groups(session: Session) -> None:
    from app.analysis.seller_coverage import coverage, to_markdown

    _seed_funnel(session)
    report = coverage(session, **BAND)
    assert report["companies"] == 14
    assert report["funnel_stages"]["advisor_review"] == 10
    assert report["peer_groups_all_sizes"] == {"62": 10}
    assert report["eligible_companies_with_index"] == 10
    assert report["flags"] == {"group_parent": 1, "holding_activity": 1}
    latest = report["financial_rows_by_year_and_scope"][f"{LATEST} standalone"]
    assert latest["rows"] == 14 and latest["revenue"] == 14 and latest["employees_fte"] == 14
    assert "## Field coverage" in to_markdown(report)
