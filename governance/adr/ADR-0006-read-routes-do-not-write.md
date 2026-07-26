# ADR-0006: Read Routes Do Not Write — Deterministic Claim-Evidence Outputs

Status: Approved
Date: 2026-07-26
Epic: EPIC-RDEC-2026-07-VERIFIED-FIXES
Owner: Enterprise Architect
Extends: ADR-0005 D6, from the dashboard render to every HTTP GET route.

## Context

Approved under G1 authority by the Enterprise Architect, on two escalations raised from G2 by the
Principal implementing Epic 7. Both were escalated correctly: ADR-0005 D6 scoped its approval to the
dashboard and said so, so extending it is an ADR decision, not an implementation detail.

**Escalation 5 — the pack budget is not met, and the residual is entirely the write.** Measured at
120 projects and 1,440 cost lines:

| Path | Time | Statements | Commits |
|---|---|---|---|
| pack, cold, pre-change | 8.32s | 6,596 | 120 |
| pack, cold, post-change | 9.65s | 6,354 | 120 |
| pack read path, nothing left to assess | 0.19s | 23 | 0 |
| budget | 0.70s | — | — |

The batching increment did what it was asked to do — the read path is 23 statements — and the cold
number did not move, because the cost is not the reads. It is 120 commits inside a GET, each of
which expires the SQLAlchemy identity map and forces the just-batched rows to be re-read one
attribute at a time.

The pack's previously reported "before" of 1.40s was an artefact of a shared module-scoped benchmark
dataset: the dashboard test ran first and paid for all 120 assessment writes, so the pack test never
did. Cold on the original tree the pack was 8.32s. **Nothing regressed. That budget was never being
met, and the number that said otherwise was measuring a warmed dataset.** This is recorded here so
QA does not read the corrected figure as a regression introduced by this epic.

**Escalation 6 — the pack's content depends on how many times it has been rendered.**
`app/reports.py:236` builds every project context in one batch *before* any score is computed, and
`:243-244` appends an entitlement note only `if context.entitlement`. Scoring then creates the
assessment. So on a first render the note is absent and on a second render, from identical data, it
is present. The same shape exists in the project memo, where `app/reports.py:200-201` prints
`Not assessed` on a first render.

This predates the epic and it is not a performance defect. ADR-0001 Lane 2 makes the claim-period
pack an artefact handed to Finance, competent professionals, and Ayming; ADR-0001 line 107 confines
the Hub to "decision support and evidence capture only". **A document offered as claim evidence
whose content is a function of render history rather than of the recorded facts is not evidence.**
That is the governing reason this is decided now rather than deferred.

**Write-on-GET is not confined to the pack.** With scoring left at its `sync=True` default, these
GET handlers still create and commit an `EntitlementAssessment`:

- `app/main.py:2016` — `GET /projects/{project_id}`
- `app/main.py:2029` — `GET /projects/{project_id}/assessment`
- `app/main.py:2086` — `GET /projects/{project_id}/costs`
- `app/main.py:2153` — `GET /projects/{project_id}/evidence`
- `app/main.py:2233` — `GET /projects/{project_id}/competent-professional`
- `app/main.py:2318` — `GET /projects/{project_id}/report`
- `app/main.py:2372` — `GET /claim-periods/{period_id}/pack`, via
  `generate_claim_period_pack_markdown` -> `score_project_context` at `app/reports.py:239`

**The fact that makes this safe.** `POST /projects/{project_id}/assessment` already calls
`sync_entitlement_for_project` explicitly and unconditionally at `app/main.py:2077`, independently of
scoring. Removing the write from every GET therefore does not remove the record's genesis; it moves
it from "somebody looked at a page" to "somebody saved the assessment". That is a strengthening of
the audit trail, not a weakening of it: an `EntitlementAssessment` create event attributable to a
page render records no human act and is misleading in an audit history that ADR-0002 line 39
deliberately preserves.

