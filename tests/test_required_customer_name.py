"""G5b E6-REQUIRED-NAME: a customer must have a name.

``create_customer`` and ``update_customer`` accepted an empty ``customer_name`` and stored a
blank record plus an audit row for it. This was found by a G5b empty-body probe, which created
exactly such a row in the sponsor's live database. Every sibling create route already refuses its
required field; these two did not.

The assertions here are deliberately about STORED STATE as well as status code: a route that
returns 400 while still writing the row would pass a status-only test, and writing the row was
the whole of the incident.
"""

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, func, select

from app.database import get_session
from app.main import app
from app.models import AuditEvent, Customer


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def table_row_counts(session) -> dict[str, int]:
    return {
        name: session.exec(select(func.count()).select_from(table)).one()
        for name, table in SQLModel.metadata.tables.items()
    }


VALID_FORM = {
    "customer_name": "Transport for London",
    "sector": "Rail",
    "transport_domain": "rail",
    "customer_type": "local authority",
    "corporation_tax_status": "no",
}


def post_customer(session, data):
    client = client_for(session)
    try:
        return client.post("/customers", data=data, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()


def test_an_empty_body_post_creates_nothing(session):
    """The exact G5b probe: POST /customers with no fields at all."""
    before = table_row_counts(session)

    response = post_customer(session, {})

    assert response.status_code == 400
    assert "Customer name is required." in response.text
    assert table_row_counts(session) == before, "the refused request still wrote rows"
    assert session.exec(select(Customer)).all() == []
    assert session.exec(select(AuditEvent)).all() == []


def test_a_blank_customer_name_is_refused(session):
    before = table_row_counts(session)

    response = post_customer(session, dict(VALID_FORM, customer_name=""))

    assert response.status_code == 400
    assert "Customer name is required." in response.text
    assert table_row_counts(session) == before


def test_a_whitespace_only_customer_name_is_refused(session):
    """Spaces are not a name. Guarding on the raw value would have let this through."""
    before = table_row_counts(session)

    response = post_customer(session, dict(VALID_FORM, customer_name="   "))

    assert response.status_code == 400
    assert "Customer name is required." in response.text
    assert table_row_counts(session) == before


def test_a_named_customer_is_still_created(session):
    """Positive control. Without this the refusals above could be a route that never works."""
    response = post_customer(session, VALID_FORM)

    assert response.status_code == 303
    stored = session.exec(select(Customer)).all()
    assert len(stored) == 1
    assert stored[0].customer_name == "Transport for London"
    # Untouched by this change, asserted so a regression in either is visible here:
    # the enum still parses and the corporation-tax default still applies.
    assert stored[0].transport_domain == "rail"
    assert stored[0].customer_type == "local authority"
    assert stored[0].corporation_tax_status == "no"


def test_an_unknown_enum_value_still_falls_back_as_before(session):
    """Enum handling is explicitly NOT changed by this increment."""
    response = post_customer(session, dict(VALID_FORM, transport_domain="not-a-domain"))

    assert response.status_code == 400
    assert "Customer name is required." not in response.text


def test_update_refuses_a_blank_name_and_leaves_the_record_alone(session):
    customer = Customer(
        customer_name="Original Name",
        sector="Rail",
        transport_domain="rail",
        customer_type="local authority",
        corporation_tax_status="no",
    )
    session.add(customer)
    session.commit()
    session.refresh(customer)
    customer_id = customer.id
    before = table_row_counts(session)

    client = client_for(session)
    try:
        response = client.post(
            f"/customers/{customer_id}/update",
            data=dict(VALID_FORM, customer_name=""),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    session.expire_all()
    reloaded = session.get(Customer, customer_id)
    assert response.status_code == 400
    assert "Customer name is required." in response.text
    assert reloaded.customer_name == "Original Name", "the refused update still overwrote the name"
    assert reloaded.sector == "Rail"
    assert table_row_counts(session) == before


def test_update_still_saves_a_named_customer(session):
    customer = Customer(customer_name="Original Name", customer_type="local authority")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    customer_id = customer.id

    client = client_for(session)
    try:
        response = client.post(
            f"/customers/{customer_id}/update",
            data=dict(VALID_FORM, customer_name="Renamed Customer"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    session.expire_all()
    assert response.status_code == 303
    assert session.get(Customer, customer_id).customer_name == "Renamed Customer"
