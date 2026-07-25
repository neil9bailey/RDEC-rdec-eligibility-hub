from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.models import CostLine, EvidenceItem, ReviewDecision, RDProject
from app.services import (
    CAVEAT,
    aif_readiness_for_period,
    bulk_project_contexts,
    calculate_project_score,
    cost_summary_by_category,
    cost_validation_warnings,
    get_project_context,
    money,
    project_qualifying_spend,
    score_project_context,
    signed_opinion,
)
from app.rule_loader import rules_version_summary


COST_OUTPUT_CAVEAT = "Qualifying expenditure captured for review; relief value and payable credit are not calculated by this MVP."
ENTITLEMENT_CAVEAT = "Contracted-out and irrelievable-client treatment requires tax review."


def bullet_list(items: list[str]) -> str:
    if not items:
        return "- None recorded.\n"
    return "".join(f"- {item}\n" for item in items)


def generated_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def rule_version_lines() -> list[str]:
    return [f"{name}: {version}" for name, version in rules_version_summary().items()]


def people_time_lines(costs: list[CostLine]) -> list[str]:
    lines = []
    for cost in costs:
        if cost.cost_input_type != "people_time":
            continue
        lines.append(
            f"{cost.person_or_supplier_name or 'Unnamed person'}"
            f" ({cost.person_role or 'role not recorded'}), {cost.activity or 'activity not recorded'}: "
            f"{cost.hours:g} hours at {money(cost.hourly_rate)} / {cost.days:g} days at {money(cost.day_rate)}; "
            f"gross {money(cost.gross_cost)}, qualifying {money(cost.qualifying_amount)}"
        )
    return lines


