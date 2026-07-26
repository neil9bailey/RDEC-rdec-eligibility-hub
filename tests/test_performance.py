"""Performance budgets for the two slow read paths, measured through the real routes.

This module is a benchmark harness only. It contains no optimisation: the work it measures
belongs to app/main.py, app/services.py and app/reports.py.

Per ADR-0002 Ruling R4 the cost was in app/services.py dashboard_metrics, which scored every
project in turn; app/company_setup.py and app/review_cockpit.py are not the hot path and stay
frozen.

State after Epic 7 and ADR-0006, at 120 projects and 1440 cost lines. Both read paths are inside
budget on a quiet host: batched loading took ``GET /`` from ~1500 statements to 45, and ADR-0006
D1/D2 removed the write-on-GET that dominated the pack (cold pack, measured by the author of that
increment: 0.45s / 23 statements / 0 commits against a 0.70s budget; pre-epic it was 8.32s / 6596
statements / 120 commits). The blocked-budget reason constant and the xfail it justified are gone
with the defect, per ADR-0006 D5.1.

WHAT EACH ASSERTION IS WORTH, which is the point of ADR-0006 D5.2 -- "a wall-clock budget may be
an xfail; an invariant may not":

* Statement counts, commit counts and the dataset-size guard are deterministic. They are the same
  number on any machine, under any load, so they are hard assertions and are never skipped.
* A wall clock is not. Measured in this container while several other agents' containers shared
  the host, all three candidate metrics moved with the host and not with the code:

  | metric                              | spread over samples of one unchanged render |
  |-------------------------------------|---------------------------------------------|
  | wall clock                          | x1.75 (dashboard) .. x2.89 (pack)           |
  | CPU time (``time.process_time``)    | tracks wall within ~5%: the work costs more |
  |                                     | CPU under contention, so this fixes nothing |
  | wall / same-process calibration     | x2.37 .. x2.86 -- no better than raw wall   |

  Raw numbers: the pack read path measured 0.19s-0.45s when the epic's measurements were taken and
  1.15s-3.33s here, unchanged. A budget that only holds on an idle machine is not a verified
  budget, and neither is one whose threshold has been raised until the machine it runs on passes.

So the wall-clock tests below state their own precondition and check it. A fixed reference workload
of ORM+SQLite queries -- touching none of the code under test, so it can only move when the machine
does -- is timed in the same process immediately before the samples. Above
REFERENCE_CEILING_SECONDS the measured render times are reported and the budget is not asserted,
because the number is evidence about the host. At or below it the budget is asserted at its stated
value, unchanged.

The ceiling is calibrated, not guessed. Measured in this container, full suite each time:

| host state                          | reference workload  | best of 3 renders   | budgets |
|-------------------------------------|---------------------|---------------------|---------|
| several agents' containers competing | 0.464s / 0.502s     | GET / 1.35s         | missed  |
|                                      | 0.472s / 0.657s     | pack 0.83s          | missed  |
| host quiet                           | 0.114s / 0.175s     | GET / 0.32s         | met     |
|                                      |                     | pack 0.40s          | met     |

0.20s sits between the two states with room either side. The ratio of the best render to the
reference stayed between 1.3x and 2.8x across every sample taken in all three states, so a host
that only just passes the precondition renders both routes in about 0.6s -- inside the 0.70s pack
budget and well inside the 1.00s dashboard one. A red from these tests is therefore the code, which
is the only thing a budget is worth asserting about.
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
PACK_QUERY_CEILING = 60

#: Renders taken per budget test. The best of them is asserted: contention only ever adds time, so
#: the fastest sample is the closest reading of what the code costs.
TIMED_SAMPLES = 3

#: The reference workload: fixed, dataset-independent ORM+SQLite work whose cost is a reading of
#: how much of the host is actually available right now. LIMIT keeps it the same size whatever
#: PROJECT_COUNT becomes, so it measures the machine and never the benchmark.
REFERENCE_ROUNDS = 40
REFERENCE_ROW_LIMIT = 20

#: The precondition for reading a wall clock at all, calibrated against the measured host states
#: in the module docstring. Lower it and the budgets go dormant on a host that could have proved
#: them; raise it and a contended host produces a red that says nothing about the code.
REFERENCE_CEILING_SECONDS = 0.20


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
def unrendered_hub(tmp_path):
    """A dataset no render has touched yet (ADR-0006 D5.3).

    A write-on-GET is a lazy create-if-missing, so it only ever happens on the first render.
    Sharing the module dataset would let an earlier test do that first render and turn every
    commit count below into a false zero. Project count is irrelevant here -- the assertions are
    "writes nothing" and "does not load per project", neither of which is a function of size -- so
    this stays small and cheap enough to be function-scoped.
    """
    yield from started_app(tmp_path / "unrendered.db", 5)


def measure(client: TestClient, url: str) -> tuple[float, int]:
    start = time.perf_counter()
    response = client.get(url)
    return time.perf_counter() - start, response.status_code


def count_statements_and_commits(engine, action) -> tuple[int, int]:
    """Statements and COMMITs issued by ``action``. Both are machine-independent."""
    statements = 0
    commits = 0

    def on_statement(connection, cursor, statement, parameters, context, executemany):
        nonlocal statements
        statements += 1

    def on_commit(connection):
        nonlocal commits
        commits += 1

    event.listen(engine, "before_cursor_execute", on_statement)
    event.listen(engine, "commit", on_commit)
    try:
        action()
    finally:
        event.remove(engine, "before_cursor_execute", on_statement)
        event.remove(engine, "commit", on_commit)
    return statements, commits


def reference_workload_seconds(engine) -> float:
    """How long this host takes over a fixed slice of the work the routes are made of.

    Best of two runs: the first pays SQLAlchemy's one-off statement compilation, which is not a
    property of the host. Nothing here touches the routes under test, so this can never move
    because the application changed -- only because the machine did.
    """

    def once() -> float:
        start = time.perf_counter()
        with Session(engine) as session:
            for _ in range(REFERENCE_ROUNDS):
                list(session.exec(select(RDProject).limit(REFERENCE_ROW_LIMIT)))
                list(session.exec(select(CostLine).limit(REFERENCE_ROW_LIMIT)))
        return time.perf_counter() - start

    return min(once(), once())


def assert_within_budget(client: TestClient, engine, url: str, budget: float, label: str) -> None:
    """Assert a wall-clock budget, but only where a wall clock is evidence about the code.

    The samples are always measured and always reported, whichever way this goes: a skip that
    hides the number would be worse than the flaky failure it replaces. The budget itself is
    never adjusted to suit the host -- the precondition is what moves, and it is checked, not
    assumed.
    """
    reference = reference_workload_seconds(engine)
    samples = []
    for _ in range(TIMED_SAMPLES):
        elapsed, status = measure(client, url)
        assert status == 200, f"{label} returned {status}"
        samples.append(elapsed)
    best = min(samples)
    measured = ", ".join(f"{value:.2f}s" for value in samples)

    if reference > REFERENCE_CEILING_SECONDS:
        pytest.skip(
            f"{label} measured {measured} against a {budget:.2f}s budget, but the budget is NOT "
            f"asserted: this host took {reference:.3f}s over the reference workload, above the "
            f"{REFERENCE_CEILING_SECONDS:.2f}s ceiling, so the wall clock is reporting host load "
            f"rather than the cost of the code. The statement and commit invariants for this "
            f"route are asserted unconditionally elsewhere in this module."
        )

    assert best < budget, (
        f"{label} took {best:.2f}s at {PROJECT_COUNT} projects, budget {budget:.2f}s "
        f"(samples {measured}). The host was quiet: the reference workload took "
        f"{reference:.3f}s, inside its {REFERENCE_CEILING_SECONDS:.2f}s ceiling, so this is the "
        f"code and not the machine."
    )


def test_the_benchmark_dataset_is_the_size_the_budgets_assume(benchmark):
    """Guard: without this, a silently empty dataset would make every budget below pass."""
    _, engine, _ = benchmark
    with Session(engine) as session:
        projects = len(list(session.exec(select(RDProject))))
        cost_lines = len(list(session.exec(select(CostLine))))
    assert projects == PROJECT_COUNT
    assert cost_lines == COST_LINE_COUNT


def test_dashboard_renders_within_budget(benchmark):
    client, engine, _ = benchmark
    assert_within_budget(client, engine, "/", DASHBOARD_BUDGET_SECONDS, "GET /")


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


def test_claim_period_pack_renders_within_budget(benchmark):
    """ADR-0006 D5.1: no marker. The write-on-GET the old one blamed no longer exists.

    D5.2 would have allowed the xfail to stay had the cold render still missed 0.70s after
    D1-D4. It does not: 0.45s / 23 statements / 0 commits. Keeping a marker whose reason string
    described a defect that had been fixed is the failure mode ADR-0006 D5 exists to close, and
    the marker was reporting XPASS -- red -- for the same reason.
    """
    client, engine, period_id = benchmark
    assert_within_budget(
        client,
        engine,
        f"/claim-periods/{period_id}/pack",
        PACK_BUDGET_SECONDS,
        "GET /claim-periods/{id}/pack",
    )


def test_the_pack_read_path_is_batched_and_writes_nothing(benchmark):
    """The durable half of the pack's budget: what it does, not how long the host took over it.

    ADR-0006 D5.4 removed this test's warm-up render. The warm-up existed to settle the
    write-on-GET before measuring, and with D1/D2 landed there is no write to settle -- a warm-up
    would now only hide a returning one. The wall-clock assertion moved out to the budget test
    above, where the precondition for reading a wall clock is checked; nothing here is skippable.

    Measured: 23 statements, 0 commits at 120 projects.
    """
    client, engine, period_id = benchmark
    url = f"/claim-periods/{period_id}/pack"
    status = 0

    def render():
        nonlocal status
        _, status = measure(client, url)

    statements, commits = count_statements_and_commits(engine, render)

    assert status == 200
    assert commits == 0, f"a pack render issued {commits} COMMIT(s); ADR-0006 D1 forbids it"
    assert statements < PACK_QUERY_CEILING, (
        f"the pack issued {statements} statements at {PROJECT_COUNT} projects "
        f"(ceiling {PACK_QUERY_CEILING}); per-project loading has returned"
    )


def test_the_first_pack_render_writes_nothing_and_stays_batched(unrendered_hub):
    """ADR-0006 D5.3, on a database that has never been rendered.

    The test above runs against the shared dataset, which by then has been rendered several
    times. A returning lazy create-if-missing would commit on the *first* render only, so it
    would be invisible there and visible here. The statement ceiling is the same ceiling: the
    cold path must be batched too, or a first render costs the operator per-project loading.
    """
    client, engine, period_id = unrendered_hub
    url = f"/claim-periods/{period_id}/pack"
    status = 0

    def render():
        nonlocal status
        _, status = measure(client, url)

    statements, commits = count_statements_and_commits(engine, render)

    assert status == 200
    assert commits == 0, f"the first pack render issued {commits} COMMIT(s); ADR-0006 D1 forbids it"
    assert statements < PACK_QUERY_CEILING, (
        f"the first pack render issued {statements} statements (ceiling {PACK_QUERY_CEILING})"
    )


def test_the_dashboard_does_not_write_on_a_get(unrendered_hub):
    """ADR-0005 D6 and verification 11: the read-only render must issue no COMMIT."""
    client, engine, _ = unrendered_hub
    status = 0

    def render():
        nonlocal status
        _, status = measure(client, "/")

    _, commits = count_statements_and_commits(engine, render)

    assert status == 200
    assert commits == 0, f"GET / issued {commits} COMMIT statements on a first render"