Three further forces point the same way. ADR-0005 D3 turns foreign-key enforcement on after a clean
scan; once it is live, a constraint failure on a write inside a GET returns a 500 for the whole
document — the precise hazard D6 was written to remove from the dashboard, and the pack is the more
claim-critical of the two. ADR-0005 D6's own reasoning is not dashboard-specific in any respect. And
`entitlement_facts_for_context` (`app/services.py:382`) is already a pure resolver that both the
persisting and the non-persisting path share, so a read-only route can reach the identical answer
without a row.

## Decision

### D1. No HTTP GET handler may commit

This is the governing rule and it is an invariant, not a target. A GET handler in `app/main.py` must
not cause a `COMMIT`. It applies to every existing and future GET route, including Markdown download
routes, which are GETs that happen to return `text/markdown`.

The exception register for D1 is **empty at approval**. A Principal who finds a GET route that
genuinely must write returns to G1 naming the route and the reason. They do not add a quiet
exception.

### D2. Scoring on a read path uses `sync=False`

Every call to `calculate_project_score` or `score_project_context` reached from a GET handler passes
`sync=False`. Named sites, all of which change: `app/main.py:2016`, `:2029`, `:2086`, `:2153`,
`:2233`, `:2318`, and `app/reports.py:239` (reached from `app/main.py:2372`).

`app/main.py:2029` is included deliberately. ADR-0005 D6 said assessments "continue to be created on
the project assessment page, where the user is performing a write"; on the **GET** of that page the
user is not performing a write. That sentence is amended in ADR-0005 by this ruling.

Signatures are unchanged. `sync: bool = True` stays the default so that write paths keep today's
behaviour without edit.

### D3. Assessments are created on write paths only

`sync_entitlement_for_project` is called from `POST /projects/{project_id}/assessment`
(`app/main.py:2077`) and from no read path. No other route gains a call to it under this ADR.

### D4. Deterministic entitlement reporting in outputs

D1-D3 alone would make the pack *permanently* omit an entitlement note for any project with no
stored assessment, which is a content regression, not a fix. The content rule is therefore decided
here, in the same increment. **D1-D3 must not be implemented without D4.**

**D4.1 — claim-period pack.** `generate_claim_period_pack_markdown` emits exactly one entitlement
note per project in the period, in the existing project order, derived from the resolved position and
never from the presence of a row:

```python
# stored assessment present
f"{project.project_title}: {context.entitlement.status} - {context.entitlement.rationale} (recorded assessment)"
# no stored assessment
f"{project.project_title}: {result.status} - {result.rationale} (resolved from current project facts; no assessment recorded yet)"
```

where `result` is `entitlement_facts_for_context(context)[1]` — the same pure resolver that
`sync_entitlement_for_project` persists, so the recorded and resolved wordings can never disagree for
the same facts.

**D4.2 — project memo.** The entitlement lines at `app/reports.py:200-201` take the same resolved
value, and the literal `"Not assessed"` fallback is removed. Required shape:

```
- Status: {status}
- Rationale: {rationale}
- Assessment recorded: {"yes" if context.entitlement else "no - resolved from current project facts"}
```

**D4.3 — vocabulary and caveats unchanged.** `CAVEAT` and `ENTITLEMENT_CAVEAT` render exactly as
today, in the same positions. `(recorded assessment)`, `(resolved from current project facts; no
assessment recorded yet)` and `no - resolved from current project facts` are provenance statements
about the Hub's own records. They are not ratings, not approvals, and not tax statements
(ADR-0002 line 59), and they introduce no term into the controlled vocabulary fixed by
ADR-0001 line 72.

**D4.4 — no backfill.** No increment under this ADR may create, update, or delete an
`EntitlementAssessment` in order to make outputs consistent. Consistency comes from resolving, not
from writing.

### D5. Where a bound is not met, the record of it is a test, not a comment

`tests/test_performance.py` is amended as follows.

**D5.1** Remove `@pytest.mark.xfail` from `test_claim_period_pack_renders_within_budget` and remove
the `PACK_BUDGET_BLOCKED` reason constant. On the evidence above — 23 statements and 0.19s once
nothing needs assessing — the cold render is expected at roughly 0.2s against a 0.70s budget.

