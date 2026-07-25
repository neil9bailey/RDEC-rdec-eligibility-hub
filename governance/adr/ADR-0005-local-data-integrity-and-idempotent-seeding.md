# ADR-0005: Local Data Integrity — Foreign-Key Enforcement and Idempotent Seeding

Status: Approved
Date: 2026-07-25
Epic: EPIC-RDEC-2026-07-VERIFIED-FIXES
Owner: Enterprise Architect

## Context

Approved under G1 authority by the Enterprise Architect. Everything decided here fits inside
ADR-0002 line 54's prohibition on schema migration.

**D3 — foreign keys are not enforced.** SQLite defaults `PRAGMA foreign_keys` to `0` on every
connection and `app/database.py` never sets it. Create routes parse parent identifiers as plain
integers (`app/form_utils.py:24 parse_required_int`) with no existence check. Proven: orphan
contract, solution, project, and period records can be created. The mitigating factor is that the
real forms use `<select>` dropdowns, so the exposure in normal use is low — but ADR-0004's import
path writes the same columns from an uploaded file, so the mitigation does not cover the whole
surface.

Two facts materially change how this must be done.

*Fact 1.* The sponsor's live `./data/rdec_hub.db` may already contain orphans. Enabling the pragma
without a pre-scan converts a silent, tolerated data problem into a hard failure at an arbitrary
future moment — most likely mid-workflow, in front of a customer.

*Fact 2, found while ruling and load-bearing.* `app/database.py:44-46 apply_sqlite_schema_updates`
adds `customer.business_unit_id` with `ALTER TABLE customer ADD COLUMN business_unit_id INTEGER` —
**no `REFERENCES` clause**. SQLite cannot add a foreign-key constraint to an existing table. So on a
freshly created database `create_all` emits the constraint (the model declares
`foreign_key="businessunit.id"` at `app/models.py:60`), while on an upgraded database — which the
sponsor's is — that column has **no constraint in the DDL at all**. Enforcement is therefore
inconsistent between fresh and upgraded databases, and for that one column the database layer will
never be the control.

**`tests/conftest.py:10` builds its own engine** with `create_engine("sqlite://")`, entirely bypassing
`app.database.engine`. A pragma installed on the module-level engine instance would give the tests
zero coverage and let test behaviour diverge from production silently — the worst failure mode
available, because the suite would go green while production changed.

**Seed idempotency.** `app/seed.py:122 seed_business_units` and `:149 seed_reference_customers` run on
every startup and match on exact name (`:134`, `:156-160`). Rename a seeded record and it is silently
recreated, forever. `README.md:105` claims reference data is "loaded automatically when the SQLite
database is empty". That is false. Making it first-run-only needs persisted state, and ADR-0002 `:54`
forbids schema migration.

## Decision

### D1. Pragma installation — on the `Engine` class, through a shared factory

**D1.1** Install `PRAGMA foreign_keys` via a SQLAlchemy `connect` event listener registered on the
`Engine` **class**, not on any engine instance:

```python
# app/database.py
from sqlalchemy import event
from sqlalchemy.engine import Engine

FK_ENFORCEMENT = False   # module-level; flipped only after a clean scan (D3)

@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    if not _is_sqlite(dbapi_connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA foreign_keys={'ON' if FK_ENFORCEMENT else 'OFF'}")
    cursor.close()
```

Registering on the class is what makes `conftest.py`'s independent engine covered. The sqlite guard
keeps a future non-SQLite URL unaffected.

**D1.2** Relying on import side effects to register a listener is fragile — it disappears the moment
someone reorders imports, and nothing fails loudly. **Mandate a factory instead:**

```python
# app/database.py
def make_engine(database_url: str, **kwargs) -> Engine
```

`app/database.py` builds its module-level `engine = make_engine(settings.database_url, ...)`, and
`tests/conftest.py:10` is changed to
`make_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)`. Both
paths then provably share one construction route. This is the only change permitted to `conftest.py`
in this increment.

**D1.3** Flipping `FK_ENFORCEMENT` after the scan has no effect on connections already in the pool.
**`engine.dispose()` must be called immediately after the flip** so every subsequent connection
re-runs the listener with the new value. This step is easy to omit and silently defeats the whole
control; it is mandatory and must be covered by a test.

### D2. Prove the constraints exist before trusting them

**Hard precondition. This increment stops and returns to G1 if it is not met.**

