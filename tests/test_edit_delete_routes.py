import pytest
from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from app.models import Contract, Customer
from tests.test_routes_validation_audit import table_row_counts


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


# --- Missing record id: regression net -------------------------------------------------------
# The delete routes above already do the right thing for an id that is not in the database:
# `delete_or_block` looks the record up, finds nothing and redirects (app/main.py:297-300). The
# project and claim-period routes below do not. They either raise (HTTP 500 for a URL a user can
# reach by editing the address bar or following a stale bookmark) or, worse, write a row that
# references a project or period that does not exist before failing:
#
#   POST /projects/{missing}/costs                   500, one orphan cost line committed
#   POST /projects/{missing}/evidence                500, one orphan evidence item committed
#   POST /projects/{missing}/competent-professional  303, one orphan opinion committed
#   POST /claim-periods/{missing}/readiness          303, one orphan submission status committed
#
# Expected behaviour is the same as the delete routes: redirect (303) and write nothing. Every
# case was a strict xfail until the read routes started looking the record up (E3-D1) and the POST
# routes started checking the parent before building the child (E3-D2). The markers are gone and
# the cases below stand as the regression net.
#
# The row-count assertion is the load-bearing half and must not be reduced to a status check: two
# of these cases returned a perfectly correct 303 while committing the orphan row, so they were
# only ever detectable by counting rows. This also has to keep holding once foreign keys are
# enforced (ADR-0005), where an orphan write becomes a hard error rather than a silent row.

MISSING_ID = 999999

VALID_COST_FORM = {
    "cost_input_type": "people_time",
    "cost_category": "staff",
    "hours": "10",
    "hourly_rate": "100",
    "apportionment_percentage": "50",
}
VALID_EVIDENCE_FORM = {
    "source_system": "Jira",
    "date_created": "2025-06-01",
    "evidence_type": "experiment",
    "relevance_tag": "uncertainty",
    "strength": "strong",
}
VALID_PROFESSIONAL_FORM = {
    "professional_name": "Reviewer",
    "years_relevant_experience": "12",
    "signoff_status": "draft",
}
VALID_READINESS_FORM = {
    "aif_submitted": "on",
    "aif_submission_date": "2026-03-31",
    "notes": "Readiness note.",
}

MISSING_RECORD_ROUTES = [
    ("GET", f"/projects/{MISSING_ID}", None),
    ("GET", f"/projects/{MISSING_ID}/assessment", None),
    ("GET", f"/projects/{MISSING_ID}/costs", None),
    ("GET", f"/projects/{MISSING_ID}/evidence", None),
    ("GET", f"/projects/{MISSING_ID}/competent-professional", None),
    ("GET", f"/projects/{MISSING_ID}/report", None),
    ("GET", f"/projects/{MISSING_ID}/report?format=md", None),
    ("GET", f"/claim-periods/{MISSING_ID}/readiness", None),
    ("GET", f"/claim-periods/{MISSING_ID}/pack", None),
    ("GET", f"/claim-periods/{MISSING_ID}/pack?format=md", None),
    ("POST", f"/projects/{MISSING_ID}/costs", VALID_COST_FORM),
    ("POST", f"/projects/{MISSING_ID}/evidence", VALID_EVIDENCE_FORM),
    ("POST", f"/projects/{MISSING_ID}/competent-professional", VALID_PROFESSIONAL_FORM),
    ("POST", f"/claim-periods/{MISSING_ID}/readiness", VALID_READINESS_FORM),
]

@pytest.mark.parametrize(
    "method, path, form",
    MISSING_RECORD_ROUTES,
    ids=[f"{method} {path}" for method, path, _ in MISSING_RECORD_ROUTES],
)
def test_routes_for_a_missing_record_redirect_and_write_nothing(seeded_session, method, path, form):
    def override_session():
        yield seeded_session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    before = table_row_counts(seeded_session)
    try:
        # raise_server_exceptions=False so an unhandled error is observed as a 500 response and the
        # row-count assertion still runs, rather than the exception ending the test early.
        client = TestClient(app, raise_server_exceptions=False)
        response = client.request(method, path, data=form, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303, f"{method} {path} returned {response.status_code}"
    assert table_row_counts(seeded_session) == before, f"{method} {path} changed stored data"
