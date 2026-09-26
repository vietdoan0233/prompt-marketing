"""Live connectors, website enrichment and production source-catalog guarantees (no network access)."""

import urllib.robotparser

import pytest
import yaml
from sqlalchemy import func, select

from app.config import API_ROOT, get_settings
from app.connectors.base import CandidateRef, ConnectorError, FetchedSnapshot
from app.connectors.http import PoliteClient
from app.connectors.registries import BrregConnector, PrhConnector
from app.connectors.website import WebsiteConnector
from app.domain import website_extract as wx
from app.models import Company, CompanyFact, Contact, Source
from app.services import ingestion
from tests.conftest import MERGERO_HEADER, import_csv

IMPRESSUM_DE = """Impressum
Angaben gemäß § 5 DDG
Beispielwerk Maschinenbau GmbH & Co. KG
Industriestraße 1
Registergericht: Amtsgericht Ulm, HRB 735450
USt-IdNr.: DE 114 194 819
Geschäftsführer: Dr. Anna Beis­piel, Tom Muster (CEO) und Lena Probe
Verantwortlich für den Inhalt: Redaktion
"""


# ------------------------------------------------------------------ extraction rules


def test_impressum_extraction():
    assert wx.extract_legal_name(IMPRESSUM_DE) == "Beispielwerk Maschinenbau GmbH & Co. KG"
    assert wx.extract_registry_ids(IMPRESSUM_DE, "DE") == ["DE:HRB735450:ULM"]
    assert wx.extract_vat_ids(IMPRESSUM_DE, "DE") == ["DE114194819"]
    text, _ = wx.html_to_text_and_links(f"<p>{IMPRESSUM_DE}</p>", "https://x.test/impressum")
    names = [n for n, _ in wx.extract_managing_directors(text)]
    assert names == ["Dr. Anna Beispiel", "Tom Muster", "Lena Probe"]


def test_court_before_number_and_english_labels():
    text = "Registered Office: Augsburg HRB 41839\nExecutive Board: Jane Roe (Chief Executive Officer), John Doe\n"
    assert wx.extract_registry_ids(text, "DE") == ["DE:HRB41839:AUGSBURG"]
    assert [n for n, _ in wx.extract_managing_directors(text)] == ["Jane Roe", "John Doe"]
    assert wx.extract_registry_ids("Bad Oeynhausen District Court HRA 6218", "DE") == [
        "DE:HRA6218:BADOEYNHAUSEN"
    ]


def test_register_number_without_court_is_not_an_identity():
    assert wx.extract_registry_ids("Handelsregister HRB 4455", "DE") == []


def test_headings_and_truncated_names_are_not_people():
    text = "Managing Directors:\nAdditional Information\nVorständin Finanzen\n"
    assert wx.extract_managing_directors(text) == []


def test_nordic_ids_require_valid_checksum():
    assert wx.extract_registry_ids("Org.nr. 912 345 688", "NO") == ["NO:912345688"]
    assert wx.extract_registry_ids("Org.nr. 912 345 689", "NO") == []  # bad mod-11
    assert wx.extract_registry_ids("Y-tunnus: 0112038-9", "FI") == ["FI:0112038-9"]


def test_link_categorisation_uses_whole_tokens():
    links = [
        ("https://acme.test/c/CLS_DUST_WATER_MANAGEMENT", "Dust management"),
        ("https://acme.test/future/robots-for-the-people", "People"),
        ("https://acme.test/ueber-uns", "Über uns"),
        ("https://acme.test/karriere", "Karriere"),
        ("https://acme.test/impressum", "Impressum"),
        ("https://other.test/impressum", "Impressum"),
    ]
    cats = wx.categorize_links(links, "acme.test")
    assert cats == {
        "impressum": "https://acme.test/impressum",
        "about": "https://acme.test/ueber-uns",
        "careers": "https://acme.test/karriere",
    }


def test_job_counting_and_external_ats():
    links = [
        ("https://acme.test/karriere/stellen/entwickler-m-w-d", "Entwickler (m/w/d)"),
        ("https://acme.test/karriere/stellen/vertrieb", "Vertrieb Innendienst"),
        ("https://acme.test/karriere", "Karriere"),
        ("https://acme.jobs.personio.de/job/1", "Jobs"),
    ]
    assert wx.count_job_postings(links, "https://acme.test/karriere", "acme.test") == (
        2,
        "acme.jobs.personio.de",
    )
    assert wx.count_job_postings([], "https://acme.test/karriere", "acme.test") == (None, None)