**D5.2** If, and only if, the measured cold render still misses 0.70s after D1-D4, the marker may
stay, under three conditions:

- the reason string states the measured seconds, statement count, and commit count, and names the
  specific remaining cause;
- it is accompanied by the assertions in D5.3, which are machine-independent and **may never be
  xfail'd**; and
- the residual is recorded as an open concern in the G3 handoff.

A wall-clock budget may be an xfail. An invariant may not. The failure mode this closes is a real
constraint surviving only as prose inside a marker nobody reads.

**D5.3** New, non-xfail: on a database that has never been rendered, the **first** `GET
/claim-periods/{id}/pack` issues zero commits and fewer than `PACK_QUERY_CEILING` statements. Use the
`unrendered_dashboard`-style fixture, not the shared module dataset, for the reason given in Context.

**D5.4** `test_the_pack_read_path_is_batched_and_writes_nothing_once_assessed` loses its warm-up
`client.get(url)` (`tests/test_performance.py:267`) and measures the first render. The
"once_assessed" qualifier goes with it.

**D5.5 — the determinism guard for Escalation 6, and the point of this ADR.** On a database that has
never been rendered, render `GET /claim-periods/{id}/pack` twice and assert the two response bodies
are identical after masking only the `**Generated at:**` line. Repeat for
`GET /projects/{id}/report`. Both tests must be shown to fail on the pre-fix tree; a determinism
test that passes before the fix is testing nothing.

**D5.6** A route-level invariant test for D1: iterate the GET routes of `app.main` that the suite can
call with seeded identifiers and assert a `commit` event counter records zero for each. Routes the
test cannot call are listed explicitly in the test with a one-line reason, so every exclusion is
visible in the source rather than implied by absence.

**D5.7** The existing ADR-0005 D6 dashboard tests, including
`tests/test_scoring_golden_output.py:338`, stay as they are.

### D6. Out of scope, and explicitly not authorised

- No change to entitlement rules, statuses, rationale text, or scoring weights.
- No new route, dependency, schema change, background worker, or cache.
- No change to `app/company_setup.py` or `app/review_cockpit.py` — ADR-0002 Ruling R4's freeze
  stands, and neither is on this path.
- No further performance optimisation. If the pack meets its budget once the write is gone, the
  performance question is closed.

## Architecture Baseline

Unchanged from ADR-0002: Python 3.12, FastAPI, SQLModel/SQLAlchemy, SQLite, Jinja2 + HTMX, pytest,
Docker Desktop. No schema migration, frontend framework, background worker, cloud service, external
LLM, portal login, or HMRC integration. This ADR adds no module, no route, and no dependency; it
changes argument values at named call sites and the content of two Markdown sections.

## Guardrails

- Preserve `Requires competent professional and tax review.`
- The Hub must not decide eligibility. Nothing in D4 may read as an approval, a rejection, or a
  verdict.
- No record is created, updated, or deleted by a render.
- `data/` is the sponsor's live runtime state. No increment under this ADR may reset or rewrite it.
- Audit history stays preserved by purge; this ADR removes machine-generated create events from it,
  never existing ones.

## Consequences

Positive:

- The pack and the memo become a function of the recorded facts alone. The same period rendered
  twice produces the same document.
- The pack's residual cost disappears with the writes, and the budget stops needing an xfail.
- Every entitlement position in the pack now appears, including those never saved, each labelled with
  where it came from. Previously an unassessed project was silently absent.
- `EntitlementAssessment` create events in the audit history come to mean "a person saved an
  assessment".
- Once ADR-0005 D3 enables foreign-key enforcement, no read route can 500 on a write constraint.

Negative and risks:

- The pack lists more entitlement lines than before. Mitigated by the provenance label and by
  `ENTITLEMENT_CAVEAT` immediately above the list.
- Fewer `EntitlementAssessment` rows exist, so a JSON/CSV export of that dataset contains fewer rows
  than an operator may expect. Accepted and documented here: the missing rows recorded page views.
