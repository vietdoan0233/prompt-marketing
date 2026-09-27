"""GET /companies sorting that must hold across pages, not only within the visible page."""

from sqlalchemy.orm import Session

from app.models import Company, CompanyFact, utcnow

FIELDS = ["registry_id", "employees", "industry_code", "website", "city"]


def _company(session: Session, name: str, n_fields: int, codes: list[str]) -> Company:
    company = Company(legal_name=name, normalized_name=name.casefold(), country="EE", industry_codes=codes)
    session.add(company)
    session.flush()
    now = utcnow()
    for i, field in enumerate(FIELDS[:n_fields]):
        session.add(
            CompanyFact(
                company_id=company.id,
                field_name=field,
                value_json=f"{field}-{name}",
                value_hash=f"{name}-{i}",
                source_id="ee-ariregister",
                observed_at=now,
                valid_from=now,
                base_confidence="verified",
                confidence="verified",
                usage_policy="internal-only",
            )
        )
    return company


def _names(client, **params) -> list[str]:
    out: list[str] = []
    page = 1
    while True:
        body = client.get(
            "/companies", params={"min_employees": 0, "page_size": 2, "page": page, **params}
        ).json()
        out += [item["legal_name"] for item in body["items"]]
        if page * body["page_size"] >= body["total"]:
            return out
        page += 1


def test_completeness_sort_orders_the_whole_set_before_paging(client, session: Session) -> None:
    # Insertion order deliberately differs from completeness order.
    for name, n in [("A", 1), ("B", 5), ("C", 0), ("D", 3), ("E", 4)]:
        _company(session, name, n, ["62011"])
    session.commit()

    assert _names(client, sort="completeness", order="desc") == ["B", "E", "D", "A", "C"]
    assert _names(client, sort="completeness", order="asc") == ["C", "A", "D", "E", "B"]


def test_sector_sort_uses_emtak_division_with_missing_codes_last(client, session: Session) -> None:
    _company(session, "Software", 1, ["62011"])
    _company(session, "Food", 1, ["10110"])
    _company(session, "Unknown", 1, [])
    _company(session, "Printing", 1, ["18121"])
    session.commit()

    assert _names(client, sort="sector", order="asc") == ["Food", "Printing", "Software", "Unknown"]
    assert _names(client, sort="sector", order="desc") == ["Software", "Printing", "Food", "Unknown"]
