"""Findings B2 and B3: a cost line must survive its own edit form.

Runtime-proven before the fix, over HTTP:

* B2 - a people-time line created as 100h @ GBP 40 stored ``gross_cost=4000``.
  Editing the hours to 50 stored ``hours=50`` and left ``gross_cost=4000``,
  because ``cost_line_from_form`` recalculated only when the posted gross was 0
  and the edit form pre-fills the stored gross. The stored line contradicted
  itself and overstated the claim two-fold.
* B3 - editing any cost line cleared ``activity_id`` (1 before, None after),
  because ``update_cost_line`` rebuilt the row from a blank ``CostLine``
  candidate and the form posts no activity link. The free-text ``activity``
  survived, so ``cost_validation_warnings`` never fired: the loss was invisible.
"""

from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.main import app
from app.models import Activity, CostLine, RDProject
from app.services import cost_validation_warnings


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def edit_form(cost: CostLine, **overrides) -> dict:
    """Exactly what ``_cost_lines.html`` posts, including the pre-filled gross."""
    data = {
        "cost_input_type": cost.cost_input_type,
        "activity": cost.activity,
        "cost_category": cost.cost_category,
        "person_or_supplier_name": cost.person_or_supplier_name,
        "person_role": cost.person_role,
        "hours": str(cost.hours),
        "hourly_rate": str(cost.hourly_rate),
        "days": str(cost.days),
        "day_rate": str(cost.day_rate),
        "gross_cost": str(cost.gross_cost),
        "apportionment_percentage": str(cost.apportionment_percentage),
        "paid_status": cost.paid_status,
        "uk_or_overseas": cost.uk_or_overseas,
        "connected_party_status": cost.connected_party_status,
        "paye_nic_notes": cost.paye_nic_notes,
        "evidence_link": cost.evidence_link,
        "notes": cost.notes,
    }
    data.update(overrides)
    return data


def create_people_time_cost(client, project_id: int, **overrides) -> dict:
    data = {
        "cost_input_type": "people_time",
        "cost_category": "staff",
        "person_or_supplier_name": "Test Engineer",
        "activity": "Prototype investigation",
        "hours": "100",
        "hourly_rate": "40",
        "days": "0",
        "day_rate": "0",
        "gross_cost": "",
        "apportionment_percentage": "100",
        "paid_status": "paid",
        "uk_or_overseas": "UK",
        "evidence_link": "Timesheet 1",
    }
    data.update(overrides)
    response = client.post(f"/projects/{project_id}/costs", data=data, follow_redirects=False)
    assert response.status_code in (200, 303), response.text
    return data


def latest_cost(session, project_id: int) -> CostLine:
    costs = list(session.exec(select(CostLine).where(CostLine.project_id == project_id)))
    return max(costs, key=lambda cost: cost.id or 0)


def test_editing_hours_recalculates_the_people_time_gross_cost(seeded_session):
    """100h @ GBP 40 = 4000; the same line at 50h must store 2000, not 4000."""
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        create_people_time_cost(client, project.id)
        cost = latest_cost(seeded_session, project.id)
        assert cost.gross_cost == 4000
        assert cost.qualifying_amount == 4000

        response = client.post(
            f"/costs/{cost.id}/update",
            data=edit_form(cost, hours="50"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    seeded_session.refresh(cost)
    assert response.status_code == 303
    assert cost.hours == 50
    assert cost.gross_cost == 2000
    assert cost.qualifying_amount == 2000


def test_editing_the_rate_recalculates_the_people_time_gross_cost(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        create_people_time_cost(client, project.id)
        cost = latest_cost(seeded_session, project.id)

        client.post(
            f"/costs/{cost.id}/update",
            data=edit_form(cost, hourly_rate="60"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    seeded_session.refresh(cost)
    assert cost.hourly_rate == 60
    assert cost.gross_cost == 6000


def test_a_typed_gross_cost_override_is_kept_on_create_and_across_later_edits(seeded_session):
    """The field is labelled "Gross cost override": a deliberate figure must stand."""
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        create_people_time_cost(client, project.id, gross_cost="5500")
        cost = latest_cost(seeded_session, project.id)
        assert cost.gross_cost == 5500

        client.post(
            f"/costs/{cost.id}/update",
            data=edit_form(cost, hours="50"),
            follow_redirects=False,
        )
        seeded_session.refresh(cost)
        first_edit_gross = cost.gross_cost

        client.post(
            f"/costs/{cost.id}/update",
            data=edit_form(cost, gross_cost="7250"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    seeded_session.refresh(cost)
    assert first_edit_gross == 5500
    assert cost.gross_cost == 7250


def test_a_direct_cost_gross_is_never_recalculated(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        create_people_time_cost(
            client,
            project.id,
            cost_input_type="direct_cost",
            cost_category="consumables",
            hours="0",
            hourly_rate="0",
            gross_cost="1200",
        )
        cost = latest_cost(seeded_session, project.id)

        client.post(
            f"/costs/{cost.id}/update",
            data=edit_form(cost, hours="80", hourly_rate="10"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    seeded_session.refresh(cost)
    assert cost.cost_input_type == "direct_cost"
    assert cost.gross_cost == 1200


def test_editing_a_cost_line_keeps_its_activity_link(seeded_session):
    """B3: the form posts no activity link, so an edit must not clear the stored one."""
    project = seeded_session.exec(select(RDProject)).first()
    activity = Activity(project_id=project.id, activity_name="Linked prototype activity")
    seeded_session.add(activity)
    seeded_session.commit()
    seeded_session.refresh(activity)

    client = client_for(seeded_session)
    try:
        create_people_time_cost(client, project.id)
        cost = latest_cost(seeded_session, project.id)
        cost.activity_id = activity.id
        seeded_session.add(cost)
        seeded_session.commit()
        seeded_session.refresh(cost)
        assert cost.activity_id == activity.id

        response = client.post(
            f"/costs/{cost.id}/update",
            data=edit_form(cost, hours="50"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    seeded_session.refresh(cost)
    assert response.status_code == 303
    assert cost.id is not None
    assert cost.activity_id == activity.id
    # The loss was invisible precisely because the free-text activity survived it,
    # so the missing-activity-link flag stayed silent either way.
    assert not any("activity link" in warning for warning in cost_validation_warnings(cost))
