"""ADR-0002 Ruling R6: quantities are bounded, and so is the gross derived from them.

Finding B1 closed the typed gross cost at ``MAX_MONETARY_AMOUNT``. It re-entered through
a second input path, because ``Hours`` and ``Days`` were parsed with no maximum at all.
Runtime-proven on the pre-fix tree, through the cost form:

    hours=1e300 x hourly_rate=1e12   -> stored gross_cost = inf
    hours=1e300 x hourly_rate=1000   -> stored gross_cost = 1e303
    parse_money("1e300", maximum=None) -> 1e300 with no error   (fail-open)

Two independent controls are pinned here, because either one alone still lets a figure
through: a bound on each quantity, and a bound on the *resolved* people-time gross, which
is the number actually written to the claim. Every refusal asserts the stored-row delta as
well as the status code -- a refusal that still writes a row is not a refusal.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.form_utils import (
    MAX_MONETARY_AMOUNT,
    MAX_QUANTITY_AMOUNT,
    effective_maximum,
    parse_decimal_amount,
    parse_money,
)
from app.main import app
from app.models import AuditEvent, CostLine, RDProject

# The exact wording required by ADR-0002 Ruling R6.
CALCULATED_BOUND_MESSAGE = (
    "Calculated people time cost must be 1,000,000,000,000 or less. "
    "Check the hours, days, and rates."
)


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def people_time_form(**overrides) -> dict:
    data = {
        "cost_input_type": "people_time",
        "cost_category": "staff",
        "person_or_supplier_name": "Test Engineer",
        "activity": "Prototype investigation",
        "hours": "10",
        "hourly_rate": "40",
        "days": "0",
        "day_rate": "0",
        "gross_cost": "",
        "apportionment_percentage": "50",
        "paid_status": "paid",
        "uk_or_overseas": "UK",
        "evidence_link": "Timesheet 1",
    }
    data.update(overrides)
    return data


def cost_rows(session) -> list[CostLine]:
    return list(session.exec(select(CostLine)))


def audit_row_count(session) -> int:
    return len(list(session.exec(select(AuditEvent))))


def test_effective_maximum_lets_a_call_tighten_the_bound_but_never_raise_it():
    assert effective_maximum(None) == MAX_MONETARY_AMOUNT
    assert effective_maximum(MAX_MONETARY_AMOUNT * 1000) == MAX_MONETARY_AMOUNT
    assert effective_maximum(MAX_QUANTITY_AMOUNT) == MAX_QUANTITY_AMOUNT
    assert effective_maximum(100.0) == 100.0


@pytest.mark.parametrize("maximum", [None, MAX_MONETARY_AMOUNT * 1000])
def test_a_caller_cannot_switch_the_monetary_bound_off(maximum):
    """R6 conformance: maximum=None and a larger ceiling both still refuse MAX + 1."""
    over_the_cap = str(MAX_MONETARY_AMOUNT + 1)

    money_errors: list[str] = []
    decimal_errors: list[str] = []
    money = parse_money(over_the_cap, "Gross cost", money_errors, maximum=maximum)
    decimal = parse_decimal_amount(over_the_cap, "Hours", decimal_errors, maximum=maximum)

    assert money == 0.0
    assert decimal == 0.0
    assert money_errors == ["Gross cost must be 1,000,000,000,000 or less."]
    assert decimal_errors == ["Hours must be 1,000,000,000,000 or less."]


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("hours", "1e300", "Hours must be 1,000,000 or less."),
        ("hours", str(MAX_QUANTITY_AMOUNT + 1), "Hours must be 1,000,000 or less."),
        ("days", "1e300", "Days must be 1,000,000 or less."),
        ("days", str(MAX_QUANTITY_AMOUNT + 1), "Days must be 1,000,000 or less."),
    ],
)
def test_a_quantity_above_the_bound_is_refused_and_nothing_is_written(
    seeded_session, field, value, message
):
    project = seeded_session.exec(select(RDProject)).first()
    costs_before = len(cost_rows(seeded_session))
    audits_before = audit_row_count(seeded_session)
    client = client_for(seeded_session)
    try:
        response = client.post(
            f"/projects/{project.id}/costs",
            data=people_time_form(**{field: value}),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert message in response.text
    assert len(cost_rows(seeded_session)) == costs_before
    assert audit_row_count(seeded_session) == audits_before


def test_the_proven_overflow_pair_can_no_longer_reach_the_database(seeded_session):
    """hours=1e300 at the rate cap stored `inf` before this change."""
    project = seeded_session.exec(select(RDProject)).first()
    costs_before = len(cost_rows(seeded_session))
    client = client_for(seeded_session)
    try:
        response = client.post(
            f"/projects/{project.id}/costs",
            data=people_time_form(hours="1e300", hourly_rate=str(MAX_MONETARY_AMOUNT)),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert len(cost_rows(seeded_session)) == costs_before
    assert all(float(row.gross_cost or 0) <= MAX_MONETARY_AMOUNT for row in cost_rows(seeded_session))


def test_individually_valid_hours_and_rate_whose_product_is_not_are_refused(seeded_session):
    """The derived check, isolated: each field is inside its own bound, the product is not.

    1,000,000 hours at 1,000,000,000 per hour is 1e15. Nothing rejects that but the check on
    the resolved figure, so this test fails if the quantity bound is the only control added.
    """
    project = seeded_session.exec(select(RDProject)).first()
    costs_before = len(cost_rows(seeded_session))
    audits_before = audit_row_count(seeded_session)
    client = client_for(seeded_session)
    try:
        response = client.post(
            f"/projects/{project.id}/costs",
            data=people_time_form(
                hours=str(MAX_QUANTITY_AMOUNT),
                hourly_rate="1000000000",
            ),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert CALCULATED_BOUND_MESSAGE in response.text
    assert "Hours must be" not in response.text
    assert "Hourly rate must be" not in response.text
    assert len(cost_rows(seeded_session)) == costs_before
    assert audit_row_count(seeded_session) == audits_before


def test_a_valid_people_time_line_is_unaffected(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    costs_before = len(cost_rows(seeded_session))
    client = client_for(seeded_session)
    try:
        response = client.post(
            f"/projects/{project.id}/costs",
            data=people_time_form(hours="10", hourly_rate="40", days="2", day_rate="300"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    rows = cost_rows(seeded_session)
    assert len(rows) == costs_before + 1
    created = max(rows, key=lambda row: row.id or 0)
    assert created.gross_cost == 1000.0
    assert created.hours == 10.0
    assert created.days == 2.0


def test_the_edit_route_applies_the_same_derived_bound_and_leaves_the_row_alone(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        client.post(
            f"/projects/{project.id}/costs",
            data=people_time_form(hours="10", hourly_rate="40"),
            follow_redirects=False,
        )
        cost = max(cost_rows(seeded_session), key=lambda row: row.id or 0)
        assert cost.gross_cost == 400.0
        response = client.post(
            f"/costs/{cost.id}/update",
            data=people_time_form(
                hours=str(MAX_QUANTITY_AMOUNT),
                hourly_rate="1000000000",
                gross_cost="400",
            ),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert CALCULATED_BOUND_MESSAGE in response.text
    seeded_session.refresh(cost)
    assert cost.gross_cost == 400.0
    assert cost.hours == 10.0
