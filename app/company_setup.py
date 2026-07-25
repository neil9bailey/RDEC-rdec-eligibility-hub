from __future__ import annotations

from datetime import date
import re

from app.models import AccountingPeriod, Company


COMPANY_SETUP_CAVEAT = (
    "Decision support only. Company setup readiness helps Finance and reviewers find missing or inconsistent "
    "capture fields; it does not decide tax, RDEC eligibility, claim notification, AIF, CT600, or agent authority."
)


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_utr(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_paye_reference(value: object) -> str:
    return clean_text(value).upper()


def normalize_vat_number(value: object) -> str:
    text = re.sub(r"[\s-]+", "", str(value or "")).upper()
    if text and text.isdigit():
        return f"GB{text}"
    return text


def normalize_sic_code(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_company_payload(form) -> dict:
    return {
        "company_name": clean_text(form.get("company_name")),
        "utr": normalize_utr(form.get("utr")),
        "paye_reference": normalize_paye_reference(form.get("paye_reference")),
        "vat_number": normalize_vat_number(form.get("vat_number")),
        "sic_code": normalize_sic_code(form.get("sic_code")),
        "registered_office_country": clean_text(form.get("registered_office_country")) or "United Kingdom",
        "registered_office_region": clean_text(form.get("registered_office_region")),
        "senior_rd_contact_name": clean_text(form.get("senior_rd_contact_name")),
        "senior_rd_contact_role": clean_text(form.get("senior_rd_contact_role")),
        "senior_rd_contact_email": clean_text(form.get("senior_rd_contact_email")).lower(),
        "senior_rd_contact_phone": clean_text(form.get("senior_rd_contact_phone")),
        "agent_name": clean_text(form.get("agent_name")),
        "agent_reference": clean_text(form.get("agent_reference")),
        "agent_email": clean_text(form.get("agent_email")).lower(),
        "agent_phone": clean_text(form.get("agent_phone")),
        "agent_role": clean_text(form.get("agent_role")),
    }


def valid_email(value: str) -> bool:
    if not value:
        return True
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))


def validate_company_payload(payload: dict) -> list[str]:
    errors: list[str] = []
    if not payload["company_name"]:
        errors.append("Company name is required.")
    if payload["senior_rd_contact_email"] and not valid_email(payload["senior_rd_contact_email"]):
        errors.append("Senior R&D contact email must look like an email address.")
    if payload["agent_email"] and not valid_email(payload["agent_email"]):
        errors.append("Agent email must look like an email address.")
    return errors


def accounting_period_label(start: date, end: date) -> str:
    if start.year == end.year:
        return f"FY{end.year}"
    return f"FY{start.year}/{str(end.year)[-2:]}"


def validate_accounting_period_dates(
    start_date: date | None,
    end_date: date | None,
    period_of_account_start: date | None,
    period_of_account_end: date | None,
    claim_notification_submitted: bool,
    claim_notification_date: date | None,
) -> list[str]:
    errors: list[str] = []
    if start_date and end_date and end_date < start_date:
        errors.append("Accounting period end must be on or after accounting period start.")
    if period_of_account_start and period_of_account_end and period_of_account_end < period_of_account_start:
        errors.append("Period of account end must be on or after period of account start.")
    if claim_notification_submitted and not claim_notification_date:
        errors.append("Claim notification date is required when claim notification is marked as submitted.")
    return errors


def _item(label: str, status: str, detail: str) -> dict:
    return {
        "label": label,
        "status": status,
        "detail": detail,
        "badge_class": "green" if status == "complete" else "amber" if status == "review" else "red",
    }


