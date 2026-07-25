"""Shared pytest fixtures.

The DATABASE_URL override below MUST stay above the ``app`` imports and must be an
unconditional override rather than a default.

``app/database.py`` builds its module-level engine from ``settings.database_url`` at
import time, and ``tests/test_startup.py`` enters the FastAPI lifespan, which calls
``init_db()`` against that engine. ``settings.database_url`` defaults to
``sqlite:///./data/rdec_hub.db``, and ``docker-compose.yml`` sets exactly that value
while bind-mounting ``./data`` -- the sponsor's live database with real customer
records. A startup test run without this override would create tables, seed reference
data, and write audit rows into it.
"""

import os
import shutil
import tempfile
from pathlib import Path

TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="rdec-hub-pytest-"))
TEST_DATABASE_URL = f"sqlite:///{(TEST_DB_DIR / 'pytest_hub.db').as_posix()}"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.seed import seed_demo_data  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    """Remove only the throwaway directory this module created."""
    if TEST_DB_DIR.parent == Path(tempfile.gettempdir()) and TEST_DB_DIR.name.startswith("rdec-hub-pytest-"):
        shutil.rmtree(TEST_DB_DIR, ignore_errors=True)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def seeded_session(session):
    seed_demo_data(session)
    return session
