from __future__ import annotations

from contextlib import asynccontextmanager
import json
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, col, select

from app import domain
from app.audit import compact_snapshot, log_event
from app.company_setup import (
    accounting_period_label,
    company_setup_context,
    normalize_company_payload,
    validate_accounting_period_dates,
    validate_company_payload,
)
from app.database import engine, get_session, init_db
from app.data_management import (
    DATASETS,
    DEPENDENCY_RULES,
    DataOperationError,
    apply_import,
    cleanup_candidates,
    csv_export_zip_bytes,
    data_inventory,
    decode_import_payload,
    delete_unused_records,
    encode_import_payload,
    json_export_bytes,
    parse_import_file,
    purge_records,
    purge_scope_cards,
    build_import_plan,
)
from app.form_utils import (
    parse_bool as parse_form_bool,
    parse_decimal_amount,
    parse_enum,
    parse_money,
    parse_optional_date,
    parse_percentage,
    parse_optional_int,
    parse_required_date,
    parse_required_int,
    validation_error_response,
)
from app.framework_intelligence import (
    FRAMEWORK_AGENT_CAVEAT,
    configured_framework_source_types,
    create_intelligence_report,
    create_portal_retrieval_run,
    extract_document_intelligence,
    framework_intelligence_metrics,
    framework_opportunity_review_context,
    latest_snapshot_by_source,
    official_framework_source_allowed,
    run_framework_agent_for_profile,
    run_single_source_check,
    seed_framework_sources,
    source_readiness_for_source,
    watch_profile_suggestion_cards,
)
from app.models import (
    AccountingPeriod,
    Activity,
    AuditEvent,
    BuyerPortalInstance,
    BusinessUnit,
    ClaimPeriodSubmissionStatus,
    Company,
    CompetentProfessionalOpinion,
    Contract,
    CostLine,
    Customer,
    CustomerWatchProfile,
    ExtractedQualityQuestion,
    EntitlementAssessment,
    EvidenceItem,
    ExtractedRequirement,
    FrameworkAgentRun,
    FrameworkOpportunity,
    FrameworkSource,
    IntelligenceReport,
    OpportunityDocument,
    PortalRetrievalRun,
    ProcurementPlatform,
    RDProject,
    RDECOpportunitySignal,
    ReviewDecision,
    Solution,
    SourceCheckSnapshot,
    TechnicalUncertainty,
)
from app.knowledge_agent import knowledge_agent_summary, knowledge_review_actions, run_live_source_checks
from app.reports import generate_claim_period_pack_markdown, generate_evidence_index_markdown, generate_project_memo_markdown
from app.review_cockpit import review_workflow_context
from app.rule_loader import rules_version_summary
from app.rules_engine import get_rules, validate_all_rules
from app.seed import seed_business_units, seed_demo_data
from app.services import (
    CAVEAT,
    aif_readiness_for_period,
    bulk_aif_readiness,
    bulk_project_scores,
    calculate_project_score,
    calculate_people_time_gross,
    calculate_qualifying_amount,
    dashboard_metrics,
    deadline_warning,
    get_project_context,
    sync_entitlement_for_project,
)
from app.settings import BASE_DIR, get_settings


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


def friendly_query_error(value: str) -> str:
    if not value:
        return ""
    if value == "purge_disabled":
        return "Purge is turned off. An administrator must enable it before this action is available."
    if value.startswith("delete_blocked_"):
        linked_area = value.removeprefix("delete_blocked_").replace("_", " ")
        return f"This record is still used by {linked_area}. Remove or reassign those links first."
    return "The requested change could not be completed. Review the record and try again."


def template_context(request: Request, **extra):
    base = {
        "request": request,
        "app_name": get_settings().app_name,
        "caveat": CAVEAT,
        "rules_versions": rules_version_summary(),
        "domain": domain,
        "flash_error": friendly_query_error(str(request.query_params.get("error") or "")),
        "flash_notice": str(request.query_params.get("notice") or ""),
    }
    base.update(extra)
    return base


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def safe_framework_return_path(value: object, default: str) -> str:
    path = str(value or "").strip()
    if path.startswith("/framework-intelligence"):
        return path
    return default


