from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from typing import Iterable

from sqlmodel import Session, col, select

from app.audit import compact_snapshot, log_event
from app.models import (
    AccountingPeriod,
    Activity,
    ClaimPeriodSubmissionStatus,
    Company,
    CompetentProfessionalOpinion,
    Contract,
    CostLine,
    Customer,
    EntitlementAssessment,
    EvidenceItem,
    RDProject,
    Solution,
)
from app.rules_engine import get_rules
from app.schemas import AIFSelectionResult, EntitlementResult, ScoreResult
from app.text_matching import TermMatch, find_matches


CAVEAT = "Requires competent professional and tax review."

# ADR-0002 Ruling R2: the DISPLAYED text for a stored entitlement status, and nothing else.
# The stored value stays exactly as ``assess_entitlement`` produced it -- it is the ``.badge``
# CSS class, the value the assessment form submits, and the column written to the database --
# so nothing here changes what is stored, submitted or scored.
#
# This mapping is byte-identical to ``ENTITLEMENT_LABELS`` in ``app/templates/_labels.html``,
# and ``tests/test_reports_and_models.py`` parses that template and asserts they still match.
# The screen and the Markdown exports handed to HMRC and to the tax advisor therefore cannot
# describe the same stored status in two different ways, which is exactly what happened when
# the eligibility panel was humanised and the export builders were not.
#
# The wording is the ADR-0001 line 72 vocabulary ("review required", "blocked") and states an
# indicator, never an approval, a rejection or a verdict (ADR-0002 line 59).
ENTITLEMENT_STATUS_LABELS = {
    "blocked": "Blocked",
    "supplier_likely": "Supplier indicators",
    "customer_likely": "Customer indicators",
    "ambiguous_tax_review": "Review required",
}

# Mirrors the template macro's fallback so the two surfaces cannot diverge on an unknown value.
# It is not a silent degrade: ``test_every_entitlement_status_the_engine_can_produce_has_a_label``
# asserts every status ``assess_entitlement`` can return is an explicit key above, so this
# default is unreachable for any status the engine actually produces, and a new status added
# without a label turns that test red rather than quietly rendering as "Review required".
ENTITLEMENT_LABEL_FALLBACK = "Review required"


def entitlement_label(status: str | None) -> str:
    """The reviewer-facing label for a stored entitlement status."""
    return ENTITLEMENT_STATUS_LABELS.get(str(status or ""), ENTITLEMENT_LABEL_FALLBACK)


@dataclass
class ProjectContext:
    project: RDProject
    solution: Solution | None
    customer: Customer | None
    contract: Contract | None
    period: AccountingPeriod | None
    company: Company | None
    submission_status: ClaimPeriodSubmissionStatus | None
    activities: list[Activity]
    opinions: list[CompetentProfessionalOpinion]
    evidence: list[EvidenceItem]
    costs: list[CostLine]
    entitlement: EntitlementAssessment | None


def as_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def money(value: float | int | None) -> str:
    return f"GBP {float(value or 0):,.2f}"


def is_finite_number(value: float | int | None) -> bool:
    try:
        return isfinite(float(value or 0))
    except (TypeError, ValueError):
        return False


def calculate_qualifying_amount(gross_cost: float, apportionment_percentage: float) -> float:
    """Qualifying amount, guarded against non-finite input and overflow.

    A stored nan/inf gross cost or percentage previously propagated straight into
    report totals and CSV exports (and returned HTTP 500 for "nan"). The guard
    here is a last-resort arithmetic safety net, not the control: the real
    control is validation at the form layer (app.form_utils.parse_money /
    parse_percentage). Whenever this guard fires, cost_validation_warnings()
    surfaces the affected cost line so the degrade is visible, never silent.
    """
    if not is_finite_number(gross_cost) or not is_finite_number(apportionment_percentage):
        return 0.0
    amount = float(gross_cost or 0) * float(apportionment_percentage or 0) / 100
    if not isfinite(amount):
        return 0.0
    return round(amount, 2)


def calculate_people_time_gross(hours: float, hourly_rate: float, days: float, day_rate: float) -> float:
    return round((float(hours or 0) * float(hourly_rate or 0)) + (float(days or 0) * float(day_rate or 0)), 2)


def project_qualifying_spend(costs: Iterable[CostLine]) -> float:
    """Total qualifying spend, excluding any cost line that is not a real number.

    A single stored nan or inf would otherwise poison the whole total, and a nan
    total silently defeats every downstream comparison (nan <= 0 is False, so a
    period would report as ready). Excluded lines are flagged individually by
    cost_validation_warnings().
    """
    total = 0.0
    for cost in costs:
        amount = cost.qualifying_amount or calculate_qualifying_amount(cost.gross_cost, cost.apportionment_percentage)
        if not is_finite_number(amount):
            continue
        total += float(amount or 0)
    return round(total, 2)


