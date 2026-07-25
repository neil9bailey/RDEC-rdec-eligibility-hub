from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import select

from app.company_setup import accounting_period_label, company_setup_context, normalize_company_payload
from app.database import get_session
from app.main import app
from app.models import AccountingPeriod, ClaimPeriodSubmissionStatus, Company


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_company_setup_context_flags_missing_and_review_items(session):
    company = Company(
        company_name="Telent Limited",
        utr="1234567890",
        paye_reference="951/NA12345",
        registered_office_country="United Kingdom",
        registered_office_region="England",
        senior_rd_contact_name="Amira Shah",
        senior_rd_contact_role="CTO",
        senior_rd_contact_email="amira@example.local",
        senior_rd_contact_phone="020 7000 0000",
        agent_name="Evidence Review LLP",
    )
    session.add(company)
    session.commit()
    session.refresh(company)

    context = company_setup_context([company], [])
    readiness = context["by_company_id"][company.id]

    assert readiness["missing_count"] == 1
    assert readiness["review_count"] == 1
    assert any(item["label"] == "Accounting periods" and item["status"] == "missing" for item in readiness["items"])
    assert any(item["label"] == "Agent details" and item["status"] == "review" for item in readiness["items"])


def test_company_create_normalizes_machine_readable_fields(session):
    client = client_for(session)
    try:
        response = client.post(
            "/companies",
            data={
                "company_name": "  Telent Limited  ",
                "utr": " 12345 67890 ",
                "paye_reference": " 951/na12345 ",
                "vat_number": " gb 123 456 789 ",
                "sic_code": "62 01",
                "registered_office_country": " United Kingdom ",
                "registered_office_region": " England ",
                "senior_rd_contact_email": " REVIEWER@EXAMPLE.LOCAL ",
                "agent_email": " agent@example.local ",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    company = session.exec(select(Company).where(Company.company_name == "Telent Limited")).first()
    assert company is not None
    assert company.utr == "1234567890"
    assert company.paye_reference == "951/NA12345"
    assert company.vat_number == "GB123456789"
    assert company.sic_code == "6201"
    assert company.senior_rd_contact_email == "reviewer@example.local"


def test_invalid_company_email_returns_validation_error(session):
    client = client_for(session)
    try:
        response = client.post(
            "/companies",
            data={"company_name": "Telent Limited", "senior_rd_contact_email": "not-an-email"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "Senior R&D contact email must look like an email address." in response.text


def test_accounting_period_auto_label_and_submission_status(session):
    company = Company(company_name="Telent Limited")
    session.add(company)
    session.commit()
    session.refresh(company)
    client = client_for(session)
    try:
        response = client.post(
            "/accounting-periods",
            data={
                "company_id": str(company.id),
                "label": "",
                "start_date": "2025-04-01",
                "end_date": "2026-03-31",
                "period_of_account_start": "2025-04-01",
                "period_of_account_end": "2026-03-31",
                "claim_notification_submitted": "on",
                "claim_notification_date": "2025-09-01",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    period = session.exec(select(AccountingPeriod)).first()
    assert period is not None
    assert period.label == "FY2025/26"
    assert period.claim_notification_submitted is True
    assert session.exec(select(ClaimPeriodSubmissionStatus)).first() is not None


def test_accounting_period_rejects_bad_date_order(session):
    company = Company(company_name="Telent Limited")
    session.add(company)
    session.commit()
    session.refresh(company)
    client = client_for(session)
    try:
        response = client.post(
            "/accounting-periods",
            data={
                "company_id": str(company.id),
                "start_date": "2026-03-31",
                "end_date": "2025-04-01",
                "period_of_account_start": "2025-04-01",
                "period_of_account_end": "2026-03-31",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "Accounting period end must be on or after accounting period start." in response.text


def test_accounting_period_label_helper_handles_calendar_year():
    assert accounting_period_label(date(2026, 1, 1), date(2026, 12, 31)) == "FY2026"


def test_company_payload_normalization_handles_empty_values():
    payload = normalize_company_payload({})
    assert payload["registered_office_country"] == "United Kingdom"
    assert payload["company_name"] == ""
