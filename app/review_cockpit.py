from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.models import (
    AccountingPeriod,
    CompetentProfessionalOpinion,
    Contract,
    CostLine,
    Customer,
    EvidenceItem,
    RDProject,
    Solution,
)


def _stage(
    number: int,
    title: str,
    description: str,
    status: str,
    status_label: str,
    href: str,
    action: str,
    detail: str,
) -> dict[str, Any]:
    tone = {"complete": "green", "attention": "red", "in_progress": "amber", "not_started": "weak"}[status]
    return {
        "number": number,
        "title": title,
        "description": description,
        "status": status,
        "status_label": status_label,
        "tone": tone,
        "href": href,
        "action": action,
        "detail": detail,
    }


def review_workflow_context(
    session: Session,
    dashboard_metrics: dict[str, Any],
    company_setup: dict[str, Any],
    framework_metrics: dict[str, Any],
) -> dict[str, Any]:
    customers = list(session.exec(select(Customer)))
    contracts = list(session.exec(select(Contract)))
    solutions = list(session.exec(select(Solution)))
    projects = list(session.exec(select(RDProject)))
    periods = list(session.exec(select(AccountingPeriod)))

    company_items = company_setup.get("items", [])
    company_complete = bool(company_items) and all(
        item["ready"] and item["review_count"] == 0 for item in company_items
    )
    if company_complete:
        company_status, company_label = "complete", "Ready for review"
    elif company_items:
        company_status, company_label = "in_progress", "Needs attention"
    else:
        company_status, company_label = "attention", "Start here"

    context_counts = (len(customers), len(contracts), len(solutions))
    if all(context_counts):
        context_status, context_label = "complete", "Work described"
    elif any(context_counts):
        context_status, context_label = "in_progress", "Partly complete"
    else:
        context_status, context_label = "not_started", "Not started"

    required_assessment_fields = (
        "field_of_science_or_technology",
        "baseline_knowledge",
        "advance_sought",
        "scientific_or_technological_uncertainties",
        "boundary_explanation",
    )
    incomplete_assessments = sum(
        1 for project in projects if any(not getattr(project, field).strip() for field in required_assessment_fields)
    )
    if not projects:
        project_status, project_label = "attention", "Add a project"
    elif incomplete_assessments:
        project_status, project_label = "in_progress", f"{incomplete_assessments} need detail"
    else:
        project_status, project_label = "complete", "Assessments complete"

    evidence_project_ids = {item.project_id for item in session.exec(select(EvidenceItem))}
    cost_project_ids = {item.project_id for item in session.exec(select(CostLine))}
    signed_project_ids = {
        item.project_id
        for item in session.exec(
            select(CompetentProfessionalOpinion).where(CompetentProfessionalOpinion.signoff_status == "signed")
        )
    }
    review_ready_projects = {
        project.id
        for project in projects
        if project.id in evidence_project_ids and project.id in cost_project_ids and project.id in signed_project_ids
    }
    evidence_gap_count = max(len(projects) - len(review_ready_projects), 0)
    if not projects:
        evidence_status, evidence_label = "not_started", "Waiting for projects"
    elif evidence_gap_count:
        evidence_status, evidence_label = "in_progress", f"{evidence_gap_count} need evidence"
    else:
        evidence_status, evidence_label = "complete", "Evidence ready"

    ready_periods = dashboard_metrics["aif_ready_periods"]
    total_periods = dashboard_metrics["aif_total_periods"]
    if not total_periods:
        final_status, final_label = "not_started", "Add a period"
    elif ready_periods == total_periods:
        final_status, final_label = "complete", "Ready for final review"
    else:
        final_status, final_label = "in_progress", f"{total_periods - ready_periods} need review"

    first_period_href = f"/claim-periods/{periods[0].id}/readiness" if periods else "/companies#periods"
    stages = [
        _stage(
            1,
            "Company setup",
            "Confirm the claimant, senior contact and accounting period.",
            company_status,
            company_label,
            "/companies",
            "Open company setup",
            f"{company_setup.get('ready_count', 0)} ready; {company_setup.get('needs_detail_count', 0)} need details.",
        ),
        _stage(
            2,
            "Describe the work",
            "Link the customer, contract and solution that provide the business context.",
            context_status,
            context_label,
            "/customers",
            "Open work context",
            f"{len(customers)} customers, {len(contracts)} contracts and {len(solutions)} solutions.",
        ),
        _stage(
            3,
            "Review R&D projects",
            "Record the advance, uncertainty, work carried out and project boundary.",
            project_status,
            project_label,
            "/projects",
            "Open R&D projects",
            f"{len(projects)} projects; {incomplete_assessments} need core assessment details.",
        ),
        _stage(
            4,
            "Add evidence and costs",
            "Link supporting evidence, reconcile costs and obtain competent professional review.",
            evidence_status,
            evidence_label,
            "/costs",
            "Open evidence and costs",
            f"{len(review_ready_projects)} of {len(projects)} projects have evidence, costs and signed review.",
        ),
        _stage(
            5,
            "Complete final review",
            "Check the accounting-period position and prepare the review pack.",
            final_status,
            final_label,
            first_period_href,
            "Open final review",
            f"{ready_periods} of {total_periods} accounting periods have no current readiness warnings.",
        ),
    ]

    actions = [
        {
            "title": stage["title"],
            "text": stage["detail"],
            "href": stage["href"],
            "action": stage["action"],
            "tone": stage["tone"],
            "status": stage["status_label"],
        }
        for stage in stages
        if stage["status"] != "complete"
    ]
    if framework_metrics.get("pending_signals"):
        actions.append(
            {
                "title": "Review new opportunity suggestions",
                "text": f"{framework_metrics['pending_signals']} suggestions are waiting for a person to accept or reject them.",
                "href": "/framework-intelligence/requirements",
                "action": "Open suggestions",
                "tone": "amber",
                "status": "Review needed",
            }
        )
    if framework_metrics.get("source_warnings"):
        actions.append(
            {
                "title": "Check source warnings",
                "text": f"{framework_metrics['source_warnings']} source checks need attention before their results are relied on.",
                "href": "/framework-intelligence/source-changes",
                "action": "Open source checks",
                "tone": "red",
                "status": "Attention",
            }
        )

    complete_count = sum(1 for stage in stages if stage["status"] == "complete")
    next_stage = next((stage for stage in stages if stage["status"] != "complete"), stages[-1])
    return {
        "stages": stages,
        "actions": actions[:6],
        "complete_count": complete_count,
        "progress_percent": int(round((complete_count / len(stages)) * 100)),
        "next_stage": next_stage,
    }
