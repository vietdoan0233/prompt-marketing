import pytest

from app.domain import normalize as n
from app.domain.records import build_record


@pytest.mark.parametrize(
    ("country", "raw", "canonical"),
    [
        ("FI", "2931457-2", "FI:2931457-2"),
        ("FI", "29314572", "FI:2931457-2"),
        ("SE", "556812-3458", "SE:556812-3458"),
        ("SE", "165568123458", "SE:556812-3458"),
        ("NO", "912 345 688", "NO:912345688"),
        ("DK", "41234567", "DK:41234567"),
        ("CH", "CHE-112.345.676", "CH:CHE112345676"),
        ("AT", "FN 234567k", "AT:FN234567k"),
        ("DE", "HRB 123456, Amtsgericht München", "DE:HRB123456:MUNCHEN"),
        ("DE", "HRB 98765 München", "DE:HRB98765:MUNCHEN"),
        ("DE", "HRB 201234 B, Amtsgericht Charlottenburg", "DE:HRB201234B:CHARLOTTENBURG"),
    ],
)
def test_registry_ids_normalize(country, raw, canonical):
    assert n.normalize_registry_id(country, raw).canonical == canonical


@pytest.mark.parametrize(
    ("country", "raw"),
    [
        ("FI", "2931457-3"),
        ("SE", "556812-3999"),
        ("NO", "912345689"),
        ("CH", "CHE-112.345.677"),
        ("DE", "HRB 4455"),
    ],
)
def test_invalid_registry_ids_are_rejected_not_guessed(country, raw):
    res = n.normalize_registry_id(country, raw)
    assert res.canonical is None
    assert res.warning


def test_vat_registry_equivalence_is_deterministic():
    assert n.normalize_registry_id("FI", "3018822-7").derived_vat == "FI30188227"
    assert n.registry_from_vat("FI", "FI30188227") == "FI:3018822-7"
    assert n.normalize_vat("NO", "NO 912 345 688 MVA") == ("NO912345688", None)
    assert n.normalize_vat("CH", "CHE-112.345.676 MWST") == ("CHE112345676", None)
    assert n.registry_from_vat("CH", "CHE112345676") == "CH:CHE112345676"
    assert n.normalize_vat("DE", "DE12")[0] is None


def test_domains():
    assert (
        n.normalize_domain("https://www.Alpenblick-Software.example/impressum")
        == "alpenblick-software.example"
    )
    assert n.normalize_domain("www.kuusisto-analytics.example") == "kuusisto-analytics.example"
    assert n.normalize_domain("not a url") is None
    assert n.is_generic_domain("gmail.com")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("35", (35, 35)),
        ("20-49", (20, 49)),
        ("20 – 49 anställda", (20, 49)),
        ("250+", (250, None)),
        ("1 200", (1200, 1200)),
        ("unknown", None),
        ("", None),
        ("many", None),
    ],
)
def test_employee_ranges(raw, expected):
    assert n.parse_employee_range(raw) == expected


def test_name_normalization_strips_legal_forms_and_accents():
    assert n.normalize_name("Vänern Industri AB") == n.normalize_name("Vanern Industri")
    assert n.normalize_name("Spree Digital UG (haftungsbeschränkt)") == "spree digital"
    assert n.normalize_name("NORDLYS PROGRAMVARE AS") == "nordlys programvare"


def test_email_is_validated_never_constructed():
    assert n.normalize_email("Liisa.K@Example.example") == ("liisa.k@example.example", None)
    assert n.normalize_email("not-an-email")[0] is None
    assert not any(name.startswith(("guess", "infer", "generate")) for name in dir(n))


def test_record_drops_fields_not_allowed_and_keeps_original_values():
    rec = build_record(
        1,
        {
            "legal_name": "Test Oy",
            "country": "Finland",
            "registry_id": "29314572",
            "revenue": "5M",
            "currency": "EUR",
        },
        source_countries=["FI"],
        allowed_fields=["legal_name", "registry_id"],
    )
    assert rec.ok
    assert "revenue" not in rec.facts
    assert any("revenue" in w and "not allowed" in w for w in rec.warnings)
    assert rec.facts["registry_id"] == "2931457-2"
    assert rec.originals["registry_id"] == "29314572"


def test_record_without_headcount_stays_unknown():
    rec = build_record(
        1,
        {"legal_name": "X Oy", "country": "FI"},
        source_countries=["FI"],
        allowed_fields=["legal_name", "employees"],
    )
    assert "employees" not in rec.facts
    assert any("qualification is unknown" in w for w in rec.warnings)