Before any of D3 is relied upon, the Principal must run `PRAGMA foreign_key_list(<table>)` against a
freshly created schema for at least `contract`, `solution`, `rdproject`, `accountingperiod`,
`costline`, `evidenceitem` and record the output as evidence. If a table has no foreign-key entry,
the pragma is a no-op for it and enabling it is theatre. Adding a constraint to an existing SQLite
table requires a table rebuild, which ADR-0002 `:54` forbids — so the remedy would be an architecture
question, not an implementation one.

**Known result of this check, already established:** `customer.business_unit_id` has a constraint on
a fresh database and **no constraint on an upgraded one** (Fact 2 above). Consequently the
application-layer check in D4 is not defence in depth for that column — it is the **only** control.
The Principal must not skip D4 on the grounds that the database now enforces links.

### D3. Orphan pre-scan — report, withhold the new control, never touch the data

**Ruled: on orphans found, the application starts normally, does not enable the pragma, and tells the
operator. It never modifies, quarantines, or deletes a record.**

The reasoning is that enabling foreign-key enforcement is the *new* thing, so the new thing is what
should be withheld. Refusing to start would strand the sponsor with a live database and no way to
inspect it. Continuing silently would convert a known problem into an unpredictable future 500.
Withholding the new control is the only option that is both safe and non-destructive.

**D3.1** New module `app/data_integrity.py`:

```python
@dataclass(frozen=True)
class OrphanRecord:
    child_dataset: str
    child_id: int
    child_display: str
    field: str
    parent_dataset: str
    missing_parent_id: int

def scan_orphans(session: Session) -> list[OrphanRecord]
```

**D3.2** The checks are derived from `DatasetSpec.foreign_keys` in `app/data_management.py:66` —
**the same single source of truth the import path uses.** Two consumers, one list. A second
hand-maintained list of parent/child relationships would drift, and the drift would be invisible.

**D3.3** Startup order, exactly:

1. `init_db()` (existing).
2. `scan_orphans()` — a read, run with `FK_ENFORCEMENT` still `False`, so the scan can never itself
   fail on the condition it is looking for.
3. If the scan is clean: set `FK_ENFORCEMENT = True`, then `engine.dispose()` (D1.3).
4. If the scan finds orphans: leave `FK_ENFORCEMENT = False`, log each orphan, and store the report
   in a module-level variable.

**D3.4** Surfacing. `app/main.py:147 template_context` already injects into every rendered page from
one place. Add `data_integrity_warning` there and render a persistent banner in `base.html`. The
banner states plainly that some records point at records that no longer exist, that link checking is
therefore turned off, and links to a read-only `/data-integrity` page listing every orphan with its
display name and the specific remedy ("open this contract and choose a customer", and so on).

**D3.5 — no automatic repair, ever.** Quarantine is **explicitly rejected**: relocating a user's
RDEC evidence records is a destructive, non-obvious mutation, and ADR-0002 `:60` already establishes
that imports do not delete records absent from a file. The same conservatism binds here. No
auto-delete, no auto-nulling of a foreign key, no auto-reparenting. The operator fixes orphans
through the normal edit screens.

**D3.6** Add `enforce_foreign_keys: bool = True` to `app/settings.py` as an operator escape hatch,
documented in `README.md`. When set false, log loudly at startup and show the same banner. Default is
on.

### D4. Application-layer link checking — additive, no signature changes

A raw `IntegrityError` reaching a user is not acceptable: ADR-0002 `:19` requires plain business
language. And per D2, for `customer.business_unit_id` on an upgraded database there is no database
control at all. Both layers are required.

**D4.1** Add to `app/data_integrity.py` (not `form_utils.py`, which must stay free of persistence
imports):

```python
def require_parent(session, model, item_id, field_label, errors) -> None
```

It appends `"<Field label> does not match a record in the Hub."` to `errors` when
`session.get(model, item_id)` is `None`. This reuses the existing `errors` list plus
`validation_error_response` pattern (`app/form_utils.py:84`) and needs **no signature change
anywhere**. Call it at every create and update route that accepts a parent identifier.

**D4.2** Register a FastAPI exception handler for `sqlalchemy.exc.IntegrityError` returning
`validation_error_response(["This change would leave a record linked to something that no longer exists."])`
with status 400, so the database backstop never surfaces a stack trace. This is the same layering
principle as ADR-0004 D5.

### D5. Idempotent reference seeding — sentinel `AuditEvent`, no schema change

