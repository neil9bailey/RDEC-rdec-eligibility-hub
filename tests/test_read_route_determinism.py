"""ADR-0006: no HTTP GET writes, and a rendered document is a function of the recorded facts.

Escalation 6, runtime-proven before the fix: the claim-period pack listed an entitlement note
only where an ``EntitlementAssessment`` row existed, and the row was created by the render
itself. Contexts are built for every project *before* any score is computed, so on a first
render the note was absent and on a second render, from identical data, it was present. The
project memo had the same shape, printing ``Not assessed`` on a first render only.

A document offered as claim evidence whose content is a function of render history rather than
of the recorded facts is not evidence, so the determinism tests below are the point of the ADR.
They are written to fail on the pre-fix tree; a determinism test that passes before the fix is
testing nothing.

The fixture builds its own dataset with no ``EntitlementAssessment`` anywhere and never renders
before the assertion. ``seed_demo_data`` calls ``sync_entitlement_for_project`` for every seeded
project, so the shared seeded fixture pre-satisfies the very condition under test and would make
all of this vacuous.
"""

from __future__ import annotations

from datetime import date
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func
from sqlalchemy import select as sa_select
from sqlmodel import Session, SQLModel, select

import app.database as database
import app.main as main
from app.models import (
    AccountingPeriod,
    AuditEvent,
    Company,
    Contract,
    CostLine,
    Customer,
    EntitlementAssessment,
    EvidenceItem,
    RDProject,
    Solution,
)


PROJECT_COUNT = 3

GENERATED_AT_LINE = re.compile(r"^\*\*Generated at:\*\*.*$", re.MULTILINE)

RESOLVED_LABEL = "(resolved from current project facts; no assessment recorded yet)"
RECORDED_LABEL = "(recorded assessment)"

# GET routes this module cannot call, each with the reason, so every exclusion is visible in the
# source rather than implied by absence (ADR-0006 D5.6).
UNCALLABLE_GET_ROUTES = {
    "/docs": "FastAPI-generated documentation, not an app.main handler",
    "/docs/oauth2-redirect": "FastAPI-generated documentation, not an app.main handler",
    "/redoc": "FastAPI-generated documentation, not an app.main handler",
    "/openapi.json": "FastAPI-generated schema, not an app.main handler",
    "/framework-intelligence/opportunities/{opportunity_id}": "no opportunity identifier in this dataset",
    "/framework-intelligence/opportunities/{opportunity_id}/documents": "no opportunity identifier in this dataset",
    "/framework-intelligence/reports/{report_id}": "no intelligence report identifier in this dataset",
}