def get_project_context(session: Session, project_id: int) -> ProjectContext:
    project = session.get(RDProject, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    solution = session.get(Solution, project.solution_id)
    customer = session.get(Customer, solution.customer_id) if solution else None
    contract = session.get(Contract, solution.contract_id) if solution and solution.contract_id else None
    period = session.get(AccountingPeriod, project.accounting_period_id) if project.accounting_period_id else None
    company = session.get(Company, period.company_id) if period else None
    submission_status = None
    if period:
        submission_status = session.exec(
            select(ClaimPeriodSubmissionStatus).where(ClaimPeriodSubmissionStatus.accounting_period_id == period.id)
        ).first()

    activities = list(session.exec(select(Activity).where(Activity.project_id == project.id)))
    opinions = list(session.exec(select(CompetentProfessionalOpinion).where(CompetentProfessionalOpinion.project_id == project.id)))
    evidence = list(session.exec(select(EvidenceItem).where(EvidenceItem.project_id == project.id)))
    costs = list(session.exec(select(CostLine).where(CostLine.project_id == project.id)))
    entitlement = session.exec(select(EntitlementAssessment).where(EntitlementAssessment.project_id == project.id)).first()

    return ProjectContext(
        project=project,
        solution=solution,
        customer=customer,
        contract=contract,
        period=period,
        company=company,
        submission_status=submission_status,
        activities=activities,
        opinions=opinions,
        evidence=evidence,
        costs=costs,
        entitlement=entitlement,
    )


# SQLite caps the number of bound variables in one statement. Chunking the IN lists keeps the bulk
# loaders correct on datasets far larger than the ones benchmarked, instead of failing at a size
# nobody tested.
IN_CLAUSE_CHUNK = 400


def chunked(values: list[int]) -> Iterable[list[int]]:
    for start in range(0, len(values), IN_CLAUSE_CHUNK):
        yield values[start : start + IN_CLAUSE_CHUNK]


def load_by_ids(session: Session, model, ids: set[int]) -> dict[int, object]:
    """Rows of ``model`` by primary key. Missing ids are simply absent, as ``session.get`` returns
    None for them."""
    wanted = sorted(value for value in ids if value)
    loaded: dict[int, object] = {}
    for chunk in chunked(wanted):
        for row in session.exec(select(model).where(col(model.id).in_(chunk))):
            loaded[row.id] = row
    return loaded


def group_by_owner(session: Session, model, owner_field: str, owner_ids: list[int]) -> dict[int, list]:
    """Child rows grouped by their owner id, ordered by primary key within each group.

    The per-row equivalents in ``get_project_context`` and ``aif_readiness_for_period`` select
    without an ORDER BY, which SQLite answers in rowid order. Ordering explicitly by id reproduces
    that same order and removes the dependence on the query plan, so a grouped read can never
    quietly reorder a project's cost lines, evidence or warnings.
    """
    grouped: dict[int, list] = {owner_id: [] for owner_id in owner_ids}
    for chunk in chunked(owner_ids):
        rows = session.exec(
            select(model).where(col(getattr(model, owner_field)).in_(chunk)).order_by(col(model.id))
        )
        for row in rows:
            grouped.setdefault(getattr(row, owner_field), []).append(row)
    return grouped


def first_by_owner(session: Session, model, owner_field: str, owner_ids: list[int]) -> dict[int, object]:
    """The row a per-row ``.first()`` would have returned for each owner: the lowest primary key."""
    return {
        owner_id: rows[0]
        for owner_id, rows in group_by_owner(session, model, owner_field, owner_ids).items()
        if rows
    }


def bulk_project_contexts(session: Session, projects: list[RDProject]) -> dict[int, ProjectContext]:
    """``get_project_context`` for many projects in a fixed number of queries.

    Content and ordering are identical to calling ``get_project_context`` once per project; that
    equivalence is asserted directly in tests/test_scoring_golden_output.py rather than assumed,
    because a batched loader that silently reordered or dropped a related row would change
    published scores with nothing else in the suite failing.

    ``get_project_context`` keeps its signature and its per-project behaviour: 17 call sites across
    three modules depend on it, so this is a new route alongside it, not a replacement.
    """
    if not projects:
        return {}
    project_ids = [project.id or 0 for project in projects]
    solutions = load_by_ids(session, Solution, {project.solution_id for project in projects})
    customers = load_by_ids(session, Customer, {solution.customer_id for solution in solutions.values()})
    contracts = load_by_ids(
        session, Contract, {solution.contract_id for solution in solutions.values() if solution.contract_id}
    )
    periods = load_by_ids(
        session, AccountingPeriod, {project.accounting_period_id for project in projects if project.accounting_period_id}
    )
    companies = load_by_ids(session, Company, {period.company_id for period in periods.values()})
    submissions = first_by_owner(
        session, ClaimPeriodSubmissionStatus, "accounting_period_id", sorted(periods)
    )
    activities = group_by_owner(session, Activity, "project_id", project_ids)
    opinions = group_by_owner(session, CompetentProfessionalOpinion, "project_id", project_ids)
    evidence = group_by_owner(session, EvidenceItem, "project_id", project_ids)
    costs = group_by_owner(session, CostLine, "project_id", project_ids)
    entitlements = first_by_owner(session, EntitlementAssessment, "project_id", project_ids)

    contexts: dict[int, ProjectContext] = {}
    for project in projects:
        project_id = project.id or 0
        solution = solutions.get(project.solution_id)
        period = periods.get(project.accounting_period_id) if project.accounting_period_id else None
        contexts[project_id] = ProjectContext(
            project=project,
            solution=solution,
            customer=customers.get(solution.customer_id) if solution else None,
            contract=contracts.get(solution.contract_id) if solution and solution.contract_id else None,
            period=period,
            company=companies.get(period.company_id) if period else None,
            submission_status=submissions.get(period.id) if period else None,
            activities=activities.get(project_id, []),
            opinions=opinions.get(project_id, []),
            evidence=evidence.get(project_id, []),
            costs=costs.get(project_id, []),
            entitlement=entitlements.get(project_id),
        )
    return contexts


def has_text(value: str | None, minimum: int = 1) -> bool:
    return len((value or "").strip()) >= minimum


def review_flag_message(label: str, matches: list[TermMatch]) -> str:
    """Render a review flag that names each matched term and quotes its excerpt.

    ADR-0003 D3 Stage 3: a reviewer must be able to see which term fired and
    where, so they can overrule it with evidence. A generic label listing every
    configured term is not acceptable for a review flag.
    """
    seen: set[str] = set()
    details: list[str] = []
    for match in matches:
        if match.term in seen:
            continue
        seen.add(match.term)
        details.append(f'"{match.term}" in "{match.excerpt}"')
    if not details:
        return label
    return f"{label} Matched terms: {'; '.join(details)}"


def signed_opinion(opinions: list[CompetentProfessionalOpinion]) -> CompetentProfessionalOpinion | None:
    return next((op for op in opinions if op.signoff_status == "signed"), None)


def cost_validation_warnings(cost: CostLine) -> list[str]:
    rules = get_rules()
    warnings: list[str] = []
    if cost.paid_status != "paid":
        warnings.append(f"Cost line {cost.id or '-'}: {rules.cost_validation_flag('unpaid_cost')}")
    if cost.cost_category in {"externally provided workers", "subcontractors"} and cost.uk_or_overseas != "UK":
        warnings.append(f"Cost line {cost.id or '-'}: {rules.cost_validation_flag('overseas_contractor_or_epw')}")
    if not has_text(cost.evidence_link):
        warnings.append(f"Cost line {cost.id or '-'}: {rules.cost_validation_flag('missing_evidence')}")
    if not is_finite_number(cost.gross_cost) or not is_finite_number(cost.apportionment_percentage):
        warnings.append(
            f"Cost line {cost.id or '-'} holds a gross cost or apportionment percentage that is not a "
            "real number, so it is excluded from qualifying amount totals until it is corrected."
        )
    if is_finite_number(cost.gross_cost) and float(cost.gross_cost or 0) < 0:
        warnings.append(
            f"Cost line {cost.id or '-'} has a negative gross cost, which reduces the claimed total."
        )
    if is_finite_number(cost.apportionment_percentage) and float(cost.apportionment_percentage or 0) < 0:
        warnings.append(
            f"Cost line {cost.id or '-'} has a negative apportionment percentage."
        )
    if cost.apportionment_percentage > 100:
        warnings.append(f"Cost line {cost.id or '-'}: {rules.cost_validation_flag('apportionment_over_100')}")
    if not cost.activity_id and not has_text(cost.activity):
        warnings.append(f"Cost line {cost.id or '-'}: {rules.cost_validation_flag('missing_activity_link')}")
    if cost.cost_input_type == "people_time":
        if not has_text(cost.person_or_supplier_name):
            warnings.append(f"Cost line {cost.id or '-'} is missing the person name.")
        if cost.hours <= 0 and cost.days <= 0:
            warnings.append(f"Cost line {cost.id or '-'} has no people time recorded.")
        if cost.hourly_rate <= 0 and cost.day_rate <= 0 and cost.gross_cost <= 0:
            warnings.append(f"Cost line {cost.id or '-'} has no people rate or gross cost recorded.")
    return warnings


def assess_entitlement(
    customer_type: str,
    customer_corporation_tax_status: str,
    customer_intended_or_contemplated_rd: bool,
    supplier_initiated_rd: bool,
    uncertainty_discovered_during_delivery: bool,
    contract_specified_technical_uncertainty: bool,
    another_party_could_claim: bool,
    grant_funded_or_subsidised: bool,
    company_role: str,
) -> EntitlementResult:
    rules = get_rules()
    if customer_corporation_tax_status in {"", "unknown", None}:
        customer_corporation_tax_status = rules.default_corporation_tax_status(
            customer_type,
            fallback="unknown",
        )

    if another_party_could_claim and contract_specified_technical_uncertainty:
        return EntitlementResult(
            status="blocked",
            rationale="Another party appears to own or control the claim position under the current facts.",
        )

    if customer_corporation_tax_status == "no":
        if customer_intended_or_contemplated_rd or contract_specified_technical_uncertainty or grant_funded_or_subsidised:
            return EntitlementResult(
                status="ambiguous_tax_review",
                rationale="Customer appears irrelievable, but contract/funding facts need tax review before supplier treatment is relied on.",
            )
        return EntitlementResult(
            status="supplier_likely",
            rationale="Customer is not expected to be chargeable to Corporation Tax and supplier performed the R&D, pending review.",
        )

    if uncertainty_discovered_during_delivery and not customer_intended_or_contemplated_rd:
        return EntitlementResult(
            status="supplier_likely",
            rationale="Supplier discovered the technical uncertainty during delivery and customer did not appear to contemplate this R&D.",
        )

    if supplier_initiated_rd and not customer_intended_or_contemplated_rd:
        return EntitlementResult(
            status="supplier_likely",
            rationale="Supplier appears to have initiated the R&D rather than delivering a defined customer R&D requirement.",
        )

    if customer_corporation_tax_status == "yes" and customer_intended_or_contemplated_rd:
        status = "customer_likely" if contract_specified_technical_uncertainty else "ambiguous_tax_review"
        return EntitlementResult(
            status=status,
            rationale="Customer is potentially chargeable to Corporation Tax and appears to have intended or contemplated the R&D.",
        )

    if grant_funded_or_subsidised or customer_corporation_tax_status == "unknown":
        return EntitlementResult(
            status="ambiguous_tax_review",
            rationale="Funding, customer tax status, or contractual role facts are mixed or unknown.",
        )

    return EntitlementResult(
        status="ambiguous_tax_review",
        rationale=f"Entitlement for company role '{company_role or 'unknown'}' needs tax review on the full contract chain.",
    )


def entitlement_facts_for_context(context: ProjectContext) -> tuple[str, EntitlementResult]:
    """Resolve a project's entitlement position from its facts alone.

    Pure: no query, no write, no session. Both entitlement routes go through here --
    ``sync_entitlement_for_project`` persists what it returns, and
    ``calculate_project_score(..., sync=False)`` reads it without persisting -- so the two can never
    reach different answers from the same facts.

    The resolved customer corporation tax status is returned alongside the result because the stored
    assessment records the *resolved* status. Where the customer's status is blank or unknown, the
    rules-engine default is substituted here; that substitution is recorded on the assessment rather
    than applied invisibly at scoring time.
    """
    project = context.project
    customer = context.customer
    contract = context.contract
    customer_tax_status = customer.corporation_tax_status if customer else "unknown"
    if customer and customer_tax_status in {"", "unknown"}:
        customer_tax_status = get_rules().default_corporation_tax_status(customer.customer_type)
    result = assess_entitlement(
        customer_type=customer.customer_type if customer else "",
        customer_corporation_tax_status=customer_tax_status,
        customer_intended_or_contemplated_rd=(
            contract.customer_intended_or_contemplated_rd if contract else False
        ),
        supplier_initiated_rd=project.supplier_initiated_rd,
        uncertainty_discovered_during_delivery=project.uncertainty_discovered_during_delivery,
        contract_specified_technical_uncertainty=(
            contract.technical_uncertainty_described if contract else project.contract_specified_technical_uncertainty
        ),
        another_party_could_claim=project.another_party_could_claim,
        grant_funded_or_subsidised=project.grant_funded_or_subsidised,
        company_role=project.company_role,
    )
    return customer_tax_status, result


def sync_entitlement_for_project(session: Session, project_id: int) -> EntitlementAssessment:
    context = get_project_context(session, project_id)
    project = context.project
    customer = context.customer
    contract = context.contract
    customer_tax_status, result = entitlement_facts_for_context(context)
    action = "update" if context.entitlement else "create"
    assessment = context.entitlement or EntitlementAssessment(project_id=project.id or 0)
    before_snapshot = compact_snapshot(assessment) if context.entitlement else ""
    assessment.customer_type = customer.customer_type if customer else ""
    assessment.customer_corporation_tax_status = customer_tax_status
    assessment.customer_intended_or_contemplated_rd = contract.customer_intended_or_contemplated_rd if contract else False
    assessment.supplier_initiated_rd = project.supplier_initiated_rd
    assessment.uncertainty_discovered_during_delivery = project.uncertainty_discovered_during_delivery
    assessment.contract_specified_technical_uncertainty = (
        contract.technical_uncertainty_described if contract else project.contract_specified_technical_uncertainty
    )
    assessment.another_party_could_claim = project.another_party_could_claim
    assessment.grant_funded_or_subsidised = project.grant_funded_or_subsidised
    assessment.company_role = project.company_role
    assessment.status = result.status
    assessment.rationale = result.rationale
    session.add(assessment)
    session.flush()
    log_event(
        session,
        entity_type="EntitlementAssessment",
        entity_id=assessment.id,
        action=action,
        summary=f"Entitlement assessment {action}d for project {project.project_title}",
        before=before_snapshot,
        after=assessment,
    )
    session.commit()
    return assessment


def entitlement_status_for_scoring(session: Session, context: ProjectContext, *, sync: bool) -> str:
    """The entitlement status a score should use, honouring ADR-0005 D6.

    A stored assessment always wins, so a reviewed project scores off its reviewed position. Where
    none is stored, ``sync=True`` creates and commits one (the user is on a write path) and
    ``sync=False`` resolves the identical position in memory and leaves the database untouched.

    The read-only branch deliberately produces the *same* status as the write branch. A score that
    varied with whether an assessment row happened to exist yet would depend on render history
    rather than on the project's facts, so the dashboard and the project page would publish
    different ratings for one project. D6 permits that divergence; it does not require it, and the
    determinism of a published score outranks it.
    """
    if context.entitlement is not None:
        return context.entitlement.status
    if sync:
        return sync_entitlement_for_project(session, context.project.id or 0).status
    return entitlement_facts_for_context(context)[1].status


def calculate_project_score(session: Session, project_id: int, *, sync: bool = True) -> ScoreResult:
    """Score one project, loading its context. Signature unchanged: 17 call sites depend on it."""
    return score_project_context(session, get_project_context(session, project_id), sync=sync)


def score_project_context(session: Session, context: ProjectContext, *, sync: bool = True) -> ScoreResult:
    """The scoring model itself, over an already-loaded context.

    Split out so a batched caller can score many projects without re-reading each context. This is
    the single implementation of the model -- ``calculate_project_score`` is a thin wrapper over it,
    so a per-project score and a batched score cannot diverge.
    """
    rules = get_rules()
    weights = rules.eligibility_weights()
    # ADR-0002 R8: the reviewer-facing sentences below name the band by the same rule-file
    # ``description`` the badge shows, read once here through the one accessor, so prose and
    # badge cannot drift and an operator editing eligibility_weights.yml gets consistent copy.
    bands = rules.score_bands()
    negative_advance_terms = rules.negative_advance_terms()
    negative_uncertainty_terms = rules.negative_uncertainty_terms()
    advance_stop_phrases = rules.review_flag_stop_phrases("advance")
    uncertainty_stop_phrases = rules.review_flag_stop_phrases("uncertainty")
    project = context.project
    score = 0
    blockers: list[str] = []
    warnings: list[str] = []
    positives: list[str] = []
    next_actions: list[str] = []

    if project.accounting_period_id:
        positives.append("Accounting period linked.")
    else:
        blockers.append(rules.blocker_label("missing_accounting_period"))

    if project.rd_start_date and project.boundary_explanation:
        boundary_points = weights["qualifying_project_boundary"]
        if not project.rd_end_date and project.outcome in {"resolved", "partially resolved", "abandoned"}:
            boundary_points = 6
            warnings.append("R&D end date is missing for a project with a stated outcome.")
        score += boundary_points
        positives.append("Project boundary has start and narrative support.")
    else:
        warnings.append("Project boundary needs stronger start/stop evidence.")

    if has_text(project.field_of_science_or_technology):
        score += weights["field_of_science_or_technology"]
        positives.append("Field of science or technology captured.")
    else:
        blockers.append(rules.blocker_label("missing_field"))

    if not has_text(project.advance_sought):
        blockers.append(rules.blocker_label("missing_advance"))
    else:
        # ADR-0003 D1/D2: negative wording is a review flag, never a blocker. The
        # narrative exists, so this branch awards its points as normal and the
        # existing warning cap keeps the project below green.
        advance_matches = find_matches(project.advance_sought, negative_advance_terms, advance_stop_phrases)
        if advance_matches:
            warnings.append(
                review_flag_message(rules.review_flag_label("non_technical_advance_review"), advance_matches)
            )
        advance_points = weights["advance_sought"] if has_text(project.wider_field_explanation, 80) else 10
        score += advance_points
        positives.append("Advance sought recorded beyond ordinary delivery language.")
        if advance_points < weights["advance_sought"]:
            warnings.append("Explain more clearly why the advance is in the wider field, not only internal knowledge.")

    if not has_text(project.scientific_or_technological_uncertainties):
        blockers.append(rules.blocker_label("missing_uncertainty"))
    else:
        uncertainty_matches = find_matches(
            project.scientific_or_technological_uncertainties,
            negative_uncertainty_terms,
            uncertainty_stop_phrases,
        )
        if uncertainty_matches:
            warnings.append(
                review_flag_message(
                    rules.review_flag_label("non_technical_uncertainty_review"), uncertainty_matches
                )
            )
        uncertainty_points = (
            weights["scientific_or_technological_uncertainty"]
            if has_text(project.competent_professionals_could_not_resolve, 70)
            else 10
        )
        score += uncertainty_points
        positives.append("Scientific or technological uncertainty captured.")
        if uncertainty_points < weights["scientific_or_technological_uncertainty"]:
            warnings.append("Competent professional uncertainty explanation is thin.")

    if has_text(project.experiments_prototypes_tests, 80) and context.activities:
        score += weights["resolution_activity"]
        positives.append("Resolution activity is supported by activities and technical narrative.")
    elif has_text(project.experiments_prototypes_tests):
        score += 8
        warnings.append("Resolution activity exists but needs stronger activity-level evidence.")
    else:
        warnings.append("Experiments, prototypes, tests, or iterations are not yet described.")

    if signed_opinion(context.opinions):
        score += weights["competent_professional_support"]
        positives.append("Signed competent professional opinion present.")
    else:
        blockers.append(rules.blocker_label("missing_competent_professional_signoff"))
        # ADR-0002 R8. The quotation marks are load-bearing: they keep the sentence grammatical
        # whatever the description is edited to, and they signal that the phrase names a status
        # rather than asserting that the project has become one.
        green_description = str(bands["green"]["description"])
        next_actions.append(
            f'Obtain a signed competent professional opinion before treating this project as "{green_description}".'
        )

    if context.evidence:
        strong_evidence = sum(1 for item in context.evidence if item.strength == "strong")
        positives.append(f"{len(context.evidence)} evidence item(s) linked.")
        if strong_evidence == 0:
            warnings.append("Evidence exists but no item is marked strong.")
    else:
        blockers.append(rules.blocker_label("missing_evidence"))
        next_actions.append("Add evidence for advance, uncertainty, resolution activity, cost, and boundaries.")

    if context.costs:
        all_cost_warnings: list[str] = []
        for cost in context.costs:
            all_cost_warnings.extend(cost_validation_warnings(cost))
        if not all_cost_warnings:
            score += weights["cost_traceability"]
            positives.append("Cost lines have evidence and valid apportionment.")
        else:
            score += 5
            warnings.extend(all_cost_warnings)
    else:
        blockers.append(rules.blocker_label("missing_costs"))
        next_actions.append("Add cost lines with activity links, evidence, and apportionment.")

    entitlement_status = entitlement_status_for_scoring(session, context, sync=sync)
    if entitlement_status == "supplier_likely":
        score += weights["claim_entitlement"]
        positives.append("Supplier entitlement indicators are present.")
    elif entitlement_status == "ambiguous_tax_review":
        score += 2
        warnings.append("Claim entitlement is ambiguous and needs tax review.")
    elif entitlement_status == "customer_likely":
        score += 1
        warnings.append("Customer entitlement indicators may be stronger than supplier indicators.")
    elif entitlement_status == "blocked":
        blockers.append(rules.blocker_label("entitlement_blocked"))

    if context.submission_status and context.submission_status.ct600_submitted:
        aif_missing = not context.submission_status.aif_submitted
        aif_after_ct600 = (
            context.submission_status.aif_submission_date
            and context.submission_status.ct600_submission_date
            and context.submission_status.aif_submission_date > context.submission_status.ct600_submission_date
        )
        if aif_missing or aif_after_ct600:
            blockers.append(rules.blocker_label("aif_timing_risk"))
            next_actions.append("Review AIF and CT600 sequencing before relying on claim readiness.")

    score = max(0, min(100, int(score)))
    green_min = int(bands["green"]["min"])
    amber_min = int(bands["amber"]["min"])
    weak_min = int(bands["weak"]["min"])
    if warnings and not blockers and score >= green_min:
        score = green_min - 1
        # ADR-0002 R8, as above: the band is named by its rule-file description, in quotes.
        amber_description = str(bands["amber"]["description"])
        warnings.append(
            f'Warnings keep this project at "{amber_description}" until the review points are resolved.'
        )
    if blockers:
        rating = str(bands["red"]["label"])
        rating_label = str(bands["red"]["description"])
    elif score >= green_min:
        rating = str(bands["green"]["label"])
        rating_label = str(bands["green"]["description"])
    elif score >= amber_min:
        rating = str(bands["amber"]["label"])
        rating_label = str(bands["amber"]["description"])
    elif score >= weak_min:
        rating = str(bands["weak"]["label"])
        rating_label = str(bands["weak"]["description"])
    else:
        rating = str(bands["red"]["label"])
        rating_label = str(bands["red"]["description"])

    if rating != "green":
        next_actions.append("Resolve blockers and warnings before including in an audit-ready claim pack.")
    next_actions.append(CAVEAT)

    return ScoreResult(
        score=score,
        rating=rating,
        rating_label=rating_label,
        blockers=dedupe(blockers),
        warnings=dedupe(warnings),
        positive_indicators=dedupe(positives),
        recommended_next_actions=dedupe(next_actions),
    )


def bulk_project_scores(
    session: Session, projects: list[RDProject], *, sync: bool = True
) -> dict[int, ScoreResult]:
    """Scores for many projects, keyed by project id, in the order given."""
    contexts = bulk_project_contexts(session, projects)
    return {
        project.id or 0: score_project_context(session, contexts[project.id or 0], sync=sync)
        for project in projects
    }


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def claim_notification_deadline(period: AccountingPeriod) -> date:
    # HMRC describes this as 6 months after the period of account end. This MVP
    # uses a date-safe approximation by stepping to the same day six months later
    # and falling back to month end where needed.
    months_after_period_end = get_rules().claim_notification_months_after_period_end()
    month = period.period_of_account_end.month + months_after_period_end
    year = period.period_of_account_end.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    day = period.period_of_account_end.day
    while day > 27:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, day)