**Ruled: a sentinel `AuditEvent` row.** No new table, no new column, no marker file.

- `AuditEvent` is an existing table, is deliberately preserved by every purge scope (ADR-0002 `:39`),
  and is already the application's durable event record.
- A marker file is rejected: inside the image it is lost on rebuild; inside `data/` it is a second
  source of truth that can diverge from the database it describes.

**D5.1** After a successful reference seed, write:

```
AuditEvent(entity_type="ReferenceData", entity_id=0, action="seed_complete",
           summary="Reference business units and customers seeded (reference_seed_version=1).")
```

**D5.2** On every startup, `seed_reference_data()` first queries for an `AuditEvent` with
`entity_type == "ReferenceData"`, `action == "seed_complete"`, and the **current**
`REFERENCE_SEED_VERSION` in its summary. If one exists it returns immediately, touching neither
`BusinessUnit` nor `Customer`.

**D5.3** Versioning without schema. A new reference-data wave bumps the code constant
`REFERENCE_SEED_VERSION`. An older sentinel present plus a newer constant runs the new wave and
writes a new sentinel. Re-seedability is preserved.

**D5.4** The `BUSINESS_UNIT_RENAMES` migration block (`app/seed.py:123-130`) is a one-time rename. It
moves **inside** the guarded block, so it stops running once the sentinel exists. Leaving it
unconditional would keep a historical migration live forever.

**D5.5 — the most likely mis-implementation, stated so it cannot happen.** `seed_demo_data` is
**out of scope and must not be guarded by the sentinel.** It is opt-in via `SEED_DEMO_DATA` and
`tests/conftest.py:21 seeded_session` depends on it running afresh against a new in-memory database
for every test. Applying the sentinel guard to `seed_demo_data` will break every rules-engine test.
Guard `seed_business_units` and `seed_reference_customers` only.

**D5.6** Accepted consequence, to be documented rather than fixed: `audit_events` is
`importable=False` (`app/data_management.py:240`), so restoring a JSON backup does not restore the
sentinel. An empty database therefore re-seeds reference data. That is the correct behaviour.

**D5.7** `README.md:105` is factually wrong and must be corrected in the same increment (AGENTS.md
Docs Sweep). Replacement text:

> Reference business units and customers are loaded once, on the first run against a database that
> has no reference-data seed marker. If you rename or delete a seeded record afterwards, the Hub
> respects that and does not recreate it.

### D6. Approved deviation — remove the write-on-GET from the dashboard render

`calculate_project_score:369` calls `sync_entitlement_for_project`, which commits (`:266`), from
inside the read-only GET dashboard render (`app/main.py:313` -> `app/services.py:601`). Once
foreign-key enforcement is live, a constraint failure on that path would return a 500 for the whole
dashboard.

**Approved here so that Epic 7 does not have to return to G1:** `calculate_project_score` gains a
`sync: bool = True` parameter. `dashboard_metrics` (`app/services.py:597`) calls it with
`sync=False` and treats a missing `EntitlementAssessment` as "not yet reviewed" rather than creating
one. Assessments continue to be created on the project assessment page, where the user is performing
a write.

This is design-altering — it changes when a derived record comes into existence — which is why it is
recorded here rather than left to implementation. Verification is in the list below.

## Architecture Baseline

Unchanged from ADR-0002. Python 3.12, FastAPI, SQLModel/SQLAlchemy, SQLite, Jinja2 + HTMX, pytest,
Docker Desktop.

**No schema migration.** The pragma is a connection setting. The sentinel is a row in an existing
table. `require_parent` and `scan_orphans` are queries. `make_engine` is a refactor of existing
construction. One new stdlib-plus-SQLAlchemy module, one new setting, one new read-only route.

## Guardrails

- Preserve `Requires competent professional and tax review.` The `/data-integrity` page renders
  through `base.html` and carries it like every other page.
- The integrity banner and page are operational information. They must not be worded as a tax,
  accounting, or eligibility statement (ADR-0002 `:59`).
- No record is ever modified, moved, or deleted by the scan.
- `data/` is the sponsor's live runtime state (AGENTS.md ground rules). No increment under this ADR
  may delete, replace, reset, or commit it.
- Foreign-key enforcement defaults on, but only after a clean scan. Fail-closed on the control, never
  on the application.
- Audit history remains preserved by purge; the sentinel row must never be purgeable.

## Consequences

Positive:

- Orphans become impossible to create on a clean database, at two independent layers.
- The sponsor learns about pre-existing orphans through a banner and a list rather than a 500.
- Tests exercise the same pragma path as production, so the suite can no longer go green while
  production diverges.
- A renamed seeded record stays renamed.
- The dashboard stops writing on GET.

Negative and risks:

- On a database with pre-existing orphans, enforcement stays off until the operator cleans up. The
  banner is the only pressure. Accepted: correctness of the operator's data outranks speed of
  rollout.
- `customer.business_unit_id` remains unenforced at the database layer on upgraded databases. D4 is
  the compensating control and must not be skipped.
- The `Engine`-class listener applies to every engine in the process, including any future one. The
  sqlite guard bounds it.
- The sentinel couples reference seeding to `AuditEvent`. Documented in D5.6.
- `dashboard_metrics` may now show "not yet reviewed" where it previously created an assessment on
  the fly. This is a visible behaviour change and belongs in G4 UAT.

Migration and rollback: no data migration. Rollback is a code revert; the sentinel row is inert if
the guarding code is removed, and no record shape changes.

## Verification

1. `docker compose run --rm app pytest -q` green; count reported.
2. **D2 precondition evidence:** recorded `PRAGMA foreign_key_list(...)` output for the six named
   tables on a fresh schema, plus explicit confirmation of the `customer.business_unit_id`
   fresh-versus-upgraded difference.
3. **Test-path coverage (this is the point of D1):** a test that opens a session from the **test**
   engine and asserts `PRAGMA foreign_keys` returns `1`; and a test that inserting a child with a
   non-existent parent raises `IntegrityError`. The second is essential — the first alone would pass
   even if no constraint existed in the DDL.
4. **D1.3:** a test that flips `FK_ENFORCEMENT` and asserts a connection taken **after**
   `engine.dispose()` reports the new pragma value, and one taken without `dispose()` does not.
5. **Pre-scan:** a fixture database seeded with a deliberate orphan asserts `scan_orphans` finds
   exactly it, that `FK_ENFORCEMENT` stays `False`, that the app still serves `GET /` with status
   200, that the banner text appears, and that `/data-integrity` lists the orphan by display name.
6. **No mutation:** the same test asserts the orphan row is byte-identical after startup.
7. **Clean path:** a clean fixture database asserts the scan is empty, `FK_ENFORCEMENT` becomes
   `True`, and no banner renders.
8. **D4:** a POST to a create route with a non-existent parent identifier returns 400 with the plain
   business message, not a stack trace, and creates nothing. Repeat with foreign-key enforcement both
   on and off — the message must be identical either way.
9. **D5:** start twice against the same database; rename a seeded business unit between starts;
   assert it is not recreated and that exactly one `seed_complete` sentinel exists. Assert
   `seed_demo_data` still seeds a fresh in-memory database on every call and that the existing
   `seeded_session` tests are untouched.
10. **D5.7:** `README.md:105` reads as the replacement text.
11. **D6:** a test asserting `GET /` issues zero `COMMIT` statements, using a SQLAlchemy
    `before_cursor_execute` or `commit` event counter. Plus a query-count assertion at 120 projects
    to give Epic 7 its baseline.
12. `docker compose run --rm app python -m compileall app` passes.
13. **UAT path (user-facing):** the banner and `/data-integrity` page are user-facing, so a live
    end-user session is required at G4 — the sponsor opening the app against their real
    `./data/rdec_hub.db` and confirming that either no banner appears or the listed orphans are
    recognisable and actionable. Synthetic capture is insufficient.

## ARB checklist

- Traces to epic: yes — EPIC-RDEC-2026-07-VERIFIED-FIXES, finding D3 and the Epic-4 seed idempotency
  item.
- Baseline updated: yes — this ADR. ADR-0002 `:54` respected in full; no amendment to ADR-0002 is
  required by this ADR.
- NFRs preserved: yes — no schema change, no dependency, one startup read, and D6 removes writes from
  a render path.
- Consumers identified: `app/database.py`, `app/settings.py`, `app/main.py`, `app/seed.py`,
  `app/services.py`, `app/data_management.py` (as the foreign-key source of truth),
  `app/templates/base.html`, `tests/conftest.py`, `README.md`.
- Cross-cutting: partly — `tests/conftest.py` is shared by every test module, and D6 touches the
  dashboard hot path that Epic 7 also owns. Sequencing is a Delivery Lead concern; no CTO escalation
  required.
