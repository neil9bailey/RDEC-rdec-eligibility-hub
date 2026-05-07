from fastapi.testclient import TestClient

from app.main import app
from app.models import Contract, Customer


def test_update_customer_route(seeded_session):
    customer = Customer(
        customer_name="Original Customer",
        sector="Transport",
        transport_domain="rail",
        customer_type="private transport operator",
        corporation_tax_status="unknown",
    )
    seeded_session.add(customer)
    seeded_session.commit()
    seeded_session.refresh(customer)

    def override_session():
        yield seeded_session

    app.dependency_overrides.clear()
    from app.database import get_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.post(
            f"/customers/{customer.id}/update",
            data={
                "customer_name": "Updated Customer",
                "sector": "Critical infrastructure",
                "transport_domain": "highways",
                "customer_type": "local authority",
                "corporation_tax_status": "no",
                "notes": "Updated notes",
            },
            follow_redirects=False,
        )
        updated = seeded_session.get(Customer, customer.id)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert updated.customer_name == "Updated Customer"
    assert updated.corporation_tax_status == "no"


def test_delete_unlinked_customer_route(seeded_session):
    customer = Customer(customer_name="Delete Me")
    seeded_session.add(customer)
    seeded_session.commit()
    seeded_session.refresh(customer)
    customer_id = customer.id

    def override_session():
        yield seeded_session

    app.dependency_overrides.clear()
    from app.database import get_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.post(f"/customers/{customer_id}/delete", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert seeded_session.get(Customer, customer_id) is None


def test_delete_linked_customer_is_blocked(seeded_session):
    customer = Customer(customer_name="Linked Customer")
    seeded_session.add(customer)
    seeded_session.commit()
    seeded_session.refresh(customer)
    contract = Contract(contract_name="Linked Contract", customer_id=customer.id)
    seeded_session.add(contract)
    seeded_session.commit()

    def override_session():
        yield seeded_session

    app.dependency_overrides.clear()
    from app.database import get_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.post(f"/customers/{customer.id}/delete", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert "delete_blocked_contracts" in response.headers["location"]
    assert seeded_session.get(Customer, customer.id) is not None
