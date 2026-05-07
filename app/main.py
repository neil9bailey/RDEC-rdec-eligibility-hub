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
    Activity,
    BusinessUnit,
    ClaimPeriodSubmissionStatus,
    Company,
    CompetentProfessionalOpinion,
    Contract,
    CostLine,
    Customer,
    EntitlementAssessment,
    EvidenceItem,
    RDProject,
    ReviewDecision,
    Solution,
    TechnicalUncertainty,
)
from app.knowledge_agent import knowledge_agent_summary, knowledge_review_actions, run_live_source_checks
from app.reports import generate_claim_period_pack_markdown, generate_evidence_index_markdown, generate_project_memo_markdown
from app.rule_loader import rules_version_summary
from app.seed import seed_business_units, seed_demo_data
from app.services import (
    CAVEAT,
    aif_readiness_for_period,
    as_bool,
    calculate_project_score,
    calculate_people_time_gross,
    calculate_qualifying_amount,
    dashboard_metrics,
    deadline_warning,
    get_project_context,
    parse_date,
    sync_entitlement_for_project,
)
from app.settings import BASE_DIR, get_settings


DEPENDENCY_RULES = {
    Company: [(AccountingPeriod, AccountingPeriod.company_id, "accounting periods")],
    AccountingPeriod: [(RDProject, RDProject.accounting_period_id, "R&D projects")],
    BusinessUnit: [
        (BusinessUnit, BusinessUnit.parent_id, "child business units"),
        (Customer, Customer.business_unit_id, "customers"),
    ],
    Customer: [(Contract, Contract.customer_id, "contracts"), (Solution, Solution.customer_id, "solutions")],
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
}


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


def cost_line_from_form(form, project_id: int, cost: CostLine | None = None) -> CostLine:
    hours = float(form.get("hours") or 0)
    hourly_rate = float(form.get("hourly_rate") or 0)
    days = float(form.get("days") or 0)
    day_rate = float(form.get("day_rate") or 0)
    gross_cost = float(form.get("gross_cost") or 0)
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
    cost.time_period_start = parse_date(str(form.get("time_period_start") or ""))
    cost.time_period_end = parse_date(str(form.get("time_period_end") or ""))
    cost.hours = hours
    cost.hourly_rate = hourly_rate
    cost.days = days
    cost.day_rate = day_rate
    cost.gross_cost = gross_cost
    cost.apportionment_percentage = float(form.get("apportionment_percentage") or 0)
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
    session.delete(item)
    session.commit()
    return redirect(redirect_path)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    projects = list(session.exec(select(RDProject).order_by(col(RDProject.project_title))))
    metrics = dashboard_metrics(session)
    return templates.TemplateResponse(
        "dashboard.html",
        template_context(request, metrics=metrics, projects=projects),
    )


@app.get("/knowledge-agent", response_class=HTMLResponse)
def knowledge_agent(request: Request, session: Session = Depends(get_session)):
    summary = knowledge_agent_summary(session)
    actions = knowledge_review_actions(session)
    return templates.TemplateResponse(
        "knowledge_agent.html",
        template_context(request, summary=summary, actions=actions, live_checks=None),
    )


