"""Finding E7-3: creating or renaming a business unit must reach change history.

``tests/test_business_units.py`` holds the strict-xfail net for this finding and is
owned by another role, so these are the baton holder's own proving tests for the
``app/main.py`` change. They assert the parts a reviewer depends on: the event
exists, it is attributed to the right record, and a rename says what the unit used
to be called.
"""

import json

from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.main import app
from app.models import AuditEvent, BusinessUnit


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def business_unit_events(session, unit_id: int) -> list[AuditEvent]:
    return [
        event
        for event in session.exec(select(AuditEvent).where(AuditEvent.entity_type == "BusinessUnit"))
        if event.entity_id == unit_id
    ]


def test_creating_a_business_unit_records_the_new_record(session):
    client = client_for(session)
    try:
        response = client.post(
            "/business-units",
            data={"name": "Signalling", "description": "Rail signalling delivery.", "active": "on"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    unit = session.exec(select(BusinessUnit).where(BusinessUnit.name == "Signalling")).first()
    assert response.status_code == 303
    assert unit is not None
    events = business_unit_events(session, unit.id)
    assert [event.action for event in events] == ["create"]
    assert events[0].summary == "Created business unit Signalling"
    assert json.loads(events[0].after_json)["name"] == "Signalling"


def test_renaming_a_business_unit_records_the_previous_and_the_new_name(session):
    unit = BusinessUnit(name="Highways North", description="Regional highways unit.")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    client = client_for(session)
    try:
        response = client.post(
            f"/business-units/{unit.id}/update",
            data={"name": "Highways", "description": "Regional highways unit.", "active": "on"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    session.refresh(unit)
    events = business_unit_events(session, unit.id)
    assert response.status_code == 303
    assert unit.name == "Highways"
    assert [event.action for event in events] == ["update"]
    assert json.loads(events[0].before_json)["name"] == "Highways North"
    assert json.loads(events[0].after_json)["name"] == "Highways"
    assert events[0].summary == "Updated business unit Highways"


def test_a_refused_business_unit_form_records_nothing(session):
    audits_before = len(list(session.exec(select(AuditEvent))))
    units_before = len(list(session.exec(select(BusinessUnit))))
    client = client_for(session)
    try:
        response = client.post(
            "/business-units",
            data={"name": "Broken", "parent_id": "not-a-number"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert len(list(session.exec(select(BusinessUnit)))) == units_before
    assert len(list(session.exec(select(AuditEvent)))) == audits_before
