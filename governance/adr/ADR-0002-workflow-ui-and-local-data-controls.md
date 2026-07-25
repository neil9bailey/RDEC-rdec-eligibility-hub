# ADR-0002: Workflow UI and Guarded Local Data Controls

Status: Approved
Date: 2026-07-20
Epic: EPIC-RDEC-2026-07-BUSINESS-OPERATIONS
Owner: Enterprise Architect

## Context

The Hub already supports record-by-record create, update, dependency-aware delete, audit history, and Markdown report downloads. The current navigation exposes implementation-oriented product areas rather than the order in which a business user completes an RDEC review. Data can be edited on individual pages, but there is no single place to export selected working data, preview controlled imports, identify unused records, or administer a deliberately enabled purge.

The human has approved a business-operations increment that makes the experience workflow-led and adds guarded local data controls. These controls must not imply production data governance, automatic claim decisions, or HMRC submission capability.

## Decision

### Workflow-led presentation

- Present the main navigation in the order users complete the work: overview, company setup, work context, R&D review, evidence and costs, and final review.
- Use plain business language in navigation, headings, actions, validation, and status text.
- Build the dashboard around an automatically prioritised next-action queue and visible workflow progress.
- Keep specialist areas such as guidance checks, opportunity review, data management, and change history available as supporting tools.

### Local data management

- Add a local data-management area using the existing FastAPI, Jinja, SQLModel, audit, and SQLite patterns.
- Allow users to choose which data areas to export as a JSON backup bundle or a ZIP of review-friendly CSV files.
- Treat JSON as the restore/re-import format. Neutralise spreadsheet formula prefixes in CSV exports and describe CSV as a review format.
- Require every import to pass a server-side preview before it can be applied.
- Support two explicit import modes: add new records only, or add new records and update matches.
- Match by record identifier first and a conservative natural key second. Revalidate fields and links immediately before applying changes.
- Limit upload size and row count. Never execute uploaded content and never treat uploaded paths as files to open.
- Record applied imports, cleanup, purge, and export actions in local audit history.

### Cleanup and purge safety

- Cleanup may list only records with no current dependent records. A user selects each record and types a confirmation phrase before removal.
- Full purge remains disabled by default and must be deliberately enabled by an operator in application settings.
- An enabled purge requires a selected scope, backup acknowledgement, and an exact typed phrase.
- Purge scopes delete working records in dependency order while preserving rule files, configured source catalogues, business-unit reference data, and audit history.
- No import, cleanup, or purge operation may submit data externally or change RDEC eligibility logic.

## Architecture Baseline

Keep:

- Python 3.12
- FastAPI
- SQLModel / SQLAlchemy
- SQLite
- Jinja2 and HTMX
- pytest
- Docker Desktop workflow

No schema migration, frontend framework, background worker, cloud service, external LLM, portal login, or HMRC integration is introduced.

## Guardrails

- Preserve: `Requires competent professional and tax review.`
- Do not present a workflow stage as a tax, accounting, or eligibility approval.
- Imports do not delete records that are absent from the uploaded file.
- Cleanup eligibility is recalculated at deletion time.
- Purge is unavailable unless explicitly enabled and is never a release-default capability.
- Real end-user acceptance remains outstanding until a human completes the live workflow.

## Verification

- Focused tests for export selection, CSV safety, import preview, add/update modes, relationship validation, cleanup eligibility, and purge configuration.
- Existing route, rules, reports, intelligence, and audit tests remain green.
- Docker build and compile check pass.
- Fresh-browser desktop and mobile passes cover overview, company setup, and data management with no horizontal overflow.
- Human live UAT is required before G6 release approval.

## Approval

Approved for implementation from the human's 2026-07-20 instruction to simplify the UI, make navigation workflow-driven, and add configurable import, export, cleanup, purge, deletion, re-add, and update controls.

## Amendments and G1 Rulings

Amended 2026-07-25 by the Enterprise Architect under G1 authority, for epic
EPIC-RDEC-2026-07-VERIFIED-FIXES. Two Decision clauses are amended and four conformance rulings are
recorded. The original text of each amended line is retained below so the change is auditable. Where
an amendment and the original Decision section conflict, the amendment governs from 2026-07-25.

### Amendment A1 — line 27 (CSV formula neutralisation)

Driver: ADR-0004 D3. Finding C5 proved that `app/data_management.py:339 _safe_csv_value` prefixes an
apostrophe to any value starting with `-`, so `-500` exports as `'-500` and Excel treats it as text.
CSV is the stated Finance and Ayming review format, so every negative figure in a review pack was
unusable for arithmetic. This is the original line working as designed, so the line is amended rather
than the code silently narrowed.

