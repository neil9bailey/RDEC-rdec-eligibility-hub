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
