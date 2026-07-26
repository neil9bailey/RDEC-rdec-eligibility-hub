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
from markupsafe import escape
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select
from starlette.requests import Request

import app.data_integrity as data_integrity
import app.database as database
import app.main as main
import app.seed as seed
from app.models import AuditEvent, BusinessUnit, Company, Contract, Customer
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


# Link integrity: pragma installation, constraint presence, and the orphan pre-scan.
# ADR-0005 D1, D2 and D3. These exercise the control directly, against their own engine; the
# lifespan tests further down prove the application actually calls it.


FK_EVIDENCE_TABLES = (
    "contract",
    "solution",
    "rdproject",
    "accountingperiod",
    "costline",
    "evidenceitem",
)


@pytest.fixture()
def fk_engine(tmp_path):
    """A file-backed engine: the dispose test needs a database that survives pool disposal."""
    engine = database.make_engine(f"sqlite:///{(tmp_path / 'fk.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        database.FK_ENFORCEMENT = False
        data_integrity.INTEGRITY_REPORT = ()
        data_integrity.INTEGRITY_WARNING = None
        engine.dispose()


def pragma_value(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("PRAGMA foreign_keys")).scalar_one()


def seed_minimum_parents(session) -> int:
    unit = BusinessUnit(name="Transport", description="Test unit")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    customer = Customer(business_unit_id=unit.id, customer_name="Test Customer")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return int(customer.id)


def test_the_pragma_listener_covers_an_engine_built_anywhere(fk_engine):
    """D1.1: the listener is on the Engine class, so the tests' own engine is covered."""
    assert database.FK_ENFORCEMENT is False
    assert pragma_value(fk_engine) == 0

    database.set_fk_enforcement(True, fk_engine)
    assert pragma_value(fk_engine) == 1


def test_flipping_the_flag_without_disposing_leaves_a_pooled_connection_unchanged(fk_engine):
    """D1.3: this is the step that is easy to omit and silently defeats the whole control."""
    connection = fk_engine.connect()
    try:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 0
        database.FK_ENFORCEMENT = True
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 0
    finally:
        connection.close()

    fk_engine.dispose()
    assert pragma_value(fk_engine) == 1


def test_enforcement_off_accepts_a_child_row_with_no_parent(fk_engine):
    """The behaviour in production today, recorded so the change is visible."""
    with Session(fk_engine) as session:
        session.add(Contract(contract_name="Orphan contract", customer_id=4242))
        session.commit()
        assert session.exec(select(Contract)).first().customer_id == 4242


def test_enforcement_on_rejects_a_child_row_with_no_parent(fk_engine):
    """ADR-0005 verification 3: proves a constraint exists in the DDL, not just that a flag is set."""
    database.set_fk_enforcement(True, fk_engine)
    with Session(fk_engine) as session:
        session.add(Contract(contract_name="Orphan contract", customer_id=4242))
        with pytest.raises(IntegrityError):
            session.commit()


def test_a_fresh_schema_carries_every_foreign_key_the_scan_relies_on(fk_engine):
    """ADR-0005 D2 precondition evidence, kept as a test so it cannot quietly stop being true."""
    with fk_engine.connect() as connection:
        for table in FK_EVIDENCE_TABLES:
            constraints = connection.execute(text(f"PRAGMA foreign_key_list({table})")).fetchall()
            assert constraints, f"{table} has no foreign key in the fresh schema"
    assert data_integrity.missing_foreign_key_constraints(fk_engine) == {}


def test_an_alter_added_column_is_reported_as_unenforced(fk_engine, monkeypatch):
    """ADR-0005 Fact 2: the upgraded database the sponsor runs can differ from a fresh one."""
    with fk_engine.begin() as connection:
        ddl = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='customer'")
        ).scalar_one()
        legacy_ddl = re.sub(r",(\s*\))", r"\1", "\n".join(
            line for line in ddl.splitlines() if "business_unit_id" not in line
        ))
        connection.execute(text("DROP TABLE customer"))
        connection.execute(text(legacy_ddl))

    monkeypatch.setattr(database, "engine", fk_engine)
    database.apply_sqlite_schema_updates()

    assert data_integrity.missing_foreign_key_constraints(fk_engine) == {"customer": ("business_unit_id",)}