def wants_partial(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


FRAMEWORK_SOURCE_TYPES = configured_framework_source_types()
FRAMEWORK_OPPORTUNITY_STATUSES = ["new", "watching", "bid_review", "archived", "rejected"]
FRAMEWORK_REVIEW_STATUSES = ["pending", "accepted", "challenged", "rejected"]
PORTAL_ACCESS_STATUSES = ["unknown", "registered", "needs_registration", "blocked", "retired"]
DOCUMENT_RETRIEVAL_STATUSES = ["linked", "manual_required", "retrieved", "review_required", "archived"]


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
    latest_snapshots = latest_snapshot_by_source(
        list(session.exec(select(SourceCheckSnapshot).order_by(col(SourceCheckSnapshot.checked_at).desc()).limit(200)))
    )
    profiles = list(session.exec(select(CustomerWatchProfile).order_by(col(CustomerWatchProfile.profile_name))))
    platforms = list(session.exec(select(ProcurementPlatform).order_by(col(ProcurementPlatform.name))))
    portal_instances = list(session.exec(select(BuyerPortalInstance).order_by(col(BuyerPortalInstance.portal_name))))
    return {
        "customers": customers,
        "business_units": business_units,
        "sources": sources,
        "profiles": profiles,
        "platforms": platforms,
        "portal_instances": portal_instances,
        "customer_map": {customer.id: customer for customer in customers},
        "business_unit_map": {unit.id: unit for unit in business_units},
        "source_map": {source.id: source for source in sources},
        "source_readiness_map": {
            source.id: source_readiness_for_source(source, latest_snapshots.get(source.id or 0)) for source in sources
        },
        "profile_map": {profile.id: profile for profile in profiles},
        "platform_map": {platform.id: platform for platform in platforms},
        "portal_instance_map": {portal.id: portal for portal in portal_instances},
        "watch_profile_suggestions": watch_profile_suggestion_cards(),
        "framework_source_types": FRAMEWORK_SOURCE_TYPES,
        "framework_opportunity_statuses": FRAMEWORK_OPPORTUNITY_STATUSES,
        "framework_review_statuses": FRAMEWORK_REVIEW_STATUSES,
        "portal_access_statuses": PORTAL_ACCESS_STATUSES,
        "document_retrieval_statuses": DOCUMENT_RETRIEVAL_STATUSES,
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


# Finding B3: `update_cost_line` rebuilds the stored row from a blank `CostLine`
# candidate, so every field the cost form does not post was silently overwritten with
# the model default. `activity_id` is the one that matters: an activity link recorded
# by an import or by seeded data (proven: 1 before an edit, None after) was cleared by
# an unrelated edit, and because the free-text `activity` survived,
# `cost_validation_warnings` never raised the missing-activity-link flag. The loss was
# invisible in the UI and in the claim pack. These fields are carried over untouched.
COST_FIELDS_NOT_ON_THE_COST_FORM = frozenset({"id", "activity_id"})


def resolve_people_time_gross(
    submitted_gross: float,
    hours: float,
    hourly_rate: float,
    days: float,
    day_rate: float,
    existing: CostLine | None = None,
) -> float:
    """Keep a people-time gross cost consistent with the hours and rates recorded on it.

    Finding B2: the old rule recalculated the gross only when the posted value was
    0, but the edit form pre-fills the stored gross, so on an edit it was never 0
    and the recalculation never ran. Changing 100 hours to 50 stored ``hours=50``
    beside ``gross_cost=4000``: a self-contradictory line that overstates the claim
    two-fold, with nothing on screen to show it.

    The rule below is explicit rather than a silent correction, and it still honours
    the deliberate "Gross cost override" the form offers:

    * nothing posted -> calculate from hours and rates (the create path, unchanged);
    * no stored line to compare against -> a posted figure is a deliberate override
      and is kept (the create path, unchanged);
    * on an edit, where the stored gross was itself the calculated figure for the
      stored hours and rates and the posted gross is still that same figure, the
      user did not retype it. It is a stale pre-fill, so it follows the new hours
      and rates;
    * anything else -> the posted figure differs from the calculation the stored
      row had, which is exactly what the override field is for, so it is kept.
    """
    calculated = calculate_people_time_gross(hours, hourly_rate, days, day_rate)
    if submitted_gross == 0:
        return calculated
    if existing is None:
        return submitted_gross
    stored_gross = round(float(existing.gross_cost or 0), 2)
    stored_calculated = calculate_people_time_gross(
        existing.hours,
        existing.hourly_rate,
        existing.days,
        existing.day_rate,
    )
    if round(submitted_gross, 2) == stored_gross == stored_calculated:
        return calculated
    return submitted_gross


def cost_line_from_form(
    form,
    project_id: int,
    errors: list[str],
    cost: CostLine | None = None,
    existing: CostLine | None = None,
) -> CostLine:
    # Finding B1 (ADR-0002 Ruling R3): `parse_float` is frozen and admits nan, inf and
    # underscore separators, which reached `CostLine` and then the qualifying amount,
    # the report totals and the CSV exports. Proven before this change: gross 1000 at
    # 500% stored a qualifying 5000; at -50% it stored -500; a gross of -1000 stored
    # -1000; 1e308 stored inf; and "nan" returned HTTP 500. These value fields now use
    # the bounded, finite parsers, so a nonsensical figure is refused at the form with
    # a message instead of being written to the claim.
    hours = parse_decimal_amount(form.get("hours"), "Hours", errors)
    hourly_rate = parse_money(form.get("hourly_rate"), "Hourly rate", errors)
    days = parse_decimal_amount(form.get("days"), "Days", errors)
    day_rate = parse_money(form.get("day_rate"), "Day rate", errors)
    gross_cost = parse_money(form.get("gross_cost"), "Gross cost", errors)
    cost_input_type = str(form.get("cost_input_type") or "direct_cost")
    if cost_input_type == "people_time":
        gross_cost = resolve_people_time_gross(gross_cost, hours, hourly_rate, days, day_rate, existing)
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
    # A percentage above 100 is now refused at the form. The stored-data flag
    # `apportionment_over_100` in `app/services.py` deliberately stays: rows already in
    # the database, and rows arriving through import, still have to be surfaced rather
    # than silently accepted.
    cost.apportionment_percentage = parse_percentage(
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


# Change history is read by Finance and by an external reviewer, so an audit summary
# must name the business record. `f"Deleted {model.__name__} {item_id}"` wrote the
# Python class name into the stored summary, which rendered as "Deleted BusinessUnit 7".
# The labels are read from the data-management dataset catalogue rather than copied, so
# the deletion wording and the export/cleanup wording cannot drift apart. Rows already
# written keep their old summary; only new events read this way.
DATASET_LABEL_BY_MODEL = {spec.model: spec.label for spec in DATASETS}


def deleted_record_summary(model, item_id: int) -> str:
    label = DATASET_LABEL_BY_MODEL.get(model, "")
    if not label:
        return f"Deleted record {item_id}"
    return f"Deleted {label.lower()} record {item_id}"


def delete_or_block(session: Session, model, item_id: int, redirect_path: str) -> RedirectResponse:
    item = session.get(model, item_id)
    if not item:
        return redirect(redirect_path)
    for child_model, child_field, label in DEPENDENCY_RULES.get(model, []):
        child = session.exec(select(child_model).where(child_field == item_id)).first()
        if child:
            error = quote(f"delete_blocked_{label.replace(' ', '_')}")
            return redirect(f"{redirect_path}?error={error}")
    delete_with_audit(session, item, deleted_record_summary(model, item_id))
    return redirect(redirect_path)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    projects = list(session.exec(select(RDProject).order_by(col(RDProject.project_title))))
    metrics = dashboard_metrics(session)
    companies = list(session.exec(select(Company).order_by(col(Company.company_name))))
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    setup_context = company_setup_context(companies, periods)
    signed_project_ids = {
        opinion.project_id
        for opinion in session.exec(
            select(CompetentProfessionalOpinion).where(CompetentProfessionalOpinion.signoff_status == "signed")
        )
    }
    strong_evidence_project_ids = {
        item.project_id for item in session.exec(select(EvidenceItem).where(EvidenceItem.strength == "strong"))
    }
    total_qualifying_costs = round(sum(cost.qualifying_amount or 0 for cost in session.exec(select(CostLine))), 2)
    framework_metrics = framework_intelligence_metrics(session)
    workflow = review_workflow_context(session, metrics, setup_context, framework_metrics)
    framework_signals = list(
        session.exec(select(RDECOpportunitySignal).order_by(col(RDECOpportunitySignal.created_at).desc()).limit(3))
    )
    recent_requirements = list(
        session.exec(select(ExtractedRequirement).order_by(col(ExtractedRequirement.created_at).desc()).limit(3))
    )
    opportunity_ids = {signal.opportunity_id for signal in framework_signals}
    opportunity_ids.update(requirement.opportunity_id for requirement in recent_requirements)
    opportunities = {
        opportunity.id: opportunity
        for opportunity in session.exec(select(FrameworkOpportunity))
        if opportunity.id in opportunity_ids
    }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        template_context(
            request,
            metrics=metrics,
            projects=projects,
            company_setup=setup_context,
            workflow=workflow,
            signed_project_count=len(signed_project_ids),
            strong_evidence_project_count=len(strong_evidence_project_ids),
            total_qualifying_costs=total_qualifying_costs,
            framework_metrics=framework_metrics,
            framework_signals=framework_signals,
            recent_requirements=recent_requirements,
            opportunity_map=opportunities,
        ),
    )


def data_management_response(
    request: Request,
    session: Session,
    *,
    error_message: str = "",
    import_preview: dict | None = None,
    import_payload: str = "",
    status_code: int = 200,
):
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "data_management.html",
        template_context(
            request,
            inventory=data_inventory(session),
            cleanup_items=cleanup_candidates(session),
            purge_scopes=purge_scope_cards(session),
            editable_areas=[
                {"label": "Company and accounting periods", "href": "/companies"},
                {"label": "Customers", "href": "/customers"},
                {"label": "Contracts", "href": "/contracts"},
                {"label": "Solutions", "href": "/solutions"},
                {"label": "R&D projects", "href": "/projects"},
                {"label": "Costs", "href": "/costs"},
                {"label": "Evidence", "href": "/evidence-index"},
                {"label": "Opportunities", "href": "/framework-intelligence/opportunities"},
            ],
            data_features={
                "import": settings.data_import_enabled,
                "export": settings.data_export_enabled,
                "cleanup": settings.data_cleanup_enabled,
                "purge": settings.data_purge_enabled,
            },
            error_message=error_message,
            import_preview=import_preview,
            import_payload=import_payload,
        ),
        status_code=status_code,
    )


@app.get("/data-management", response_class=HTMLResponse)
def data_management(request: Request, session: Session = Depends(get_session)):
    return data_management_response(request, session)


@app.post("/data-management/export")
async def export_data(request: Request, session: Session = Depends(get_session)):
    if not get_settings().data_export_enabled:
        return data_management_response(request, session, error_message="Exports are turned off by the administrator.", status_code=403)
    form = await request.form()
    dataset_keys = [str(value) for value in form.getlist("data_areas")]
    export_format = str(form.get("export_format") or "json")
    try:
        if export_format == "json":
            content = json_export_bytes(session, dataset_keys)
            media_type = "application/json"
            filename = "rdec-hub-data.json"
        elif export_format == "csv_zip":
            content = csv_export_zip_bytes(session, dataset_keys)
            media_type = "application/zip"
            filename = "rdec-hub-csv-files.zip"
        else:
            raise DataOperationError("Choose JSON backup or CSV files.")
    except DataOperationError as exc:
        return data_management_response(request, session, error_message=str(exc), status_code=400)
    log_event(
        session,
        entity_type="DataExport",
        entity_id=None,
        action="export",
        summary=f"Exported {len(dataset_keys)} selected data areas as {export_format}",
        after=json.dumps({"data_areas": dataset_keys, "format": export_format}, sort_keys=True),
    )
    session.commit()
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/data-management/import/preview", response_class=HTMLResponse)
async def preview_data_import(request: Request, session: Session = Depends(get_session)):
    if not get_settings().data_import_enabled:
        return data_management_response(request, session, error_message="Imports are turned off by the administrator.", status_code=403)
    form = await request.form()
    upload = form.get("import_file")
    if not upload or not hasattr(upload, "read"):
        return data_management_response(request, session, error_message="Choose a JSON or CSV file to preview.", status_code=400)
    try:
        content = await upload.read()
        datasets = parse_import_file(
            str(getattr(upload, "filename", "") or ""),
            content,
            str(form.get("data_area") or ""),
        )
        plan = build_import_plan(session, datasets, str(form.get("import_mode") or "add_only"))
        payload = encode_import_payload(plan)
    except DataOperationError as exc:
        return data_management_response(request, session, error_message=str(exc), status_code=400)
    return data_management_response(request, session, import_preview=plan, import_payload=payload)


@app.post("/data-management/import/apply")
async def apply_data_import(request: Request, session: Session = Depends(get_session)):
    if not get_settings().data_import_enabled:
        return data_management_response(request, session, error_message="Imports are turned off by the administrator.", status_code=403)
    form = await request.form()
    try:
        mode, datasets = decode_import_payload(str(form.get("import_payload") or ""))
        result = apply_import(session, datasets, mode)
    except DataOperationError as exc:
        return data_management_response(request, session, error_message=str(exc), status_code=400)
    notice = quote(
        f"Import complete: {result['created']} added, {result['updated']} updated and {result['skipped']} unchanged."
    )
    return redirect(f"/data-management?notice={notice}")


@app.post("/data-management/cleanup")
async def cleanup_data(request: Request, session: Session = Depends(get_session)):
    if not get_settings().data_cleanup_enabled:
        return data_management_response(request, session, error_message="Cleanup is turned off by the administrator.", status_code=403)
    form = await request.form()
    if str(form.get("cleanup_confirmation") or "").strip() != "DELETE UNUSED":
        return data_management_response(request, session, error_message="Type DELETE UNUSED exactly to remove the selected records.", status_code=400)
    try:
        removed = delete_unused_records(session, [str(value) for value in form.getlist("cleanup_items")])
    except DataOperationError as exc:
        return data_management_response(request, session, error_message=str(exc), status_code=400)
    return redirect(f"/data-management?notice={quote(f'{removed} unused record(s) removed.')}")


@app.post("/data-management/purge")
async def purge_data(request: Request, session: Session = Depends(get_session)):
    if not get_settings().data_purge_enabled:
        return redirect("/data-management?error=purge_disabled")
    form = await request.form()
    if not parse_form_bool(form.get("backup_acknowledged")):
        return data_management_response(
            request,
            session,
            error_message="Confirm that the required backup has been downloaded before purging data.",
            status_code=400,
        )
    try:
        counts = purge_records(
            session,
            str(form.get("purge_scope") or ""),
            str(form.get("purge_confirmation") or ""),
        )
    except DataOperationError as exc:
        return data_management_response(request, session, error_message=str(exc), status_code=400)
    removed = sum(counts.values())
    return redirect(f"/data-management?notice={quote(f'Purge complete: {removed} working record(s) removed. Change history was preserved.')}")


@app.get("/final-review", response_class=HTMLResponse)
def final_review(request: Request, session: Session = Depends(get_session)):
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    readiness_by_period = bulk_aif_readiness(session, periods)
    reviews = [readiness_by_period[period.id or 0] for period in periods]
    return templates.TemplateResponse(
        request,
        "final_review.html",
        template_context(request, reviews=reviews),
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
    review_context = framework_opportunity_review_context(session, limit=6)
    return templates.TemplateResponse(
        request,
        "framework_intelligence.html",
        template_context(
            request,
            metrics=framework_intelligence_metrics(session),
            opportunities=opportunities,
            requirements=requirements,
            rdec_review_queue=review_context,
            opportunity_review_map=review_context["by_opportunity_id"],
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
        source_family=str(form.get("source_family") or "official_notice"),
        base_url=str(form.get("base_url") or query_url),
        query_url=query_url,
        official=parse_form_bool(form.get("official")) if form.get("official") is not None else True,
        active=parse_form_bool(form.get("active")) if form.get("active") is not None else True,
        review_frequency=str(form.get("review_frequency") or "manual"),
        coverage=str(form.get("coverage") or ""),
        auth_model=str(form.get("auth_model") or "none"),
        data_format=str(form.get("data_format") or source_type),
        dedupe_strategy=str(form.get("dedupe_strategy") or "ocid_or_reference"),
        change_tracking_enabled=(
            parse_form_bool(form.get("change_tracking_enabled"))
            if form.get("change_tracking_enabled") is not None
            else True
        ),
        requires_human_approval=parse_form_bool(form.get("requires_human_approval")),
        connector_status=str(form.get("connector_status") or "configured"),
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
    source.source_family = str(form.get("source_family") or "official_notice")
    source.base_url = str(form.get("base_url") or query_url)
    source.query_url = query_url
    source.official = parse_form_bool(form.get("official"))
    source.active = parse_form_bool(form.get("active"))
    source.review_frequency = str(form.get("review_frequency") or "manual")
    source.coverage = str(form.get("coverage") or "")
    source.auth_model = str(form.get("auth_model") or "none")
    source.data_format = str(form.get("data_format") or source_type)
    source.dedupe_strategy = str(form.get("dedupe_strategy") or "ocid_or_reference")
    source.change_tracking_enabled = parse_form_bool(form.get("change_tracking_enabled"))
    source.requires_human_approval = parse_form_bool(form.get("requires_human_approval"))
    source.connector_status = str(form.get("connector_status") or "configured")
    source.notes = str(form.get("notes") or "")
    save_with_audit(session, source, "update", f"Updated framework source {source.name}", before_snapshot)
    return redirect("/framework-intelligence/sources")


@app.post("/framework-intelligence/sources/{source_id}/delete")
def delete_framework_source(source_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, FrameworkSource, source_id, "/framework-intelligence/sources")


@app.post("/framework-intelligence/sources/{source_id}/check")
def check_framework_source(source_id: int, session: Session = Depends(get_session)):
    try:
        run_single_source_check(session, source_id)
    except ValueError as exc:
        return validation_error_response([str(exc)], "/framework-intelligence/source-catalogue")
    return redirect("/framework-intelligence/source-changes")


@app.get("/framework-intelligence/source-catalogue", response_class=HTMLResponse)
def framework_source_catalogue_page(request: Request, session: Session = Depends(get_session)):
    snapshots = list(
        session.exec(select(SourceCheckSnapshot).order_by(col(SourceCheckSnapshot.checked_at).desc()).limit(50))
    )
    return templates.TemplateResponse(
        request,
        "framework_source_catalogue.html",
        template_context(request, snapshots=snapshots, **framework_reference_context(session)),
    )


@app.get("/framework-intelligence/source-changes", response_class=HTMLResponse)
def framework_source_changes(request: Request, session: Session = Depends(get_session)):
    snapshots = list(
        session.exec(select(SourceCheckSnapshot).order_by(col(SourceCheckSnapshot.checked_at).desc()).limit(200))
    )
    return templates.TemplateResponse(
        request,
        "framework_source_changes.html",
        template_context(request, snapshots=snapshots, **framework_reference_context(session)),
    )


@app.get("/framework-intelligence/portal-platforms", response_class=HTMLResponse)
def portal_platforms(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "framework_portal_platforms.html",
        template_context(request, **framework_reference_context(session)),
    )


@app.post("/framework-intelligence/portal-instances")
async def create_portal_instance(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors: list[str] = []
    platform_id = parse_required_int(form.get("platform_id"), "Platform", errors)
    customer_id = parse_optional_int(form.get("customer_id"), "Customer", errors)
    business_unit_id = parse_optional_int(form.get("business_unit_id"), "Business unit", errors)
    access_status = parse_enum(
        form.get("access_status"),
        PORTAL_ACCESS_STATUSES,
        "Access status",
        errors,
        "unknown",
    )
    portal_name = str(form.get("portal_name") or "").strip()
    if not portal_name:
        errors.append("Portal name is required.")
    if errors:
        return validation_error_response(errors, "/framework-intelligence/portal-platforms")
    portal = BuyerPortalInstance(
        portal_name=portal_name,
        platform_id=platform_id,
        customer_id=customer_id,
        business_unit_id=business_unit_id,
        portal_url=str(form.get("portal_url") or ""),
        account_reference=str(form.get("account_reference") or ""),
        access_status=access_status,
        document_retrieval_mode=str(form.get("document_retrieval_mode") or "manual"),
        notes=str(form.get("notes") or ""),
    )
    save_with_audit(session, portal, "create", f"Created buyer portal instance {portal.portal_name}")
    return redirect("/framework-intelligence/portal-platforms")


@app.post("/framework-intelligence/portal-instances/{portal_id}/update")
async def update_portal_instance(portal_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    portal = session.get(BuyerPortalInstance, portal_id)
    if not portal:
        return redirect("/framework-intelligence/portal-platforms")
    errors: list[str] = []
    platform_id = parse_required_int(form.get("platform_id"), "Platform", errors)
    customer_id = parse_optional_int(form.get("customer_id"), "Customer", errors)
    business_unit_id = parse_optional_int(form.get("business_unit_id"), "Business unit", errors)
    access_status = parse_enum(
        form.get("access_status"),
        PORTAL_ACCESS_STATUSES,
        "Access status",
        errors,
        "unknown",
    )
    portal_name = str(form.get("portal_name") or "").strip()
    if not portal_name:
        errors.append("Portal name is required.")
    if errors:
        return validation_error_response(errors, "/framework-intelligence/portal-platforms")
    before_snapshot = compact_snapshot(portal)
    portal.portal_name = portal_name
    portal.platform_id = platform_id
    portal.customer_id = customer_id
    portal.business_unit_id = business_unit_id
    portal.portal_url = str(form.get("portal_url") or "")
    portal.account_reference = str(form.get("account_reference") or "")
    portal.access_status = access_status
    portal.document_retrieval_mode = str(form.get("document_retrieval_mode") or "manual")
    portal.notes = str(form.get("notes") or "")
    save_with_audit(session, portal, "update", f"Updated buyer portal instance {portal.portal_name}", before_snapshot)
    return redirect("/framework-intelligence/portal-platforms")


@app.post("/framework-intelligence/portal-instances/{portal_id}/delete")
def delete_portal_instance(portal_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, BuyerPortalInstance, portal_id, "/framework-intelligence/portal-platforms")


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
    minimum_value = parse_money(form.get("minimum_value"), "Minimum value", errors)
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
    minimum_value = parse_money(form.get("minimum_value"), "Minimum value", errors)
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
def framework_opportunities(request: Request, status: str | None = None, session: Session = Depends(get_session)):
    opportunity_query = select(FrameworkOpportunity).order_by(col(FrameworkOpportunity.updated_at).desc())
    status_filter = status if status in FRAMEWORK_OPPORTUNITY_STATUSES else None
    if status_filter:
        opportunity_query = opportunity_query.where(FrameworkOpportunity.status == status_filter)
    opportunities = list(session.exec(opportunity_query.limit(200)))
    signals = list(session.exec(select(RDECOpportunitySignal)))
    documents = list(session.exec(select(OpportunityDocument)))
    review_context = framework_opportunity_review_context(session, limit=200)
    signal_counts: dict[int, int] = {}
    for signal in signals:
        signal_counts[signal.opportunity_id] = signal_counts.get(signal.opportunity_id, 0) + 1
    document_counts: dict[int, int] = {}
    for document in documents:
        document_counts[document.opportunity_id] = document_counts.get(document.opportunity_id, 0) + 1
    return templates.TemplateResponse(
        request,
        "framework_opportunities.html",
        template_context(
            request,
            opportunities=opportunities,
            signal_counts=signal_counts,
            document_counts=document_counts,
            opportunity_review_map=review_context["by_opportunity_id"],
            status_filter=status_filter,
            **framework_reference_context(session),
        ),
    )


@app.get("/framework-intelligence/opportunities/{opportunity_id}", response_class=HTMLResponse)
def framework_opportunity_workbench(opportunity_id: int, request: Request, session: Session = Depends(get_session)):
    opportunity = session.get(FrameworkOpportunity, opportunity_id)
    if not opportunity:
        return redirect("/framework-intelligence/opportunities")
    requirements = list(
        session.exec(
            select(ExtractedRequirement)
            .where(ExtractedRequirement.opportunity_id == opportunity_id)
            .order_by(col(ExtractedRequirement.created_at).desc())
        )
    )
    signals = list(
        session.exec(
            select(RDECOpportunitySignal)
            .where(RDECOpportunitySignal.opportunity_id == opportunity_id)
            .order_by(col(RDECOpportunitySignal.created_at).desc())
        )
    )
    documents = list(
        session.exec(
            select(OpportunityDocument)
            .where(OpportunityDocument.opportunity_id == opportunity_id)
            .order_by(col(OpportunityDocument.extracted_at).desc())
        )
    )
    quality_questions = list(
        session.exec(
            select(ExtractedQualityQuestion)
            .where(ExtractedQualityQuestion.opportunity_id == opportunity_id)
            .order_by(col(ExtractedQualityQuestion.created_at).desc())
        )
    )
    retrieval_runs = list(
        session.exec(
            select(PortalRetrievalRun)
            .where(PortalRetrievalRun.opportunity_id == opportunity_id)
            .order_by(col(PortalRetrievalRun.started_at).desc())
        )
    )
    review_context = framework_opportunity_review_context(session, limit=200)
    return templates.TemplateResponse(
        request,
        "framework_opportunity_workbench.html",
        template_context(
            request,
            opportunity=opportunity,
            review=review_context["by_opportunity_id"].get(opportunity.id),
            requirements=requirements,
            signals=signals,
            documents=documents,
            quality_questions=quality_questions,
            retrieval_runs=retrieval_runs,
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
    return redirect(safe_framework_return_path(form.get("return_to"), "/framework-intelligence/opportunities"))


@app.post("/framework-intelligence/opportunities/{opportunity_id}/delete")
def delete_framework_opportunity(opportunity_id: int, session: Session = Depends(get_session)):
    return delete_or_block(session, FrameworkOpportunity, opportunity_id, "/framework-intelligence/opportunities")


@app.get("/framework-intelligence/opportunities/{opportunity_id}/documents", response_class=HTMLResponse)
def opportunity_documents(opportunity_id: int, request: Request, session: Session = Depends(get_session)):
    opportunity = session.get(FrameworkOpportunity, opportunity_id)
    if not opportunity:
        return redirect("/framework-intelligence/opportunities")
    documents = list(
        session.exec(
            select(OpportunityDocument)
            .where(OpportunityDocument.opportunity_id == opportunity_id)
            .order_by(col(OpportunityDocument.extracted_at).desc())
        )
    )
    quality_questions = list(
        session.exec(
            select(ExtractedQualityQuestion)
            .where(ExtractedQualityQuestion.opportunity_id == opportunity_id)
            .order_by(col(ExtractedQualityQuestion.created_at).desc())
        )
    )
    retrieval_runs = list(
        session.exec(
            select(PortalRetrievalRun)
            .where(PortalRetrievalRun.opportunity_id == opportunity_id)
            .order_by(col(PortalRetrievalRun.started_at).desc())
        )
    )
    return templates.TemplateResponse(
        request,
        "framework_opportunity_documents.html",
        template_context(
            request,
            opportunity=opportunity,
            documents=documents,
            quality_questions=quality_questions,
            retrieval_runs=retrieval_runs,
            **framework_reference_context(session),
        ),
    )


@app.post("/framework-intelligence/opportunities/{opportunity_id}/documents")
async def create_opportunity_document(opportunity_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    opportunity = session.get(FrameworkOpportunity, opportunity_id)
    if not opportunity:
        return redirect("/framework-intelligence/opportunities")
    errors: list[str] = []
    title = str(form.get("title") or "").strip()
    if not title:
        errors.append("Document title is required.")
    retrieval_status = parse_enum(
        form.get("retrieval_status"),
        DOCUMENT_RETRIEVAL_STATUSES,
        "Retrieval status",
        errors,
        "linked",
    )
    if errors:
        return validation_error_response(errors, f"/framework-intelligence/opportunities/{opportunity_id}/documents")
    document = OpportunityDocument(
        opportunity_id=opportunity_id,
        title=title,
        document_type=str(form.get("document_type") or "itt_document"),
        url_or_path=str(form.get("url_or_path") or ""),
        source_hash=str(form.get("source_hash") or ""),
        retrieval_status=retrieval_status,
        human_review_status="pending",
        platform_name=str(form.get("platform_name") or ""),
        content_summary=str(form.get("content_summary") or ""),
        notes=str(form.get("notes") or ""),
    )
    session.add(document)
    session.flush()
    log_event(
        session,
        entity_type="OpportunityDocument",
        entity_id=document.id,
        action="create",
        summary=f"Created opportunity document {document.title}",
        after=document,
    )
    extract_document_intelligence(session, opportunity, document, str(form.get("document_text") or ""))
    session.commit()
    return redirect(f"/framework-intelligence/opportunities/{opportunity_id}/documents")


@app.post("/framework-intelligence/opportunities/{opportunity_id}/retrieval-runs")
async def create_retrieval_run(opportunity_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    if not session.get(FrameworkOpportunity, opportunity_id):
        return redirect("/framework-intelligence/opportunities")
    errors: list[str] = []
    portal_instance_id = parse_optional_int(form.get("portal_instance_id"), "Portal instance", errors)
    if errors:
        return validation_error_response(errors, f"/framework-intelligence/opportunities/{opportunity_id}/documents")
    create_portal_retrieval_run(
        session,
        opportunity_id,
        portal_instance_id=portal_instance_id,
        notes=str(form.get("notes") or ""),
    )
    return redirect(f"/framework-intelligence/opportunities/{opportunity_id}/documents")


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
    return redirect(safe_framework_return_path(form.get("return_to"), "/framework-intelligence/requirements"))


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
    return redirect(safe_framework_return_path(form.get("return_to"), "/framework-intelligence/requirements"))


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
    setup_context = company_setup_context(companies, periods)
    return templates.TemplateResponse(
        request,
        "companies.html",
        template_context(request, companies=companies, periods=periods, company_setup=setup_context),
    )


@app.post("/companies")
async def create_company(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    payload = normalize_company_payload(form)
    errors = validate_company_payload(payload)
    if errors:
        return validation_error_response(errors, "/companies")
    company = Company(
        company_name=payload["company_name"],
        utr=payload["utr"],
        paye_reference=payload["paye_reference"],
        vat_number=payload["vat_number"],
        sic_code=payload["sic_code"],
        registered_office_country=payload["registered_office_country"],
        registered_office_region=payload["registered_office_region"],
        northern_ireland_registered=parse_form_bool(form.get("northern_ireland_registered")),
        senior_rd_contact_name=payload["senior_rd_contact_name"],
        senior_rd_contact_role=payload["senior_rd_contact_role"],
        senior_rd_contact_email=payload["senior_rd_contact_email"],
        senior_rd_contact_phone=payload["senior_rd_contact_phone"],
        agent_name=payload["agent_name"],
        agent_reference=payload["agent_reference"],
        agent_email=payload["agent_email"],
        agent_phone=payload["agent_phone"],
        agent_role=payload["agent_role"],
    )
    save_with_audit(session, company, "create", f"Created company {company.company_name}")
    return redirect("/companies")


@app.post("/companies/{company_id}/update")
async def update_company(company_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    company = session.get(Company, company_id)
    if not company:
        return redirect("/companies")
    payload = normalize_company_payload(form)
    errors = validate_company_payload(payload)
    if errors:
        return validation_error_response(errors, "/companies")
    before_snapshot = compact_snapshot(company)
    company.company_name = payload["company_name"]
    company.utr = payload["utr"]
    company.paye_reference = payload["paye_reference"]
    company.vat_number = payload["vat_number"]
    company.sic_code = payload["sic_code"]
    company.registered_office_country = payload["registered_office_country"]
    company.registered_office_region = payload["registered_office_region"]
    company.northern_ireland_registered = parse_form_bool(form.get("northern_ireland_registered"))
    company.senior_rd_contact_name = payload["senior_rd_contact_name"]
    company.senior_rd_contact_role = payload["senior_rd_contact_role"]
    company.senior_rd_contact_email = payload["senior_rd_contact_email"]
    company.senior_rd_contact_phone = payload["senior_rd_contact_phone"]
    company.agent_name = payload["agent_name"]
    company.agent_reference = payload["agent_reference"]
    company.agent_email = payload["agent_email"]
    company.agent_phone = payload["agent_phone"]
    company.agent_role = payload["agent_role"]
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
    claim_notification_submitted = parse_form_bool(form.get("claim_notification_submitted"))
    errors.extend(
        validate_accounting_period_dates(
            start_date,
            end_date,
            period_of_account_start,
            period_of_account_end,
            claim_notification_submitted,
            claim_notification_date,
        )
    )
    if errors:
        return validation_error_response(errors, "/companies")
    label = str(form.get("label") or "").strip() or accounting_period_label(start_date, end_date)
    period = AccountingPeriod(
        company_id=company_id,
        label=label,
        start_date=start_date,
        end_date=end_date,
        period_of_account_start=period_of_account_start,
        period_of_account_end=period_of_account_end,
        claim_notification_submitted=claim_notification_submitted,
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
    claim_notification_submitted = parse_form_bool(form.get("claim_notification_submitted"))
    errors.extend(
        validate_accounting_period_dates(
            start_date,
            end_date,
            period_of_account_start,
            period_of_account_end,
            claim_notification_submitted,
            claim_notification_date,
        )
    )
    if errors:
        return validation_error_response(errors, "/companies")
    before_snapshot = compact_snapshot(period)
    period.company_id = company_id
    period.label = str(form.get("label") or "").strip() or accounting_period_label(start_date, end_date)
    period.start_date = start_date
    period.end_date = end_date
    period.period_of_account_start = period_of_account_start
    period.period_of_account_end = period_of_account_end
    period.claim_notification_submitted = claim_notification_submitted
    period.claim_notification_date = claim_notification_date
    period.prior_claim_within_3_years = parse_form_bool(form.get("prior_claim_within_3_years"))
    period.scheme_notes = str(form.get("scheme_notes") or "")
    save_with_audit(session, period, "update", f"Updated accounting period {period.label}", before_snapshot)
    return redirect("/companies")


@app.post("/accounting-periods/{period_id}/delete")
def delete_accounting_period(period_id: int, session: Session = Depends(get_session)):
    linked_project = session.exec(select(RDProject).where(RDProject.accounting_period_id == period_id)).first()
    if linked_project:
        return redirect(f"/companies?error={quote('delete_blocked_R&D_projects')}")
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
    # Finding E7-3: this route used to call session.add and session.commit directly, so
    # creating or renaming a business unit left no record in change history. The business
    # unit is the organisational key that customers, watch profiles, opportunities, buyer
    # portals and intelligence reports hang off, so an unrecorded rename silently moves
    # every one of them.
    save_with_audit(session, unit, "create", f"Created business unit {unit.name}")
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
    # The snapshot is taken before the fields change: a rename record that cannot say
    # what the unit used to be called is not a change record.
    before_snapshot = compact_snapshot(unit)
    unit.name = str(form.get("name") or "")
    unit.parent_id = parent_id
    unit.description = str(form.get("description") or "")
    unit.active = parse_form_bool(form.get("active"))
    save_with_audit(session, unit, "update", f"Updated business unit {unit.name}", before_snapshot)
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
    scores = bulk_project_scores(session, projects)
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
            error = quote(f"delete_blocked_{label.replace(' ', '_')}")
            return redirect(f"/projects?error={error}")
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
    # get_project_context raises for a project id that is not in the database, which reaches the
    # user as HTTP 500 on an ordinary journey: delete a project, press Back, click any of its tabs.
    # Every project and claim-period page below therefore looks the record up first and redirects
    # to the list, the same way the delete routes do (delete_or_block).
    if not session.get(RDProject, project_id):
        return redirect("/projects")
    context = get_project_context(session, project_id)
    score = calculate_project_score(session, project_id)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        template_context(request, context=context, score=score),
    )


@app.get("/projects/{project_id}/assessment", response_class=HTMLResponse)
def project_assessment(project_id: int, request: Request, session: Session = Depends(get_session)):
    if not session.get(RDProject, project_id):
        return redirect("/projects")
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
    if not session.get(RDProject, project_id):
        return redirect("/projects")
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
    # The parent is checked before the child row is built, not after. These add routes used to
    # save first and fail later, which committed a cost line, an evidence item, an opinion or a
    # submission status pointing at a project or period that does not exist - an orphan that no
    # screen can reach but that still counts towards claim totals and change history.
    if not session.get(RDProject, project_id):
        return redirect("/projects")
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
    candidate = cost_line_from_form(
        form,
        cost.project_id,
        errors,
        CostLine(project_id=cost.project_id),
        existing=cost,
    )
    if errors:
        return validation_error_response(errors, f"/projects/{cost.project_id}/costs")
    for field_name in CostLine.model_fields:
        if field_name in COST_FIELDS_NOT_ON_THE_COST_FORM:
            continue
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
    if not session.get(RDProject, project_id):
        return redirect("/projects")
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
    if not session.get(RDProject, project_id):
        return redirect("/projects")
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
    if not session.get(RDProject, project_id):
        return redirect("/projects")
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
    if not session.get(RDProject, project_id):
        return redirect("/projects")
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
    # Guarded ahead of the markdown build, which reads the project context itself, so the
    # ?format=md download path is covered too.
    if not session.get(RDProject, project_id):
        return redirect("/projects")
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
    # Accounting periods are managed from the companies page, so that is the list to fall back to
    # (the same target update_accounting_period and delete_accounting_period already use).
    if not session.get(AccountingPeriod, period_id):
        return redirect("/companies")
    readiness = aif_readiness_for_period(session, period_id)
    return templates.TemplateResponse(
        request,
        "claim_period_readiness.html",
        template_context(request, readiness=readiness),
    )


@app.post("/claim-periods/{period_id}/readiness")
async def update_claim_period_submission(period_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    if not session.get(AccountingPeriod, period_id):
        return redirect("/companies")
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
    if not session.get(AccountingPeriod, period_id):
        return redirect("/companies")
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
