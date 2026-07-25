"""Finding B1, wired: the bounded parsers must actually guard the routes.

``tests/test_cost_value_validation.py`` pins the helpers in isolation. These tests
pin the call sites, because a helper nobody calls fixes nothing. Runtime-proven
through the form before this change:

    gross 1000 @ 500%  -> stored qualifying 5000
    gross 1000 @ -50%  -> stored qualifying -500
    gross -1000        -> stored -1000
    gross 1e308        -> stored inf
    gross "nan"        -> HTTP 500

Every case below asserts the stored-row delta as well as the status code: a refusal
that still writes a row is not a refusal.

``parse_float`` stays frozen (ADR-0002 Ruling R3) and its negative control lives in
``tests/test_cost_value_validation.py``. The stored-data flag for an apportionment
over 100 also stays, for rows that are already in the database.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.main import app
from app.models import AuditEvent, CostLine, CustomerWatchProfile, RDProject
from app.services import cost_validation_warnings


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def cost_form(**overrides) -> dict:
    data = {
        "cost_input_type": "direct_cost",
        "cost_category": "staff",
        "person_or_supplier_name": "Test Supplier",
        "activity": "Prototype investigation",
        "gross_cost": "1000",
        "apportionment_percentage": "50",
        "paid_status": "paid",
        "uk_or_overseas": "UK",
        "evidence_link": "Invoice 1",
    }
    data.update(overrides)
    return data


def cost_row_count(session) -> int:
    return len(list(session.exec(select(CostLine))))


def audit_row_count(session) -> int:
    return len(list(session.exec(select(AuditEvent))))


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("apportionment_percentage", "500", "Apportionment percentage must be 100 or less."),
        ("apportionment_percentage", "-50", "Apportionment percentage cannot be negative."),
        ("apportionment_percentage", "nan", "Apportionment percentage must be a real number."),
        ("gross_cost", "-1000", "Gross cost cannot be negative."),
        ("gross_cost", "1e308", "Gross cost must be 1,000,000,000,000 or less."),
        ("gross_cost", "nan", "Gross cost must be a real number."),
        ("gross_cost", "inf", "Gross cost must be a real number."),
        ("gross_cost", "1_000", "Gross cost must be a plain number without underscore separators."),
    ],
)
def test_cost_create_route_refuses_a_nonsensical_value_and_writes_nothing(
    seeded_session, field, value, message
):
    project = seeded_session.exec(select(RDProject)).first()
    costs_before = cost_row_count(seeded_session)
    audits_before = audit_row_count(seeded_session)
    client = client_for(seeded_session)
    try:
        response = client.post(
            f"/projects/{project.id}/costs",
            data=cost_form(**{field: value}),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert message in response.text
    assert cost_row_count(seeded_session) == costs_before
    assert audit_row_count(seeded_session) == audits_before


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("hours", "-5", "Hours cannot be negative."),
        ("hours", "nan", "Hours must be a real number."),
        ("days", "-1", "Days cannot be negative."),
        ("hourly_rate", "nan", "Hourly rate must be a real number."),
        ("day_rate", "-40", "Day rate cannot be negative."),
    ],
)
def test_people_time_route_refuses_a_nonsensical_time_or_rate(seeded_session, field, value, message):
    project = seeded_session.exec(select(RDProject)).first()
    costs_before = cost_row_count(seeded_session)
    client = client_for(seeded_session)
    try:
        people_time = {
            "cost_input_type": "people_time",
            "hours": "10",
            "hourly_rate": "40",
            "days": "0",
            "day_rate": "0",
            "gross_cost": "",
        }
        people_time[field] = value
        response = client.post(
            f"/projects/{project.id}/costs",
            data=cost_form(**people_time),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert message in response.text
    assert cost_row_count(seeded_session) == costs_before


def test_cost_update_route_refuses_the_same_values(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        client.post(f"/projects/{project.id}/costs", data=cost_form(), follow_redirects=False)
        cost = max(
            session_costs := list(seeded_session.exec(select(CostLine).where(CostLine.project_id == project.id))),
            key=lambda row: row.id or 0,
        )
        assert session_costs
        response = client.post(
            f"/costs/{cost.id}/update",
            data=cost_form(gross_cost="1000", apportionment_percentage="500"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    seeded_session.refresh(cost)
    assert response.status_code == 400
    assert "Apportionment percentage must be 100 or less." in response.text
    assert cost.gross_cost == 1000
    assert cost.apportionment_percentage == 50
    assert cost.qualifying_amount == 500


def test_a_valid_cost_still_stores_and_calculates_normally(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        response = client.post(
            f"/projects/{project.id}/costs",
            data=cost_form(gross_cost="1000.55", apportionment_percentage="37.5"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    cost = max(
        list(seeded_session.exec(select(CostLine).where(CostLine.project_id == project.id))),
        key=lambda row: row.id or 0,
    )
    assert response.status_code == 303
    assert cost.gross_cost == 1000.55
    assert cost.apportionment_percentage == 37.5
    assert cost.qualifying_amount == 375.21


def test_stored_apportionment_over_100_is_still_flagged_for_legacy_and_imported_rows():
    """The form now refuses it, so the soft flag is the only remaining guard for rows
    that are already in the database or that arrive through an import."""
    legacy = CostLine(
        project_id=1,
        cost_category="staff",
        gross_cost=1000,
        apportionment_percentage=120,
        paid_status="paid",
        uk_or_overseas="UK",
        evidence_link="Invoice 7",
        activity="Prototype",
    )

    assert any("over 100%" in warning for warning in cost_validation_warnings(legacy))


@pytest.mark.parametrize(
    "value,message",
    [
        ("nan", "Minimum value must be a real number."),
        ("-2000", "Minimum value cannot be negative."),
        ("1e308", "Minimum value must be 1,000,000,000,000 or less."),
    ],
)
def test_watch_profile_minimum_value_is_bounded(seeded_session, value, message):
    profiles_before = len(list(seeded_session.exec(select(CustomerWatchProfile))))
    client = client_for(seeded_session)
    try:
        response = client.post(
            "/framework-intelligence/watch-profiles",
            data={"profile_name": "Bounded profile", "minimum_value": value},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert message in response.text
    assert len(list(seeded_session.exec(select(CustomerWatchProfile)))) == profiles_before
