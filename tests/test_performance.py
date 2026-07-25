"""Performance budgets for the two slow read paths, measured through the real routes.

This module is a benchmark harness only. It contains no optimisation: the N+1 work it measures
belongs to another increment, across app/main.py, app/services.py and app/reports.py.

To see the measured numbers rather than an 'x', run:

    docker compose run --rm --no-deps app pytest -q tests/test_performance.py --runxfail

Per ADR-0002 Ruling R4 the cost was in app/services.py dashboard_metrics, which scored every
project in turn; app/company_setup.py and app/review_cockpit.py are not the hot path and stay
frozen.

State after Epic 7 E7-1a/E7-1b, at 120 projects and 1440 cost lines:

* ``GET /`` is inside budget and its marker is gone. Batched loading took it from ~1500 queries to
  45, and ADR-0005 D6 removed the writes.
* ``GET /claim-periods/{id}/pack`` is NOT inside budget and keeps its marker. The remaining cost is
  not N+1 reads -- it is the write-on-GET that D6 did not cover: the pack still calls
  ``calculate_project_score`` with the default ``sync=True``, so a first render of a period whose
  projects have no EntitlementAssessment creates one per project, committing each time. Every
  commit expires the session's identity map, so the batch-loaded rows are then re-read one
  attribute at a time. Removing that write is a decision about when an auditable record comes into
  existence AND about the pack's own content, so it is EA's call, not an implementation detail.

A caveat on the pack's pre-fix number, which QA should not read as a regression: this module shares
one module-scoped dataset, and before D6 the dashboard test ran first and created all 120
assessments, so the pack test never paid for them. It now measures a genuine cold first render.
"""

from __future__ import annotations

from datetime import date
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

import app.database as database
import app.main as main
from app.models import (
    AccountingPeriod,
    Company,
    Contract,
    CostLine,
    Customer,
    RDProject,
    Solution,
)


PROJECT_COUNT = 120
COST_LINES_PER_PROJECT = 12
COST_LINE_COUNT = PROJECT_COUNT * COST_LINES_PER_PROJECT

DASHBOARD_BUDGET_SECONDS = 1.0
PACK_BUDGET_SECONDS = 0.7
DASHBOARD_QUERY_CEILING = 80

PACK_BUDGET_BLOCKED = (
    "pending EA decision: the pack's remaining cost is the write-on-GET that ADR-0005 D6 scoped to "
    "the dashboard only. Measured 8.07s cold / 0.96s warm after batching."
)


def build_dataset(session: Session, project_count: int = PROJECT_COUNT) -> int:
    """Create the projects and their cost lines. Returns the accounting period id."""
    company = Company(company_name="Benchmark Services Ltd", utr="1234567890")
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

    customer = Customer(customer_name="Benchmark Transport Authority", sector="Public sector transport")
    session.add(customer)
    session.commit()
    session.refresh(customer)

    contract = Contract(contract_name="Benchmark Framework", customer_id=customer.id or 0)
    session.add(contract)
    session.commit()
    session.refresh(contract)

    solutions = [
        Solution(
            solution_name=f"Benchmark solution {index}",
            customer_id=customer.id or 0,
            contract_id=contract.id,
        )
        for index in range(project_count)
    ]
    session.add_all(solutions)
    session.commit()
    for solution in solutions:
        session.refresh(solution)

    projects = [
        RDProject(
            solution_id=solution.id or 0,
            accounting_period_id=period.id,
            project_title=f"Benchmark project {index}",
            scientific_or_technological_uncertainties="Uncertainty recorded for benchmark data.",
            advance_sought="Advance recorded for benchmark data.",
            rd_start_date=date(2025, 4, 1),
            rd_end_date=date(2026, 3, 31),
        )
        for index, solution in enumerate(solutions)
    ]
    session.add_all(projects)
    session.commit()
    for project in projects:
        session.refresh(project)

    cost_lines = [
        CostLine(
            project_id=project.id or 0,
            activity=f"Benchmark activity {line}",
            cost_category="staff",
            person_or_supplier_name="Benchmark team",
            gross_cost=10000.0,
            apportionment_percentage=50.0,
            qualifying_amount=5000.0,
            paid_status="paid",
            uk_or_overseas="UK",
        )
        for project in projects
        for line in range(COST_LINES_PER_PROJECT)
    ]
    session.add_all(cost_lines)
    session.commit()

    return int(period.id or 0)