def test_scan_orphans_reports_the_orphan_and_changes_nothing(fk_engine):
    with Session(fk_engine) as session:
        customer_id = seed_minimum_parents(session)
        session.add(Contract(contract_name="Kept contract", customer_id=customer_id))
        session.add(Contract(contract_name="Orphan contract", customer_id=4242))
        session.commit()
        before = [
            (contract.id, contract.contract_name, contract.customer_id)
            for contract in session.exec(select(Contract))
        ]

        orphans = data_integrity.scan_orphans(session)

        after = [
            (contract.id, contract.contract_name, contract.customer_id)
            for contract in session.exec(select(Contract))
        ]

    assert len(orphans) == 1
    orphan = orphans[0]
    assert orphan.child_dataset == "contracts"
    assert orphan.child_display == "Orphan contract"
    assert orphan.field == "customer_id"
    assert orphan.parent_dataset == "customers"
    assert orphan.missing_parent_id == 4242
    assert after == before


def test_scan_orphans_uses_the_import_paths_foreign_key_source_of_truth():
    """D3.2: two consumers, one list. A second hand-maintained list would drift invisibly."""
    from app.data_management import DATASET_BY_KEY, DATASETS

    checked = [(spec.key, field, parent) for spec in DATASETS for field, parent in spec.foreign_keys]
    assert checked, "the import path declares no foreign keys"
    for _, _, parent_key in checked:
        assert parent_key in DATASET_BY_KEY


def test_an_orphan_withholds_enforcement_instead_of_repairing_anything(fk_engine):
    """D3: the new control is what gets withheld; the operator's records are left alone."""
    with Session(fk_engine) as session:
        seed_minimum_parents(session)
        session.add(Contract(contract_name="Orphan contract", customer_id=4242))
        session.commit()

        result = data_integrity.apply_foreign_key_policy(session, fk_engine)

        surviving = [contract.contract_name for contract in session.exec(select(Contract))]

    assert result.enforcement_enabled is False
    assert database.FK_ENFORCEMENT is False
    assert pragma_value(fk_engine) == 0
    assert len(result.orphans) == 1
    assert "Orphan contract" in surviving
    assert "link checking is switched off" in (result.warning or "")
    assert data_integrity.INTEGRITY_WARNING == result.warning


def test_a_clean_database_enables_enforcement_and_disposes_the_pool(fk_engine):
    with Session(fk_engine) as session:
        seed_minimum_parents(session)
        result = data_integrity.apply_foreign_key_policy(session, fk_engine)

    assert result.enforcement_enabled is True
    assert result.orphans == ()
    assert result.warning is None
    assert pragma_value(fk_engine) == 1


def test_the_operator_escape_hatch_withholds_enforcement(fk_engine, monkeypatch):
    """D3.6: ENFORCE_FOREIGN_KEYS=false, logged loudly, banner still shown."""
    monkeypatch.setattr(database.settings, "enforce_foreign_keys", False)
    with Session(fk_engine) as session:
        seed_minimum_parents(session)
        result = data_integrity.apply_foreign_key_policy(session, fk_engine)

    assert result.enforcement_enabled is False
    assert pragma_value(fk_engine) == 0


#: Copy that would turn an operational report into a claim of repair, and copy that would pass a
#: verdict on the operator's records. ADR-0005 D3.5 forbids the first outright -- the Hub never
#: modifies, quarantines or deletes -- and ADR-0002 line 59 forbids the second. Stated here rather
#: than imported from the template test that also checks them: this asserts the *published string*,
#: which is composed in app/data_integrity.py and reaches the page as data, and a shared constant
#: across two independently owned files would couple them for no gain.
BANNER_FORBIDDEN_COPY = (
    "repair",
    "fixed",
    "fix them",
    "corrected",
    "cleaned up",
    "removed for you",
    "we will",
    "invalid",
    "corrupt",
    "damaged",
)
FORBIDDEN_VERDICT_WORDS = ("not eligible", "rejected", "fails", "approved", "qualifies")


def assert_reads_as_an_operational_report(warning: str) -> None:
    lowered = warning.lower()
    offenders = [word for word in BANNER_FORBIDDEN_COPY if word in lowered]
    assert not offenders, f"the published warning {warning!r} contains {offenders}"
    for word in FORBIDDEN_VERDICT_WORDS:
        assert word not in lowered, f"the published warning reads as a verdict: {word!r}"


