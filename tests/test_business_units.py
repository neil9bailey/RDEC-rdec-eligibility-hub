from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.main import app
from app.models import AuditEvent, BusinessUnit, Customer
from app.seed import seed_business_units


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def business_unit_audit_events(session, unit_id: int | None = None) -> list[AuditEvent]:
    events = list(session.exec(select(AuditEvent).where(AuditEvent.entity_type == "BusinessUnit")))
    if unit_id is None:
        return events
    return [event for event in events if event.entity_id == unit_id]


def test_reference_business_units_seed_cleanly(session):
    seed_business_units(session)
    units = {unit.name: unit for unit in session.exec(select(BusinessUnit))}
    customers = list(session.exec(select(Customer)))

    assert "Transport" in units
    assert "Highways" in units
    assert "Rail" in units
    assert "SCADA" in units
    assert "TfL" in units
    assert "Network Services" in units
    assert "HPC / Hinkley Point C" in units
    assert "Nuclear Power" in units
    assert "Core Central Asset Management" in units
    assert units["Highways"].parent_id == units["Transport"].id
    assert any(
        customer.customer_name == "Transport for London (TfL)" and customer.business_unit_id == units["TfL"].id
        for customer in customers
    )
    assert any(
        customer.customer_name == "National Rail" and customer.business_unit_id == units["Rail"].id
        for customer in customers
    )
    assert any(
        customer.customer_name == "National Rail" and customer.business_unit_id == units["SCADA"].id
        for customer in customers
    )


def test_reference_business_units_rename_legacy_labels(session):
    transport = BusinessUnit(name="Transport", description="Top-level transport business unit.")
    scada = BusinessUnit(name="Scada", description="Transport business unit for SCADA work.")
    hinkley = BusinessUnit(name="HPC / Hinckley Point C", description="HPC / Hinckley Point C business unit.")
    session.add(transport)
    session.commit()
    session.refresh(transport)
    scada.parent_id = transport.id
    session.add(scada)
    session.add(hinkley)
    session.commit()

    seed_business_units(session)
    seed_business_units(session)
    units = {unit.name: unit for unit in session.exec(select(BusinessUnit))}
    national_rail_customers = list(
        session.exec(select(Customer).where(Customer.customer_name == "National Rail"))
    )

    assert "Scada" not in units
    assert "Hinckley" not in " ".join(units)
    assert "SCADA" in units
    assert "HPC / Hinkley Point C" in units
    assert len(national_rail_customers) == 2


# --- Business-unit audit trail --------------------------------------------------------------
# The business unit is the organisational key that customers, watch profiles, opportunities,
# portal instances and intelligence reports hang off, so a rename or a new unit must leave a
# record in change history (ADR-0002 line 21 keeps change history as a supporting tool, and the
# rest of the app records create/update/delete through save_with_audit).
#
# app/main.py create_business_unit and update_business_unit used to call session.add and
# session.commit directly, never save_with_audit, so neither wrote an AuditEvent. The three tests
# below were written as strict xfails against that gap; they became real passes when the baton
# holder closed it (EPIC-RDEC-2026-07-VERIFIED-FIXES, E7-3), so the markers are gone and the tests
# now stand as the regression net. A change history that records only deletes cannot say who
# created or renamed the business unit a claim hangs off.


def test_business_unit_delete_route_writes_an_audit_event(session):
    """Positive control: the audit plumbing does reach BusinessUnit over HTTP.

    Delete goes through delete_or_block -> delete_with_audit, so this test proves the create and
    update tests below exercise the product rather than an artefact of this harness.
    """
    unit = BusinessUnit(name="Deletable Unit", description="Unused business unit.")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    unit_id = unit.id
    client = client_for(session)
    try:
        response = client.post(f"/business-units/{unit_id}/delete", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    events = business_unit_audit_events(session, unit_id)
    assert response.status_code == 303
    assert session.get(BusinessUnit, unit_id) is None
    assert [event.action for event in events] == ["delete"]


def test_business_unit_create_route_writes_an_audit_event(session):
    client = client_for(session)
    try:
        response = client.post(
            "/business-units",
            data={"name": "Audited Unit", "description": "New business unit.", "active": "on"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    unit = session.exec(select(BusinessUnit).where(BusinessUnit.name == "Audited Unit")).first()
    assert response.status_code == 303
    assert unit is not None
    assert [event.action for event in business_unit_audit_events(session, unit.id)] == ["create"]


def test_business_unit_update_route_writes_an_audit_event(session):
    unit = BusinessUnit(name="Renameable Unit", description="Original description.")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    client = client_for(session)
    try:
        response = client.post(
            f"/business-units/{unit.id}/update",
            data={"name": "Renamed Unit", "description": "Original description.", "active": "on"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    session.refresh(unit)
    assert response.status_code == 303
    assert unit.name == "Renamed Unit"
    assert [event.action for event in business_unit_audit_events(session, unit.id)] == ["update"]


def test_business_unit_rename_audit_event_records_the_previous_name(session):
    """A rename audit that cannot say what the unit was called before is not a change record."""
    unit = BusinessUnit(name="Previous Unit Name", description="Original description.")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    client = client_for(session)
    try:
        client.post(
            f"/business-units/{unit.id}/update",
            data={"name": "Later Unit Name", "description": "Original description.", "active": "on"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    events = business_unit_audit_events(session, unit.id)
    assert events, "no audit event was written for the rename"
    assert "Previous Unit Name" in events[0].before_json
    assert "Later Unit Name" in events[0].after_json