def build_dataset(session: Session) -> tuple[int, list[int]]:
    """A period with projects, cost lines, and deliberately no entitlement assessments."""
    company = Company(company_name="Determinism Services Ltd", utr="1234567890")
    session.add(company)
    session.commit()
    session.refresh(company)

    period = AccountingPeriod(
        company_id=company.id or 0,
        label="FY2025/26",
        start_date=date(2025, 4, 1),
        end_date=date(2026, 3, 31),
        period_of_account_start=date(2025, 4, 1),
        period_of_account_end=date(2026, 3, 31),
    )
    session.add(period)
    session.commit()
    session.refresh(period)

    customer = Customer(
        customer_name="Determinism Transport Authority",
        sector="Public sector transport",
        customer_type="public sector body",
    )
    session.add(customer)
    session.commit()
    session.refresh(customer)

    contract = Contract(contract_name="Determinism Framework", customer_id=customer.id or 0)
    session.add(contract)
    session.commit()
    session.refresh(contract)

    projects: list[RDProject] = []
    for index in range(PROJECT_COUNT):
        solution = Solution(
            solution_name=f"Determinism solution {index}",
            customer_id=customer.id or 0,
            contract_id=contract.id,
        )
        session.add(solution)
        session.commit()
        session.refresh(solution)
        project = RDProject(
            solution_id=solution.id or 0,
            accounting_period_id=period.id,
            project_title=f"Determinism project {index}",
            scientific_or_technological_uncertainties="Uncertainty recorded for this dataset.",
            advance_sought="Advance recorded for this dataset.",
            rd_start_date=date(2025, 4, 1),
            rd_end_date=date(2026, 3, 31),
            company_role="framework supplier",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        session.add(
            CostLine(
                project_id=project.id or 0,
                activity="Prototype investigation",
                cost_category="staff",
                person_or_supplier_name="Determinism team",
                gross_cost=10000.0,
                apportionment_percentage=50.0,
                qualifying_amount=5000.0,
                paid_status="paid",
                uk_or_overseas="UK",
            )
        )
        session.commit()
        projects.append(project)

    assert not list(session.exec(select(EntitlementAssessment))), "fixture must start unassessed"
    return int(period.id or 0), [int(project.id or 0) for project in projects]


@pytest.fixture(autouse=True)
def _the_application_carries_no_session_override():
    """Every proof in this module is "no row was created". An override makes that free.

    This module deliberately does not install a ``get_session`` override: it rebinds the
    engine and lets the real dependency run, so the rows it counts are the rows the
    application actually wrote. That makes it the victim, not the source, of the known
    ``client_for`` leak - a module that clears ``app.dependency_overrides`` when the *next*
    client is built leaves its last test's override bound to a session that is already torn
    down, and a plain ``TestClient(main.app)`` built afterwards writes into that dead session.
    Writes then vanish and every assertion here passes for the wrong reason.

    So the precondition is asserted rather than silently repaired: repairing it would hide the
    leak in whichever module caused it, and passing without it would manufacture conformance.
    The teardown clear is the pattern this module owes the next one, and holds even if a future
    test here installs an override.
    """
    leaked = sorted(getattr(key, "__name__", repr(key)) for key in main.app.dependency_overrides)
    assert not leaked, (
        "a previous test module left dependency overrides installed on app.main.app "
        f"({leaked}); every 'no row was created' assertion in this module would pass "
        "vacuously because the write went to a torn-down session. Clear overrides in an "
        "autouse teardown, not when the next client is built."
    )
    yield
    main.app.dependency_overrides.clear()


@pytest.fixture()
def unrendered_hub(tmp_path):
    """The real app on a throwaway database that no render has touched yet."""
    engine = database.make_engine(f"sqlite:///{(tmp_path / 'determinism.db').as_posix()}")
    original_database_engine = database.engine
    original_main_engine = main.engine
    database.engine = engine
    main.engine = engine
    try:
        with TestClient(main.app) as client:
            with Session(engine) as session:
                period_id, project_ids = build_dataset(session)
            yield client, engine, period_id, project_ids
    finally:
        database.engine = original_database_engine
        main.engine = original_main_engine
        engine.dispose()


def mask_generated_at(body: str) -> str:
    """Mask only the timestamp line. Everything else must be reproducible."""
    return GENERATED_AT_LINE.sub("**Generated at:** <masked>", body)


def count_commits(engine, action):
    commits = 0

    def on_commit(_connection):
        nonlocal commits
        commits += 1

    event.listen(engine, "commit", on_commit)
    try:
        action()
    finally:
        event.remove(engine, "commit", on_commit)
    return commits


def markdown_section(body: str, heading: str) -> list[str]:
    """The bullet lines under one heading.

    Scoped deliberately: a project title also prefixes lines in the project list, the readiness
    matrix and the evidence gaps, so an unscoped search for the title would count those too and
    could report an entitlement note that is not there.
    """
    lines = body.splitlines()
    assert heading in lines, f"heading {heading!r} not found"
    start = lines.index(heading) + 1
    section: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        section.append(line)
    return [line for line in section if line.startswith("- ")]


def entitlement_notes(body: str) -> list[str]:
    return markdown_section(body, "## Contracted-out / public sector entitlement notes")


def assessment_rows(engine) -> list[EntitlementAssessment]:
    with Session(engine) as session:
        return list(session.exec(select(EntitlementAssessment)))


def clear_assessments(engine) -> None:
    """Put the database back to genuinely unassessed.

    Only the *first* route to write creates the rows; every route measured after it then finds
    them already there and reports a false zero. Measured on the pre-fix tree, the whole route
    sweep reported commits for one URL alone, because that URL had assessed all three projects
    for everything that followed. Clearing between measurements is what makes the per-route
    assertion mean anything.
    """
    with Session(engine) as session:
        for row in session.exec(select(EntitlementAssessment)):
            session.delete(row)
        session.commit()


def callable_get_urls(period_id: int, project_ids: list[int]) -> list[str]:
    project_id = project_ids[0]
    substitutions = {"{project_id}": str(project_id), "{period_id}": str(period_id)}
    urls: list[str] = []
    for route in main.app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if "GET" not in methods or not path.startswith("/"):
            continue
        if path in UNCALLABLE_GET_ROUTES:
            continue
        url = path
        for placeholder, value in substitutions.items():
            url = url.replace(placeholder, value)
        assert "{" not in url, f"unfilled path parameter in {path}; add it to UNCALLABLE_GET_ROUTES"
        urls.append(url)
    urls.extend(
        [
            f"/projects/{project_id}/report?format=md",
            f"/claim-periods/{period_id}/pack?format=md",
            "/evidence-index?format=md",
        ]
    )
    return sorted(set(urls))


def test_the_claim_period_pack_renders_identically_twice(unrendered_hub):
    """ADR-0006 D5.5. Fails on the pre-fix tree: the first render omits every entitlement note."""
    client, _, period_id, _ = unrendered_hub
    url = f"/claim-periods/{period_id}/pack"

    first = client.get(url)
    second = client.get(url)
    first_download = client.get(f"{url}?format=md")
    second_download = client.get(f"{url}?format=md")

    assert first.status_code == 200
    assert mask_generated_at(first.text) == mask_generated_at(second.text)
    assert mask_generated_at(first_download.text) == mask_generated_at(second_download.text)


def test_the_project_memo_renders_identically_twice(unrendered_hub):
    """ADR-0006 D5.5. Fails on the pre-fix tree: the first render prints ``Not assessed``."""
    client, _, _, project_ids = unrendered_hub
    url = f"/projects/{project_ids[0]}/report"

    first = client.get(url)
    second = client.get(url)
    first_download = client.get(f"{url}?format=md")
    second_download = client.get(f"{url}?format=md")

    assert first.status_code == 200
    assert mask_generated_at(first.text) == mask_generated_at(second.text)
    assert mask_generated_at(first_download.text) == mask_generated_at(second_download.text)


def test_no_get_route_commits(unrendered_hub):
    """ADR-0006 D1, the governing invariant, over every GET route the suite can call."""
    client, engine, period_id, project_ids = unrendered_hub
    urls = callable_get_urls(period_id, project_ids)
    assert len(urls) >= 20, f"route enumeration collapsed: {urls}"

    committed = {}
    statuses = {}
    for url in urls:
        clear_assessments(engine)
        commits = count_commits(engine, lambda url=url: statuses.__setitem__(url, client.get(url).status_code))
        if commits:
            committed[url] = commits

    assert committed == {}, f"GET routes issued COMMITs: {committed}"
    # A route that 500s reaches no write and would pass the assertion above for the wrong reason.
    assert all(status < 400 for status in statuses.values()), f"unexpected statuses: {statuses}"


def test_no_render_creates_an_entitlement_assessment(unrendered_hub):
    """ADR-0006 D3 and D4.4: rows come from a save, never from a page view, and never from a backfill."""
    client, engine, period_id, project_ids = unrendered_hub
    for url in callable_get_urls(period_id, project_ids):
        client.get(url)

    assert assessment_rows(engine) == []


def test_the_pack_reports_every_project_with_its_provenance(unrendered_hub):
    """ADR-0006 D4.1: exactly one note per project, labelled with where the position came from."""
    client, engine, period_id, project_ids = unrendered_hub

    body = client.get(f"/claim-periods/{period_id}/pack?format=md").text

    with Session(engine) as session:
        titles = [
            session.get(RDProject, project_id).project_title for project_id in project_ids
        ]
    notes = entitlement_notes(body)
    assert len(notes) == len(titles), f"expected one note per project, got {notes}"
    for title in titles:
        matching = [line for line in notes if line.startswith(f"- {title}: ")]
        assert len(matching) == 1, f"expected one entitlement note for {title}, got {matching}"
        assert RESOLVED_LABEL in matching[0]
        assert RECORDED_LABEL not in matching[0]
    assert "Requires competent professional and tax review." in body


def test_the_memo_records_whether_an_assessment_is_stored(unrendered_hub):
    """ADR-0006 D4.2: the ``Not assessed`` literal is gone and provenance is stated instead."""
    client, _, _, project_ids = unrendered_hub

    body = client.get(f"/projects/{project_ids[0]}/report?format=md").text

    assert "- Assessment recorded: no - resolved from current project facts" in body
    assert "Not assessed" not in body
    assert "- Status: " in body
    assert "- Rationale: " in body
    assert "Requires competent professional and tax review." in body


def test_saving_an_assessment_creates_exactly_one_row_and_one_audit_event(unrendered_hub):
    """ADR-0006 D3: the record's genesis moves to the save, and the label follows it."""
    client, engine, period_id, project_ids = unrendered_hub
    project_id = project_ids[0]
    with Session(engine) as session:
        audits_before = len(list(session.exec(select(AuditEvent))))

    response = client.post(
        f"/projects/{project_id}/assessment",
        data={
            "accounting_period_id": str(period_id),
            "outcome": "unresolved",
            "company_role": "framework supplier",
            "advance_sought": "Advance recorded for this dataset.",
            "scientific_or_technological_uncertainties": "Uncertainty recorded for this dataset.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    rows = assessment_rows(engine)
    assert len(rows) == 1
    assert rows[0].project_id == project_id
    with Session(engine) as session:
        audit_events = list(session.exec(select(AuditEvent)))
        entitlement_events = [
            item for item in audit_events if item.entity_type == "EntitlementAssessment"
        ]
        title = session.get(RDProject, project_id).project_title
    assert len(entitlement_events) == 1
    assert len(audit_events) == audits_before + 2  # the project update and the assessment

    body = client.get(f"/claim-periods/{period_id}/pack?format=md").text
    notes = entitlement_notes(body)
    assert len(notes) == PROJECT_COUNT
    saved_note = [line for line in notes if line.startswith(f"- {title}: ")]
    assert len(saved_note) == 1
    assert RECORDED_LABEL in saved_note[0]
    assert sum(RESOLVED_LABEL in line for line in notes) == PROJECT_COUNT - 1


# --------------------------------------------------------------------------------------
# P3-ADR0006-NO-UNINTENDED-WRITE
#
# ADR-0006 Verification item 5, amendment A2, property P3: "no unintended write is added".
#
# Item 5 used to read `grep -n "sync=" app/main.py app/reports.py` shows sync=False "nowhere
# on a POST path". Raised at G3 as a contradiction, because sync=False does appear on two POST
# handlers. The EA ruled the clause over-broad and the code conformant: both sites are the htmx
# render branch, reached after save_with_audit has already committed the row the route exists
# to write. sync=True there would create an EntitlementAssessment plus an audit event as a side
# effect of saving a cost line, and - because the full-page branch of the same POST redirects to
# a GET that scores sync=False - one user action would produce two different database outcomes
# depending on whether htmx was active.
#
# A grep over a 2,400-line module cannot tell a handler's write phase from its render phase, and
# that distinction is the whole subject of the ADR. So the property is measured instead of spelt:
# take a row count of every table before and after, and read off what the request actually wrote.
#
# Status is not evidence here. Two routes in this application return a perfectly correct 303
# while committing an orphan row, so a status-only assertion would report conformance for a
# request that had written.
# --------------------------------------------------------------------------------------

HTMX = {"HX-Request": "true"}

VALID_COST_FORM = {
    "cost_input_type": "direct_cost",
    "cost_category": "consumables",
    "activity": "Rig calibration",
    "person_or_supplier_name": "Aerodyne Ltd",
    "gross_cost": "12000",
    "apportionment_percentage": "80",
    "paid_status": "paid",
    "uk_or_overseas": "UK",
    "connected_party_status": "unconnected",
    "evidence_link": "INV-2291",
}

VALID_EVIDENCE_FORM = {
    "source_system": "Manual upload / note",
    "source_reference": "SPIKE-114",
    "url_or_file_path": "//evidence/spike-114.md",
    "date_created": "2026-02-11",
    "evidence_type": "technical spike",
    "relevance_tag": "uncertainty",
    "strength": "strong",
    "notes": "Rig calibration spike write-up.",
}

#: The two POSTs the ruling is about: routes that write something *other* than an assessment.
NON_ASSESSMENT_POSTS = {
    "costs": ("costs", VALID_COST_FORM, CostLine),
    "evidence": ("evidence", VALID_EVIDENCE_FORM, EvidenceItem),
}

ASSESSMENTS = EntitlementAssessment.__tablename__
AUDIT = AuditEvent.__tablename__


def table_counts(engine) -> dict[str, int]:
    """One row count per mapped table.

    Counting every table rather than only ``EntitlementAssessment`` is deliberate: the write
    this property forbids arrives with an audit event attached, and a per-model count would
    report the assessment while missing the second row it drags into the history.
    """
    with Session(engine) as session:
        return {
            name: session.execute(sa_select(func.count()).select_from(table)).scalar_one()
            for name, table in sorted(SQLModel.metadata.tables.items())
        }


def rows_written(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Tables whose row count moved, and by how much. Empty means the request wrote nothing."""
    return {name: after[name] - before[name] for name in after if after[name] != before[name]}


def assert_htmx_branch(response) -> None:
    """The htmx branch really ran: a fragment, not a document, with the out-of-band panel."""
    assert response.status_code == 200, response.status_code
    body = response.text
    assert "<!doctype html>" not in body.lower(), "htmx POST returned a full page, not a partial"
    assert 'id="save-errors" hx-swap-oob="innerHTML"' in body
    assert 'id="eligibility-score"' in body


def assert_full_page_branch(response, expected_location: str) -> None:
    """The full-page branch really ran: the redirect, not the partial."""
    assert response.status_code == 303, response.status_code
    assert response.headers["location"] == expected_location
    assert 'hx-swap-oob' not in response.text


@pytest.mark.parametrize("route_key", sorted(NON_ASSESSMENT_POSTS))
@pytest.mark.parametrize("htmx", [True, False], ids=["htmx_branch", "full_page_branch"])
def test_a_cost_or_evidence_save_creates_no_entitlement_assessment(unrendered_hub, route_key, htmx):
    """ADR-0006 item 5 / P3, on both branches of both routes.

    The forbidden write and the required one are asserted together. "No assessment row" is
    trivially true of a request that saved nothing at all - a rejected form, a missing parent,
    a 500 - so the cost line or evidence item this route exists to store must be shown to have
    landed in the same breath.
    """
    client, engine, _, project_ids = unrendered_hub
    project_id = project_ids[0]
    segment, payload, child_model = NON_ASSESSMENT_POSTS[route_key]
    url = f"/projects/{project_id}/{segment}"
    child_table = child_model.__tablename__

    before = table_counts(engine)
    response = client.post(
        url,
        data=payload,
        headers=HTMX if htmx else {},
        follow_redirects=False,
    )
    after = table_counts(engine)

    if htmx:
        assert_htmx_branch(response)
    else:
        assert_full_page_branch(response, url)

    written = rows_written(before, after)
    # The intended write. Without this the property below is satisfied by doing nothing.
    assert written.get(child_table) == 1, f"the save did not store a row: {written}"
    # P3 itself.
    assert written.get(ASSESSMENTS, 0) == 0, (
        f"POST {url} created {written.get(ASSESSMENTS)} EntitlementAssessment row(s); "
        "an assessment's genesis must be a person saving one, not a panel that rendered"
    )
    # Nothing else moved either - an assessment arrives with an audit event, and a count
    # restricted to the assessments table would report the row but not its companion.
    assert written == {child_table: 1, AUDIT: 1}, f"unexpected rows written: {written}"


@pytest.mark.parametrize("route_key", sorted(NON_ASSESSMENT_POSTS))
def test_htmx_and_full_page_saves_leave_the_same_rows_behind(unrendered_hub, route_key):
    """One user action, one database outcome, whether or not htmx was active (ADR-0006 A1).

    This is the reason the ruling made ``sync=False`` on the render branch required rather than
    merely permitted. Comparing the two branches against each other, rather than each against a
    literal, is what makes the divergence itself the failure.
    """
    client, engine, _, project_ids = unrendered_hub
    project_id = project_ids[0]
    segment, payload, _ = NON_ASSESSMENT_POSTS[route_key]
    url = f"/projects/{project_id}/{segment}"

    before_full_page = table_counts(engine)
    full_page = client.post(url, data=payload, follow_redirects=False)
    after_full_page = table_counts(engine)
    partial = client.post(url, data=payload, headers=HTMX, follow_redirects=False)
    after_partial = table_counts(engine)

    assert_full_page_branch(full_page, url)
    assert_htmx_branch(partial)
    full_page_rows = rows_written(before_full_page, after_full_page)
    partial_rows = rows_written(after_full_page, after_partial)
    assert partial_rows == full_page_rows, (
        "the same save wrote different rows depending on the transport: "
        f"full page {full_page_rows}, htmx {partial_rows}"
    )
    assert ASSESSMENTS not in full_page_rows


def test_the_row_counter_sees_the_write_that_is_supposed_to_happen(unrendered_hub):
    """The control for P3: prove the measurement can tell the two POST classes apart.

    P3 asserts an absence, and an absence is also what a broken counter reports, and what a
    build with entitlement writing removed altogether would report. Both POST classes are
    therefore run through the identical apparatus in one test: the cost save must move no
    assessment row and the assessment save must move exactly one. If
    ``POST /projects/{id}/assessment`` ever stopped recording its assessment this goes red,
    which is precisely what P3 on its own could not do.
    """
    client, engine, period_id, project_ids = unrendered_hub
    project_id = project_ids[0]

    before_cost = table_counts(engine)
    client.post(f"/projects/{project_id}/costs", data=VALID_COST_FORM, follow_redirects=False)
    after_cost = table_counts(engine)

    saved = client.post(
        f"/projects/{project_id}/assessment",
        data={
            "accounting_period_id": str(period_id),
            "outcome": "unresolved",
            "company_role": "framework supplier",
            "advance_sought": "Advance recorded for this dataset.",
            "scientific_or_technological_uncertainties": "Uncertainty recorded for this dataset.",
        },
        follow_redirects=False,
    )
    after_assessment = table_counts(engine)

    assert saved.status_code == 303
    cost_rows = rows_written(before_cost, after_cost)
    assessment_rows_written = rows_written(after_cost, after_assessment)
    assert cost_rows.get(ASSESSMENTS, 0) == 0, cost_rows
    assert assessment_rows_written.get(ASSESSMENTS) == 1, (
        "the assessment route wrote no EntitlementAssessment, so P3's absence proves nothing: "
        f"{assessment_rows_written}"
    )
