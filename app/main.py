from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, col, select

from app import domain
from app.database import engine, get_session, init_db
from app.models import (
    AccountingPeriod,
    BusinessUnit,
    ClaimPeriodSubmissionStatus,
    Company,
    CompetentProfessionalOpinion,
    Contract,
    CostLine,
    Customer,
    EvidenceItem,
    RDProject,
    Solution,
)
from app.reports import generate_claim_period_pack_markdown, generate_evidence_index_markdown, generate_project_memo_markdown
from app.rule_loader import rules_version_summary
from app.seed import seed_business_units, seed_demo_data
from app.services import (
    CAVEAT,
    aif_readiness_for_period,
    as_bool,
    calculate_project_score,
    calculate_qualifying_amount,
    dashboard_metrics,
    deadline_warning,
    get_project_context,
    parse_date,
    sync_entitlement_for_project,
)
from app.settings import BASE_DIR, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    if settings.seed_reference_data:
        with Session(engine) as session:
            seed_business_units(session)
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


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    projects = list(session.exec(select(RDProject).order_by(col(RDProject.project_title))))
    metrics = dashboard_metrics(session)
    return templates.TemplateResponse(
        "dashboard.html",
        template_context(request, metrics=metrics, projects=projects),
    )


@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request, session: Session = Depends(get_session)):
    companies = list(session.exec(select(Company).order_by(col(Company.company_name))))
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    return templates.TemplateResponse(
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
        northern_ireland_registered=as_bool(form.get("northern_ireland_registered")),
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
    session.add(company)
    session.commit()
    return redirect("/companies")


@app.post("/accounting-periods")
async def create_accounting_period(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    period = AccountingPeriod(
        company_id=int(form.get("company_id") or 0),
        label=str(form.get("label") or ""),
        start_date=parse_date(str(form.get("start_date") or "")) or date.today(),
        end_date=parse_date(str(form.get("end_date") or "")) or date.today(),
        period_of_account_start=parse_date(str(form.get("period_of_account_start") or "")) or date.today(),
        period_of_account_end=parse_date(str(form.get("period_of_account_end") or "")) or date.today(),
        claim_notification_submitted=as_bool(form.get("claim_notification_submitted")),
        claim_notification_date=parse_date(str(form.get("claim_notification_date") or "")),
        prior_claim_within_3_years=as_bool(form.get("prior_claim_within_3_years")),
        scheme_notes=str(form.get("scheme_notes") or ""),
    )
    session.add(period)
    session.commit()
    session.refresh(period)
    session.add(ClaimPeriodSubmissionStatus(accounting_period_id=period.id or 0))
    session.commit()
    return redirect("/companies")


@app.get("/customers", response_class=HTMLResponse)
def customers(request: Request, session: Session = Depends(get_session)):
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    business_units = list(session.exec(select(BusinessUnit).order_by(col(BusinessUnit.name))))
    business_unit_map = {unit.id: unit for unit in business_units}
    return templates.TemplateResponse(
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
    customer = Customer(
        business_unit_id=int(form.get("business_unit_id")) if form.get("business_unit_id") else None,
        customer_name=str(form.get("customer_name") or ""),
        sector=str(form.get("sector") or ""),
        transport_domain=str(form.get("transport_domain") or "other"),
        customer_type=str(form.get("customer_type") or "other"),
        corporation_tax_status=str(form.get("corporation_tax_status") or "unknown"),
        notes=str(form.get("notes") or ""),
    )
    session.add(customer)
    session.commit()
    return redirect("/customers")


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
    unit = BusinessUnit(
        name=str(form.get("name") or ""),
        parent_id=int(form.get("parent_id")) if form.get("parent_id") else None,
        description=str(form.get("description") or ""),
        active=as_bool(form.get("active")) if form.get("active") is not None else True,
    )
    session.add(unit)
    session.commit()
    return redirect("/business-units")


@app.get("/contracts", response_class=HTMLResponse)
def contracts(request: Request, session: Session = Depends(get_session)):
    contracts = list(session.exec(select(Contract).order_by(col(Contract.contract_name))))
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    customer_map = {customer.id: customer for customer in customers}
    return templates.TemplateResponse(
        "contracts.html",
        template_context(request, contracts=contracts, customers=customers, customer_map=customer_map),
    )


@app.post("/contracts")
async def create_contract(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    contract = Contract(
        contract_name=str(form.get("contract_name") or ""),
        customer_id=int(form.get("customer_id") or 0),
        contract_type=str(form.get("contract_type") or "other"),
        start_date=parse_date(str(form.get("start_date") or "")),
        end_date=parse_date(str(form.get("end_date") or "")),
        customer_requested_rd=as_bool(form.get("customer_requested_rd")),
        customer_intended_or_contemplated_rd=as_bool(form.get("customer_intended_or_contemplated_rd")),
        technical_uncertainty_described=as_bool(form.get("technical_uncertainty_described")),
        ip_owner=str(form.get("ip_owner") or ""),
        funding_grant_notes=str(form.get("funding_grant_notes") or ""),
        public_sector_procurement_notes=str(form.get("public_sector_procurement_notes") or ""),
        contract_evidence_links=str(form.get("contract_evidence_links") or ""),
    )
    session.add(contract)
    session.commit()
    return redirect("/contracts")


@app.get("/solutions", response_class=HTMLResponse)
def solutions(request: Request, session: Session = Depends(get_session)):
    solutions = list(session.exec(select(Solution).order_by(col(Solution.solution_name))))
    customers = list(session.exec(select(Customer).order_by(col(Customer.customer_name))))
    contracts = list(session.exec(select(Contract).order_by(col(Contract.contract_name))))
    customer_map = {customer.id: customer for customer in customers}
    contract_map = {contract.id: contract for contract in contracts}
    return templates.TemplateResponse(
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
    constraints = ", ".join(form.getlist("transport_environment_constraints"))
    solution = Solution(
        solution_name=str(form.get("solution_name") or ""),
        customer_id=int(form.get("customer_id") or 0),
        contract_id=int(form.get("contract_id")) if form.get("contract_id") else None,
        solution_description=str(form.get("solution_description") or ""),
        business_problem=str(form.get("business_problem") or ""),
        technical_architecture_summary=str(form.get("technical_architecture_summary") or ""),
        transport_environment_constraints=constraints,
        initial_radar_status=str(form.get("initial_radar_status") or "amber"),
        radar_reason=str(form.get("radar_reason") or ""),
    )
    session.add(solution)
    session.commit()
    return redirect("/solutions")


@app.get("/projects", response_class=HTMLResponse)
def projects(request: Request, session: Session = Depends(get_session)):
    projects = list(session.exec(select(RDProject).order_by(col(RDProject.project_title))))
    solutions = list(session.exec(select(Solution).order_by(col(Solution.solution_name))))
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    solution_map = {solution.id: solution for solution in solutions}
    period_map = {period.id: period for period in periods}
    scores = {project.id: calculate_project_score(session, project.id or 0) for project in projects}
    return templates.TemplateResponse(
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


@app.post("/projects")
async def create_project(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    project = RDProject(
        solution_id=int(form.get("solution_id") or 0),
        accounting_period_id=int(form.get("accounting_period_id")) if form.get("accounting_period_id") else None,
        project_title=str(form.get("project_title") or ""),
        field_of_science_or_technology=str(form.get("field_of_science_or_technology") or ""),
        baseline_knowledge=str(form.get("baseline_knowledge") or ""),
        advance_sought=str(form.get("advance_sought") or ""),
        scientific_or_technological_uncertainties=str(form.get("scientific_or_technological_uncertainties") or ""),
        outcome=str(form.get("outcome") or "unresolved"),
        rd_start_date=parse_date(str(form.get("rd_start_date") or "")),
        rd_end_date=parse_date(str(form.get("rd_end_date") or "")),
        boundary_explanation=str(form.get("boundary_explanation") or ""),
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    sync_entitlement_for_project(session, project.id or 0)
    return redirect(f"/projects/{project.id}/assessment")


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        "project_detail.html",
        template_context(request, context=context, score=score),
    )


@app.get("/projects/{project_id}/assessment", response_class=HTMLResponse)
def project_assessment(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    return templates.TemplateResponse(
        "project_assessment.html",
        template_context(request, context=context, score=score, periods=periods),
    )


@app.post("/projects/{project_id}/assessment")
async def update_project_assessment(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    project = session.get(RDProject, project_id)
    if not project:
        return redirect("/projects")
    project.accounting_period_id = int(form.get("accounting_period_id")) if form.get("accounting_period_id") else None
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
    project.outcome = str(form.get("outcome") or "unresolved")
    project.rd_start_date = parse_date(str(form.get("rd_start_date") or ""))
    project.rd_end_date = parse_date(str(form.get("rd_end_date") or ""))
    project.boundary_explanation = str(form.get("boundary_explanation") or "")
    project.non_qualifying_delivery_activities = str(form.get("non_qualifying_delivery_activities") or "")
    project.supplier_initiated_rd = as_bool(form.get("supplier_initiated_rd"))
    project.uncertainty_discovered_during_delivery = as_bool(form.get("uncertainty_discovered_during_delivery"))
    project.contract_specified_technical_uncertainty = as_bool(form.get("contract_specified_technical_uncertainty"))
    project.another_party_could_claim = as_bool(form.get("another_party_could_claim"))
    project.grant_funded_or_subsidised = as_bool(form.get("grant_funded_or_subsidised"))
    project.company_role = str(form.get("company_role") or "framework supplier")
    project.described_in_aif = as_bool(form.get("described_in_aif"))
    session.add(project)
    session.commit()
    sync_entitlement_for_project(session, project_id)
    return redirect(f"/projects/{project_id}/assessment")


@app.get("/projects/{project_id}/costs", response_class=HTMLResponse)
def project_costs(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        "project_costs.html",
        template_context(request, context=context, score=score),
    )


@app.post("/projects/{project_id}/costs")
async def add_project_cost(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    cost = CostLine(
        project_id=project_id,
        activity=str(form.get("activity") or ""),
        cost_category=str(form.get("cost_category") or "other"),
        person_or_supplier_name=str(form.get("person_or_supplier_name") or ""),
        gross_cost=float(form.get("gross_cost") or 0),
        apportionment_percentage=float(form.get("apportionment_percentage") or 0),
        paid_status=str(form.get("paid_status") or "paid"),
        uk_or_overseas=str(form.get("uk_or_overseas") or "unknown"),
        connected_party_status=str(form.get("connected_party_status") or "unknown"),
        paye_nic_notes=str(form.get("paye_nic_notes") or ""),
        evidence_link=str(form.get("evidence_link") or ""),
        notes=str(form.get("notes") or ""),
    )
    cost.qualifying_amount = calculate_qualifying_amount(cost.gross_cost, cost.apportionment_percentage)
    session.add(cost)
    session.commit()
    context = get_project_context(session, project_id)
    if wants_partial(request):
        return templates.TemplateResponse("_cost_lines.html", template_context(request, context=context))
    return redirect(f"/projects/{project_id}/costs")


@app.get("/projects/{project_id}/evidence", response_class=HTMLResponse)
def project_evidence(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        "project_evidence.html",
        template_context(request, context=context, score=score),
    )


@app.post("/projects/{project_id}/evidence")
async def add_project_evidence(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    evidence = EvidenceItem(
        project_id=project_id,
        source_system=str(form.get("source_system") or "Manual upload / note"),
        source_reference=str(form.get("source_reference") or ""),
        url_or_file_path=str(form.get("url_or_file_path") or ""),
        date_created=parse_date(str(form.get("date_created") or "")),
        evidence_type=str(form.get("evidence_type") or "technical spike"),
        relevance_tag=str(form.get("relevance_tag") or "uncertainty"),
        strength=str(form.get("strength") or "moderate"),
        notes=str(form.get("notes") or ""),
    )
    session.add(evidence)
    session.commit()
    context = get_project_context(session, project_id)
    if wants_partial(request):
        return templates.TemplateResponse("_evidence_items.html", template_context(request, context=context))
    return redirect(f"/projects/{project_id}/evidence")


@app.get("/projects/{project_id}/competent-professional", response_class=HTMLResponse)
def project_competent_professional(project_id: int, request: Request, session: Session = Depends(get_session)):
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        "project_competent_professional.html",
        template_context(request, context=context, score=score),
    )


@app.post("/projects/{project_id}/competent-professional")
async def add_competent_professional(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    opinion = CompetentProfessionalOpinion(
        project_id=project_id,
        professional_name=str(form.get("professional_name") or ""),
        role=str(form.get("role") or ""),
        qualifications=str(form.get("qualifications") or ""),
        years_relevant_experience=int(form.get("years_relevant_experience") or 0),
        relevant_field_expertise=str(form.get("relevant_field_expertise") or ""),
        opinion_text=str(form.get("opinion_text") or ""),
        signoff_status=str(form.get("signoff_status") or "draft"),
        signoff_date=parse_date(str(form.get("signoff_date") or "")),
        reviewer_comments=str(form.get("reviewer_comments") or ""),
    )
    session.add(opinion)
    session.commit()
    return redirect(f"/projects/{project_id}/competent-professional")


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
        "project_report.html",
        template_context(request, context=context, score=score, markdown=markdown),
    )


@app.get("/claim-periods/{period_id}/readiness", response_class=HTMLResponse)
def claim_period_readiness(period_id: int, request: Request, session: Session = Depends(get_session)):
    readiness = aif_readiness_for_period(session, period_id)
    return templates.TemplateResponse(
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
    submission.ct600_submitted = as_bool(form.get("ct600_submitted"))
    submission.ct600_submission_date = parse_date(str(form.get("ct600_submission_date") or ""))
    submission.aif_submitted = as_bool(form.get("aif_submitted"))
    submission.aif_submission_date = parse_date(str(form.get("aif_submission_date") or ""))
    submission.notes = str(form.get("notes") or "")
    session.add(submission)
    session.commit()
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
    return templates.TemplateResponse("evidence_index.html", template_context(request, markdown=markdown))


@app.get("/health")
def health():
    return {"status": "ok", "rules": rules_version_summary()}