def deadline_warning(period: AccountingPeriod, today: date | None = None) -> str | None:
    today = today or date.today()
    deadline = claim_notification_deadline(period)
    if period.claim_notification_submitted or period.prior_claim_within_3_years:
        return None
    if today > deadline:
        return f"Claim notification deadline appears to have passed on {deadline.isoformat()}."
    if deadline - today <= timedelta(days=get_rules().claim_notification_warning_days()):
        return f"Claim notification deadline approaching: {deadline.isoformat()}."
    return None


def aif_project_selection(project_spend: dict[int, float], described_project_ids: set[int] | None = None) -> AIFSelectionResult:
    described_project_ids = described_project_ids or set()
    rules = get_rules().aif_selection_rules()
    one_to_three = rules["one_to_three"]
    four_to_ten = rules["four_to_ten"]
    more_than_ten = rules["more_than_ten"]
    total = round(sum(project_spend.values()), 2)
    count = len(project_spend)
    if count == 0:
        return AIFSelectionResult(
            project_count=0,
            total_qualifying_expenditure=0,
            selected_project_ids=[],
            selected_qualifying_expenditure=0,
            coverage_percentage=0,
            minimum_described_projects=0,
            selection_method="none",
            notes=[],
            ready=False,
            warnings=["No projects linked to this accounting period."],
        )

    sorted_projects = sorted(project_spend.items(), key=lambda item: (-item[1], item[0]))
    notes: list[str] = []
    selection_method = "describe all projects"
    top_10_fallback = False
    if count <= int(one_to_three["max_projects"]):
        selected = sorted_projects
        minimum_described = count
    elif count <= int(four_to_ten["max_projects"]):
        minimum_described = int(four_to_ten["minimum_described"])
        target = total * (float(four_to_ten["minimum_qualifying_expenditure_percentage"]) / 100)
        selected = sorted_projects[:3]
        running = sum(amount for _, amount in selected)
        index = 3
        while running < target and index < len(sorted_projects):
            selected.append(sorted_projects[index])
            running += sorted_projects[index][1]
            index += 1
        selection_method = "minimum 3 projects and 50% qualifying expenditure"
    else:
        minimum_described = int(more_than_ten["minimum_described"])
        max_described = int(more_than_ten["max_described_if_threshold_requires_more"])
        target = total * (float(more_than_ten["minimum_qualifying_expenditure_percentage"]) / 100)
        selected = sorted_projects[:minimum_described]
        running = sum(amount for _, amount in selected)
        index = minimum_described
        while running < target and index < len(sorted_projects):
            if len(selected) >= max_described:
                top_10_fallback = True
                break
            selected.append(sorted_projects[index])
            running += sorted_projects[index][1]
            index += 1
        if top_10_fallback:
            selected = sorted_projects[:max_described]
            selection_method = "top 10 fallback"
            fallback_note = more_than_ten.get("top_10_fallback_note")
            notes.append(
                str(fallback_note)
                if fallback_note
                else "Top 10 fallback applied because reaching 50% qualifying expenditure would require more than 10 project descriptions."
            )
        else:
            selection_method = str(more_than_ten.get("selection_method") or "largest qualifying expenditure first")

    selected_ids = [project_id for project_id, _ in selected]
    selected_spend = round(sum(amount for _, amount in selected), 2)
    coverage = round((selected_spend / total) * 100, 2) if total else 0
    warnings: list[str] = []
    if total <= 0:
        # A period with no qualifying expenditure cannot evidence AIF coverage at
        # any project count. Without this, 1-3 zero-spend projects reported
        # ready=True at 0% coverage with no warning at all.
        warnings.append(
            "No qualifying expenditure is recorded against the projects in this period, "
            "so AIF project selection and coverage cannot be evidenced."
        )
    if count > 3 and len(selected_ids) < minimum_described:
        warnings.append("At least 3 projects must be described.")
    missing_descriptions = [project_id for project_id in selected_ids if project_id not in described_project_ids]
    if missing_descriptions:
        warnings.append(f"Selected project descriptions missing for: {', '.join(map(str, missing_descriptions))}.")
    if count > 3 and coverage < 50 and not top_10_fallback:
        warnings.append("Selected projects do not yet cover at least 50% of qualifying expenditure.")

    return AIFSelectionResult(
        project_count=count,
        total_qualifying_expenditure=total,
        selected_project_ids=selected_ids,
        selected_qualifying_expenditure=selected_spend,
        coverage_percentage=coverage,
        minimum_described_projects=minimum_described,
        selection_method=selection_method,
        notes=notes,
        ready=not warnings,
        warnings=warnings,
    )


