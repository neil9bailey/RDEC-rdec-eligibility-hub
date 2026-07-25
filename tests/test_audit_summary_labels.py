"""Finding E5: a change-history summary must not leak a Python class name.

``delete_or_block`` wrote ``f"Deleted {model.__name__} {item_id}"`` into the stored
audit summary, so a Finance reviewer opening change history read
"Deleted BusinessUnit 7". The wording now comes from the same dataset labels the
export, cleanup and import screens use, so the two cannot drift apart.

Historical rows keep the summary they were written with; only new events change.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.data_management import DATASET_BY_KEY
from app.database import get_session
from app.main import DATASET_LABEL_BY_MODEL, app, deleted_record_summary
from app.models import AuditEvent, BusinessUnit, Company, Contract, Customer, Solution


#: Every model reached through ``delete_or_block`` in ``app/main.py``. A model missing
#: from the dataset catalogue would silently fall back to a summary with no label.
DELETABLE_MODELS = [
    "framework_sources",
    "portal_instances",
    "watch_profiles",
    "opportunities",
    "companies",
    "accounting_periods",
    "customers",
    "business_units",
    "contracts",
    "solutions",
]


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


@pytest.mark.parametrize("dataset_key", DELETABLE_MODELS)
def test_every_deletable_model_has_a_business_label(dataset_key):
    model = DATASET_BY_KEY[dataset_key].model

    assert model in DATASET_LABEL_BY_MODEL
    summary = deleted_record_summary(model, 7)
    assert model.__name__ not in summary
    assert summary == f"Deleted {DATASET_BY_KEY[dataset_key].label.lower()} record 7"


@pytest.mark.parametrize("model", [BusinessUnit, Company, Contract, Customer, Solution])
def test_a_summary_carries_no_camel_case_class_name(model):
    summary = deleted_record_summary(model, 3)

    assert not re.search(r"\b[A-Z][a-z]+[A-Z]", summary)


def test_an_unmapped_model_still_never_leaks_its_class_name():
    class InternalWorkingRecord:
        pass

    assert deleted_record_summary(InternalWorkingRecord, 12) == "Deleted record 12"


def test_deleting_a_business_unit_writes_a_business_language_summary(session):
    unit = BusinessUnit(name="Retired Unit", description="No longer used.")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    unit_id = unit.id
    client = client_for(session)
    try:
        response = client.post(f"/business-units/{unit_id}/delete", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    events = [
        event
        for event in session.exec(select(AuditEvent).where(AuditEvent.entity_type == "BusinessUnit"))
        if event.entity_id == unit_id
    ]
    assert response.status_code == 303
    assert session.get(BusinessUnit, unit_id) is None
    assert [event.action for event in events] == ["delete"]
    assert events[0].summary == f"Deleted business units record {unit_id}"
    assert "BusinessUnit" not in events[0].summary