def test_website_parse_only_emits_stated_facts():
    evidence = {
        "domain": "beispielwerk.test",
        "final_host": "beispielwerk.test",
        "country": "DE",
        "pages": {
            "home": {"url": "https://beispielwerk.test/", "status": 200, "text": "Willkommen", "links": []},
            "impressum": {
                "url": "https://beispielwerk.test/impressum",
                "status": 200,
                "text": IMPRESSUM_DE,
                "links": [],
            },
            "about": {
                "url": "https://beispielwerk.test/ueber-uns",
                "status": 200,
                "text": "Ein Familienunternehmen seit 1950.",
                "links": [],
            },
        },
    }
    row = WebsiteConnector.parse(None, FetchedSnapshot("beispielwerk.test", evidence, None))[0]  # type: ignore[arg-type]
    assert row["enrichment_only"] is True
    assert row["legal_name"] == "Beispielwerk Maschinenbau GmbH & Co. KG"
    assert row["registry_id"] == "HRB 735450, Amtsgericht Ulm"
    assert row["family_business_signal"] == "true" and "founder_signal" not in row
    assert "open_positions" not in row  # no careers page -> no hiring claim
    assert row["evidence"]["registry_id"] == "https://beispielwerk.test/impressum"
    assert all(c["contact_basis"] == "public-business" and "contact_email" not in c for c in row["contacts"])


# ------------------------------------------------------------------ enrichment ingestion semantics


def _web_source(session, countries=("DE",)):
    src = Source(
        id="web-test",
        tier="C",
        name="web",
        provider="t",
        region="dach",
        countries=list(countries),
        source_type="website",
        source_mode="public-approved",
        permission_status="approved",
        connector_type="website",
        connector_config={"live": True},
        enabled=True,
        allowed_fields=[
            "legal_name",
            "registry_id",
            "vat_id",
            "website",
            "open_positions",
            "founder_signal",
            "family_business_signal",
            "contacts",
        ],
        base_confidence="verified",
        usage_policy="internal-only",
        retention_days=14,
        trust_rank=25,
    )
    session.add(src)
    session.commit()
    return src


def _run_rows(session, source, rows):
    class Stub:
        parser_version = "stub"

        def parse(self, snap):
            return snap.payload

    run = ingestion._new_run(session, source, source.id, kind="discovery", actor="test", min_employees=20)
    return ingestion._execute(session, run, source, Stub(), [FetchedSnapshot("x", rows, None, 200)])


def test_enrichment_never_creates_companies(session):
    src = _web_source(session)
    run = _run_rows(
        session,
        src,
        [
            {
                "source_key": "web:unknown.test",
                "country": "DE",
                "website": "unknown.test",
                "legal_name": "Unknown GmbH",
                "enrichment_only": True,
            }
        ],
    )
    assert run.records[0].outcome == "rejected"
    assert "matched no existing company" in run.records[0].errors[0]
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_enrichment_attaches_by_domain_with_evidence_and_estimated_signals(session):
    import_csv(
        session, "mergero-csv", MERGERO_HEADER + "M-1,Beispielwerk GmbH,DE,Ulm,,,beispielwerk.test,55,,,,\n"
    )
    src = _web_source(session)
    run = _run_rows(
        session,
        src,
        [
            {
                "source_key": "web:beispielwerk.test",
                "country": "DE",
                "website": "beispielwerk.test",
                "enrichment_only": True,
                "registry_id": "HRB 735450, Amtsgericht Ulm",
                "open_positions": "7",
                "founder_signal": "true",
                "estimated_fields": ["open_positions", "founder_signal"],
                "evidence": {
                    "registry_id": "https://beispielwerk.test/impressum",
                    "open_positions": "https://beispielwerk.test/karriere",
                },
                "contacts": [
                    {
                        "contact_name": "Anna Beispiel",
                        "contact_role": "Geschäftsführer",
                        "contact_basis": "public-business",
                    }
                ],
                "source_url": "https://beispielwerk.test/impressum",
            }
        ],
    )
    assert run.records[0].outcome == "updated", run.records[0].errors
    facts = {
        f.field_name: f
        for f in session.scalars(select(CompanyFact).where(CompanyFact.source_id == "web-test"))
    }
    assert set(facts) == {
        "registry_id",
        "open_positions",
        "founder_signal",
    }  # no self-referential website fact
    assert facts["open_positions"].confidence == "estimated"
    assert facts["open_positions"].source_url == "https://beispielwerk.test/karriere"
    assert facts["registry_id"].confidence == "verified"
    contact = session.scalars(select(Contact)).one()
    assert contact.email is None and contact.contact_basis == "public-business"