def aif_readiness_for_period(session: Session, period_id: int) -> dict:
    """Readiness for one period. Signature unchanged: call sites in three modules depend on it."""
    period = session.get(AccountingPeriod, period_id)
    if not period:
        raise ValueError(f"Accounting period {period_id} not found")
    company = session.get(Company, period.company_id)
    projects = list(session.exec(select(RDProject).where(RDProject.accounting_period_id == period.id)))
    costs_by_project = group_by_owner(
        session, CostLine, "project_id", [project.id or 0 for project in projects]
    )
    submission = session.exec(
        select(ClaimPeriodSubmissionStatus).where(ClaimPeriodSubmissionStatus.accounting_period_id == period.id)
    ).first()
    return build_aif_readiness(period, company, projects, costs_by_project, submission)


def build_aif_readiness(
    period: AccountingPeriod,
    company: Company | None,
    projects: list[RDProject],
    costs_by_project: dict[int, list[CostLine]],
    submission: ClaimPeriodSubmissionStatus | None,
) -> dict:
    """Assemble the readiness result from already-loaded parts.

    Both ``aif_readiness_for_period`` and ``bulk_aif_readiness`` funnel through here, so the single
    and batched routes are the same computation over the same shapes and cannot drift apart.
    """
    spend: dict[int, float] = {}
    for project in projects:
        spend[project.id or 0] = project_qualifying_spend(costs_by_project.get(project.id or 0, []))
    described_ids = {p.id or 0 for p in projects if p.described_in_aif}
    selection = aif_project_selection(spend, described_ids)
    warnings = list(selection.warnings)
    if not company or not company.utr or not company.senior_rd_contact_name:
        warnings.append("Company details or senior R&D contact details are incomplete.")
    if submission and submission.ct600_submitted:
        if not submission.aif_submitted:
            warnings.append("CT600 has been submitted but AIF is not marked as submitted.")
        elif submission.aif_submission_date and submission.ct600_submission_date and submission.ct600_submission_date < submission.aif_submission_date:
            warnings.append("CT600 submission date is before the AIF submission date.")
    return {
        "period": period,
        "company": company,
        "projects": projects,
        "selection": selection,
        "submission": submission,
        "warnings": dedupe(warnings),
        "ready": not warnings,
    }


