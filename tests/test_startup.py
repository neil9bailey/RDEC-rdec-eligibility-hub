"""Coverage for the application startup path.

Before this module no test entered the ``TestClient`` context manager, so the FastAPI
lifespan -- ``init_db()``, ``apply_sqlite_schema_updates()``, ``validate_all_rules()``
and reference/demo seeding -- had never run under test.

Entering the lifespan writes to whatever ``app.database.engine`` points at. Every test
here binds that engine (and the separate name ``app.main`` bound at import time) to a
per-test temporary file first, and the live repository database is fingerprinted to
prove it was never opened.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import Session, select

import app.database as database
import app.main as main
import app.seed as seed
from app.models import AuditEvent, BusinessUnit, Company, Customer
from app.settings import get_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_DB_PATH = REPO_ROOT / "data" / "rdec_hub.db"


def live_db_fingerprint() -> tuple[int, int] | None:
    """Size and nanosecond mtime of the sponsor's live database, or None if absent."""
    if not LIVE_DB_PATH.exists():
        return None
    stat = LIVE_DB_PATH.stat()
    return (stat.st_size, stat.st_mtime_ns)


@pytest.fixture()
def startup_engine(tmp_path, monkeypatch):
    db_path = tmp_path / "startup_hub.db"
    engine = database.create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(main, "engine", engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def startup_client(startup_engine):
    with TestClient(main.app) as client:
        yield client


def test_test_session_database_url_is_isolated_from_the_repository_database():
    """Guard: the suite must never resolve to ./data/rdec_hub.db."""
    configured = os.environ["DATABASE_URL"]
    assert configured.startswith("sqlite:///")
    resolved = Path(configured.replace("sqlite:///", "", 1)).resolve()
    assert resolved != LIVE_DB_PATH.resolve()
    assert resolved.parent != LIVE_DB_PATH.parent
    assert get_settings().database_url == configured


def test_startup_creates_the_schema_and_serves_health_routes(startup_client, startup_engine):
    tables = set(inspect(startup_engine).get_table_names())
    assert {"company", "customer", "businessunit", "rdproject", "costline", "auditevent"}.issubset(tables)

    assert startup_client.get("/healthz").status_code == 200
    assert startup_client.get("/health").status_code == 200
    assert startup_client.get("/evidence-index").status_code == 200


def test_startup_upgrades_a_legacy_database_without_a_foreign_key(startup_engine, startup_client):
    """apply_sqlite_schema_updates() is the only path that adds customer.business_unit_id.

    It also records ADR-0005 Fact 2: SQLite cannot attach a REFERENCES clause via
    ALTER TABLE, so the upgraded column carries no constraint while the freshly created
    one does. The fresh constraint is asserted first, from the schema startup just built.
    """
    with startup_engine.begin() as connection:
        fresh_constraints = connection.execute(text("PRAGMA foreign_key_list(customer)")).fetchall()
        ddl = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='customer'")
        ).scalar_one()
    assert any(row[3] == "business_unit_id" for row in fresh_constraints), "fresh schema should carry the FK"

    legacy_ddl = "\n".join(line for line in ddl.splitlines() if "business_unit_id" not in line)
    legacy_ddl = re.sub(r",(\s*\))", r"\1", legacy_ddl)
    assert "business_unit_id" not in legacy_ddl

    with startup_engine.begin() as connection:
        connection.execute(text("DROP TABLE customer"))
        connection.execute(text(legacy_ddl))
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(customer)")).fetchall()}
    assert "business_unit_id" not in columns

    database.apply_sqlite_schema_updates()

    with startup_engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(customer)")).fetchall()}
        upgraded_constraints = connection.execute(text("PRAGMA foreign_key_list(customer)")).fetchall()
    assert "business_unit_id" in columns
    assert upgraded_constraints == [], "ALTER TABLE cannot add a REFERENCES clause in SQLite"


def test_startup_is_idempotent_across_two_runs(startup_engine):
    with TestClient(main.app):
        pass
    with Session(startup_engine) as session:
        first = len(list(session.exec(select(BusinessUnit))))

    with TestClient(main.app):
        pass
    with Session(startup_engine) as session:
        second = len(list(session.exec(select(BusinessUnit))))
        names = [unit.name for unit in session.exec(select(BusinessUnit))]

    assert first == second
    assert len(names) == len(set(names))


