from collections.abc import Generator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy import text

from app.settings import get_settings


settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


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