Original text:

> Treat JSON as the restore/re-import format. Neutralise spreadsheet formula prefixes in CSV exports and describe CSV as a review format.

Amended text, effective 2026-07-25:

> Treat JSON as the restore/re-import format. Neutralise spreadsheet formula prefixes in CSV exports
> by prefixing an apostrophe to any value beginning with `=`, `+`, `@`, TAB, CR, or LF, and to any
> value beginning with `-` that is not a plain signed number matching
> `^-(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$`. Signed numeric values export as numbers so that
> Finance and Ayming reviewers can total them. Describe CSV as a review format, never as a restore
> format.

This narrows a signed-off security control. The residual CSV-injection risk is stated in full in
ADR-0004 D3 and must be reviewed at G5 before G6 release.

### Amendment A2 — line 30 (import record identity)

Driver: ADR-0004 D1. Finding C2 proved that a CSV carrying `id=1` overwrote an unrelated live
contract wholesale, and that the preview never disclosed which record would be destroyed. This is the
original line working as designed: an identifier in an uploaded file asserts identity in the
exporting database's namespace but is interpreted in the live database's namespace, and no shared
identifier space exists between them.

Original text:

> Match by record identifier first and a conservative natural key second. Revalidate fields and links immediately before applying changes.

Amended text, effective 2026-07-25:

> Match by conservative natural key first. A record identifier present in an uploaded file is
> treated as untrusted data, not as identity: it never selects a record to update and is never
> written to the database, unless the operator explicitly chooses the separate "restore by
> identifier" mode. Restore by identifier is disabled by default, requires the same deliberate
> operator enablement as purge, and its preview must name the live record that each incoming
> identifier would overwrite. Datasets without a natural key can be created but are never updated by
> a default import. Revalidate fields and links immediately before applying changes.

### Ruling R1 — line 70 and `overflow-x: hidden`

Line 70 requires: "Fresh-browser desktop and mobile passes cover overview, company setup, and data
management with no horizontal overflow."

`app/static/styles.css:31` and `:41` set `overflow-x: hidden` on `html` and `body`. That does not
prevent overflow; it hides it. Content wider than the viewport becomes unreachable rather than
absent, so line 70 is not actually satisfied on its own terms. It also disables `position: sticky`
on descendants, which is the proven cause of the new workflow navigation scrolling away.

Ruled: `overflow-x: hidden` may be removed from `html` and `body`. Line 70 is then satisfied by
fixing the real overflow sources, and the binding conformance proof becomes a measurement, not a
screenshot:

- For each of overview (`/`), company setup, and data management, at 360x640 and 1280x800, from a
  fresh browser profile with `overflow-x` unset, assert
  `document.documentElement.scrollWidth <= document.documentElement.clientWidth` and
  `document.body.scrollWidth <= window.innerWidth`. Report the measured numbers per page per
  viewport.
- Screenshots remain required as UAT evidence but are not the conformance proof for line 70.
- Permitted remedy for wide tables: wrap them in a local `.table-scroll { overflow-x: auto; }`
  container so the table scrolls rather than the page. A local scroll container does not break
  `position: sticky` on ancestors.
- Sticky must then be re-proven: scroll the overview page 800px and assert the workflow navigation's
  bounding-box `top` is unchanged.
- `scroll-padding-top: 126px` (`styles.css:30`) is kept and re-measured against the sticky header's
  actual height after the change.

No amendment to line 70 is required. It was always a statement about observed layout, never a licence
for a specific implementation.

### Ruling R2 — line 58 preserve-clause and Epic 5 copy changes

Line 58 requires: "Preserve: `Requires competent professional and tax review.`"

Ruled: the clause protects exactly one string and the mechanism that renders it. It does not freeze
any other user-facing copy.

In scope of the clause, and unchangeable: the literal string
`Requires competent professional and tax review.`; its definition in `app/services.py CAVEAT`; its
injection at `app/main.py:147-151 template_context`; its render at `app/templates/base.html:72`; the
`caveat:` key in every file under `app/rules/`; and its presence in every Markdown export and in the
JSON and CSV export manifests (`app/data_management.py:328`, `:357`).

Out of scope, and freely changeable by Epic 5: `AMBIGUOUS_TAX_REVIEW`; `CostLine #1`; raw colour
names shown as Rating values; `BU`.

Binding constraints on the replacement copy, which come from line 59 rather than line 58:

- Replacement wording must not read as an approval, a rejection, or a verdict. `not currently
  supportable` is acceptable. `not eligible`, `rejected`, `fails`, `approved`, and `qualifies` are
  not.
