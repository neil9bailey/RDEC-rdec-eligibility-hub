from __future__ import annotations

from sqlmodel import Session, select

from app.models import CostLine, EvidenceItem, ReviewDecision, RDProject
from app.services import (
    CAVEAT,
    aif_readiness_for_period,
    calculate_project_score,
    cost_summary_by_category,
    get_project_context,
    money,
    project_qualifying_spend,
)


def bullet_list(items: list[str]) -> str:
    if not items:
        return "- None recorded.\n"
    return "".join(f"- {item}\n" for item in items)


def generate_project_memo_markdown(session: Session, project_id: int) -> str:
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    project = context.project
    entitlement = context.entitlement
    cost_summary = cost_summary_by_category(context.costs)
    total_spend = project_qualifying_spend(context.costs)

    evidence_lines = [
        f"{item.relevance_tag}: {item.evidence_type} ({item.source_system} {item.source_reference}) - {item.strength}"
        for item in context.evidence
    ]
    cost_lines = [f"{category}: {money(amount)}" for category, amount in cost_summary.items()]

    return f"""# Project Eligibility Memo: {project.project_title}

**Decision-support caveat:** {CAVEAT}

## Project identity
- Solution: {context.solution.solution_name if context.solution else "Not linked"}
- Customer: {context.customer.customer_name if context.customer else "Not linked"}
- Accounting period: {context.period.label if context.period else "Missing"}
- Outcome: {project.outcome}
- R&D boundary: {project.boundary_explanation or "Not recorded"}

## Field of science or technology
{project.field_of_science_or_technology or "Not recorded"}

## Baseline
{project.baseline_knowledge or "Not recorded"}

## Advance sought
{project.advance_sought or "Not recorded"}

## Wider-field explanation
{project.wider_field_explanation or "Not recorded"}

## Scientific or technological uncertainties
{project.scientific_or_technological_uncertainties or "Not recorded"}

## Resolution activity
{project.experiments_prototypes_tests or "Not recorded"}

## Failed attempts
{project.failed_attempts or "None recorded"}

## Competent professional statement
{context.opinions[0].opinion_text if context.opinions else "No competent professional opinion recorded."}

## Evidence index
{bullet_list(evidence_lines)}
## Cost summary
{bullet_list(cost_lines)}
Total qualifying amount captured: {money(total_spend)}

## Entitlement assessment
- Status: {entitlement.status if entitlement else "Not assessed"}
- Rationale: {entitlement.rationale if entitlement else "Not assessed"}

## Score and risk rating
- Score: {score.score}
- Rating: {score.rating} ({score.rating_label})

## Blockers
{bullet_list(score.blockers)}
## Warnings
{bullet_list(score.warnings)}
## Recommended next actions
{bullet_list(score.recommended_next_actions)}
"""


def generate_claim_period_pack_markdown(session: Session, period_id: int) -> str:
    readiness = aif_readiness_for_period(session, period_id)
    period = readiness["period"]
    company = readiness["company"]
    projects = readiness["projects"]
    submission = readiness["submission"]
    all_costs: list[CostLine] = []
    evidence_gaps: list[str] = []
    entitlement_notes: list[str] = []
    project_lines: list[str] = []

    for project in projects:
        context = get_project_context(session, project.id or 0)
        score = calculate_project_score(session, project.id or 0)
        all_costs.extend(context.costs)
        if not context.evidence:
            evidence_gaps.append(f"{project.project_title}: no evidence linked")
        if context.entitlement:
            entitlement_notes.append(f"{project.project_title}: {context.entitlement.status} - {context.entitlement.rationale}")
        project_lines.append(
            f"{project.project_title}: {score.rating} / {score.score}, spend {money(project_qualifying_spend(context.costs))}"
        )

    cost_summary = cost_summary_by_category(all_costs)
    cost_lines = [f"{category}: {money(amount)}" for category, amount in cost_summary.items()]
    review_decisions = list(session.exec(select(ReviewDecision).where(ReviewDecision.project_id.in_([p.id for p in projects]))))
    approval_lines = [
        f"{decision.created_at.date().isoformat()} - {decision.reviewer_name}: {decision.decision_status} ({decision.comments})"
        for decision in review_decisions
    ]
    submission_line = "No submission status captured."
    if submission:
        submission_line = (
            f"AIF submitted: {submission.aif_submitted} ({submission.aif_submission_date or 'n/a'}); "
            f"CT600 submitted: {submission.ct600_submitted} ({submission.ct600_submission_date or 'n/a'})"
        )

    return f"""# Claim Period Pack: {period.label}

**Decision-support caveat:** {CAVEAT}

## Company details
- Company: {company.company_name if company else "Missing"}
- UTR: {company.utr if company else "Missing"}
- PAYE reference: {company.paye_reference if company else "Missing"}
- Senior R&D contact: {company.senior_rd_contact_name if company else "Missing"}
- Northern Ireland registered: {company.northern_ireland_registered if company else "Unknown"}

## Accounting period
- Start: {period.start_date}
- End: {period.end_date}
- Scheme determination placeholder: merged RDEC / ERIS review required.

## Project list
{bullet_list(project_lines)}
## Total qualifying spend by category
{bullet_list(cost_lines)}
## AIF readiness
- Ready: {readiness["ready"]}
- Project count: {readiness["selection"].project_count}
- Selected project IDs: {", ".join(map(str, readiness["selection"].selected_project_ids)) or "None"}
- Selected expenditure coverage: {readiness["selection"].coverage_percentage}%
- Submission status: {submission_line}

## Contracted-out / public sector entitlement notes
{bullet_list(entitlement_notes)}
## Evidence gaps
{bullet_list(evidence_gaps)}
## AIF and pack warnings
{bullet_list(readiness["warnings"])}
## Approval trail
{bullet_list(approval_lines)}
"""


def generate_evidence_index_markdown(session: Session) -> str:
    projects = list(session.exec(select(RDProject)))
    lines = [f"# Evidence Index\n\n**Decision-support caveat:** {CAVEAT}\n"]
    for project in projects:
        lines.append(f"\n## {project.project_title}\n")
        items = list(session.exec(select(EvidenceItem).where(EvidenceItem.project_id == project.id)))
        if not items:
            lines.append("- No evidence linked.\n")
            continue
        for item in sorted(items, key=lambda i: (i.relevance_tag, i.evidence_type)):
            lines.append(
                f"- {item.relevance_tag}: {item.evidence_type} | {item.source_system} | "
                f"{item.source_reference} | {item.strength} | {item.url_or_file_path}\n"
            )
    return "".join(lines)