def test_startup_seeds_reference_data_by_default(startup_client, startup_engine):
    with Session(startup_engine) as session:
        units = {unit.name for unit in session.exec(select(BusinessUnit))}
        customers = {customer.customer_name for customer in session.exec(select(Customer))}
    assert {"Transport", "Highways", "Rail", "SCADA", "TfL"}.issubset(units)
    assert "Transport for London (TfL)" in customers


def test_startup_skips_reference_seeding_when_the_setting_is_off(startup_engine, monkeypatch):
    monkeypatch.setattr(get_settings(), "seed_reference_data", False)
    with TestClient(main.app):
        pass
    with Session(startup_engine) as session:
        assert list(session.exec(select(BusinessUnit))) == []


def test_startup_seeds_demo_data_when_the_setting_is_on(startup_engine, monkeypatch):
    """The SEED_DEMO_DATA path documented in README.md, exercised end to end."""
    monkeypatch.setattr(get_settings(), "seed_demo_data", True)
    with TestClient(main.app):
        pass
    with Session(startup_engine) as session:
        companies = [company.company_name for company in session.exec(select(Company))]
    assert "Northstar Digital Services Ltd" in companies


def test_startup_fails_loudly_when_rule_validation_fails(startup_engine, monkeypatch):
    def broken_validate_all_rules():
        raise ValueError("rule file missing a required key")

    monkeypatch.setattr(main, "validate_all_rules", broken_validate_all_rules)
    with pytest.raises(ValueError, match="required key"):
        with TestClient(main.app):
            pass


def test_reference_seeding_writes_exactly_one_sentinel_and_respects_a_rename(startup_engine):
    """ADR-0005 D5, verification 9: renamed reference records must stay renamed."""
    with TestClient(main.app):
        pass
    with Session(startup_engine) as session:
        unit = session.exec(select(BusinessUnit).where(BusinessUnit.name == "SCADA")).first()
        assert unit is not None
        unit.name = "SCADA and operational technology"
        session.add(unit)
        session.commit()

    with TestClient(main.app):
        pass

    with Session(startup_engine) as session:
        names = {unit.name for unit in session.exec(select(BusinessUnit))}
        sentinels = list(
            session.exec(
                select(AuditEvent).where(AuditEvent.action == seed.REFERENCE_SEED_ACTION)
            )
        )
    assert "SCADA" not in names
    assert "SCADA and operational technology" in names
    assert len(sentinels) == 1
    assert sentinels[0].entity_type == "ReferenceData"
    assert seed.reference_seed_marker() in sentinels[0].summary


def test_reference_seeding_does_not_recreate_a_deleted_reference_customer(startup_engine):
    with TestClient(main.app):
        pass
    with Session(startup_engine) as session:
        customer = session.exec(
            select(Customer).where(Customer.customer_name == "Transport for London (TfL)")
        ).first()
        assert customer is not None
        session.delete(customer)
        session.commit()

    with TestClient(main.app):
        pass

    with Session(startup_engine) as session:
        names = [customer.customer_name for customer in session.exec(select(Customer))]
    assert "Transport for London (TfL)" not in names


def test_a_reference_seed_version_bump_runs_a_new_wave(startup_engine, monkeypatch):
    """ADR-0005 D5.3: re-seedability is preserved without a schema change."""
    with TestClient(main.app):
        pass
    with Session(startup_engine) as session:
        unit = session.exec(select(BusinessUnit).where(BusinessUnit.name == "Rail")).first()
        session.delete(unit)
        session.commit()

    monkeypatch.setattr(seed, "REFERENCE_SEED_VERSION", 2)
    with TestClient(main.app):
        pass

    with Session(startup_engine) as session:
        names = {unit.name for unit in session.exec(select(BusinessUnit))}
        sentinels = list(
            session.exec(
                select(AuditEvent).where(AuditEvent.action == seed.REFERENCE_SEED_ACTION)
            )
        )
    assert "Rail" in names
    assert len(sentinels) == 2


def test_demo_seeding_is_not_guarded_by_the_reference_sentinel(session):
    """ADR-0005 D5.5: seed_demo_data must keep seeding a fresh database on every call."""
    seed.seed_demo_data(session)

    assert seed.reference_seed_complete(session) is True
    companies = [company.company_name for company in session.exec(select(Company))]
    units = {unit.name for unit in session.exec(select(BusinessUnit))}
    assert "Northstar Digital Services Ltd" in companies
    assert "Transport" in units


def test_startup_never_touches_the_live_repository_database(startup_engine):
    before = live_db_fingerprint()
    with TestClient(main.app) as client:
        client.get("/healthz")
        client.get("/")
    assert live_db_fingerprint() == before
