"""Engine construction, schema bring-up, and the foreign-key pragma listener.

ADR-0005 D1 lives here. The orphan scan and the enforcement policy that reads its result live in
``app/data_integrity.py`` (Ruling R1); this module must not import them back, because a
compatibility re-export would reinstate the dependency inversion that ruling removed.
"""

from collections.abc import Generator
from pathlib import Path
from typing import Any
import sqlite3

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from app.settings import get_settings


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