@app.post("/knowledge-agent/check", response_class=HTMLResponse)
def knowledge_agent_check(request: Request, session: Session = Depends(get_session)):
    live_checks = run_live_source_checks(session)
    summary = knowledge_agent_summary(session)
    actions = knowledge_review_actions(session)
    return templates.TemplateResponse(
        "knowledge_agent.html",
        template_context(request, summary=summary, actions=actions, live_checks=live_checks),
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


@app.post("/companies/{company_id}/update")
async def update_company(company_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    company = session.get(Company, company_id)
    if not company:
        return redirect("/companies")
    company.company_name = str(form.get("company_name") or "")
    company.utr = str(form.get("utr") or "")
    company.paye_reference = str(form.get("paye_reference") or "")
    company.vat_number = str(form.get("vat_number") or "")
    company.sic_code = str(form.get("sic_code") or "")
    company.registered_office_country = str(form.get("registered_office_country") or "United Kingdom")
    company.registered_office_region = str(form.get("registered_office_region") or "")
    company.northern_ireland_registered = as_bool(form.get("northern_ireland_registered"))
    company.senior_rd_contact_name = str(form.get("senior_rd_contact_name") or "")
    company.senior_rd_contact_role = str(form.get("senior_rd_contact_role") or "")
    company.senior_rd_contact_email = str(form.get("senior_rd_contact_email") or "")
    company.senior_rd_contact_phone = str(form.get("senior_rd_contact_phone") or "")
    company.agent_name = str(form.get("agent_name") or "")
    company.agent_reference = str(form.get("agent_reference") or "")
    company.agent_email = str(form.get("agent_email") or "")
    company.agent_phone = str(form.get("agent_phone") or "")
    company.agent_role = str(form.get("agent_role") or "")
    session.add(company)
    session.commit()
    return redirect("/companies")


@app.post("/companies/{company_id}/delete")
def delete_company(company_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, Company, company_id, "/companies")


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


@app.post("/accounting-periods/{period_id}/update")
async def update_accounting_period(period_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    period = session.get(AccountingPeriod, period_id)
    if not period:
        return redirect("/companies")
    period.company_id = int(form.get("company_id") or 0)
    period.label = str(form.get("label") or "")
    period.start_date = parse_date(str(form.get("start_date") or "")) or date.today()
    period.end_date = parse_date(str(form.get("end_date") or "")) or date.today()
    period.period_of_account_start = parse_date(str(form.get("period_of_account_start") or "")) or date.today()
    period.period_of_account_end = parse_date(str(form.get("period_of_account_end") or "")) or date.today()
    period.claim_notification_submitted = as_bool(form.get("claim_notification_submitted"))
    period.claim_notification_date = parse_date(str(form.get("claim_notification_date") or ""))
    period.prior_claim_within_3_years = as_bool(form.get("prior_claim_within_3_years"))
    period.scheme_notes = str(form.get("scheme_notes") or "")
    session.add(period)
    session.commit()
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
        session.delete(submission)
        session.commit()
    return delete_or_block(session, AccountingPeriod, period_id, "/companies")


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


@app.post("/customers/{customer_id}/update")
async def update_customer(customer_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    customer = session.get(Customer, customer_id)
    if not customer:
        return redirect("/customers")
    customer.business_unit_id = int(form.get("business_unit_id")) if form.get("business_unit_id") else None
    customer.customer_name = str(form.get("customer_name") or "")
    customer.sector = str(form.get("sector") or "")
    customer.transport_domain = str(form.get("transport_domain") or "other")
    customer.customer_type = str(form.get("customer_type") or "other")
    customer.corporation_tax_status = str(form.get("corporation_tax_status") or "unknown")
    customer.notes = str(form.get("notes") or "")
    session.add(customer)
    session.commit()
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


@app.post("/business-units/{unit_id}/update")
async def update_business_unit(unit_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    unit = session.get(BusinessUnit, unit_id)
    if not unit:
        return redirect("/business-units")
    unit.name = str(form.get("name") or "")
    unit.parent_id = int(form.get("parent_id")) if form.get("parent_id") else None
    unit.description = str(form.get("description") or "")
    unit.active = as_bool(form.get("active"))
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


@app.post("/contracts/{contract_id}/update")
async def update_contract(contract_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    contract = session.get(Contract, contract_id)
    if not contract:
        return redirect("/contracts")
    contract.contract_name = str(form.get("contract_name") or "")
    contract.customer_id = int(form.get("customer_id") or 0)
    contract.contract_type = str(form.get("contract_type") or "other")
    contract.start_date = parse_date(str(form.get("start_date") or ""))
    contract.end_date = parse_date(str(form.get("end_date") or ""))
    contract.customer_requested_rd = as_bool(form.get("customer_requested_rd"))
    contract.customer_intended_or_contemplated_rd = as_bool(form.get("customer_intended_or_contemplated_rd"))
    contract.technical_uncertainty_described = as_bool(form.get("technical_uncertainty_described"))
    contract.ip_owner = str(form.get("ip_owner") or "")
    contract.funding_grant_notes = str(form.get("funding_grant_notes") or "")
    contract.public_sector_procurement_notes = str(form.get("public_sector_procurement_notes") or "")
    contract.contract_evidence_links = str(form.get("contract_evidence_links") or "")
    session.add(contract)
    session.commit()
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


@app.post("/solutions/{solution_id}/update")
async def update_solution(solution_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    solution = session.get(Solution, solution_id)
    if not solution:
        return redirect("/solutions")
    solution.solution_name = str(form.get("solution_name") or "")
    solution.customer_id = int(form.get("customer_id") or 0)
    solution.contract_id = int(form.get("contract_id")) if form.get("contract_id") else None
    solution.solution_description = str(form.get("solution_description") or "")
    solution.business_problem = str(form.get("business_problem") or "")
    solution.technical_architecture_summary = str(form.get("technical_architecture_summary") or "")
    selected_constraints = form.getlist("transport_environment_constraints")
    solution.transport_environment_constraints = ", ".join(selected_constraints) if selected_constraints else str(form.get("transport_environment_constraints_text") or "")
    solution.initial_radar_status = str(form.get("initial_radar_status") or "amber")
    solution.radar_reason = str(form.get("radar_reason") or "")
    session.add(solution)
    session.commit()
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


@app.post("/projects/{project_id}/update")
async def update_project(project_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    project = session.get(RDProject, project_id)
    if not project:
        return redirect("/projects")
    project.solution_id = int(form.get("solution_id") or 0)
    project.accounting_period_id = int(form.get("accounting_period_id")) if form.get("accounting_period_id") else None
    project.project_title = str(form.get("project_title") or "")
    project.field_of_science_or_technology = str(form.get("field_of_science_or_technology") or "")
    project.baseline_knowledge = str(form.get("baseline_knowledge") or "")
    project.advance_sought = str(form.get("advance_sought") or "")
    project.scientific_or_technological_uncertainties = str(form.get("scientific_or_technological_uncertainties") or "")
    project.outcome = str(form.get("outcome") or "unresolved")
    project.rd_start_date = parse_date(str(form.get("rd_start_date") or ""))
    project.rd_end_date = parse_date(str(form.get("rd_end_date") or ""))
    project.boundary_explanation = str(form.get("boundary_explanation") or "")
    session.add(project)
    session.commit()
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
        session.delete(entitlement)
    session.delete(project)
    session.commit()
    return redirect("/projects")


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
    cost = cost_line_from_form(form, project_id)
    session.add(cost)
    session.commit()
    context = get_project_context(session, project_id)
    if wants_partial(request):
        return templates.TemplateResponse("_cost_lines.html", template_context(request, context=context))
    return redirect(f"/projects/{project_id}/costs")


@app.post("/costs/{cost_id}/update")
async def update_cost_line(cost_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    cost = session.get(CostLine, cost_id)
    if not cost:
        return redirect("/costs")
    cost = cost_line_from_form(form, cost.project_id, cost)
    session.add(cost)
    session.commit()
    return redirect(f"/projects/{cost.project_id}/costs")


@app.post("/costs/{cost_id}/delete")
def delete_cost_line(cost_id: int, session: Session = Depends(get_session)):
    cost = session.get(CostLine, cost_id)
    project_id = cost.project_id if cost else None
    if cost:
        session.delete(cost)
        session.commit()
    return redirect(f"/projects/{project_id}/costs" if project_id else "/costs")


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


@app.post("/evidence/{evidence_id}/update")
async def update_evidence_item(evidence_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    evidence = session.get(EvidenceItem, evidence_id)
    if not evidence:
        return redirect("/evidence-index")
    evidence.source_system = str(form.get("source_system") or "Manual upload / note")
    evidence.source_reference = str(form.get("source_reference") or "")
    evidence.url_or_file_path = str(form.get("url_or_file_path") or "")
    evidence.date_created = parse_date(str(form.get("date_created") or ""))
    evidence.evidence_type = str(form.get("evidence_type") or "technical spike")
    evidence.relevance_tag = str(form.get("relevance_tag") or "uncertainty")
    evidence.strength = str(form.get("strength") or "moderate")
    evidence.notes = str(form.get("notes") or "")
    session.add(evidence)
    session.commit()
    return redirect(f"/projects/{evidence.project_id}/evidence")


@app.post("/evidence/{evidence_id}/delete")
def delete_evidence_item(evidence_id: int, session: Session = Depends(get_session)):
    evidence = session.get(EvidenceItem, evidence_id)
    project_id = evidence.project_id if evidence else None
    if evidence:
        session.delete(evidence)
        session.commit()
    return redirect(f"/projects/{project_id}/evidence" if project_id else "/evidence-index")


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


@app.post("/competent-professional/{opinion_id}/update")
async def update_competent_professional(opinion_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    opinion = session.get(CompetentProfessionalOpinion, opinion_id)
    if not opinion:
        return redirect("/projects")
    opinion.professional_name = str(form.get("professional_name") or "")
    opinion.role = str(form.get("role") or "")
    opinion.qualifications = str(form.get("qualifications") or "")
    opinion.years_relevant_experience = int(form.get("years_relevant_experience") or 0)
    opinion.relevant_field_expertise = str(form.get("relevant_field_expertise") or "")
    opinion.opinion_text = str(form.get("opinion_text") or "")
    opinion.signoff_status = str(form.get("signoff_status") or "draft")
    opinion.signoff_date = parse_date(str(form.get("signoff_date") or ""))
    opinion.reviewer_comments = str(form.get("reviewer_comments") or "")
    session.add(opinion)
    session.commit()
    return redirect(f"/projects/{opinion.project_id}/competent-professional")


@app.post("/competent-professional/{opinion_id}/delete")
def delete_competent_professional(opinion_id: int, session: Session = Depends(get_session)):
    opinion = session.get(CompetentProfessionalOpinion, opinion_id)
    project_id = opinion.project_id if opinion else None
    if opinion:
        session.delete(opinion)
        session.commit()
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
