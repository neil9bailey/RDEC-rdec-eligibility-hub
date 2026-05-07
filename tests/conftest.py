import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.seed import seed_demo_data


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def seeded_session(session):
    seed_demo_data(session)
    return session