- The controlled vocabulary fixed by ADR-0001 line 72 — `R&D candidate`, `review required`, `strong
  indicators`, `blocked`, `pending competent professional and tax review` — must not be replaced with
  invented terminology.
- The rating **value** (`green`, `amber`, `red`) is a CSS class and a dictionary key in
  `dashboard_metrics`. Epic 5 may change only the displayed text, by using the existing
  `ScoreResult.rating_label` and the `description` values already present in
  `app/rules/eligibility_weights.yml:26,31,36,41`. A find-and-replace on the value would break the
  score-band lookup, the rating counts, and the stylesheet. This is the likely mis-implementation.
- Conformance test: assert the exact caveat string appears in the rendered HTML of every GET route
  that returns HTML, and assert `CAVEAT` is byte-identical to
  `Requires competent professional and tax review.`

### Ruling R3 — `parse_float` stays additive-only

The Delivery Lead's mandate is confirmed and strengthened. `app/form_utils.py:36 parse_float` has
nine call sites in `app/main.py`. Changing its signature changes an interface consumed across a
2296-line module that four epics touch concurrently, and no ADR requires the change.

Ruled: the name, signature, and behaviour of `parse_float` are frozen for
EPIC-RDEC-2026-07-VERIFIED-FIXES. Finding B1's remedy must be a new additive helper in the same
module, for example
`parse_decimal_amount(value, field_name, errors, default=0.0, minimum=None, maximum=None, allow_negative=False)`.

- Call sites migrate one at a time, each with its own test, and **only** where B1's finding actually
  applies. Migrating all nine "for consistency" is drift and will be rejected.
- `parse_float` may be deleted only once zero call sites remain, as a separate increment.
- Added to B1's scope, not deferred: Python's `float()` accepts `nan`, `inf`, `-inf`, and underscore
  separators such as `1_000`. `parse_float` therefore admits non-finite values into
  `CostLine.gross_cost` and `apportionment_percentage`, which propagate through
  `calculate_qualifying_amount` into report totals and into CSV exports. The new helper must reject
  non-finite values via `math.isfinite` and must reject underscore separators.

### Ruling R4 — frozen modules and Epic 7 performance work

`app/company_setup.py` and `app/review_cockpit.py` are frozen (sponsor work in flight, no findings
against them). The question was whether Epic 7's dashboard performance work must be allowed to touch
them because `app/main.py:316` and `:328` call into both.

Evidence from tracing the hot path:

- `app/company_setup.py:214 company_setup_context(companies, periods)` receives two pre-fetched lists
  and issues zero queries. It is a pure function. Not N+1.
- `app/review_cockpit.py:43 review_workflow_context` issues a fixed set of full-table selects
  (`:49-53`, `:91-92`, `:95`) regardless of project count. O(1) queries. Not N+1.
- The measured 5.42s at 120 projects is in `app/services.py:597 dashboard_metrics`: line 601 calls
  `calculate_project_score` per project, each invoking `get_project_context` (roughly ten queries)
  and potentially `sync_entitlement_for_project`, which commits (`:266`); plus two further queries per
  project at `:609-610`. That is roughly 1,500 queries and up to 120 commits on a single GET.

Ruled: Epic 7 performance work **may not touch** `app/company_setup.py` or `app/review_cockpit.py`.
They are not the hot path and the freeze holds. Epic 7's target is `app/services.py`
(`dashboard_metrics`, `get_project_context`, `calculate_project_score`) and `app/main.py:310-345`.

Conditional release: if a Principal produces a per-function measurement at 120 projects — for example
a SQLAlchemy `before_cursor_execute` counter — showing that either frozen module contributes more
than 10% of dashboard wall time, they return to G1 with that evidence for a re-ruling. No
measurement, no touch.

The write-on-GET at `app/services.py:369` must be removed as part of the same work. That change is
approved in advance in ADR-0005 D6 so Epic 7 does not need a further G1 pass.

### Ruling R5 — baseline checkpoint co-sign

Co-signed 2026-07-25. This ADR file was itself untracked at the time of the ruling, so the governance
baseline was not reproducible and no ADR could be amended against a reviewable base revision.
Committing the working tree is therefore a precondition of amendments A1 and A2, not merely repository
hygiene. Conditions are recorded in the G1 handoff for EPIC-RDEC-2026-07-VERIFIED-FIXES.

### Related ADRs

- ADR-0003 — term-matching precision and the decision-support boundary. Does not amend this ADR.
- ADR-0004 — import identity, idempotency and enforcement. Amends lines 27 and 30 (A1, A2 above).
- ADR-0005 — local data integrity: foreign-key enforcement and idempotent seeding. Operates within
  line 54; does not amend this ADR.
