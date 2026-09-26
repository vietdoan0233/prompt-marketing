import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import Base, get_session, make_engine
from app.main import app
from app.models import Source
from app.services import ingestion
from app.services.source_registry import sync_sources

FIXTURES = get_settings().fixtures_dir
# Tests use their own synthetic source registry + fixtures; production (config/sources.yaml) is live-only.
TEST_SOURCES = yaml.safe_load((FIXTURES / "sources_test.yaml").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    # A developer's api/.env may enable live connectors; the test suite must never hit the network.
    monkeypatch.setattr(get_settings(), "live_connectors_enabled", False)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    # Default: throwaway SQLite per test. Set TEST_DATABASE_URL to run the suite against PostgreSQL.
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with maker() as s:
        sync_sources(s, actor="test", config=TEST_SOURCES)
        s.commit()
        yield s
    engine.dispose()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def import_csv(session: Session, source_id: str, text: str, file_name: str = "test.csv", **kw):
    return ingestion.start_csv_run(
        session, source_id=source_id, file_name=file_name, text=text, actor="test", **kw
    )


def import_fixture(session: Session, source_id: str, file_name: str):
    return import_csv(session, source_id, (FIXTURES / file_name).read_text(encoding="utf-8"), file_name)


def seed_all(session: Session) -> None:
    ingestion.start_discovery_run(session, source_id="fi-prh-ytj", query={}, actor="test")
    ingestion.start_discovery_run(session, source_id="no-brreg", query={}, actor="test")
    for source_id, name in [
        ("se-bolagsverket-allabolag", "allabolag_export.csv"),
        ("de-handelsregister", "handelsregister_export.csv"),
        ("at-firmenbuch", "firmenbuch_export.csv"),
        ("ch-zefix", "zefix_export.csv"),
        ("web-impressum", "impressum_extract.csv"),
        ("licensed-firmographics", "licensed_firmographics.csv"),
        ("mergero-csv", "mergero_companies.csv"),
    ]:
        import_fixture(session, source_id, name)


def source(session: Session, source_id: str) -> Source:
    s = session.get(Source, source_id)
    assert s is not None
    return s


MERGERO_HEADER = (
    "source_key,legal_name,country,city,registry_id,vat_id,website,employees,"
    "contact_name,contact_role,contact_email,contact_basis\n"
)
