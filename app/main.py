from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, col, select

from app import domain
from app.audit import compact_snapshot, log_event
from app.database import engine, get_session, init_db
from app.form_utils import (
    parse_bool as parse_form_bool,
    parse_enum,
    parse_float,
    parse_optional_date,
    parse_optional_int,
    parse_required_date,
    parse_required_int,
    validation_error_response,
)
from app.framework_intelligence import (
    FRAMEWORK_AGENT_CAVEAT,
    create_intelligence_report,
    framework_intelligence_metrics,
    official_framework_source_allowed,
    run_framework_agent_for_profile,
    seed_framework_sources,
)
from app.models import (
    AccountingPeriod,
    Activity,
    AuditEvent,
    BusinessUnit,
    ClaimPeriodSubmissionStatus,
    Company,
    CompetentProfessionalOpinion,
    Contract,
    CostLine,
    Customer,
    CustomerWatchProfile,
    EntitlementAssessment,
    EvidenceItem,
    ExtractedRequirement,
    FrameworkAgentRun,
    FrameworkOpportunity,
    FrameworkSource,
    IntelligenceReport,
    OpportunityDocument,
    RDProject,
    RDECOpportunitySignal,
    ReviewDecision,
    Solution,
    TechnicalUncertainty,
)
from app.knowledge_agent import knowledge_agent_summary, knowledge_review_actions, run_live_source_checks
from app.reports import generate_claim_period_pack_markdown, generate_evidence_index_markdown, generate_project_memo_markdown
from app.rule_loader import rules_version_summary
from app.rules_engine import get_rules, validate_all_rules
from app.seed import seed_business_units, seed_demo_data
from app.services import (
    CAVEAT,
    aif_readiness_for_period,
    calculate_project_score,
    calculate_people_time_gross,
    calculate_qualifying_amount,
    dashboard_metrics,
    deadline_warning,
    get_project_context,
    sync_entitlement_for_project,
)
from app.settings import BASE_DIR, get_settings


