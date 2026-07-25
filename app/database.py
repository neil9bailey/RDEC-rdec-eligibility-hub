from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import logging
import sqlite3

from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from app.settings import get_settings


logger = logging.getLogger(__name__)

settings = get_settings()


# ADR-0005 D1. SQLite defaults PRAGMA foreign_keys to 0 on every connection. The flag below is the
# single source of truth for whether the pragma is set, and it stays False until an orphan pre-scan
# comes back clean (D3). Nothing in this module turns it on by itself.
FK_ENFORCEMENT = False


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    """Apply the current enforcement setting to every new SQLite connection.

    Registered on the Engine *class*, not on an engine instance (ADR-0005 D1.1), so that an
    engine built anywhere -- including the one tests/conftest.py creates independently -- is
    covered. Without that, the suite could go green while production behaved differently.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA foreign_keys={'ON' if FK_ENFORCEMENT else 'OFF'}")
    finally:
        cursor.close()


def make_engine(database_url: str, **kwargs: Any) -> Engine:
    """Build an engine through the one construction route the whole process shares.

    The listener above is registered on the Engine class, so coverage does not depend on this
    factory. The factory exists so that coverage does not depend on an import side effect
    surviving an import reorder either (ADR-0005 D1.2), and so the sqlite connect_args default
    is applied in exactly one place.
    """
    if database_url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    return create_engine(database_url, **kwargs)


engine = make_engine(settings.database_url)


def set_fk_enforcement(enabled: bool, target_engine: Engine | None = None) -> None:
    """Flip enforcement and dispose the pool so the change actually reaches connections.

    ADR-0005 D1.3: connections already in the pool keep the pragma they were opened with, so
    omitting the dispose leaves enforcement off while every flag and log line says it is on.
    """
    global FK_ENFORCEMENT
    FK_ENFORCEMENT = enabled
    (target_engine if target_engine is not None else engine).dispose()


def ensure_sqlite_parent() -> None:
    if not settings.database_url.startswith("sqlite:///"):
        return
    db_path = settings.database_url.replace("sqlite:///", "", 1)
    if db_path not in {":memory:", ""}:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    ensure_sqlite_parent()
    SQLModel.metadata.create_all(engine)
    apply_sqlite_schema_updates()


def apply_sqlite_schema_updates() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    updates = {
        "costline": {
            "cost_input_type": "cost_input_type VARCHAR DEFAULT 'direct_cost'",
            "person_role": "person_role VARCHAR DEFAULT ''",
            "time_period_start": "time_period_start DATE",
            "time_period_end": "time_period_end DATE",
            "hours": "hours FLOAT DEFAULT 0",
            "hourly_rate": "hourly_rate FLOAT DEFAULT 0",
            "days": "days FLOAT DEFAULT 0",
            "day_rate": "day_rate FLOAT DEFAULT 0",
        },
        "customer": {
            "business_unit_id": "business_unit_id INTEGER",
        },
        "frameworksource": {
            "source_family": "source_family VARCHAR DEFAULT 'official_notice'",
            "coverage": "coverage VARCHAR DEFAULT ''",
            "auth_model": "auth_model VARCHAR DEFAULT 'none'",
            "data_format": "data_format VARCHAR DEFAULT ''",
            "dedupe_strategy": "dedupe_strategy VARCHAR DEFAULT 'ocid_or_reference'",
            "change_tracking_enabled": "change_tracking_enabled BOOLEAN DEFAULT 1",
            "requires_human_approval": "requires_human_approval BOOLEAN DEFAULT 0",
            "connector_status": "connector_status VARCHAR DEFAULT 'configured'",
            "source_metadata": "source_metadata VARCHAR DEFAULT ''",
        },
        "opportunitydocument": {
            "retrieval_status": "retrieval_status VARCHAR DEFAULT 'linked'",
            "human_review_status": "human_review_status VARCHAR DEFAULT 'pending'",
            "platform_name": "platform_name VARCHAR DEFAULT ''",
            "content_summary": "content_summary VARCHAR DEFAULT ''",
        },
    }
    with engine.begin() as connection:
        for table_name, columns in updates.items():
            existing = {
                row[1]
                for row in connection.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            }
            if not existing:
                continue
            for column_name, ddl in columns.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


# --------------------------------------------------------------------------------------------
# ADR-0005 D2/D3: link integrity.
#
# These live here for now because app/main.py is owned by another increment and this module is
# already the one place that knows how the schema is built. ADR-0005 D3.1 names app/data_integrity.py
# as their final home; the names and signatures below are the ones that ADR specifies, so the
# wiring increment can move them without changing any call site.
# --------------------------------------------------------------------------------------------


DISPLAY_FIELD_ORDER = (
    "company_name",
    "customer_name",
    "contract_name",
    "solution_name",
    "project_title",
    "name",
    "label",
    "activity",
    "activity_name",
    "professional_name",
    "source_reference",
    "summary",
)


@dataclass(frozen=True)
class OrphanRecord:
    child_dataset: str
    child_id: int
    child_display: str
    field: str
    parent_dataset: str
    missing_parent_id: int


@dataclass(frozen=True)
class IntegrityScanResult:
    orphans: tuple[OrphanRecord, ...]
    enforcement_enabled: bool
    missing_constraints: dict[str, tuple[str, ...]]
    warning: str | None


# Set by apply_foreign_key_policy() at startup and read by the page renderer (ADR-0005 D3.3 step 4).
INTEGRITY_REPORT: tuple[OrphanRecord, ...] = ()
INTEGRITY_WARNING: str | None = None


def missing_foreign_key_constraints(target_engine: Engine | None = None) -> dict[str, tuple[str, ...]]:
    """Columns the models declare as foreign keys that the live schema does not enforce.

    SQLite cannot attach a REFERENCES clause through ALTER TABLE ... ADD COLUMN, so a column added
    by apply_sqlite_schema_updates() has no constraint in the DDL, permanently, while the same
    column on a freshly created database has one. Enforcement therefore differs between fresh and
    upgraded databases, and the pragma is a no-op for anything listed here (ADR-0005 D2).
    """
    active = target_engine if target_engine is not None else engine
    if active.dialect.name != "sqlite":
        return {}
    missing: dict[str, tuple[str, ...]] = {}
    with active.connect() as connection:
        present = {
            row[0]
            for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        for table_name, table in SQLModel.metadata.tables.items():
            declared = sorted({column.name for column in table.columns if column.foreign_keys})
            if not declared or table_name not in present:
                continue
            enforced = {
                row[3] for row in connection.execute(text(f"PRAGMA foreign_key_list({table_name})"))
            }
            absent = tuple(name for name in declared if name not in enforced)
            if absent:
                missing[table_name] = absent
    return missing


def _display_for(spec: Any, record: Any) -> str:
    for field in DISPLAY_FIELD_ORDER:
        value = getattr(record, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for field in spec.natural_key:
        value = getattr(record, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{spec.label} #{getattr(record, 'id', 0) or 0}"


def scan_orphans(session: Session) -> list[OrphanRecord]:
    """Report records whose parent identifier does not match an existing record.

    Read-only. It never modifies, moves, quarantines, or deletes anything (ADR-0005 D3.5): these
    are the sponsor's RDEC evidence records, and relocating them would be a destructive, non-obvious
    mutation. The relationships come from DatasetSpec.foreign_keys, the same single source of truth
    the import path uses, so the two consumers cannot drift apart (D3.2).
    """
    # Imported inside the function so this low-level module does not take a package-level
    # dependency on the much higher-level data-management module.
    from app.data_management import DATASETS, DATASET_BY_KEY

    orphans: list[OrphanRecord] = []
    parent_ids: dict[str, set[int]] = {}
    for spec in DATASETS:
        if not spec.foreign_keys:
            continue
        records = list(session.exec(select(spec.model)))
        if not records:
            continue
        for field, parent_key in spec.foreign_keys:
            parent_spec = DATASET_BY_KEY[parent_key]
            if parent_key not in parent_ids:
                parent_ids[parent_key] = {
                    int(parent.id)
                    for parent in session.exec(select(parent_spec.model))
                    if parent.id is not None
                }
            known = parent_ids[parent_key]
            for record in records:
                value = getattr(record, field, None)
                if value is None:
                    continue
                if int(value) in known:
                    continue
                orphans.append(
                    OrphanRecord(
                        child_dataset=spec.key,
                        child_id=int(getattr(record, "id", 0) or 0),
                        child_display=_display_for(spec, record),
                        field=field,
                        parent_dataset=parent_key,
                        missing_parent_id=int(value),
                    )
                )
    return orphans


def orphan_warning_text(orphans: tuple[OrphanRecord, ...] | list[OrphanRecord]) -> str | None:
    """Plain operational wording for the banner. Not a tax, accounting, or eligibility statement."""
    if not orphans:
        return None
    count = len(orphans)
    noun = "record points" if count == 1 else "records point"
    return (
        f"{count} {noun} at a record that is no longer in the Hub, so link checking is switched off "
        "until that is resolved. Nothing has been changed or removed. Open each listed record and "
        "choose the correct link on its own page."
    )


def apply_foreign_key_policy(session: Session, target_engine: Engine | None = None) -> IntegrityScanResult:
    """Scan for orphans, then either enable link enforcement or withhold it and report.

    ADR-0005 D3.3, in order: the scan is a read taken while enforcement is still off, so it can
    never fail on the condition it is looking for. A clean scan enables the pragma and disposes the
    pool. A scan that finds orphans leaves the pragma off, logs each orphan, and stores the report:
    withholding the *new* control is the only option that is neither destructive nor silent, and
    refusing to start would strand the operator with a database they cannot inspect.
    """
    global INTEGRITY_REPORT, INTEGRITY_WARNING

    active = target_engine if target_engine is not None else engine
    orphans = tuple(scan_orphans(session))
    missing = missing_foreign_key_constraints(active)

    if missing:
        logger.warning(
            "Link enforcement is partial: %s have no constraint in this database's schema.",
            ", ".join(f"{table}.{column}" for table, columns in missing.items() for column in columns),
        )

    if not settings.enforce_foreign_keys:
        set_fk_enforcement(False, active)
        INTEGRITY_REPORT = orphans
        INTEGRITY_WARNING = orphan_warning_text(orphans)
        logger.warning("Link enforcement is switched off by ENFORCE_FOREIGN_KEYS.")
        return IntegrityScanResult(orphans, False, missing, INTEGRITY_WARNING)

    if orphans:
        set_fk_enforcement(False, active)
        for orphan in orphans:
            logger.warning(
                "%s %s (id %s) points at %s id %s, which no longer exists.",
                orphan.child_dataset,
                orphan.child_display,
                orphan.child_id,
                orphan.parent_dataset,
                orphan.missing_parent_id,
            )
        INTEGRITY_REPORT = orphans
        INTEGRITY_WARNING = orphan_warning_text(orphans)
        return IntegrityScanResult(orphans, False, missing, INTEGRITY_WARNING)

    set_fk_enforcement(True, active)
    INTEGRITY_REPORT = ()
    INTEGRITY_WARNING = None
    return IntegrityScanResult((), True, missing, None)