def test_the_escape_hatch_publishes_a_banner_on_a_clean_database(fk_engine, monkeypatch):
    """D3.6, the gap this closes: enforcement off by setting, database clean, banner still shown.

    The published warning used to be ``orphan_warning_text(orphans)``, which returns None when
    there are no orphans -- so the workspace whose operator had switched link checking off saw
    nothing at all, which is the one state where no scan will ever raise it. The banner renders
    on a truthiness guard, so None is not a banner with empty text; it is no banner.
    """
    monkeypatch.setattr(database.settings, "enforce_foreign_keys", False)
    with Session(fk_engine) as session:
        seed_minimum_parents(session)
        result = data_integrity.apply_foreign_key_policy(session, fk_engine)

    assert result.orphans == (), "the database under test is meant to be clean"
    assert result.enforcement_enabled is False
    warning = result.warning
    assert warning, "enforcement is off and the operator is told nothing"
    assert data_integrity.INTEGRITY_WARNING == warning
    assert "link checking is switched off" in warning
    assert data_integrity.ENFORCEMENT_SETTING in warning, "the operator is not told which lever"
    assert "Nothing has been changed or removed." in warning
    assert "points at" not in warning and "point at" not in warning, (
        f"a clean database must not be described as having dangling links: {warning!r}"
    )
    assert_reads_as_an_operational_report(warning)


def test_the_escape_hatch_banner_reports_both_reasons_when_both_hold(fk_engine, monkeypatch):
    """Enforcement off by setting AND orphans present: neither reason may hide the other."""
    monkeypatch.setattr(database.settings, "enforce_foreign_keys", False)
    with Session(fk_engine) as session:
        seed_minimum_parents(session)
        session.add(Contract(contract_name="Orphan contract", customer_id=4242))
        session.commit()
        result = data_integrity.apply_foreign_key_policy(session, fk_engine)

    warning = result.warning or ""
    assert len(result.orphans) == 1
    assert data_integrity.ENFORCEMENT_SETTING in warning, "the setting reason is missing"
    assert "1 record points at a record that is no longer in the Hub." in warning, (
        f"the orphan reason is missing: {warning!r}"
    )
    assert "Open each listed record" in warning, "the operator is not told what to do"
    assert_reads_as_an_operational_report(warning)


def test_an_orphan_banner_is_unchanged_while_the_setting_is_on(fk_engine):
    """The wording every existing test and the rendered-page tests pin must not have moved."""
    with Session(fk_engine) as session:
        seed_minimum_parents(session)
        session.add(Contract(contract_name="Orphan contract", customer_id=4242))
        session.commit()
        result = data_integrity.apply_foreign_key_policy(session, fk_engine)

    assert result.warning == data_integrity.orphan_warning_text(result.orphans)
    assert result.warning == (
        "1 record points at a record that is no longer in the Hub, so link checking is switched "
        "off until that is resolved. Nothing has been changed or removed. Open each listed record "
        "and choose the correct link on its own page."
    )


def test_nothing_is_published_when_link_checking_is_actually_running(fk_engine):
    """The other half of D3.6: a banner on a workspace with nothing wrong would train it away."""
    with Session(fk_engine) as session:
        seed_minimum_parents(session)
        result = data_integrity.apply_foreign_key_policy(session, fk_engine)

    assert result.enforcement_enabled is True
    assert result.warning is None
    assert data_integrity.INTEGRITY_WARNING is None


# The lifespan wiring. Everything above proves the control works when it is called; these prove the
# application actually calls it, which is the part that was still inert (ADR-0005 D3.3 and D3.4).


def bare_request() -> Request:
    """A minimal GET request, enough for template_context to read its query parameters."""
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    )


def test_startup_switches_link_enforcement_on_when_the_scan_is_clean(startup_engine):
    """D3.3 step 3, end to end: the pragma is live for requests, not merely flipped in a variable."""
    assert database.FK_ENFORCEMENT is False
    with TestClient(main.app) as client:
        assert database.FK_ENFORCEMENT is True
        assert pragma_value(startup_engine) == 1
        assert data_integrity.INTEGRITY_WARNING is None
        assert main.template_context(bare_request())["data_integrity_warning"] is None
        assert client.get("/").status_code == 200

    # Shutdown hands the process-global flag back, so one lifecycle cannot silently configure
    # the next one.
    assert database.FK_ENFORCEMENT is False


def test_enforcement_switched_on_at_startup_rejects_an_orphan_write(startup_engine):
    """The pragma the lifespan installs refuses a dangling link, rather than only reading as 1."""
    with TestClient(main.app):
        with Session(startup_engine) as session:
            session.add(Contract(contract_name="Orphan contract", customer_id=4242))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        with Session(startup_engine) as session:
            assert list(session.exec(select(Contract))) == []