def company_setup_readiness(company: Company, periods: list[AccountingPeriod]) -> dict:
    items = [
        _item(
            "Company legal name",
            "complete" if company.company_name else "missing",
            company.company_name or "Capture the claimant company legal name.",
        ),
        _item(
            "UTR",
            "complete" if re.fullmatch(r"\d{10}", company.utr or "") else "missing" if not company.utr else "review",
            "10 digits captured." if re.fullmatch(r"\d{10}", company.utr or "") else "Capture or review the 10-digit UTR.",
        ),
        _item(
            "PAYE reference",
            "complete" if company.paye_reference else "missing",
            company.paye_reference or "Capture the PAYE reference used for payroll evidence.",
        ),
        _item(
            "Registered office",
            "complete" if company.registered_office_country and company.registered_office_region else "missing",
            (
                f"{company.registered_office_region}, {company.registered_office_country}"
                if company.registered_office_country and company.registered_office_region
                else "Capture country and region for company setup review."
            ),
        ),
    ]
    contact_fields = [
        company.senior_rd_contact_name,
        company.senior_rd_contact_role,
        company.senior_rd_contact_email,
        company.senior_rd_contact_phone,
    ]
    items.append(
        _item(
            "Senior R&D contact",
            "complete" if all(contact_fields) else "missing",
            "Name, role, email, and phone captured." if all(contact_fields) else "Capture name, role, email, and phone.",
        )
    )
    agent_fields = [company.agent_name, company.agent_reference, company.agent_email, company.agent_phone, company.agent_role]
    if not any(agent_fields):
        agent_status = "complete"
        agent_detail = "No agent details recorded; treat as not used for this setup."
    elif company.agent_name and company.agent_role and (company.agent_email or company.agent_phone):
        agent_status = "complete"
        agent_detail = "Agent contact details captured for review."
    else:
        agent_status = "review"
        agent_detail = "Agent details are partial; confirm name, role, and at least one contact route."
    items.append(_item("Agent details", agent_status, agent_detail))

    items.append(
        _item(
            "Accounting periods",
            "complete" if periods else "missing",
            f"{len(periods)} accounting period(s) captured." if periods else "Add at least one accounting period.",
        )
    )
    date_issue_count = sum(
        1
        for period in periods
        if period.end_date < period.start_date or period.period_of_account_end < period.period_of_account_start
    )
    items.append(
        _item(
            "Accounting date order",
            "review" if date_issue_count else "complete",
            "All accounting and period-of-account dates are ordered." if not date_issue_count else f"{date_issue_count} period(s) need date review.",
        )
    )
    notification_review_count = sum(
        1
        for period in periods
        if not period.prior_claim_within_3_years
        and (not period.claim_notification_submitted or not period.claim_notification_date)
    )
    items.append(
        _item(
            "Claim notification position",
            "review" if notification_review_count else "complete",
            (
                "Claim notification coverage is recorded for each period."
                if not notification_review_count
                else f"{notification_review_count} period(s) need claim notification review."
            ),
        )
    )

    missing_count = sum(1 for item in items if item["status"] == "missing")
    review_count = sum(1 for item in items if item["status"] == "review")
    complete_count = sum(1 for item in items if item["status"] == "complete")
    return {
        "company": company,
        "items": items,
        "missing_count": missing_count,
        "review_count": review_count,
        "complete_count": complete_count,
        "total_count": len(items),
        "completion_percent": round((complete_count / len(items)) * 100),
        "ready": missing_count == 0,
        "caveat": COMPANY_SETUP_CAVEAT,
    }


def company_setup_context(companies: list[Company], periods: list[AccountingPeriod]) -> dict:
    periods_by_company: dict[int, list[AccountingPeriod]] = {}
    for period in periods:
        periods_by_company.setdefault(period.company_id, []).append(period)
    readiness_items = [
        company_setup_readiness(company, periods_by_company.get(company.id or 0, [])) for company in companies
    ]
    return {
        "items": readiness_items,
        "by_company_id": {item["company"].id: item for item in readiness_items if item["company"].id},
        "ready_count": sum(1 for item in readiness_items if item["ready"] and item["review_count"] == 0),
        "needs_detail_count": sum(1 for item in readiness_items if item["missing_count"]),
        "review_count": sum(1 for item in readiness_items if item["review_count"] and not item["missing_count"]),
        "caveat": COMPANY_SETUP_CAVEAT,
    }
