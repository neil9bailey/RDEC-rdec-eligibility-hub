"""G5b E6-REQUIRED-FIELDS: the five records that carry a name into the claim pack must have one.

``tests/test_required_customer_name.py`` closed this finding for ``Customer`` after a G5b
empty-body probe created a blank customer in the sponsor's live database. A review then found
eight more handlers of exactly the same shape -- ten in total across five entities -- each of
which accepted an empty required field and stored a blank record plus an audit row for it:

    POST /business-units                              and /business-units/{id}/update
    POST /contracts                                   and /contracts/{id}/update
    POST /solutions                                   and /solutions/{id}/update
    POST /projects                                    and /projects/{id}/update
    POST /projects/{id}/competent-professional        and /competent-professional/{id}/update

A blank contract, solution or project propagates into the claim pack and the project memo --
the documents handed to HMRC and to Ayming. A blank competent professional is worse still,
because that name is the sign-off on the R&D judgement.

Every assertion here is about STORED STATE as well as the status code, because the two are
independent: a route that returned 400 while still writing the row would pass a status-only
test, and writing the row was the whole of the incident. ``table_row_counts`` covers every
mapped table, so it also catches the audit row the refused write used to leave behind.

The templates already mark each of these inputs ``required``. That is browser-side only and
absent from any non-browser POST, which is why it is not evidence and why these tests drive
the routes directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, func, select

from app.database import get_session
from app.main import app
from app.models import (
    AuditEvent,
    BusinessUnit,
    CompetentProfessionalOpinion,
    Contract,
    Customer,
    RDProject,
    Solution,
)


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def post(session, path: str, data: dict[str, str]):
    client = client_for(session)
    try:
        return client.post(path, data=data, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()


def table_row_counts(session) -> dict[str, int]:
    return {
        name: session.exec(select(func.count()).select_from(table)).one()
        for name, table in SQLModel.metadata.tables.items()
    }


def a_customer(session: Session) -> Customer:
    customer = Customer(customer_name="Transport for London", customer_type="local authority")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


def a_solution(session: Session) -> Solution:
    solution = Solution(solution_name="Depot Telemetry", customer_id=a_customer(session).id or 0)
    session.add(solution)
    session.commit()
    session.refresh(solution)
    return solution


def a_project(session: Session) -> RDProject:
    project = RDProject(project_title="Adaptive Signalling", solution_id=a_solution(session).id or 0)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def prepare_business_unit(session: Session) -> tuple[str, dict[str, str]]:
    return "/business-units", {"name": "Signalling", "description": "Rail signalling delivery.", "active": "on"}


def prepare_contract(session: Session) -> tuple[str, dict[str, str]]:
    customer = a_customer(session)
    return "/contracts", {
        "contract_name": "Signalling Framework Lot 3",
        "customer_id": str(customer.id),
        "contract_type": "framework",
        "start_date": "2025-04-01",
    }


def prepare_solution(session: Session) -> tuple[str, dict[str, str]]:
    customer = a_customer(session)
    return "/solutions", {
        "solution_name": "Depot Telemetry Platform",
        "customer_id": str(customer.id),
        "initial_radar_status": "amber",
        "solution_description": "Telemetry for depot plant.",
    }


def prepare_project(session: Session) -> tuple[str, dict[str, str]]:
    solution = a_solution(session)
    return "/projects", {
        "project_title": "Adaptive Signalling Control",
        "solution_id": str(solution.id),
        "outcome": "unresolved",
        "advance_sought": "A control loop that holds headway under degraded sensing.",
    }


def prepare_competent_professional(session: Session) -> tuple[str, dict[str, str]]:
    project = a_project(session)
    return f"/projects/{project.id}/competent-professional", {
        "professional_name": "Dr A. Reviewer",
        "years_relevant_experience": "12",
        "signoff_status": "draft",
        "role": "Principal systems engineer",
    }


@dataclass(frozen=True)
class Case:
    """One entity whose required field is now guarded on both its create and its update route."""

    label: str
    model: type
    field: str
    message: str
    prepare: Callable[[Session], tuple[str, dict[str, str]]]
    update_path: Callable[[int], str]
    #: A value that the route's PRE-EXISTING validation already refuses, used to prove the new
    #: guard is additive rather than a replacement for what was there.
    malformed: dict[str, str]


CASES = [
    Case(
        label="business unit",
        model=BusinessUnit,
        field="name",
        message="Business unit name is required.",
        prepare=prepare_business_unit,
        update_path=lambda record_id: f"/business-units/{record_id}/update",
        malformed={"parent_id": "not-a-number"},
    ),
    Case(
        label="contract",
        model=Contract,
        field="contract_name",
        message="Contract name is required.",
        prepare=prepare_contract,
        update_path=lambda record_id: f"/contracts/{record_id}/update",
        malformed={"start_date": "not-a-date"},
    ),
    Case(
        label="solution",
        model=Solution,
        field="solution_name",
        message="Solution name is required.",
        prepare=prepare_solution,
        update_path=lambda record_id: f"/solutions/{record_id}/update",
        malformed={"contract_id": "not-a-number"},
    ),
    Case(
        label="project",
        model=RDProject,
        field="project_title",
        message="Project title is required.",
        prepare=prepare_project,
        update_path=lambda record_id: f"/projects/{record_id}/update",
        malformed={"rd_start_date": "not-a-date"},
    ),
    Case(
        label="competent professional",
        model=CompetentProfessionalOpinion,
        field="professional_name",
        message="Professional name is required.",
        prepare=prepare_competent_professional,
        update_path=lambda record_id: f"/competent-professional/{record_id}/update",
        malformed={"years_relevant_experience": "lots"},
    ),
]

CASE_IDS = [case.label for case in CASES]


def create_one(session: Session, case: Case) -> tuple[int, dict[str, str]]:
    """Create the record through its own route, so the positive path is exercised first."""
    create_path, form = case.prepare(session)
    response = post(session, create_path, form)
    assert response.status_code == 303, f"{case.label} could not be created at all: {response.status_code}"
    record = session.exec(select(case.model)).one()
    return record.id or 0, form


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_an_empty_body_post_creates_nothing(session, case):
    """The exact shape of the G5b probe that created a blank customer on the live database."""
    create_path, _ = case.prepare(session)
    before = table_row_counts(session)

    response = post(session, create_path, {})

    assert response.status_code == 400
    assert case.message in response.text
    assert table_row_counts(session) == before, "the refused request still wrote rows"
    assert session.exec(select(case.model)).all() == []


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_a_blank_required_value_is_refused_on_create(session, case):
    create_path, form = case.prepare(session)
    before = table_row_counts(session)

    response = post(session, create_path, dict(form, **{case.field: ""}))

    assert response.status_code == 400
    assert case.message in response.text
    assert table_row_counts(session) == before
    assert session.exec(select(case.model)).all() == []


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_a_whitespace_only_required_value_is_refused_on_create(session, case):
    """Spaces are not a name. Guarding on the raw value would have let this through."""
    create_path, form = case.prepare(session)
    before = table_row_counts(session)

    response = post(session, create_path, dict(form, **{case.field: "   "}))

    assert response.status_code == 400
    assert case.message in response.text
    assert table_row_counts(session) == before
    assert session.exec(select(case.model)).all() == []


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_a_refused_create_writes_no_audit_event(session, case):
    """The incident left an audit row as well as a record; both had to stop."""
    create_path, _ = case.prepare(session)
    audits_before = len(session.exec(select(AuditEvent)).all())

    post(session, create_path, {})

    assert len(session.exec(select(AuditEvent)).all()) == audits_before


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_a_valid_create_still_stores_the_value_unstripped(session, case):
    """Positive control, and the pin on HOW the guard is written.

    The guard tests the stripped value but the route stores the submitted one, so a
    submission that was already valid is byte-for-byte unchanged. A guard implemented by
    assigning the stripped value instead would pass every refusal test above and fail here.
    """
    create_path, form = case.prepare(session)
    padded = f"  {form[case.field]}  "

    response = post(session, create_path, dict(form, **{case.field: padded}))

    assert response.status_code == 303
    stored = session.exec(select(case.model)).all()
    assert len(stored) == 1
    assert getattr(stored[0], case.field) == padded


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_existing_validation_still_fires_and_is_not_masked(session, case):
    """The guard is additive: a value the route already refused is still refused, on its own."""
    create_path, form = case.prepare(session)
    before = table_row_counts(session)

    response = post(session, create_path, dict(form, **case.malformed))

    assert response.status_code == 400
    assert case.message not in response.text, "the new guard fired on a submission that supplied the value"
    assert table_row_counts(session) == before


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_update_refuses_a_blank_value_and_leaves_the_record_alone(session, case):
    record_id, form = create_one(session, case)
    before = table_row_counts(session)

    response = post(session, case.update_path(record_id), dict(form, **{case.field: ""}))

    session.expire_all()
    reloaded = session.get(case.model, record_id)
    assert response.status_code == 400
    assert case.message in response.text
    assert getattr(reloaded, case.field) == form[case.field], "the refused update still overwrote the value"
    assert table_row_counts(session) == before


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_update_refuses_a_whitespace_only_value(session, case):
    record_id, form = create_one(session, case)
    before = table_row_counts(session)

    response = post(session, case.update_path(record_id), dict(form, **{case.field: "   "}))

    session.expire_all()
    assert response.status_code == 400
    assert case.message in response.text
    assert getattr(session.get(case.model, record_id), case.field) == form[case.field]
    assert table_row_counts(session) == before


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_update_still_saves_a_valid_value(session, case):
    """Positive control for the update half, which the refusal tests alone cannot give."""
    record_id, form = create_one(session, case)
    replacement = f"Renamed {case.label}"

    response = post(session, case.update_path(record_id), dict(form, **{case.field: replacement}))

    session.expire_all()
    assert response.status_code == 303
    assert getattr(session.get(case.model, record_id), case.field) == replacement