def started_app(db_path, project_count):
    """Bind the app to a throwaway database, run the real lifespan, and load a dataset."""
    engine = database.make_engine(f"sqlite:///{db_path.as_posix()}")
    original_database_engine = database.engine
    original_main_engine = main.engine
    database.engine = engine
    main.engine = engine
    try:
        with TestClient(main.app) as client:
            with Session(engine) as session:
                period_id = build_dataset(session, project_count)
            yield client, engine, period_id
    finally:
        database.engine = original_database_engine
        main.engine = original_main_engine
        engine.dispose()


@pytest.fixture(scope="module")
def benchmark(tmp_path_factory):
    """One dataset for the whole module: building it is not what is being measured."""
    db_path = tmp_path_factory.mktemp("benchmark") / "benchmark.db"
    yield from started_app(db_path, PROJECT_COUNT)


@pytest.fixture()
def unrendered_dashboard(tmp_path):
    """A dataset no dashboard render has touched yet.

    The write-on-GET only happens for a project with no EntitlementAssessment, so it must be
    measured on the first render. Sharing the module dataset would let an earlier test create the
    assessments and turn the commit count into a false zero. Project count is irrelevant here --
    the assertion is that a read-only render writes nothing at all -- so this stays small.
    """
    yield from started_app(tmp_path / "unrendered.db", 5)


def measure(client: TestClient, url: str) -> tuple[float, int]:
    start = time.perf_counter()
    response = client.get(url)
    return time.perf_counter() - start, response.status_code


def test_the_benchmark_dataset_is_the_size_the_budgets_assume(benchmark):
    """Guard: without this, a silently empty dataset would make every budget below pass."""
    _, engine, _ = benchmark
    with Session(engine) as session:
        projects = len(list(session.exec(select(RDProject))))
        cost_lines = len(list(session.exec(select(CostLine))))
    assert projects == PROJECT_COUNT
    assert cost_lines == COST_LINE_COUNT


def test_dashboard_renders_within_budget(benchmark):
    client, _, _ = benchmark
    elapsed, status = measure(client, "/")
    assert status == 200
    assert elapsed < DASHBOARD_BUDGET_SECONDS, (
        f"GET / took {elapsed:.2f}s at {PROJECT_COUNT} projects, "
        f"budget {DASHBOARD_BUDGET_SECONDS:.2f}s"
    )


def test_the_dashboard_does_not_issue_per_project_queries(benchmark):
    """The durable half of the budget: a wall clock is machine-dependent, a query count is not.

    Before E7-1b the dashboard issued roughly 1,500 statements at this dataset size, about a dozen
    per project. The ceiling is well above the measured 45 and well below anything per-row, so it
    fails loudly if per-project loading comes back, on any machine, however fast.
    """
    client, engine, _ = benchmark
    statements = 0

    def count_statement(connection, cursor, statement, parameters, context, executemany):
        nonlocal statements
        statements += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        _, status = measure(client, "/")
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert status == 200
    assert statements < DASHBOARD_QUERY_CEILING, (
        f"GET / issued {statements} statements at {PROJECT_COUNT} projects "
        f"(ceiling {DASHBOARD_QUERY_CEILING}); per-project loading has returned"
    )


@pytest.mark.xfail(reason=PACK_BUDGET_BLOCKED, strict=False)
def test_claim_period_pack_renders_within_budget(benchmark):
    client, _, period_id = benchmark
    elapsed, status = measure(client, f"/claim-periods/{period_id}/pack")
    assert status == 200
    assert elapsed < PACK_BUDGET_SECONDS, (
        f"GET /claim-periods/{period_id}/pack took {elapsed:.2f}s at {PROJECT_COUNT} projects, "
        f"budget {PACK_BUDGET_SECONDS:.2f}s"
    )


def test_the_dashboard_does_not_write_on_a_get(unrendered_dashboard):
    """ADR-0005 D6 and verification 11: the read-only render must issue no COMMIT."""
    client, engine, _ = unrendered_dashboard
    commits = 0

    def count_commit(connection):
        nonlocal commits
        commits += 1

    event.listen(engine, "commit", count_commit)
    try:
        _, status = measure(client, "/")
    finally:
        event.remove(engine, "commit", count_commit)

    assert status == 200
    assert commits == 0, f"GET / issued {commits} COMMIT statements on a first render"