def test_startup_withholds_enforcement_and_publishes_the_warning_for_an_orphan(startup_engine):
    """D3: the app starts, says so on every page, and does not touch the operator's records."""
    SQLModel.metadata.create_all(startup_engine)
    with Session(startup_engine) as session:
        seed_minimum_parents(session)
        session.add(Contract(contract_name="Orphan contract", customer_id=4242))
        session.commit()
        before = [
            (contract.id, contract.contract_name, contract.customer_id)
            for contract in session.exec(select(Contract))
        ]

    with TestClient(main.app) as client:
        assert database.FK_ENFORCEMENT is False
        assert pragma_value(startup_engine) == 0
        warning = data_integrity.INTEGRITY_WARNING
        assert warning is not None and "link checking is switched off" in warning
        assert main.template_context(bare_request())["data_integrity_warning"] == warning
        assert client.get("/").status_code == 200

    with Session(startup_engine) as session:
        after = [
            (contract.id, contract.contract_name, contract.customer_id)
            for contract in session.exec(select(Contract))
        ]
    assert after == before


def test_startup_publishes_the_escape_hatch_banner_and_renders_it(startup_engine, monkeypatch):
    """D3.6 end to end: enforcement off by setting, clean database, sentence on the page.

    The unit tests above prove the string is composed; this proves it is published into every
    template context and survives to the rendered HTML, which is the only form the operator ever
    sees. Patching the module globals first is what stops this lifespan from configuring a later
    test: monkeypatch restores their pre-test values on teardown.
    """
    monkeypatch.setattr(data_integrity, "INTEGRITY_WARNING", None)
    monkeypatch.setattr(data_integrity, "INTEGRITY_REPORT", ())
    monkeypatch.setattr(database.settings, "enforce_foreign_keys", False)

    with TestClient(main.app) as client:
        assert database.FK_ENFORCEMENT is False
        assert pragma_value(startup_engine) == 0
        warning = data_integrity.INTEGRITY_WARNING
        assert warning, "the escape hatch is on and no warning was published"
        assert data_integrity.ENFORCEMENT_SETTING in warning
        assert main.template_context(bare_request())["data_integrity_warning"] == warning

        response = client.get("/")
        assert response.status_code == 200
        # Compared through Jinja's own escaper, because the template renders the sentence as data
        # rather than as markup -- the property worth pinning, since the banner is composed in
        # Python. html.escape() would not do: it spells the apostrophe &#x27; and Jinja &#39;.
        assert str(escape(warning)) in response.text, "the published sentence never reaches the page"


def test_a_failing_integrity_scan_still_lets_the_application_start(startup_engine, monkeypatch):
    """Guardrail: fail closed on the control, never on the application."""

    def explode(*args, **kwargs):
        raise RuntimeError("scan defect")

    monkeypatch.setattr(main, "apply_foreign_key_policy", explode)
    with TestClient(main.app) as client:
        assert database.FK_ENFORCEMENT is False
        assert pragma_value(startup_engine) == 0
        assert client.get("/healthz").status_code == 200


# Operator-documentation conformance. These live here because README.md is the runbook for the
# startup and seeding behaviour covered by this module, and each assertion pins a statement that
# was proven false against the code or file it describes.


def readme_text() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_describes_first_run_reference_seeding():
    readme = readme_text()
    assert "loaded once, on the first run against a database that has no reference-data seed marker" in readme
    assert "loaded automatically when the SQLite database is empty" not in readme


def test_readme_lists_the_utility_routes_that_exist():
    readme = readme_text()
    for path in ("/evidence-index", "/health", "/healthz"):
        assert f"- `{path}`" in readme


def test_readme_quotes_the_version_marker_that_the_version_file_holds():
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    readme = readme_text()
    assert version in readme
    assert "Current baseline: `live-demo-version 1.0`" not in readme


def test_readme_states_the_actual_cleanup_coverage():
    from app.data_management import CLEANUP_DATASET_KEYS, DATASETS

    readme = readme_text()
    assert f"{len(CLEANUP_DATASET_KEYS)} of the {len(DATASETS)} data areas" in readme


def test_startup_never_touches_the_live_repository_database(startup_engine):
    before = live_db_fingerprint()
    with TestClient(main.app) as client:
        client.get("/healthz")
        client.get("/")
    assert live_db_fingerprint() == before
