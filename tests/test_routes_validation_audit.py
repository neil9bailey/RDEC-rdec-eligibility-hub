from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.main import app
from app.models import AuditEvent, Customer, EntitlementAssessment, RDProject
from app.services import sync_entitlement_for_project


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_route_smoke_pages(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        paths = [
            "/",
            "/knowledge-agent",
            "/source-health",
            "/framework-intelligence",
            "/framework-intelligence/source-catalogue",
            "/framework-intelligence/source-changes",
            "/framework-intelligence/portal-platforms",
            "/framework-intelligence/sources",
            "/framework-intelligence/watch-profiles",
            "/framework-intelligence/opportunities",
            "/framework-intelligence/requirements",
            "/framework-intelligence/agent-runs",
            "/framework-intelligence/reports",
            "/business-units",
            "/companies",
            "/customers",
            "/contracts",
            "/solutions",
            "/projects",
            "/costs",
            "/evidence-index",
            "/audit",
            f"/projects/{project.id}",
            f"/projects/{project.id}/assessment",
            f"/projects/{project.id}/costs",
            f"/projects/{project.id}/evidence",
            f"/projects/{project.id}/competent-professional",
            f"/projects/{project.id}/report",
            f"/claim-periods/{project.accounting_period_id}/readiness",
            f"/claim-periods/{project.accounting_period_id}/pack",
        ]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, path
    finally:
        app.dependency_overrides.clear()


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "R&D Claim Evidence Hub"}


def test_malformed_accounting_period_form_returns_400(seeded_session):
    client = client_for(seeded_session)
    try:
        response = client.post(
            "/accounting-periods",
            data={
                "company_id": "not-a-number",
                "label": "Bad AP",
                "start_date": "not-a-date",
                "end_date": "2026-03-31",
                "period_of_account_start": "2025-04-01",
                "period_of_account_end": "2026-03-31",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "Check the submitted values" in response.text


def test_malformed_cost_form_returns_400(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        response = client.post(
            f"/projects/{project.id}/costs",
            data={
                "cost_input_type": "people_time",
                "hours": "many",
                "hourly_rate": "100",
                "apportionment_percentage": "50",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "Hours must be a number" in response.text


def test_malformed_evidence_and_claim_period_dates_return_400(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        evidence_response = client.post(
            f"/projects/{project.id}/evidence",
            data={
                "source_system": "Jira",
                "date_created": "soon",
                "evidence_type": "experiment",
                "relevance_tag": "uncertainty",
                "strength": "strong",
            },
        )
        readiness_response = client.post(
            f"/claim-periods/{project.accounting_period_id}/readiness",
            data={"aif_submission_date": "bad-date"},
        )
    finally:
        app.dependency_overrides.clear()

    assert evidence_response.status_code == 400
    assert readiness_response.status_code == 400


def test_malformed_project_assessment_and_professional_values_return_400(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        assessment_response = client.post(
            f"/projects/{project.id}/assessment",
            data={
                "accounting_period_id": "bad-id",
                "outcome": "resolved",
                "rd_start_date": "not-a-date",
                "company_role": "framework supplier",
            },
        )
        professional_response = client.post(
            f"/projects/{project.id}/competent-professional",
            data={
                "professional_name": "Reviewer",
                "years_relevant_experience": "lots",
                "signoff_status": "draft",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert assessment_response.status_code == 400
    assert professional_response.status_code == 400


def test_customer_type_defaults_on_create_and_update_routes(seeded_session):
    client = client_for(seeded_session)
    try:
        client.post(
            "/customers",
            data={
                "customer_name": "Default Local Authority",
                "sector": "Transport",
                "transport_domain": "highways",
                "customer_type": "local authority",
                "corporation_tax_status": "unknown",
            },
            follow_redirects=False,
        )
        client.post(
            "/customers",
            data={
                "customer_name": "Default Private Operator",
                "sector": "Transport",
                "transport_domain": "rail",
                "customer_type": "private transport operator",
                "corporation_tax_status": "unknown",
            },
            follow_redirects=False,
        )
        client.post(
            "/customers",
            data={
                "customer_name": "Default Public Corporation",
                "sector": "Transport",
                "transport_domain": "rail",
                "customer_type": "public corporation",
                "corporation_tax_status": "unknown",
            },
            follow_redirects=False,
        )
        client.post(
            "/customers",
            data={
                "customer_name": "Explicit Status",
                "sector": "Transport",
                "transport_domain": "rail",
                "customer_type": "local authority",
                "corporation_tax_status": "yes",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    customers = {
        item.customer_name: item
        for item in seeded_session.exec(
            select(Customer).where(Customer.customer_name.in_(
                [
                    "Default Local Authority",
                    "Default Private Operator",
                    "Default Public Corporation",
                    "Explicit Status",
                ]
            ))
        )
    }
    assert customers["Default Local Authority"].corporation_tax_status == "no"
    assert customers["Default Private Operator"].corporation_tax_status == "yes"
    assert customers["Default Public Corporation"].corporation_tax_status == "unknown"
    assert customers["Explicit Status"].corporation_tax_status == "yes"


def test_customer_update_preserves_explicit_status(seeded_session):
    customer = Customer(
        customer_name="Update Explicit",
        transport_domain="rail",
        customer_type="local authority",
        corporation_tax_status="no",
    )
    seeded_session.add(customer)
    seeded_session.commit()
    seeded_session.refresh(customer)
    client = client_for(seeded_session)
    try:
        response = client.post(
            f"/customers/{customer.id}/update",
            data={
                "customer_name": "Update Explicit",
                "sector": "Transport",
                "transport_domain": "rail",
                "customer_type": "local authority",
                "corporation_tax_status": "yes",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    seeded_session.refresh(customer)
    assert response.status_code == 303
    assert customer.corporation_tax_status == "yes"


def test_customer_audit_events_for_create_update_delete(seeded_session):
    client = client_for(seeded_session)
    try:
        create_response = client.post(
            "/customers",
            data={
                "customer_name": "Audited Customer",
                "sector": "Transport",
                "transport_domain": "rail",
                "customer_type": "private transport operator",
                "corporation_tax_status": "yes",
            },
            follow_redirects=False,
        )
        customer = seeded_session.exec(select(Customer).where(Customer.customer_name == "Audited Customer")).first()
        update_response = client.post(
            f"/customers/{customer.id}/update",
            data={
                "customer_name": "Audited Customer Updated",
                "sector": "Transport",
                "transport_domain": "rail",
                "customer_type": "private transport operator",
                "corporation_tax_status": "yes",
            },
            follow_redirects=False,
        )
        delete_response = client.post(f"/customers/{customer.id}/delete", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    events = list(seeded_session.exec(select(AuditEvent).where(AuditEvent.entity_type == "Customer")))
    actions = [event.action for event in events]
    assert create_response.status_code == 303
    assert update_response.status_code == 303
    assert delete_response.status_code == 303
    assert "create" in actions
    assert "update" in actions
    assert "delete" in actions


def test_sync_entitlement_produces_audit_event(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()

    sync_entitlement_for_project(seeded_session, project.id)

    events = list(
        seeded_session.exec(
            select(AuditEvent).where(AuditEvent.entity_type == "EntitlementAssessment")
        )
    )
    assessment = seeded_session.exec(
        select(EntitlementAssessment).where(EntitlementAssessment.project_id == project.id)
    ).first()
    assert assessment is not None
    assert any(event.entity_id == assessment.id for event in events)
