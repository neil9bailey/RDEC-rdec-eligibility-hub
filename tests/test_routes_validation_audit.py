import importlib

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, func, select

from app.database import get_session
from app.main import app
from app.models import AuditEvent, Contract, Customer, EntitlementAssessment, RDProject
from app.services import sync_entitlement_for_project
from app.settings import Settings, get_settings


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def table_row_counts(session) -> dict[str, int]:
    """Row count for every mapped table.

    Purge and cleanup are the only operations that remove records, so their guard tests
    assert on counts before and after the request. A refusal that returned 400 while still
    deleting or writing rows would pass a status-only assertion.
    """
    return {
        name: session.exec(select(func.count()).select_from(table)).one()
        for name, table in SQLModel.metadata.tables.items()
    }


def test_route_smoke_pages(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        paths = [
            "/",
            "/knowledge-agent",
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


# --- Purge and cleanup HTTP guards (ADR-0002 lines 36-38 and 62) ---------------------------
# Purge is the only irreversible operation in the Hub. ADR-0002 requires it to be disabled by
# default and, when enabled, to require a selected scope, a backup acknowledgement and an exact
# typed phrase. Cleanup requires an exact typed phrase and recalculates eligibility at deletion
# time. Each guard below is pinned with a row-count assertion as well as a status assertion.

VALID_PURGE_FORM = {
    "purge_scope": "claim_work",
    "purge_confirmation": "PURGE CLAIM WORK",
    "backup_acknowledged": "on",
}

PURGE_GUARD_CASES = [
    (
        "no scope and no confirmation",
        {},
        "Confirm that the required backup has been downloaded",
    ),
    (
        "no scope with the backup acknowledged",
        {"backup_acknowledged": "on"},
        "Choose what to purge.",
    ),
    (
        "valid scope with no confirmation phrase",
        {"purge_scope": "claim_work", "backup_acknowledged": "on"},
        "Type PURGE CLAIM WORK exactly to continue.",
    ),
    (
        "valid scope with the wrong confirmation phrase",
        {
            "purge_scope": "claim_work",
            "purge_confirmation": "PURGE OPPORTUNITY WORK",
            "backup_acknowledged": "on",
        },
        "Type PURGE CLAIM WORK exactly to continue.",
    ),
    (
        "correct phrase without the backup acknowledgement",
        {"purge_scope": "claim_work", "purge_confirmation": "PURGE CLAIM WORK"},
        "Confirm that the required backup has been downloaded",
    ),
    (
        "correct phrase with trailing junk",
        {
            "purge_scope": "claim_work",
            "purge_confirmation": "PURGE CLAIM WORK NOW",
            "backup_acknowledged": "on",
        },
        "Type PURGE CLAIM WORK exactly to continue.",
    ),
    (
        "unknown scope",
        {
            "purge_scope": "everything",
            "purge_confirmation": "PURGE ALL WORKING DATA",
            "backup_acknowledged": "on",
        },
        "Choose what to purge.",
    ),
]


@pytest.mark.parametrize(
    "form, expected_message",
    [case[1:] for case in PURGE_GUARD_CASES],
    ids=[case[0] for case in PURGE_GUARD_CASES],
)
def test_enabled_purge_route_refuses_and_removes_nothing(seeded_session, monkeypatch, form, expected_message):
    monkeypatch.setattr(Settings, "data_purge_enabled", True)
    before = table_row_counts(seeded_session)
    client = client_for(seeded_session)
    try:
        response = client.post("/data-management/purge", data=form, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert expected_message in response.text
    assert table_row_counts(seeded_session) == before


def test_enabled_purge_route_applies_only_with_scope_phrase_and_backup(seeded_session, monkeypatch):
    """Positive control for the guard cases above: the same form, correct, does purge.

    Without this, a typo in a field name would make every refusal test pass vacuously.
    """
    monkeypatch.setattr(Settings, "data_purge_enabled", True)
    before = table_row_counts(seeded_session)
    assert before["rdproject"] > 0
    client = client_for(seeded_session)
    try:
        response = client.post("/data-management/purge", data=VALID_PURGE_FORM, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    after = table_row_counts(seeded_session)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/data-management?notice=")
    assert after["rdproject"] == 0
    assert after["customer"] == before["customer"]
    assert after["auditevent"] > before["auditevent"]


def test_purge_route_is_unavailable_unless_an_operator_enables_it(seeded_session):
    before = table_row_counts(seeded_session)
    client = client_for(seeded_session)
    try:
        response = client.post("/data-management/purge", data=VALID_PURGE_FORM, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert get_settings().data_purge_enabled is False
    assert response.status_code == 303
    assert response.headers["location"] == "/data-management?error=purge_disabled"
    assert table_row_counts(seeded_session) == before


def test_purge_setting_defaults_to_off_when_the_environment_is_unset(monkeypatch):
    """ADR-0002 line 37: purge is off by default, not merely off in docker-compose."""
    import app.settings as settings_module

    monkeypatch.delenv("DATA_PURGE_ENABLED", raising=False)
    try:
        reloaded = importlib.reload(settings_module)
        assert reloaded.Settings.data_purge_enabled is False
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


CLEANUP_GUARD_CASES = [
    ("no confirmation phrase", "", "unused", "Type DELETE UNUSED exactly"),
    ("lowercase confirmation phrase", "delete unused", "unused", "Type DELETE UNUSED exactly"),
    (
        "correct phrase but the record is in use",
        "DELETE UNUSED",
        "in_use",
        "no longer available",
    ),
    (
        "correct phrase but an unknown record token",
        "DELETE UNUSED",
        "bogus",
        "no longer available",
    ),
]


def cleanup_fixture_tokens(session) -> dict[str, str]:
    unused = Customer(customer_name="Cleanup Unused Customer")
    in_use = Customer(customer_name="Cleanup In-Use Customer")
    session.add_all([unused, in_use])
    session.commit()
    session.refresh(unused)
    session.refresh(in_use)
    session.add(Contract(contract_name="Cleanup Live Contract", customer_id=in_use.id))
    session.commit()
    return {
        "unused": f"customers:{unused.id}",
        "in_use": f"customers:{in_use.id}",
        "bogus": "customers:999999",
    }


@pytest.mark.parametrize(
    "confirmation, token_kind, expected_message",
    [case[1:] for case in CLEANUP_GUARD_CASES],
    ids=[case[0] for case in CLEANUP_GUARD_CASES],
)
def test_cleanup_route_refuses_and_removes_nothing(
    seeded_session, confirmation, token_kind, expected_message
):
    tokens = cleanup_fixture_tokens(seeded_session)
    before = table_row_counts(seeded_session)
    client = client_for(seeded_session)
    try:
        response = client.post(
            "/data-management/cleanup",
            data={"cleanup_confirmation": confirmation, "cleanup_items": tokens[token_kind]},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert expected_message in response.text
    assert table_row_counts(seeded_session) == before


def test_cleanup_route_removes_only_the_selected_unused_record(seeded_session):
    """Positive control for the cleanup guard cases above."""
    tokens = cleanup_fixture_tokens(seeded_session)
    before = table_row_counts(seeded_session)
    client = client_for(seeded_session)
    try:
        response = client.post(
            "/data-management/cleanup",
            data={"cleanup_confirmation": "DELETE UNUSED", "cleanup_items": tokens["unused"]},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    after = table_row_counts(seeded_session)
    remaining = {customer.customer_name for customer in seeded_session.exec(select(Customer))}
    assert response.status_code == 303
    assert response.headers["location"].startswith("/data-management?notice=")
    assert after["customer"] == before["customer"] - 1
    assert "Cleanup Unused Customer" not in remaining
    assert "Cleanup In-Use Customer" in remaining
    assert after["auditevent"] == before["auditevent"] + 1