- Any screen or count that reports how many assessments are recorded will show lower numbers. This is
  a visible change and belongs in G4 UAT.
- `(recorded assessment)` versus `(resolved ...)` is new copy in a Finance-facing document and must be
  read by a real end user before release.

Migration and rollback: no data migration, no record changes shape. Rollback is a code revert; rows
already created by earlier renders remain valid and are simply reported as `(recorded assessment)`.

## Verification

1. `docker compose run --rm app pytest -q` green, count reported, against the 446-passed/1-xfailed
   state this ADR was ruled on. No test deleted, no assertion weakened, except exactly as D5
   authorises.
2. Cold `GET /claim-periods/{id}/pack` re-measured at 120 projects: report seconds, statement count,
   and commit count. Commits must be 0.
3. D5.1 or, on the stated conditions, D5.2 — with the reason string quoted in the handoff.
4. D5.3, D5.4, D5.5, D5.6 present and passing; D5.5 demonstrated to fail on the pre-fix tree.
5. `grep -n "sync=" app/main.py app/reports.py` shows `sync=False` at every GET-reachable site named
   in D2 and nowhere on a POST path.
6. A test asserting `POST /projects/{id}/assessment` still creates exactly one `EntitlementAssessment`
   and one audit event.
7. `docker compose run --rm app python -m compileall app` passes.
8. **UAT path (user-facing), required at G4 before G6 (ADR-0001 line 115):** the sponsor downloads a
   claim-period pack for a real period, downloads it a second time, and confirms the two documents are
   the same; confirms the entitlement notes and the `(resolved from current project facts; no
   assessment recorded yet)` label read as a record of what the Hub holds and not as a decision; and
   confirms `Requires competent professional and tax review.` is present. Synthetic capture is
   insufficient.

## ARB checklist

- Traces to epic: yes — EPIC-RDEC-2026-07-VERIFIED-FIXES, Epic 7 performance work and the
  render-count-dependent pack content found during it.
- Baseline updated: yes — this ADR. ADR-0002 line 54 respected in full; no amendment to ADR-0002 is
  required by this ADR. ADR-0005 D6 is amended by the ruling recorded in ADR-0005.
- NFRs preserved: yes — fewer statements, no commits on reads, no dependency, no schema change.
- Consumers identified: `app/main.py`, `app/services.py`, `app/reports.py`,
  `tests/test_performance.py`, `tests/test_scoring_golden_output.py`, the claim-period pack and
  project memo Markdown outputs, and any export of the `entitlement_assessments` dataset.
- Cross-cutting: partly — `app/main.py` is under a serial baton and is edited by more than one
  increment, so D2's call-site changes must be scheduled through that baton. No CTO arbitration
  required: this extends an already-approved decision on its own stated reasoning and changes no
  boundary, dependency, or data model.

## Amendments and G1 Rulings

Amended 2026-07-26 by the Enterprise Architect under G1 authority, on a QA finding raised at G3
(593 passed, 0 failed, at `cdb5830`). Verification item 5 is amended and D2 gains an explicit clause.
Original text is retained so the change is auditable.

### Ruling R1 — Verification item 5 was over-broad. `sync=False` on a POST re-render is conformant

`sync=False` appears on two POST handlers: `app/main.py:2377` (`POST /projects/{id}/costs`) and
`:2463` (`POST /projects/{id}/evidence`). QA is right that this contradicts Verification item 5 as
written. **The clause is wrong, not the code.**

Both sites are the htmx branch, and in both the write has already completed and committed —
`save_with_audit` at `:2369` and `:2457` respectively — before `get_project_context` and the score
call. What follows the save is a **render**, and it re-renders the same eligibility panel that a
fresh full-page GET produces. The Principal recorded this in-code at `:2372-2373`, naming the GET
route it must match.

`sync=True` there would be the defect, for three reasons:

- It would create and commit an `EntitlementAssessment`, plus an audit event, **as a side effect of
  saving a cost line**. D1's whole purpose is that a record's genesis is a decision a person took,
  not a panel that happened to render. Saving a cost is not an entitlement review.