DEPENDENCY_RULES = {
    Company: [(AccountingPeriod, AccountingPeriod.company_id, "accounting periods")],
    AccountingPeriod: [(RDProject, RDProject.accounting_period_id, "R&D projects")],
    BusinessUnit: [
        (BusinessUnit, BusinessUnit.parent_id, "child business units"),
        (Customer, Customer.business_unit_id, "customers"),
        (CustomerWatchProfile, CustomerWatchProfile.business_unit_id, "framework watch profiles"),
        (FrameworkOpportunity, FrameworkOpportunity.business_unit_id, "framework opportunities"),
        (IntelligenceReport, IntelligenceReport.business_unit_id, "framework intelligence reports"),
    ],
    Customer: [
        (Contract, Contract.customer_id, "contracts"),
        (Solution, Solution.customer_id, "solutions"),
        (CustomerWatchProfile, CustomerWatchProfile.customer_id, "framework watch profiles"),
        (FrameworkOpportunity, FrameworkOpportunity.customer_id, "framework opportunities"),
        (IntelligenceReport, IntelligenceReport.customer_id, "framework intelligence reports"),
    ],
    Contract: [(Solution, Solution.contract_id, "solutions")],
    Solution: [(RDProject, RDProject.solution_id, "R&D projects")],
    RDProject: [
        (TechnicalUncertainty, TechnicalUncertainty.project_id, "technical uncertainties"),
        (Activity, Activity.project_id, "activities"),
        (CostLine, CostLine.project_id, "cost lines"),
        (EvidenceItem, EvidenceItem.project_id, "evidence items"),
        (CompetentProfessionalOpinion, CompetentProfessionalOpinion.project_id, "competent professional opinions"),
        (ReviewDecision, ReviewDecision.project_id, "review decisions"),
    ],
    FrameworkSource: [(FrameworkOpportunity, FrameworkOpportunity.source_id, "framework opportunities")],
    CustomerWatchProfile: [(FrameworkAgentRun, FrameworkAgentRun.watch_profile_id, "framework agent runs")],
    FrameworkOpportunity: [
        (OpportunityDocument, OpportunityDocument.opportunity_id, "opportunity documents"),
        (ExtractedRequirement, ExtractedRequirement.opportunity_id, "extracted requirements"),
        (RDECOpportunitySignal, RDECOpportunitySignal.opportunity_id, "RDEC opportunity signals"),
    ],
    ExtractedRequirement: [(RDECOpportunitySignal, RDECOpportunitySignal.requirement_id, "RDEC opportunity signals")],
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    validate_all_rules()
    settings = get_settings()
    if settings.seed_reference_data:
        with Session(engine) as session:
            seed_business_units(session)
            seed_framework_sources(session)
    if settings.seed_demo_data:
        with Session(engine) as session:
            seed_demo_data(session)
    yield


app = FastAPI(title="R&D Claim Evidence Hub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def template_context(request: Request, **extra):
    base = {
        "request": request,
        "app_name": get_settings().app_name,
        "caveat": CAVEAT,
        "rules_versions": rules_version_summary(),
        "domain": domain,
    }
    base.update(extra)
    return base


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def wants_partial(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


FRAMEWORK_SOURCE_TYPES = ["ocds_api", "web_page"]
FRAMEWORK_OPPORTUNITY_STATUSES = ["new", "watching", "bid_review", "archived", "rejected"]
FRAMEWORK_REVIEW_STATUSES = ["pending", "accepted", "challenged", "rejected"]


def apply_corporation_tax_default(customer_type: str, submitted_status: str | None, errors: list[str] | None = None) -> str:
    submitted = str(submitted_status or "").strip()
    if submitted in {"yes", "no"}:
        return submitted
    if submitted and submitted != "unknown" and errors is not None:
        errors.append("Corporation Tax status must be yes, no, or unknown.")
    return get_rules().default_corporation_tax_status(customer_type)


def save_with_audit(session: Session, item, action: str, summary: str, before_snapshot: str = ""):
    session.add(item)
    session.flush()
    log_event(
        session,
        entity_type=item.__class__.__name__,
        entity_id=item.id,
        action=action,
        summary=summary,
        before=before_snapshot,
        after=item,
    )
    session.commit()
    return item


def framework_reference_context(session: Session) -> dict:
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    business_units = list(session.exec(select(BusinessUnit).order_by(col(BusinessUnit.name))))
    sources = list(session.exec(select(FrameworkSource).order_by(col(FrameworkSource.name))))
    profiles = list(session.exec(select(CustomerWatchProfile).order_by(col(CustomerWatchProfile.profile_name))))
    return {
        "customers": customers,
        "business_units": business_units,
        "sources": sources,
        "profiles": profiles,
        "customer_map": {customer.id: customer for customer in customers},
        "business_unit_map": {unit.id: unit for unit in business_units},
        "source_map": {source.id: source for source in sources},
        "profile_map": {profile.id: profile for profile in profiles},
        "framework_source_types": FRAMEWORK_SOURCE_TYPES,
        "framework_opportunity_statuses": FRAMEWORK_OPPORTUNITY_STATUSES,
        "framework_review_statuses": FRAMEWORK_REVIEW_STATUSES,
        "framework_agent_caveat": FRAMEWORK_AGENT_CAVEAT,
    }


def delete_with_audit(session: Session, item, summary: str) -> None:
    before_snapshot = compact_snapshot(item)
    log_event(
        session,
        entity_type=item.__class__.__name__,
        entity_id=item.id,
        action="delete",
        summary=summary,
        before=before_snapshot,
        after="",
    )
    session.delete(item)
    session.commit()


def cost_line_from_form(form, project_id: int, errors: list[str], cost: CostLine | None = None) -> CostLine:
    hours = parse_float(form.get("hours"), "Hours", errors)
    hourly_rate = parse_float(form.get("hourly_rate"), "Hourly rate", errors)
    days = parse_float(form.get("days"), "Days", errors)
    day_rate = parse_float(form.get("day_rate"), "Day rate", errors)
    gross_cost = parse_float(form.get("gross_cost"), "Gross cost", errors)
    cost_input_type = str(form.get("cost_input_type") or "direct_cost")
    if cost_input_type == "people_time" and gross_cost == 0:
        gross_cost = calculate_people_time_gross(hours, hourly_rate, days, day_rate)
    cost = cost or CostLine(project_id=project_id)
    cost.project_id = project_id
    cost.activity = str(form.get("activity") or "")
    cost.cost_input_type = cost_input_type
    cost.cost_category = str(form.get("cost_category") or "other")
    cost.person_or_supplier_name = str(form.get("person_or_supplier_name") or "")
    cost.person_role = str(form.get("person_role") or "")
    cost.time_period_start = parse_optional_date(form.get("time_period_start"), "Time period start", errors)
    cost.time_period_end = parse_optional_date(form.get("time_period_end"), "Time period end", errors)
    cost.hours = hours
    cost.hourly_rate = hourly_rate
    cost.days = days
    cost.day_rate = day_rate
    cost.gross_cost = gross_cost
    cost.apportionment_percentage = parse_float(
        form.get("apportionment_percentage"),
        "Apportionment percentage",
        errors,
    )
    cost.qualifying_amount = calculate_qualifying_amount(cost.gross_cost, cost.apportionment_percentage)
    cost.paid_status = str(form.get("paid_status") or "paid")
    cost.uk_or_overseas = str(form.get("uk_or_overseas") or "unknown")
    cost.connected_party_status = str(form.get("connected_party_status") or "unknown")
    cost.paye_nic_notes = str(form.get("paye_nic_notes") or "")
    cost.evidence_link = str(form.get("evidence_link") or "")
    cost.notes = str(form.get("notes") or "")
    return cost


def delete_or_block(session: Session, model, item_id: int, redirect_path: str) -> RedirectResponse:
    item = session.get(model, item_id)
    if not item:
        return redirect(redirect_path)
    for child_model, child_field, label in DEPENDENCY_RULES.get(model, []):
        child = session.exec(select(child_model).where(child_field == item_id)).first()
        if child:
            return redirect(f"{redirect_path}?error=delete_blocked_{label.replace(' ', '_')}")
    delete_with_audit(session, item, f"Deleted {model.__name__} {item_id}")
    return redirect(redirect_path)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    projects = list(session.exec(select(RDProject).order_by(col(RDProject.project_title))))
    metrics = dashboard_metrics(session)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        template_context(request, metrics=metrics, projects=projects),
    )


@app.get("/knowledge-agent", response_class=HTMLResponse)
def knowledge_agent(request: Request, session: Session = Depends(get_session)):
    summary = knowledge_agent_summary(session)
    actions = knowledge_review_actions(session)
    return templates.TemplateResponse(
        request,
        "knowledge_agent.html",
        template_context(request, summary=summary, actions=actions, live_checks=None),
    )


@app.post("/knowledge-agent/check", response_class=HTMLResponse)
def knowledge_agent_check(request: Request, session: Session = Depends(get_session)):
    live_checks = run_live_source_checks(session)
    summary = knowledge_agent_summary(session)
    actions = knowledge_review_actions(session)
    return templates.TemplateResponse(
        request,
        "knowledge_agent.html",
        template_context(request, summary=summary, actions=actions, live_checks=live_checks),
    )


@app.get("/framework-intelligence", response_class=HTMLResponse)
def framework_intelligence(request: Request, session: Session = Depends(get_session)):
    opportunities = list(
        session.exec(select(FrameworkOpportunity).order_by(col(FrameworkOpportunity.updated_at).desc()).limit(8))
    )
    requirements = list(
        session.exec(select(ExtractedRequirement).order_by(col(ExtractedRequirement.created_at).desc()).limit(8))
    )
    reports = list(session.exec(select(IntelligenceReport).order_by(col(IntelligenceReport.generated_at).desc()).limit(5)))
    context = framework_reference_context(session)
    return templates.TemplateResponse(
        request,
        "framework_intelligence.html",
        template_context(
            request,
            metrics=framework_intelligence_metrics(session),
            opportunities=opportunities,
            requirements=requirements,
            reports=reports,
            **context,
        ),
    )


@app.get("/framework-intelligence/sources", response_class=HTMLResponse)
def framework_sources(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "framework_sources.html",
        template_context(request, **framework_reference_context(session)),
    )


@app.post("/framework-intelligence/sources")
async def create_framework_source(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    source_type = parse_enum(
        form.get("source_type"),
        FRAMEWORK_SOURCE_TYPES,
        "Source type",
        errors,
        "ocds_api",
    )
    query_url = str(form.get("query_url") or "").strip()
    if query_url and not official_framework_source_allowed(query_url):
        errors.append("Query URL must be HTTPS and on the approved official/public procurement source allow-list.")
    if not str(form.get("name") or "").strip():
        errors.append("Source name is required.")
    if not query_url:
        errors.append("Query URL is required.")
    if errors:
        return validation_error_response(errors, "/framework-intelligence/sources")
    source = FrameworkSource(
        name=str(form.get("name") or ""),
        source_type=source_type,
        base_url=str(form.get("base_url") or query_url),
        query_url=query_url,
        official=parse_form_bool(form.get("official")) if form.get("official") is not None else True,
        active=parse_form_bool(form.get("active")) if form.get("active") is not None else True,
        review_frequency=str(form.get("review_frequency") or "manual"),
        notes=str(form.get("notes") or ""),
    )
    save_with_audit(session, source, "create", f"Created framework source {source.name}")
    return redirect("/framework-intelligence/sources")


@app.post("/framework-intelligence/sources/{source_id}/update")
async def update_framework_source(source_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    source = session.get(FrameworkSource, source_id)
    if not source:
        return redirect("/framework-intelligence/sources")
    errors: list[str] = []
    source_type = parse_enum(
        form.get("source_type"),
        FRAMEWORK_SOURCE_TYPES,
        "Source type",
        errors,
        "ocds_api",
    )
    query_url = str(form.get("query_url") or "").strip()
    if query_url and not official_framework_source_allowed(query_url):
        errors.append("Query URL must be HTTPS and on the approved official/public procurement source allow-list.")
    if not str(form.get("name") or "").strip():
        errors.append("Source name is required.")
    if not query_url:
        errors.append("Query URL is required.")
    if errors:
        return validation_error_response(errors, "/framework-intelligence/sources")
    before_snapshot = compact_snapshot(source)
    source.name = str(form.get("name") or "")
    source.source_type = source_type
    source.base_url = str(form.get("base_url") or query_url)
    source.query_url = query_url
    source.official = parse_form_bool(form.get("official"))
    source.active = parse_form_bool(form.get("active"))
    source.review_frequency = str(form.get("review_frequency") or "manual")
    source.notes = str(form.get("notes") or "")
    save_with_audit(session, source, "update", f"Updated framework source {source.name}", before_snapshot)
    return redirect("/framework-intelligence/sources")


@app.post("/framework-intelligence/sources/{source_id}/delete")
def delete_framework_source(source_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, FrameworkSource, source_id, "/framework-intelligence/sources")


@app.get("/framework-intelligence/watch-profiles", response_class=HTMLResponse)
def framework_watch_profiles(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "framework_watch_profiles.html",
        template_context(request, **framework_reference_context(session)),
    )


@app.post("/framework-intelligence/watch-profiles")
async def create_watch_profile(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    customer_id = parse_optional_int(form.get("customer_id"), "Customer", errors)
    business_unit_id = parse_optional_int(form.get("business_unit_id"), "Business unit", errors)
    minimum_value = parse_float(form.get("minimum_value"), "Minimum value", errors)
    profile_name = str(form.get("profile_name") or "").strip()
    if not profile_name:
        errors.append("Watch profile name is required.")
    if errors:
        return validation_error_response(errors, "/framework-intelligence/watch-profiles")
    profile = CustomerWatchProfile(
        profile_name=profile_name,
        customer_id=customer_id,
        business_unit_id=business_unit_id,
        buyer_aliases=str(form.get("buyer_aliases") or ""),
        keywords=str(form.get("keywords") or ""),
        cpv_codes=str(form.get("cpv_codes") or ""),
        domains=str(form.get("domains") or ""),
        minimum_value=minimum_value,
        include_awards=parse_form_bool(form.get("include_awards")) if form.get("include_awards") is not None else True,
        include_future_pipeline=(
            parse_form_bool(form.get("include_future_pipeline"))
            if form.get("include_future_pipeline") is not None
            else True
        ),
        active=parse_form_bool(form.get("active")) if form.get("active") is not None else True,
        review_notes=str(form.get("review_notes") or ""),
    )
    save_with_audit(session, profile, "create", f"Created framework watch profile {profile.profile_name}")
    return redirect("/framework-intelligence/watch-profiles")


@app.post("/framework-intelligence/watch-profiles/{profile_id}/update")
async def update_watch_profile(profile_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    profile = session.get(CustomerWatchProfile, profile_id)
    if not profile:
        return redirect("/framework-intelligence/watch-profiles")
    errors: list[str] = []
    customer_id = parse_optional_int(form.get("customer_id"), "Customer", errors)
    business_unit_id = parse_optional_int(form.get("business_unit_id"), "Business unit", errors)
    minimum_value = parse_float(form.get("minimum_value"), "Minimum value", errors)
    profile_name = str(form.get("profile_name") or "").strip()
    if not profile_name:
        errors.append("Watch profile name is required.")
    if errors:
        return validation_error_response(errors, "/framework-intelligence/watch-profiles")
    before_snapshot = compact_snapshot(profile)
    profile.profile_name = profile_name
    profile.customer_id = customer_id
    profile.business_unit_id = business_unit_id
    profile.buyer_aliases = str(form.get("buyer_aliases") or "")
    profile.keywords = str(form.get("keywords") or "")
    profile.cpv_codes = str(form.get("cpv_codes") or "")
    profile.domains = str(form.get("domains") or "")
    profile.minimum_value = minimum_value
    profile.include_awards = parse_form_bool(form.get("include_awards"))
    profile.include_future_pipeline = parse_form_bool(form.get("include_future_pipeline"))
    profile.active = parse_form_bool(form.get("active"))
    profile.review_notes = str(form.get("review_notes") or "")
    save_with_audit(session, profile, "update", f"Updated framework watch profile {profile.profile_name}", before_snapshot)
    return redirect("/framework-intelligence/watch-profiles")


@app.post("/framework-intelligence/watch-profiles/{profile_id}/delete")
def delete_watch_profile(profile_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, CustomerWatchProfile, profile_id, "/framework-intelligence/watch-profiles")


@app.post("/framework-intelligence/watch-profiles/{profile_id}/run")
def run_watch_profile(profile_id: int, session: Session = Depends(get_session)):
    try:
        run_framework_agent_for_profile(session, profile_id)
    except ValueError as exc:
        return validation_error_response([str(exc)], "/framework-intelligence/watch-profiles")
    return redirect("/framework-intelligence/agent-runs")


@app.get("/framework-intelligence/opportunities", response_class=HTMLResponse)
def framework_opportunities(request: Request, session: Session = Depends(get_session)):
    opportunities = list(
        session.exec(select(FrameworkOpportunity).order_by(col(FrameworkOpportunity.updated_at).desc()).limit(200))
    )
    signals = list(session.exec(select(RDECOpportunitySignal)))
    signal_counts: dict[int, int] = {}
    for signal in signals:
        signal_counts[signal.opportunity_id] = signal_counts.get(signal.opportunity_id, 0) + 1
    return templates.TemplateResponse(
        request,
        "framework_opportunities.html",
        template_context(
            request,
            opportunities=opportunities,
            signal_counts=signal_counts,
            **framework_reference_context(session),
        ),
    )


@app.post("/framework-intelligence/opportunities/{opportunity_id}/update")
async def update_framework_opportunity(opportunity_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    opportunity = session.get(FrameworkOpportunity, opportunity_id)
    if not opportunity:
        return redirect("/framework-intelligence/opportunities")
    errors: list[str] = []
    status = parse_enum(
        form.get("status"),
        FRAMEWORK_OPPORTUNITY_STATUSES,
        "Opportunity status",
        errors,
        "new",
    )
    if errors:
        return validation_error_response(errors, "/framework-intelligence/opportunities")
    before_snapshot = compact_snapshot(opportunity)
    opportunity.status = status
    opportunity.relevance_rationale = str(form.get("relevance_rationale") or opportunity.relevance_rationale)
    save_with_audit(
        session,
        opportunity,
        "update",
        f"Updated framework opportunity {opportunity.title}",
        before_snapshot,
    )
    return redirect("/framework-intelligence/opportunities")


@app.post("/framework-intelligence/opportunities/{opportunity_id}/delete")
def delete_framework_opportunity(opportunity_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, FrameworkOpportunity, opportunity_id, "/framework-intelligence/opportunities")


@app.get("/framework-intelligence/requirements", response_class=HTMLResponse)
def framework_requirements(request: Request, session: Session = Depends(get_session)):
    requirements = list(
        session.exec(select(ExtractedRequirement).order_by(col(ExtractedRequirement.created_at).desc()).limit(200))
    )
    signals = list(session.exec(select(RDECOpportunitySignal)))
    return templates.TemplateResponse(
        request,
        "framework_requirements.html",
        template_context(
            request,
            requirements=requirements,
            signals=signals,
            **framework_reference_context(session),
        ),
    )


@app.post("/framework-intelligence/requirements/{requirement_id}/review")
async def review_framework_requirement(requirement_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    requirement = session.get(ExtractedRequirement, requirement_id)
    if not requirement:
        return redirect("/framework-intelligence/requirements")
    errors: list[str] = []
    status = parse_enum(
        form.get("human_review_status"),
        FRAMEWORK_REVIEW_STATUSES,
        "Human review status",
        errors,
        "pending",
    )
    if errors:
        return validation_error_response(errors, "/framework-intelligence/requirements")
    before_snapshot = compact_snapshot(requirement)
    requirement.human_review_status = status
    requirement.rdec_relevance_note = str(form.get("rdec_relevance_note") or requirement.rdec_relevance_note)
    save_with_audit(
        session,
        requirement,
        "update",
        f"Updated extracted requirement {requirement.id}",
        before_snapshot,
    )
    return redirect("/framework-intelligence/requirements")


@app.post("/framework-intelligence/signals/{signal_id}/review")
async def review_framework_signal(signal_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    signal = session.get(RDECOpportunitySignal, signal_id)
    if not signal:
        return redirect("/framework-intelligence/requirements")
    errors: list[str] = []
    status = parse_enum(
        form.get("human_review_status"),
        FRAMEWORK_REVIEW_STATUSES,
        "Human review status",
        errors,
        "pending",
    )
    if errors:
        return validation_error_response(errors, "/framework-intelligence/requirements")
    before_snapshot = compact_snapshot(signal)
    signal.human_review_status = status
    signal.recommended_action = str(form.get("recommended_action") or signal.recommended_action)
    save_with_audit(session, signal, "update", f"Updated RDEC opportunity signal {signal.id}", before_snapshot)
    return redirect("/framework-intelligence/requirements")


@app.get("/framework-intelligence/agent-runs", response_class=HTMLResponse)
def framework_agent_runs(request: Request, session: Session = Depends(get_session)):
    runs = list(session.exec(select(FrameworkAgentRun).order_by(col(FrameworkAgentRun.started_at).desc()).limit(100)))
    return templates.TemplateResponse(
        request,
        "framework_agent_runs.html",
        template_context(request, runs=runs, **framework_reference_context(session)),
    )


@app.get("/framework-intelligence/reports", response_class=HTMLResponse)
def framework_reports(request: Request, session: Session = Depends(get_session)):
    reports = list(session.exec(select(IntelligenceReport).order_by(col(IntelligenceReport.generated_at).desc())))
    return templates.TemplateResponse(
        request,
        "framework_reports.html",
        template_context(request, reports=reports, **framework_reference_context(session)),
    )


@app.post("/framework-intelligence/reports")
async def create_framework_report(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    customer_id = parse_optional_int(form.get("customer_id"), "Customer", errors)
    business_unit_id = parse_optional_int(form.get("business_unit_id"), "Business unit", errors)
    report_name = str(form.get("report_name") or "Framework intelligence summary").strip()
    if not report_name:
        errors.append("Report name is required.")
    if errors:
        return validation_error_response(errors, "/framework-intelligence/reports")
    report = create_intelligence_report(
        session,
        report_name=report_name,
        report_type=str(form.get("report_type") or "framework_summary"),
        customer_id=customer_id,
        business_unit_id=business_unit_id,
    )
    return redirect(f"/framework-intelligence/reports/{report.id}")


@app.get("/framework-intelligence/reports/{report_id}", response_class=HTMLResponse)
def framework_report_detail(
    report_id: int,
    request: Request,
    format: str | None = None,
    session: Session = Depends(get_session),
):
    report = session.get(IntelligenceReport, report_id)
    if not report:
        return redirect("/framework-intelligence/reports")
    if format == "md":
        filename = f"framework-intelligence-report-{report_id}.md"
        return Response(
            report.markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return templates.TemplateResponse(
        request,
        "framework_report_detail.html",
        template_context(request, report=report, **framework_reference_context(session)),
    )


@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request, session: Session = Depends(get_session)):
    companies = list(session.exec(select(Company).order_by(col(Company.company_name))))
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    return templates.TemplateResponse(
        request,
        "companies.html",
        template_context(request, companies=companies, periods=periods),
    )


@app.post("/companies")
async def create_company(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    company = Company(
        company_name=str(form.get("company_name") or ""),
        utr=str(form.get("utr") or ""),
        paye_reference=str(form.get("paye_reference") or ""),
        vat_number=str(form.get("vat_number") or ""),
        sic_code=str(form.get("sic_code") or ""),
        registered_office_country=str(form.get("registered_office_country") or "United Kingdom"),
        registered_office_region=str(form.get("registered_office_region") or ""),
        northern_ireland_registered=parse_form_bool(form.get("northern_ireland_registered")),
        senior_rd_contact_name=str(form.get("senior_rd_contact_name") or ""),
        senior_rd_contact_role=str(form.get("senior_rd_contact_role") or ""),
        senior_rd_contact_email=str(form.get("senior_rd_contact_email") or ""),
        senior_rd_contact_phone=str(form.get("senior_rd_contact_phone") or ""),
        agent_name=str(form.get("agent_name") or ""),
        agent_reference=str(form.get("agent_reference") or ""),
        agent_email=str(form.get("agent_email") or ""),
        agent_phone=str(form.get("agent_phone") or ""),
        agent_role=str(form.get("agent_role") or ""),
    )
    save_with_audit(session, company, "create", f"Created company {company.company_name}")
    return redirect("/companies")


@app.post("/companies/{company_id}/update")
async def update_company(company_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    company = session.get(Company, company_id)
    if not company:
        return redirect("/companies")
    before_snapshot = compact_snapshot(company)
    company.company_name = str(form.get("company_name") or "")
    company.utr = str(form.get("utr") or "")
    company.paye_reference = str(form.get("paye_reference") or "")
    company.vat_number = str(form.get("vat_number") or "")
    company.sic_code = str(form.get("sic_code") or "")
    company.registered_office_country = str(form.get("registered_office_country") or "United Kingdom")
    company.registered_office_region = str(form.get("registered_office_region") or "")
    company.northern_ireland_registered = parse_form_bool(form.get("northern_ireland_registered"))
    company.senior_rd_contact_name = str(form.get("senior_rd_contact_name") or "")
    company.senior_rd_contact_role = str(form.get("senior_rd_contact_role") or "")
    company.senior_rd_contact_email = str(form.get("senior_rd_contact_email") or "")
    company.senior_rd_contact_phone = str(form.get("senior_rd_contact_phone") or "")
    company.agent_name = str(form.get("agent_name") or "")
    company.agent_reference = str(form.get("agent_reference") or "")
    company.agent_email = str(form.get("agent_email") or "")
    company.agent_phone = str(form.get("agent_phone") or "")
    company.agent_role = str(form.get("agent_role") or "")
    save_with_audit(session, company, "update", f"Updated company {company.company_name}", before_snapshot)
    return redirect("/companies")


@app.post("/companies/{company_id}/delete")
def delete_company(company_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, Company, company_id, "/companies")


@app.post("/accounting-periods")
async def create_accounting_period(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    company_id = parse_required_int(form.get("company_id"), "Company", errors)
    start_date = parse_required_date(form.get("start_date"), "Accounting period start", errors)
    end_date = parse_required_date(form.get("end_date"), "Accounting period end", errors)
    period_of_account_start = parse_required_date(
        form.get("period_of_account_start"),
        "Period of account start",
        errors,
    )
    period_of_account_end = parse_required_date(form.get("period_of_account_end"), "Period of account end", errors)
    claim_notification_date = parse_optional_date(
        form.get("claim_notification_date"),
        "Claim notification date",
        errors,
    )
    if errors:
        return validation_error_response(errors, "/companies")
    period = AccountingPeriod(
        company_id=company_id,
        label=str(form.get("label") or ""),
        start_date=start_date,
        end_date=end_date,
        period_of_account_start=period_of_account_start,
        period_of_account_end=period_of_account_end,
        claim_notification_submitted=parse_form_bool(form.get("claim_notification_submitted")),
        claim_notification_date=claim_notification_date,
        prior_claim_within_3_years=parse_form_bool(form.get("prior_claim_within_3_years")),
        scheme_notes=str(form.get("scheme_notes") or ""),
    )
    save_with_audit(session, period, "create", f"Created accounting period {period.label}")
    submission = ClaimPeriodSubmissionStatus(accounting_period_id=period.id or 0)
    save_with_audit(
        session,
        submission,
        "create",
        f"Created submission status for accounting period {period.label}",
    )
    return redirect("/companies")


@app.post("/accounting-periods/{period_id}/update")
async def update_accounting_period(period_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    period = session.get(AccountingPeriod, period_id)
    if not period:
        return redirect("/companies")
    errors: list[str] = []
    company_id = parse_required_int(form.get("company_id"), "Company", errors)
    start_date = parse_required_date(form.get("start_date"), "Accounting period start", errors)
    end_date = parse_required_date(form.get("end_date"), "Accounting period end", errors)
    period_of_account_start = parse_required_date(
        form.get("period_of_account_start"),
        "Period of account start",
        errors,
    )
    period_of_account_end = parse_required_date(form.get("period_of_account_end"), "Period of account end", errors)
    claim_notification_date = parse_optional_date(
        form.get("claim_notification_date"),
        "Claim notification date",
        errors,
    )
    if errors:
        return validation_error_response(errors, "/companies")
    before_snapshot = compact_snapshot(period)
    period.company_id = company_id
    period.label = str(form.get("label") or "")
    period.start_date = start_date
    period.end_date = end_date
    period.period_of_account_start = period_of_account_start
    period.period_of_account_end = period_of_account_end
    period.claim_notification_submitted = parse_form_bool(form.get("claim_notification_submitted"))
    period.claim_notification_date = claim_notification_date
    period.prior_claim_within_3_years = parse_form_bool(form.get("prior_claim_within_3_years"))
    period.scheme_notes = str(form.get("scheme_notes") or "")
    save_with_audit(session, period, "update", f"Updated accounting period {period.label}", before_snapshot)
    return redirect("/companies")


@app.post("/accounting-periods/{period_id}/delete")
def delete_accounting_period(period_id: int, session: Session = Depends(get_session)):
    linked_project = session.exec(select(RDProject).where(RDProject.accounting_period_id == period_id)).first()
    if linked_project:
        return redirect("/companies?error=delete_blocked_R&D_projects")
    submission = session.exec(
        select(ClaimPeriodSubmissionStatus).where(ClaimPeriodSubmissionStatus.accounting_period_id == period_id)
    ).first()
    if submission:
        delete_with_audit(session, submission, f"Deleted submission status for accounting period {period_id}")
    return delete_or_block(session, AccountingPeriod, period_id, "/companies")


@app.get("/customers", response_class=HTMLResponse)
def customers(request: Request, session: Session = Depends(get_session)):
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    business_units = list(session.exec(select(BusinessUnit).order_by(col(BusinessUnit.name))))
    business_unit_map = {unit.id: unit for unit in business_units}
    return templates.TemplateResponse(
        request,
        "customers.html",
        template_context(
            request,
            customers=customers,
            business_units=business_units,
            business_unit_map=business_unit_map,
        ),
    )


@app.post("/customers")
async def create_customer(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    business_unit_id = parse_optional_int(form.get("business_unit_id"), "Business unit", errors)
    customer_type = parse_enum(
        form.get("customer_type"),
        domain.CUSTOMER_TYPES,
        "Customer type",
        errors,
        "other",
    )
    corporation_tax_status = apply_corporation_tax_default(
        customer_type,
        str(form.get("corporation_tax_status") or ""),
        errors,
    )
    if errors:
        return validation_error_response(errors, "/customers")
    customer = Customer(
        business_unit_id=business_unit_id,
        customer_name=str(form.get("customer_name") or ""),
        sector=str(form.get("sector") or ""),
        transport_domain=parse_enum(
            form.get("transport_domain"),
            domain.TRANSPORT_DOMAINS,
            "Transport domain",
            errors,
            "other",
        ),
        customer_type=customer_type,
        corporation_tax_status=corporation_tax_status,
        notes=str(form.get("notes") or ""),
    )
    if errors:
        return validation_error_response(errors, "/customers")
    save_with_audit(session, customer, "create", f"Created customer {customer.customer_name}")
    return redirect("/customers")


@app.post("/customers/{customer_id}/update")
async def update_customer(customer_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    customer = session.get(Customer, customer_id)
    if not customer:
        return redirect("/customers")
    errors: list[str] = []
    business_unit_id = parse_optional_int(form.get("business_unit_id"), "Business unit", errors)
    transport_domain = parse_enum(
        form.get("transport_domain"),
        domain.TRANSPORT_DOMAINS,
        "Transport domain",
        errors,
        "other",
    )
    customer_type = parse_enum(
        form.get("customer_type"),
        domain.CUSTOMER_TYPES,
        "Customer type",
        errors,
        "other",
    )
    corporation_tax_status = apply_corporation_tax_default(
        customer_type,
        str(form.get("corporation_tax_status") or ""),
        errors,
    )
    if errors:
        return validation_error_response(errors, "/customers")
    before_snapshot = compact_snapshot(customer)
    customer.business_unit_id = business_unit_id
    customer.customer_name = str(form.get("customer_name") or "")
    customer.sector = str(form.get("sector") or "")
    customer.transport_domain = transport_domain
    customer.customer_type = customer_type
    customer.corporation_tax_status = corporation_tax_status
    customer.notes = str(form.get("notes") or "")
    save_with_audit(session, customer, "update", f"Updated customer {customer.customer_name}", before_snapshot)
    return redirect("/customers")


@app.post("/customers/{customer_id}/delete")
def delete_customer(customer_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, Customer, customer_id, "/customers")


@app.get("/business-units", response_class=HTMLResponse)
def business_units(request: Request, session: Session = Depends(get_session)):
    units = list(session.exec(select(BusinessUnit).order_by(col(BusinessUnit.name))))
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    children_by_parent: dict[int | None, list[BusinessUnit]] = {}
    for unit in units:
        children_by_parent.setdefault(unit.parent_id, []).append(unit)
    customer_counts = {unit.id: 0 for unit in units}
    for customer in customers:
        if customer.business_unit_id in customer_counts:
            customer_counts[customer.business_unit_id] += 1
    return templates.TemplateResponse(
        request,
        "business_units.html",
        template_context(
            request,
            units=units,
            children_by_parent=children_by_parent,
            customer_counts=customer_counts,
        ),
    )


@app.post("/business-units")
async def create_business_unit(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    parent_id = parse_optional_int(form.get("parent_id"), "Parent business unit", errors)
    if errors:
        return validation_error_response(errors, "/business-units")
    unit = BusinessUnit(
        name=str(form.get("name") or ""),
        parent_id=parent_id,
        description=str(form.get("description") or ""),
        active=parse_form_bool(form.get("active")) if form.get("active") is not None else True,
    )
    session.add(unit)
    session.commit()
    return redirect("/business-units")


@app.post("/business-units/{unit_id}/update")
async def update_business_unit(unit_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    unit = session.get(BusinessUnit, unit_id)
    if not unit:
        return redirect("/business-units")
    errors: list[str] = []
    parent_id = parse_optional_int(form.get("parent_id"), "Parent business unit", errors)
    if errors:
        return validation_error_response(errors, "/business-units")
    unit.name = str(form.get("name") or "")
    unit.parent_id = parent_id
    unit.description = str(form.get("description") or "")
    unit.active = parse_form_bool(form.get("active"))
    session.add(unit)
    session.commit()
    return redirect("/business-units")


@app.post("/business-units/{unit_id}/delete")
def delete_business_unit(unit_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, BusinessUnit, unit_id, "/business-units")


@app.get("/contracts", response_class=HTMLResponse)
def contracts(request: Request, session: Session = Depends(get_session)):
    contracts = list(session.exec(select(Contract).order_by(col(Contract.contract_name))))
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    customer_map = {customer.id: customer for customer in customers}
    return templates.TemplateResponse(
        request,
        "contracts.html",
        template_context(request, contracts=contracts, customers=customers, customer_map=customer_map),
    )


@app.post("/contracts")
async def create_contract(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    customer_id = parse_required_int(form.get("customer_id"), "Customer", errors)
    contract_type = parse_enum(form.get("contract_type"), domain.CONTRACT_TYPES, "Contract type", errors, "other")
    start_date = parse_optional_date(form.get("start_date"), "Start date", errors)
    end_date = parse_optional_date(form.get("end_date"), "End date", errors)
    if errors:
        return validation_error_response(errors, "/contracts")
    contract = Contract(
        contract_name=str(form.get("contract_name") or ""),
        customer_id=customer_id,
        contract_type=contract_type,
        start_date=start_date,
        end_date=end_date,
        customer_requested_rd=parse_form_bool(form.get("customer_requested_rd")),
        customer_intended_or_contemplated_rd=parse_form_bool(form.get("customer_intended_or_contemplated_rd")),
        technical_uncertainty_described=parse_form_bool(form.get("technical_uncertainty_described")),
        ip_owner=str(form.get("ip_owner") or ""),
        funding_grant_notes=str(form.get("funding_grant_notes") or ""),
        public_sector_procurement_notes=str(form.get("public_sector_procurement_notes") or ""),
        contract_evidence_links=str(form.get("contract_evidence_links") or ""),
    )
    save_with_audit(session, contract, "create", f"Created contract {contract.contract_name}")
    return redirect("/contracts")


@app.post("/contracts/{contract_id}/update")
async def update_contract(contract_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    contract = session.get(Contract, contract_id)
    if not contract:
        return redirect("/contracts")
    errors: list[str] = []
    customer_id = parse_required_int(form.get("customer_id"), "Customer", errors)
    contract_type = parse_enum(form.get("contract_type"), domain.CONTRACT_TYPES, "Contract type", errors, "other")
    start_date = parse_optional_date(form.get("start_date"), "Start date", errors)
    end_date = parse_optional_date(form.get("end_date"), "End date", errors)
    if errors:
        return validation_error_response(errors, "/contracts")
    before_snapshot = compact_snapshot(contract)
    contract.contract_name = str(form.get("contract_name") or "")
    contract.customer_id = customer_id
    contract.contract_type = contract_type
    contract.start_date = start_date
    contract.end_date = end_date
    contract.customer_requested_rd = parse_form_bool(form.get("customer_requested_rd"))
    contract.customer_intended_or_contemplated_rd = parse_form_bool(form.get("customer_intended_or_contemplated_rd"))
    contract.technical_uncertainty_described = parse_form_bool(form.get("technical_uncertainty_described"))
    contract.ip_owner = str(form.get("ip_owner") or "")
    contract.funding_grant_notes = str(form.get("funding_grant_notes") or "")
    contract.public_sector_procurement_notes = str(form.get("public_sector_procurement_notes") or "")
    contract.contract_evidence_links = str(form.get("contract_evidence_links") or "")
    save_with_audit(session, contract, "update", f"Updated contract {contract.contract_name}", before_snapshot)
    return redirect("/contracts")


@app.post("/contracts/{contract_id}/delete")
def delete_contract(contract_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, Contract, contract_id, "/contracts")


@app.get("/solutions", response_class=HTMLResponse)
def solutions(request: Request, session: Session = Depends(get_session)):
    solutions = list(session.exec(select(Solution).order_by(col(Solution.solution_name))))
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    contracts = list(session.exec(select(Contract).order_by(col(Contract.contract_name))))
    customer_map = {customer.id: customer for customer in customers}
    contract_map = {contract.id: contract for contract in contracts}
    return templates.TemplateResponse(
        request,
        "solutions.html",
        template_context(
            request,
            solutions=solutions,
            customers=customers,
            contracts=contracts,
            customer_map=customer_map,
            contract_map=contract_map,
        ),
    )


@app.post("/solutions")
async def create_solution(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    customer_id = parse_required_int(form.get("customer_id"), "Customer", errors)
    contract_id = parse_optional_int(form.get("contract_id"), "Contract", errors)
    radar_status = parse_enum(form.get("initial_radar_status"), domain.RADAR_STATUSES, "Radar status", errors, "amber")
    if errors:
        return validation_error_response(errors, "/solutions")
    constraints = ", ".join(form.getlist("transport_environment_constraints"))
    solution = Solution(
        solution_name=str(form.get("solution_name") or ""),
        customer_id=customer_id,
        contract_id=contract_id,
        solution_description=str(form.get("solution_description") or ""),
        business_problem=str(form.get("business_problem") or ""),
        technical_architecture_summary=str(form.get("technical_architecture_summary") or ""),
        transport_environment_constraints=constraints,
        initial_radar_status=radar_status,
        radar_reason=str(form.get("radar_reason") or ""),
    )
    save_with_audit(session, solution, "create", f"Created solution {solution.solution_name}")
    return redirect("/solutions")


@app.post("/solutions/{solution_id}/update")
async def update_solution(solution_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    solution = session.get(Solution, solution_id)
    if not solution:
        return redirect("/solutions")
    errors: list[str] = []
    customer_id = parse_required_int(form.get("customer_id"), "Customer", errors)
    contract_id = parse_optional_int(form.get("contract_id"), "Contract", errors)
    radar_status = parse_enum(form.get("initial_radar_status"), domain.RADAR_STATUSES, "Radar status", errors, "amber")
    if errors:
        return validation_error_response(errors, "/solutions")
    before_snapshot = compact_snapshot(solution)
    solution.solution_name = str(form.get("solution_name") or "")
    solution.customer_id = customer_id
    solution.contract_id = contract_id
    solution.solution_description = str(form.get("solution_description") or "")
    solution.business_problem = str(form.get("business_problem") or "")
    solution.technical_architecture_summary = str(form.get("technical_architecture_summary") or "")
    selected_constraints = form.getlist("transport_environment_constraints")
    solution.transport_environment_constraints = ", ".join(selected_constraints) if selected_constraints else str(form.get("transport_environment_constraints_text") or "")
    solution.initial_radar_status = radar_status
    solution.radar_reason = str(form.get("radar_reason") or "")
    save_with_audit(session, solution, "update", f"Updated solution {solution.solution_name}", before_snapshot)
    return redirect("/solutions")


@app.post("/solutions/{solution_id}/delete")
def delete_solution(solution_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, Solution, solution_id, "/solutions")


@app.get("/projects", response_class=HTMLResponse)
def projects(request: Request, session: Session = Depends(get_session)):
    projects = list(session.exec(select(RDProject).order_by(col(RDProject.project_title))))
    solutions = list(session.exec(select(Solution).order_by(col(Solution.solution_name))))
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    solution_map = {solution.id: solution for solution in solutions}
    period_map = {period.id: period for period in periods}
    scores = {project.id: calculate_project_score(session, project.id or 0) for project in projects}
    return templates.TemplateResponse(
        request,
        "projects.html",
        template_context(
            request,
            projects=projects,
            solutions=solutions,
            periods=periods,
            solution_map=solution_map,
            period_map=period_map,
            scores=scores,
        ),
    )


@app.get("/costs", response_class=HTMLResponse)
def costs(request: Request, session: Session = Depends(get_session)):
    cost_lines = list(session.exec(select(CostLine).order_by(col(CostLine.id))))
    projects = list(session.exec(select(RDProject).order_by(col(RDProject.project_title))))
    solutions = list(session.exec(select(Solution).order_by(col(Solution.solution_name))))
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    solution_map = {solution.id: solution for solution in solutions}
    customer_map = {customer.id: customer for customer in customers}
    project_map = {project.id: project for project in projects}
    project_cost_counts = {project.id: 0 for project in projects}
    for cost in cost_lines:
        if cost.project_id in project_cost_counts:
            project_cost_counts[cost.project_id] += 1
    totals = {
        "gross_cost": round(sum(cost.gross_cost for cost in cost_lines), 2),
        "qualifying_amount": round(sum(cost.qualifying_amount for cost in cost_lines), 2),
        "people_time_lines": sum(1 for cost in cost_lines if cost.cost_input_type == "people_time"),
        "direct_cost_lines": sum(1 for cost in cost_lines if cost.cost_input_type != "people_time"),
    }
    return templates.TemplateResponse(
        request,
        "costs.html",
        template_context(
            request,
            cost_lines=cost_lines,
            projects=projects,
            project_map=project_map,
            solution_map=solution_map,
            customer_map=customer_map,
            project_cost_counts=project_cost_counts,
            totals=totals,
        ),
    )


@app.post("/projects")
async def create_project(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    solution_id = parse_required_int(form.get("solution_id"), "Solution", errors)
    accounting_period_id = parse_optional_int(form.get("accounting_period_id"), "Accounting period", errors)
    outcome = parse_enum(form.get("outcome"), domain.PROJECT_OUTCOMES, "Outcome", errors, "unresolved")
    rd_start_date = parse_optional_date(form.get("rd_start_date"), "R&D start date", errors)
    rd_end_date = parse_optional_date(form.get("rd_end_date"), "R&D end date", errors)
    if errors:
        return validation_error_response(errors, "/projects")
    project = RDProject(
        solution_id=solution_id,
        accounting_period_id=accounting_period_id,
        project_title=str(form.get("project_title") or ""),
        field_of_science_or_technology=str(form.get("field_of_science_or_technology") or ""),
        baseline_knowledge=str(form.get("baseline_knowledge") or ""),
        advance_sought=str(form.get("advance_sought") or ""),
        scientific_or_technological_uncertainties=str(form.get("scientific_or_technological_uncertainties") or ""),
        outcome=outcome,
        rd_start_date=rd_start_date,
        rd_end_date=rd_end_date,
        boundary_explanation=str(form.get("boundary_explanation") or ""),
    )
    save_with_audit(session, project, "create", f"Created R&D project {project.project_title}")
    sync_entitlement_for_project(session, project.id or 0)
    return redirect(f"/projects/{project.id}/assessment")


@app.post("/projects/{project_id}/update")
async def update_project(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    project = session.get(RDProject, project_id)
    if not project:
        return redirect("/projects")
    errors: list[str] = []
    solution_id = parse_required_int(form.get("solution_id"), "Solution", errors)
    accounting_period_id = parse_optional_int(form.get("accounting_period_id"), "Accounting period", errors)
    outcome = parse_enum(form.get("outcome"), domain.PROJECT_OUTCOMES, "Outcome", errors, "unresolved")
    rd_start_date = parse_optional_date(form.get("rd_start_date"), "R&D start date", errors)
    rd_end_date = parse_optional_date(form.get("rd_end_date"), "R&D end date", errors)
    if errors:
        return validation_error_response(errors, "/projects")
    before_snapshot = compact_snapshot(project)
    project.solution_id = solution_id
    project.accounting_period_id = accounting_period_id
    project.project_title = str(form.get("project_title") or "")
    project.field_of_science_or_technology = str(form.get("field_of_science_or_technology") or "")
    project.baseline_knowledge = str(form.get("baseline_knowledge") or "")
    project.advance_sought = str(form.get("advance_sought") or "")
    project.scientific_or_technological_uncertainties = str(form.get("scientific_or_technological_uncertainties") or "")
    project.outcome = outcome
    project.rd_start_date = rd_start_date
    project.rd_end_date = rd_end_date
    project.boundary_explanation = str(form.get("boundary_explanation") or "")
    save_with_audit(session, project, "update", f"Updated R&D project {project.project_title}", before_snapshot)
    sync_entitlement_for_project(session, project_id)
    return redirect("/projects")


@app.post("/projects/{project_id}/delete")
def delete_project(project_id: int, session: Session = Depends(get_session)):
    for child_model, child_field, label in DEPENDENCY_RULES[RDProject]:
        child = session.exec(select(child_model).where(child_field == project_id)).first()
        if child:
            return redirect(f"/projects?error=delete_blocked_{label.replace(' ', '_')}")
    project = session.get(RDProject, project_id)
    if not project:
        return redirect("/projects")
    entitlement = session.exec(select(EntitlementAssessment).where(EntitlementAssessment.project_id == project_id)).first()
    if entitlement:
        delete_with_audit(session, entitlement, f"Deleted entitlement assessment for project {project_id}")
    delete_with_audit(session, project, f"Deleted R&D project {project.project_title}")
    return redirect("/projects")


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        template_context(request, context=context, score=score),
    )


@app.get("/projects/{project_id}/assessment", response_class=HTMLResponse)
def project_assessment(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    return templates.TemplateResponse(
        request,
        "project_assessment.html",
        template_context(request, context=context, score=score, periods=periods),
    )


@app.post("/projects/{project_id}/assessment")
async def update_project_assessment(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    project = session.get(RDProject, project_id)
    if not project:
        return redirect("/projects")
    errors: list[str] = []
    accounting_period_id = parse_optional_int(form.get("accounting_period_id"), "Accounting period", errors)
    outcome = parse_enum(form.get("outcome"), domain.PROJECT_OUTCOMES, "Outcome", errors, "unresolved")
    rd_start_date = parse_optional_date(form.get("rd_start_date"), "R&D start date", errors)
    rd_end_date = parse_optional_date(form.get("rd_end_date"), "R&D end date", errors)
    company_role = parse_enum(form.get("company_role"), domain.COMPANY_ROLES, "Company role", errors, "framework supplier")
    if errors:
        return validation_error_response(errors, f"/projects/{project_id}/assessment")
    before_snapshot = compact_snapshot(project)
    project.accounting_period_id = accounting_period_id
    project.field_of_science_or_technology = str(form.get("field_of_science_or_technology") or "")
    project.baseline_knowledge = str(form.get("baseline_knowledge") or "")
    project.advance_sought = str(form.get("advance_sought") or "")
    project.wider_field_explanation = str(form.get("wider_field_explanation") or "")
    project.scientific_or_technological_uncertainties = str(form.get("scientific_or_technological_uncertainties") or "")
    project.competent_professionals_could_not_resolve = str(form.get("competent_professionals_could_not_resolve") or "")
    project.system_uncertainty_explanation = str(form.get("system_uncertainty_explanation") or "")
    project.alternatives_considered = str(form.get("alternatives_considered") or "")
    project.experiments_prototypes_tests = str(form.get("experiments_prototypes_tests") or "")
    project.failed_attempts = str(form.get("failed_attempts") or "")
    project.outcome = outcome
    project.rd_start_date = rd_start_date
    project.rd_end_date = rd_end_date
    project.boundary_explanation = str(form.get("boundary_explanation") or "")
    project.non_qualifying_delivery_activities = str(form.get("non_qualifying_delivery_activities") or "")
    project.supplier_initiated_rd = parse_form_bool(form.get("supplier_initiated_rd"))
    project.uncertainty_discovered_during_delivery = parse_form_bool(form.get("uncertainty_discovered_during_delivery"))
    project.contract_specified_technical_uncertainty = parse_form_bool(form.get("contract_specified_technical_uncertainty"))
    project.another_party_could_claim = parse_form_bool(form.get("another_party_could_claim"))
    project.grant_funded_or_subsidised = parse_form_bool(form.get("grant_funded_or_subsidised"))
    project.company_role = company_role
    project.described_in_aif = parse_form_bool(form.get("described_in_aif"))
    save_with_audit(session, project, "update", f"Updated assessment for {project.project_title}", before_snapshot)
    sync_entitlement_for_project(session, project_id)
    return redirect(f"/projects/{project_id}/assessment")


@app.get("/projects/{project_id}/costs", response_class=HTMLResponse)
def project_costs(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        request,
        "project_costs.html",
        template_context(request, context=context, score=score),
    )


@app.post("/projects/{project_id}/costs")
async def add_project_cost(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    cost = cost_line_from_form(form, project_id, errors)
    if errors:
        return validation_error_response(errors, f"/projects/{project_id}/costs")
    save_with_audit(session, cost, "create", f"Created cost line for project {project_id}")
    context = get_project_context(session, project_id)
    if wants_partial(request):
        return templates.TemplateResponse(request, "_cost_lines.html", template_context(request, context=context))
    return redirect(f"/projects/{project_id}/costs")


@app.post("/costs/{cost_id}/update")
async def update_cost_line(cost_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    cost = session.get(CostLine, cost_id)
    if not cost:
        return redirect("/costs")
    errors: list[str] = []
    before_snapshot = compact_snapshot(cost)
    candidate = cost_line_from_form(form, cost.project_id, errors, CostLine(project_id=cost.project_id))
    if errors:
        return validation_error_response(errors, f"/projects/{cost.project_id}/costs")
    for field_name in CostLine.model_fields:
        if field_name != "id":
            setattr(cost, field_name, getattr(candidate, field_name))
    save_with_audit(session, cost, "update", f"Updated cost line {cost.id}", before_snapshot)
    return redirect(f"/projects/{cost.project_id}/costs")


@app.post("/costs/{cost_id}/delete")
def delete_cost_line(cost_id: int, session: Session = Depends(get_session)):
    cost = session.get(CostLine, cost_id)
    project_id = cost.project_id if cost else None
    if cost:
        delete_with_audit(session, cost, f"Deleted cost line {cost.id}")
    return redirect(f"/projects/{project_id}/costs" if project_id else "/costs")


@app.get("/projects/{project_id}/evidence", response_class=HTMLResponse)
def project_evidence(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        request,
        "project_evidence.html",
        template_context(request, context=context, score=score),
    )


@app.post("/projects/{project_id}/evidence")
async def add_project_evidence(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    date_created = parse_optional_date(form.get("date_created"), "Evidence date created", errors)
    source_system = parse_enum(form.get("source_system"), domain.SOURCE_SYSTEMS, "Source system", errors, "Manual upload / note")
    evidence_type = parse_enum(form.get("evidence_type"), domain.EVIDENCE_TYPES, "Evidence type", errors, "technical spike")
    relevance_tag = parse_enum(form.get("relevance_tag"), domain.RELEVANCE_TAGS, "Relevance tag", errors, "uncertainty")
    strength = parse_enum(form.get("strength"), domain.EVIDENCE_STRENGTHS, "Evidence strength", errors, "moderate")
    if errors:
        return validation_error_response(errors, f"/projects/{project_id}/evidence")
    evidence = EvidenceItem(
        project_id=project_id,
        source_system=source_system,
        source_reference=str(form.get("source_reference") or ""),
        url_or_file_path=str(form.get("url_or_file_path") or ""),
        date_created=date_created,
        evidence_type=evidence_type,
        relevance_tag=relevance_tag,
        strength=strength,
        notes=str(form.get("notes") or ""),
    )
    save_with_audit(session, evidence, "create", f"Created evidence item for project {project_id}")
    context = get_project_context(session, project_id)
    if wants_partial(request):
        return templates.TemplateResponse(request, "_evidence_items.html", template_context(request, context=context))
    return redirect(f"/projects/{project_id}/evidence")


@app.post("/evidence/{evidence_id}/update")
async def update_evidence_item(evidence_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    evidence = session.get(EvidenceItem, evidence_id)
    if not evidence:
        return redirect("/evidence-index")
    errors: list[str] = []
    date_created = parse_optional_date(form.get("date_created"), "Evidence date created", errors)
    source_system = parse_enum(form.get("source_system"), domain.SOURCE_SYSTEMS, "Source system", errors, "Manual upload / note")
    evidence_type = parse_enum(form.get("evidence_type"), domain.EVIDENCE_TYPES, "Evidence type", errors, "technical spike")
    relevance_tag = parse_enum(form.get("relevance_tag"), domain.RELEVANCE_TAGS, "Relevance tag", errors, "uncertainty")
    strength = parse_enum(form.get("strength"), domain.EVIDENCE_STRENGTHS, "Evidence strength", errors, "moderate")
    if errors:
        return validation_error_response(errors, f"/projects/{evidence.project_id}/evidence")
    before_snapshot = compact_snapshot(evidence)
    evidence.source_system = source_system
    evidence.source_reference = str(form.get("source_reference") or "")
    evidence.url_or_file_path = str(form.get("url_or_file_path") or "")
    evidence.date_created = date_created
    evidence.evidence_type = evidence_type
    evidence.relevance_tag = relevance_tag
    evidence.strength = strength
    evidence.notes = str(form.get("notes") or "")
    save_with_audit(session, evidence, "update", f"Updated evidence item {evidence.id}", before_snapshot)
    return redirect(f"/projects/{evidence.project_id}/evidence")


@app.post("/evidence/{evidence_id}/delete")
def delete_evidence_item(evidence_id: int, session: Session = Depends(get_session)):
    evidence = session.get(EvidenceItem, evidence_id)
    project_id = evidence.project_id if evidence else None
    if evidence:
        delete_with_audit(session, evidence, f"Deleted evidence item {evidence.id}")
    return redirect(f"/projects/{project_id}/evidence" if project_id else "/evidence-index")


@app.get("/projects/{project_id}/competent-professional", response_class=HTMLResponse)
def project_competent_professional(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        request,
        "project_competent_professional.html",
        template_context(request, context=context, score=score),
    )


@app.post("/projects/{project_id}/competent-professional")
async def add_competent_professional(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    years = parse_optional_int(form.get("years_relevant_experience"), "Years of relevant experience", errors) or 0
    signoff_status = parse_enum(form.get("signoff_status"), domain.SIGNOFF_STATUSES, "Sign-off status", errors, "draft")
    signoff_date = parse_optional_date(form.get("signoff_date"), "Sign-off date", errors)
    if errors:
        return validation_error_response(errors, f"/projects/{project_id}/competent-professional")
    opinion = CompetentProfessionalOpinion(
        project_id=project_id,
        professional_name=str(form.get("professional_name") or ""),
        role=str(form.get("role") or ""),
        qualifications=str(form.get("qualifications") or ""),
        years_relevant_experience=years,
        relevant_field_expertise=str(form.get("relevant_field_expertise") or ""),
        opinion_text=str(form.get("opinion_text") or ""),
        signoff_status=signoff_status,
        signoff_date=signoff_date,
        reviewer_comments=str(form.get("reviewer_comments") or ""),
    )
    save_with_audit(session, opinion, "create", f"Created competent professional opinion for project {project_id}")
    return redirect(f"/projects/{project_id}/competent-professional")


@app.post("/competent-professional/{opinion_id}/update")
async def update_competent_professional(opinion_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    opinion = session.get(CompetentProfessionalOpinion, opinion_id)
    if not opinion:
        return redirect("/projects")
    errors: list[str] = []
    years = parse_optional_int(form.get("years_relevant_experience"), "Years of relevant experience", errors) or 0
    signoff_status = parse_enum(form.get("signoff_status"), domain.SIGNOFF_STATUSES, "Sign-off status", errors, "draft")
    signoff_date = parse_optional_date(form.get("signoff_date"), "Sign-off date", errors)
    if errors:
        return validation_error_response(errors, f"/projects/{opinion.project_id}/competent-professional")
    before_snapshot = compact_snapshot(opinion)
    opinion.professional_name = str(form.get("professional_name") or "")
    opinion.role = str(form.get("role") or "")
    opinion.qualifications = str(form.get("qualifications") or "")
    opinion.years_relevant_experience = years
    opinion.relevant_field_expertise = str(form.get("relevant_field_expertise") or "")
    opinion.opinion_text = str(form.get("opinion_text") or "")
    opinion.signoff_status = signoff_status
    opinion.signoff_date = signoff_date
    opinion.reviewer_comments = str(form.get("reviewer_comments") or "")
    save_with_audit(session, opinion, "update", f"Updated competent professional opinion {opinion.id}", before_snapshot)
    return redirect(f"/projects/{opinion.project_id}/competent-professional")


@app.post("/competent-professional/{opinion_id}/delete")
def delete_competent_professional(opinion_id: int, session: Session = Depends(get_session)):
    opinion = session.get(CompetentProfessionalOpinion, opinion_id)
    project_id = opinion.project_id if opinion else None
    if opinion:
        delete_with_audit(session, opinion, f"Deleted competent professional opinion {opinion.id}")
    return redirect(f"/projects/{project_id}/competent-professional" if project_id else "/projects")


@app.get("/projects/{project_id}/report", response_class=HTMLResponse)
def project_report(project_id: int, request: Request, format: str | None = None, session: Session = Depends(get_session)):
    markdown = generate_project_memo_markdown(session, project_id)
    if format == "md":
        filename = f"project-{project_id}-eligibility-memo.md"
        return Response(
            markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        request,
        "project_report.html",
        template_context(request, context=context, score=score, markdown=markdown),
    )


@app.get("/claim-periods/{period_id}/readiness", response_class=HTMLResponse)
def claim_period_readiness(period_id: int, request: Request, session: Session = Depends(get_session)):
    readiness = aif_readiness_for_period(session, period_id)
    return templates.TemplateResponse(
        request,
        "claim_period_readiness.html",
        template_context(request, readiness=readiness),
    )


@app.post("/claim-periods/{period_id}/readiness")
async def update_claim_period_submission(period_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    submission = session.exec(
        select(ClaimPeriodSubmissionStatus).where(ClaimPeriodSubmissionStatus.accounting_period_id == period_id)
    ).first()
    if not submission:
        submission = ClaimPeriodSubmissionStatus(accounting_period_id=period_id)
    errors: list[str] = []
    ct600_submission_date = parse_optional_date(form.get("ct600_submission_date"), "CT600 submission date", errors)
    aif_submission_date = parse_optional_date(form.get("aif_submission_date"), "AIF submission date", errors)
    if errors:
        return validation_error_response(errors, f"/claim-periods/{period_id}/readiness")
    before_snapshot = compact_snapshot(submission)
    submission.ct600_submitted = parse_form_bool(form.get("ct600_submitted"))
    submission.ct600_submission_date = ct600_submission_date
    submission.aif_submitted = parse_form_bool(form.get("aif_submitted"))
    submission.aif_submission_date = aif_submission_date
    submission.notes = str(form.get("notes") or "")
    action = "update" if submission.id else "create"
    save_with_audit(
        session,
        submission,
        action,
        f"Updated claim-period submission status for period {period_id}",
        before_snapshot if submission.id else "",
    )
    return redirect(f"/claim-periods/{period_id}/readiness")


@app.get("/claim-periods/{period_id}/pack", response_class=HTMLResponse)
def claim_period_pack(period_id: int, request: Request, format: str | None = None, session: Session = Depends(get_session)):
    markdown = generate_claim_period_pack_markdown(session, period_id)
    if format == "md":
        filename = f"claim-period-{period_id}-pack.md"
        return Response(
            markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    readiness = aif_readiness_for_period(session, period_id)
    return templates.TemplateResponse(
        request,
        "claim_period_pack.html",
        template_context(request, readiness=readiness, markdown=markdown),
    )


@app.get("/evidence-index", response_class=HTMLResponse)
def evidence_index(request: Request, format: str | None = None, session: Session = Depends(get_session)):
    markdown = generate_evidence_index_markdown(session)
    if format == "md":
        return Response(
            markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="evidence-index.md"'},
        )
    return templates.TemplateResponse(request, "evidence_index.html", template_context(request, markdown=markdown))


@app.get("/audit", response_class=HTMLResponse)
def audit(
    request: Request,
    entity_type: str | None = None,
    action: str | None = None,
    session: Session = Depends(get_session),
):
    query = select(AuditEvent)
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if action:
        query = query.where(AuditEvent.action == action)
    events = list(session.exec(query.order_by(col(AuditEvent.created_at).desc()).limit(100)))
    entity_types = sorted({item for item in session.exec(select(AuditEvent.entity_type)).all() if item})
    actions = sorted({item for item in session.exec(select(AuditEvent.action)).all() if item})
    return templates.TemplateResponse(
        request,
        "audit.html",
        template_context(
            request,
            events=events,
            entity_types=entity_types,
            actions=actions,
            selected_entity_type=entity_type or "",
            selected_action=action or "",
        ),
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "app": "R&D Claim Evidence Hub"}


@app.get("/health")
def health():
    return {"status": "ok", "rules": rules_version_summary()}