def evidence_matrix_lines(evidence: list[EvidenceItem]) -> list[str]:
    if not evidence:
        return []
    lines = [
        "| Relevance | Type | Source | Reference | Strength | Review note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in sorted(evidence, key=lambda evidence_item: (evidence_item.relevance_tag, evidence_item.evidence_type)):
        note = item.notes or "Review source and relevance."
        lines.append(
            f"| {item.relevance_tag or 'unclassified'} | {item.evidence_type or 'not recorded'} | "
            f"{item.source_system or 'not recorded'} | {item.source_reference or item.url_or_file_path or 'not recorded'} | "
            f"{item.strength or 'not rated'} | {note} |"
        )
    return lines


def cost_warning_lines(costs: list[CostLine], project_title: str = "") -> list[str]:
    lines: list[str] = []
    prefix = f"{project_title}: " if project_title else ""
    for cost in costs:
        for warning in cost_validation_warnings(cost):
            lines.append(f"{prefix}{warning}")
    return lines


def project_review_checklist(context, score) -> list[str]:
    project = context.project
    checklist = [
        f"Project owner: resolve {len(score.blockers)} blocker(s) and {len(score.warnings)} warning(s) before pack reliance.",
        f"Competent professional: {'signed opinion captured' if signed_opinion(context.opinions) else 'signed opinion required'}.",
        f"Evidence owner: {len(context.evidence)} evidence item(s) captured; add strong evidence for each weak or missing relevance area.",
        f"Finance owner: {len(context.costs)} cost line(s) captured; review apportionment, paid status, evidence links and overseas/EPW flags.",
        f"Tax/Ayming owner: entitlement status is {context.entitlement.status if context.entitlement else 'not assessed'}; review contracted-out and irrelievable-client facts.",
        f"AIF owner: project is {'marked as described' if project.described_in_aif else 'not yet marked as described'} in the AIF selection workflow.",
        CAVEAT,
    ]
    return checklist


def claim_period_review_checklist(readiness: dict, cost_warnings: list[str], evidence_gaps: list[str]) -> list[str]:
    selection = readiness["selection"]
    checklist = [
        f"Finance: confirm company identifiers, senior R&D contact, and total qualifying expenditure of {money(selection.total_qualifying_expenditure)}.",
        f"AIF owner: selection method is '{selection.selection_method or 'not recorded'}' with {selection.coverage_percentage}% selected expenditure coverage.",
        f"Evidence owner: resolve {len(evidence_gaps)} project evidence gap(s) before treating the pack as audit-ready.",
        f"Cost owner: resolve {len(cost_warnings)} cost warning(s), including missing evidence links, apportionment issues, and overseas/EPW review points.",
        "Competent professional: confirm each included R&D candidate has signed technical support and clear project boundaries.",
        "Tax/Ayming: review entitlement, contracted-out treatment, irrelievable-client assumptions, AIF sequencing, and scheme position.",
        CAVEAT,
    ]
    return checklist


def markdown_table(lines: list[str]) -> str:
    return "\n".join(lines) + ("\n" if lines else "")


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
    time_lines = people_time_lines(context.costs)
    evidence_matrix = evidence_matrix_lines(context.evidence)
    cost_warnings = cost_warning_lines(context.costs)
    checklist = project_review_checklist(context, score)
    executive_summary = [
        f"Current status: {score.rating} ({score.rating_label}).",
        f"Captured qualifying expenditure for review: {money(total_spend)}.",
        f"Evidence items captured: {len(context.evidence)}.",
        f"Cost warnings requiring review: {len(cost_warnings)}.",
        CAVEAT,
    ]

    return f"""# Project Eligibility Memo: {project.project_title}

**Decision-support caveat:** {CAVEAT}
**Generated at:** {generated_timestamp()}

## Executive review summary
{bullet_list(executive_summary)}

## Rule versions used
{bullet_list(rule_version_lines())}

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
## Evidence matrix
{markdown_table(evidence_matrix) or "- No evidence matrix available until evidence is captured.\n"}

## Cost summary
{COST_OUTPUT_CAVEAT}

{bullet_list(cost_lines)}
Total qualifying amount captured: {money(total_spend)}

## Cost warnings for Finance review
{bullet_list(cost_warnings)}

## People time detail
{bullet_list(time_lines)}
## Entitlement assessment
{ENTITLEMENT_CAVEAT}

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
## Reviewer checklist
{bullet_list(checklist)}
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
    people_time: list[str] = []
    cost_warnings: list[str] = []
    project_readiness_lines: list[str] = []

    # One batched load for every project in the period, instead of ~10 queries per project.
    # Contexts are built before any score is computed, exactly as the per-project loop did, so a
    # project whose entitlement assessment is created while scoring still reports no entitlement
    # note on this render -- the pack's content is unchanged by the batching.
    contexts = bulk_project_contexts(session, projects)
    for project in projects:
        context = contexts[project.id or 0]
        score = score_project_context(session, context)
        all_costs.extend(context.costs)
        if not context.evidence:
            evidence_gaps.append(f"{project.project_title}: no evidence linked")
        if context.entitlement:
            entitlement_notes.append(f"{project.project_title}: {context.entitlement.status} - {context.entitlement.rationale}")
        people_time.extend([f"{project.project_title}: {line}" for line in people_time_lines(context.costs)])
        cost_warnings.extend(cost_warning_lines(context.costs, project.project_title))
        project_lines.append(
            f"{project.project_title}: {score.rating} / {score.score}, spend {money(project_qualifying_spend(context.costs))}"
        )
        project_readiness_lines.append(
            f"{project.project_title}: rating {score.rating}, blockers {len(score.blockers)}, "
            f"warnings {len(score.warnings)}, evidence {len(context.evidence)}, costs {len(context.costs)}, "
            f"AIF described {project.described_in_aif}"
        )

    cost_summary = cost_summary_by_category(all_costs)
    cost_lines = [f"{category}: {money(amount)}" for category, amount in cost_summary.items()]
    checklist = claim_period_review_checklist(readiness, cost_warnings, evidence_gaps)
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
**Generated at:** {generated_timestamp()}

## Rule versions used
{bullet_list(rule_version_lines())}

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
## Project readiness matrix
{bullet_list(project_readiness_lines)}

## Total qualifying spend by category
{COST_OUTPUT_CAVEAT}

{bullet_list(cost_lines)}
## Cost warnings for Finance review
{bullet_list(cost_warnings)}

## People time detail
{bullet_list(people_time)}
## AIF readiness
- Ready: {readiness["ready"]}
- Project count: {readiness["selection"].project_count}
- Selected project IDs: {", ".join(map(str, readiness["selection"].selected_project_ids)) or "None"}
- Selected expenditure coverage: {readiness["selection"].coverage_percentage}%
- Selection method: {readiness["selection"].selection_method or "Not recorded"}
- Submission status: {submission_line}

## AIF project-selection notes
{bullet_list(readiness["selection"].notes)}

## Contracted-out / public sector entitlement notes
{ENTITLEMENT_CAVEAT}

{bullet_list(entitlement_notes)}
## Evidence gaps
{bullet_list(evidence_gaps)}
## AIF and pack warnings
{bullet_list(readiness["warnings"])}
## Reviewer checklist
{bullet_list(checklist)}

## Approval trail
{bullet_list(approval_lines)}
"""


def generate_evidence_index_markdown(session: Session) -> str:
    projects = list(session.exec(select(RDProject)))
    lines = [
        f"# Evidence Index\n\n**Decision-support caveat:** {CAVEAT}\n",
        f"**Generated at:** {generated_timestamp()}\n\n",
        "## Rule versions used\n",
        bullet_list(rule_version_lines()),
    ]
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