def bulk_aif_readiness(session: Session, periods: list[AccountingPeriod]) -> dict[int, dict]:
    """``aif_readiness_for_period`` for many periods in a fixed number of queries."""
    if not periods:
        return {}
    period_ids = [period.id or 0 for period in periods]
    companies = load_by_ids(session, Company, {period.company_id for period in periods})
    projects_by_period = group_by_owner(session, RDProject, "accounting_period_id", period_ids)
    all_project_ids = [project.id or 0 for projects in projects_by_period.values() for project in projects]
    costs_by_project = group_by_owner(session, CostLine, "project_id", all_project_ids)
    submissions = first_by_owner(session, ClaimPeriodSubmissionStatus, "accounting_period_id", period_ids)
    return {
        period.id or 0: build_aif_readiness(
            period,
            companies.get(period.company_id),
            projects_by_period.get(period.id or 0, []),
            costs_by_project,
            submissions.get(period.id or 0),
        )
        for period in periods
    }


def dashboard_metrics(session: Session) -> dict:
    solutions = list(session.exec(select(Solution)))
    projects = list(session.exec(select(RDProject)))
    periods = list(session.exec(select(AccountingPeriod)))
    contexts = bulk_project_contexts(session, projects)
    # ADR-0005 D6: the dashboard is a read. It must not bring an EntitlementAssessment into
    # existence, so it scores with sync=False and never commits during a GET.
    scores = [score_project_context(session, contexts[project.id or 0], sync=False) for project in projects]
    rating_counts = defaultdict(int)
    for score in scores:
        rating_counts[score.rating] += 1

    missing_cp = 0
    missing_evidence = 0
    for project in projects:
        context = contexts[project.id or 0]
        if not signed_opinion(context.opinions):
            missing_cp += 1
        if not context.evidence:
            missing_evidence += 1

    readiness_by_period = bulk_aif_readiness(session, periods)
    readiness = [readiness_by_period[period.id or 0] for period in periods]
    warnings = [warning for period in periods if (warning := deadline_warning(period))]
    return {
        "total_solutions": len(solutions),
        "total_projects": len(projects),
        "candidate_projects": sum(1 for score in scores if score.rating in {"green", "amber", "weak"}),
        "rating_counts": rating_counts,
        "missing_competent_professional": missing_cp,
        "missing_evidence": missing_evidence,
        "aif_ready_periods": sum(1 for item in readiness if item["ready"]),
        "aif_total_periods": len(readiness),
        "deadline_warnings": warnings,
        "scores": dict(zip([p.id for p in projects], scores)),
    }


def cost_summary_by_category(costs: list[CostLine]) -> dict[str, float]:
    summary: dict[str, float] = defaultdict(float)
    for cost in costs:
        summary[cost.cost_category] += cost.qualifying_amount
    return {category: round(amount, 2) for category, amount in summary.items()}


def evidence_grouped_by_project_and_tag(session: Session) -> dict[str, dict[str, list[EvidenceItem]]]:
    projects = list(session.exec(select(RDProject).order_by(col(RDProject.project_title))))
    grouped: dict[str, dict[str, list[EvidenceItem]]] = {}
    for project in projects:
        items = list(session.exec(select(EvidenceItem).where(EvidenceItem.project_id == project.id)))
        tag_map: dict[str, list[EvidenceItem]] = defaultdict(list)
        for item in items:
            tag_map[item.relevance_tag].append(item)
        grouped[project.project_title] = dict(tag_map)
    return grouped