- The full-page path for the same POST is a redirect to a GET that scores with `sync=False`. If the
  htmx branch used `sync=True`, the same user action would produce a different database outcome
  depending on whether htmx was active — a behaviour difference driven by transport, which is the
  render-history dependence this ADR exists to eliminate, wearing a different hat.
- It removes no intended write. D3 names `POST /projects/{project_id}/assessment` as the sole
  creation site and it calls `sync_entitlement_for_project` unconditionally, untouched by this.

**Item 5's real intent was "no intended write is removed and no unintended write is added". It was
written as a grep because a grep is cheap, and the grep was a bad proxy.** A textual rule over a
2,400-line module cannot distinguish a handler's write phase from its render phase, and the
distinction is the entire subject of this ADR.

### Amendment A1 — D2 gains an explicit clause on POST render phases

Appended to D2, after "Signatures are unchanged. `sync: bool = True` stays the default so that write
paths keep today's behaviour without edit.":

> A POST handler has two phases, and D1 governs the second. Once the handler's own write has
> committed, anything it renders is a read: a POST branch that re-renders a fragment — the htmx
> partial-response path — scores with `sync=False`, exactly as the GET that renders the same panel
> does. This is required, not merely permitted, so that one user action cannot produce two different
> database outcomes depending on whether htmx was active. `sync=True` in a POST handler is reserved
> for a write the route exists to perform.

### Amendment A2 — Verification item 5, restated as three machine-checkable properties

QA is right that "`sync=False` appears nowhere on a POST path" is a proxy, and that the property that
matters is behavioural. Replaced accordingly.

Original text:

> 5. `grep -n "sync=" app/main.py app/reports.py` shows `sync=False` at every GET-reachable site named
>    in D2 and nowhere on a POST path.

Amended text, effective 2026-07-26:

> 5. Three behavioural properties, each a test, together replacing the grep. Where any of them
>    conflicts with a textual or structural check, **these govern**:
>
>    **P1 — no GET writes.** The D5.6 route-level test: every GET route the suite can call issues
>    zero commits. This is the conformance proof for D1.
>
>    **P2 — the intended write still happens.** `POST /projects/{project_id}/assessment` creates
>    exactly one `EntitlementAssessment` and one `AuditEvent` (Verification item 6, unchanged).
>
>    **P3 — no unintended write is added.** A POST that is *not* the assessment route — specifically
>    `POST /projects/{id}/costs` and `POST /projects/{id}/evidence`, each exercised on **both** the
>    htmx and the full-page branch — creates no `EntitlementAssessment`. P3 is what item 5 was
>    actually trying to assert, and unlike the grep it is true by measurement rather than by spelling.
>
>    A `grep -n "sync=" app/main.py app/reports.py` remains useful as a cheap structural aid when
>    reviewing a diff, and every GET-reachable site named in D2 must still carry `sync=False`. It is
>    **not** a conformance criterion and a `sync=False` on a POST render branch is not a violation.

### Ruling R2 — the two additional sites fixed beyond D2's list are ratified

`app/reports.py:145` and `:272` were changed under D1 although D2's list named seven sites in
`app/main.py`. **Ratified, and the behaviour was correct.** D1 is an invariant; D2's list is the
enumeration that was known when the ADR was written, introduced as "Named sites, all of which
change", not as an exhaustive boundary. Finding further sites that violate the invariant, fixing
them, and flagging rather than silently absorbing them is precisely the intended response. An
invariant that a Principal may only apply to a list is a list, not an invariant.

### Record corrections noted at G3

- The golden scoring invariant is a byte-exact comparison against hard-coded expected values, not a
  SHA-256 digest as previously described to me. No ADR text relied on the mechanism: ADR-0005 Ruling
  R2 and D5.7 above bind the **test** at `tests/test_scoring_golden_output.py:338`, and QA re-ran it.
  The D6 conformance record stands unchanged.
- ADR-0004 D2.1's `new_in_file` marker keys on the declared identifier rather than the row number.
  Ruled and amended in ADR-0004's own amendment section; the binding property is unaffected.