# ------------------------------------------------------------------ registry connectors (HTTP mocked)


def test_brreg_live_query_filters_at_source(monkeypatch):
    calls = []

    def fake_get_json(self, url, params=None):
        calls.append(params)
        return {
            "_embedded": {
                "enheter": [{"organisasjonsnummer": "912345688", "navn": "X AS", "antallAnsatte": 25}]
            },
            "page": {"totalPages": 3},
        }

    monkeypatch.setattr(PoliteClient, "get_json", fake_get_json)
    refs = BrregConnector(None, live=True).discover({"naeringskode": "62", "max_records": 2}, "nordics", 20)
    assert len(refs) == 2 and len(calls) == 2
    assert (
        calls[0]["fraAntallAnsatte"] == 20
        and calls[0]["konkurs"] == "false"
        and calls[0]["naeringskode"] == "62"
    )


def test_prh_drops_ceased_companies(monkeypatch):
    def fake_get_json(self, url, params=None):
        return {
            "totalResults": 2,
            "companies": [
                {
                    "businessId": {"value": "0112038-9"},
                    "tradeRegisterStatus": "1",
                    "names": [{"name": "Active Oy", "type": "1"}],
                },
                {
                    "businessId": {"value": "0202749-3"},
                    "tradeRegisterStatus": "4",
                    "names": [{"name": "Ceased Oy", "type": "1", "endDate": "2020-03-05"}],
                },
            ],
        }

    monkeypatch.setattr(PoliteClient, "get_json", fake_get_json)
    refs = PrhConnector(None, live=True).discover({"mainBusinessLine": "62100"}, "nordics", 20)
    assert [r.reference for r in refs] == ["0112038-9"]


@pytest.mark.parametrize(("connector_type", "source_id"), [("zefix", "ch-zefix"), ("cvr", "dk-cvr")])
def test_credential_gated_registries_fail_closed(session, monkeypatch, connector_type, source_id):
    monkeypatch.setattr(get_settings(), "live_connectors_enabled", True)
    src = session.get(Source, source_id)
    src.connector_type, src.connector_config, src.enabled = connector_type, {"live": True}, True
    session.commit()
    run = ingestion.start_discovery_run(
        session, source_id=source_id, query={"name": "Informatik"}, actor="test"
    )
    assert run.status == "FAILED" and "credentials missing" in run.errors[0]["message"]
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_robots_txt_is_enforced():
    client = PoliteClient(None, respect_robots=True)
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /"])
    client._robots["https://blocked.test"] = rp
    with pytest.raises(ConnectorError, match="robots.txt"):
        client.request("GET", "https://blocked.test/impressum")


def test_website_discovery_requires_targets():
    with pytest.raises(ConnectorError):
        WebsiteConnector(60).discover({}, "dach", 20)
    refs = WebsiteConnector(60).discover({"targets": [{"domain": "a.test", "country": "DE"}]}, "dach", 20)
    assert refs == [CandidateRef("a.test", "https://a.test", {"domain": "a.test", "country": "DE"})]


# ------------------------------------------------------------------ production catalog guarantees


def test_production_catalog_is_real_and_fail_closed():
    cfg = yaml.safe_load((API_ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    blob = (API_ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")
    assert ".example" not in blob and "fixture" not in blob.replace("fixtures", "")
    sources = cfg["sources"]
    by_tier = {t: [s for s in sources if s["tier"] == t] for t in "ALCI"}
    # Every licensed source needs a contract before it can ingest.
    assert all(
        s["permission_status"] != "approved" and not s["enabled"]
        for s in by_tier["L"]
        if s["id"] != "linkedin-scrape"
    )
    # Every approved registry/website connector is a live connector.
    for s in sources:
        if s["permission_status"] == "approved" and s["connector_type"] in {
            "brreg",
            "prh",
            "zefix",
            "cvr",
            "website",
        }:
            assert s["connector_config"]["live"] is True
    covered = {c for s in by_tier["A"] for c in s["countries"]}
    assert covered == {"DE", "AT", "CH", "FI", "SE", "NO", "DK", "IS"}
    assert {c for s in by_tier["C"] for c in s["countries"]} == covered
