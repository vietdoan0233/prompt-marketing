"""Headcount qualification (>= 20 employees) and sub-scale flagging."""

import pytest
from sqlalchemy import select

from app.domain.qualification import Qualification, qualify
from app.models import Company
from tests.conftest import MERGERO_HEADER, import_csv, seed_all


@pytest.mark.parametrize(
    ("lo", "hi", "expected"),
    [
        (20, 20, Qualification.QUALIFIED),
        (20, 49, Qualification.QUALIFIED),
        (250, None, Qualification.QUALIFIED),
        (19, 19, Qualification.BELOW_THRESHOLD),
        (3, 9, Qualification.BELOW_THRESHOLD),
        (1, 1, Qualification.SUB_SCALE),
        (2, 2, Qualification.SUB_SCALE),
        (1, 2, Qualification.SUB_SCALE),
        (10, 49, Qualification.BORDERLINE),
        (None, None, Qualification.UNKNOWN),
    ],
)
def test_qualify(lo, hi, expected):
    assert qualify(lo, hi) == expected


def test_threshold_is_configurable():
    assert qualify(15, 15, min_employees=10) == Qualification.QUALIFIED


def test_import_tags_qualification_and_counts(session):
    csv = MERGERO_HEADER + (
        "A,Big Oy,FI,,2931457-2,,,45,,,,\n"
        "B,Solo Oy,FI,,3018822-7,,,1,,,,\n"
        "C,Mid Oy,FI,,2765510-3,,,12,,,,\n"
        "D,Mystery Oy,FI,,3120045-1,,,,,,,\n"
    )
    run = import_csv(session, "mergero-csv", csv)
    assert run.counts["qualified"] == 1
    assert run.counts["sub_scale"] == 1
    assert run.counts["below_threshold"] == 1
    assert run.counts["unknown_headcount"] == 1
    mystery = session.scalars(select(Company).where(Company.legal_name == "Mystery Oy")).one()
    assert mystery.estimated_employee_min is None  # never guessed


def test_company_list_defaults_to_20_plus(client, session):
    seed_all(session)
    default = client.get("/companies").json()
    assert default["filters"]["min_employees"] == 20
    assert default["items"] and all(i["estimated_employee_min"] >= 20 for i in default["items"])
    everything = client.get("/companies", params={"min_employees": 0, "page_size": 200}).json()
    sub_scale = {i["legal_name"] for i in everything["items"] if i["qualification_status"] == "sub_scale"}
    assert {
        "Grazer Web Studio e.U.",
        "Norrsken Konsult AB",
        "Spree Digital UG (haftungsbeschränkt)",
    } <= sub_scale
    assert not sub_scale & {i["legal_name"] for i in default["items"]}


def test_sub_scale_warning_in_detail(client, session):
    seed_all(session)
    items = client.get("/companies", params={"min_employees": 0, "qualification": "sub_scale"}).json()[
        "items"
    ]
    detail = client.get(f"/companies/{items[0]['id']}").json()
    assert any("sub-scale" in w for w in detail["warnings"])
