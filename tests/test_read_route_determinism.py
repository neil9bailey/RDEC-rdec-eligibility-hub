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
from sqlalchemy import event
from sqlmodel import Session, select

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
