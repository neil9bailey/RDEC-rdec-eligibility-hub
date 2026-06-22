# ADR-0001: RDEC Review Cockpit, Structured Outputs, and Traceable Intelligence

Status: Approved
Date: 2026-06-07
Epic: EPIC-RDEC-2026-06-INTELLIGENCE-EFFECTIVENESS
Owner: Enterprise Architect

## Context

The current R&D Claim Evidence Hub baseline is healthy:

- `docker compose build` passes.
- `docker compose run --rm app pytest -q` passes with 44 tests.
- `python -m pip check` reports no broken requirements.

The application already supports company setup, customers, business units, contracts, solutions, R&D candidate projects, technical assessment, evidence, people time and costs, competent professional opinions, entitlement indicators, AIF readiness, claim-period packs, audit log, Knowledge Agent checks, and guarded Framework Intelligence.

The next approved epic is to improve overall effectiveness in three areas:

- UI: make the app feel more like an operational review cockpit than a demo dashboard.
- Outputs and reports: make project memos and claim-period packs more useful for Finance, competent professionals, and Ayming review.
- Intelligence: increase value from Knowledge Agent and Framework Intelligence without creating legal, tax, accounting, bid/no-bid, HMRC submission, or final claim advice.

This is design-altering because it changes information architecture, review output structure, and the way intelligence signals are presented and governed. It must pass G1 before code changes.

## Decision

Approve a bounded MVP architecture change with four implementation lanes.

### Lane 1: Review Cockpit UI

Refocus the dashboard and core pages around operational review work:

- priority action queue
- claim-period readiness summary
- evidence and cost gap visibility
- competent professional sign-off visibility
- framework/RDEC candidate signal review status
- mobile-safe tables and navigation

The app remains server-rendered FastAPI/Jinja/HTMX. No frontend framework is introduced.

### Lane 2: Structured Markdown Outputs

Enhance existing Markdown exports rather than adding PDF/docx generation in the first wave.

Project memos and claim-period packs should include:

- executive review summary
- evidence matrix by relevance tag
- cost warning summary
- entitlement review notes
- AIF readiness and selection rationale
- reviewer checklist with owner-oriented actions
- rule version summary
- decision-support caveat

Every decision-support output must retain:

> Requires competent professional and tax review.

### Lane 3: Traceable Intelligence

Enhance Knowledge Agent and Framework Intelligence presentation with:

- source currency and source-change status
- signal rationale and matched terms/themes
- human-review status
- suggested evidence-capture prompts
- confidence/strength labels expressed as indicators, not conclusions

The system must continue to use wording such as `R&D candidate`, `review required`, `strong indicators`, `blocked`, and `pending competent professional and tax review`.

### Lane 4: Code Containment Without Architecture Replacement

Reduce risk in large modules by moving narrowly scoped helper logic into local service/report helper functions as needed.

This is not a rewrite. Keep:

- Python 3.12
- FastAPI
- SQLModel / SQLAlchemy
- SQLite
- Jinja2
- HTMX
- pytest
- Docker Desktop workflow

Do not add cloud services, background schedulers, secrets management, authenticated portal connectors, HMRC submission automation, or autonomous bid/claim decisions in this epic.

## Non-Decisions

The following are explicitly out of scope for this ADR:

- PDF, docx, or PowerPoint export
- SSO, RBAC, production audit immutability, or deployment hardening
- Postgres migration or formal database migrations
- scheduled autonomous monitoring
- authenticated portal login
- external AI/LLM calls
- legal, tax, accounting, HMRC submission, or bid/no-bid advice

These may require separate epics and ADRs.

## Guardrails

- This remains decision support and evidence capture only.
- RDEC/rule logic must remain versioned/configurable under `app/rules/`.
- Prefer official GOV.UK/HMRC manuals for R&D/RDEC rule logic.
- No secrets.
- No cloud services required for MVP.
- Must run with Docker Desktop.
- Existing tests must remain green.
- New behavior must be covered by focused tests.
- User-facing release requires real live end-user UAT evidence at G4; synthetic tests alone are insufficient.

## Consequences

Positive:

- Finance/Ayming review packs become more immediately useful.
- Users see actionable next steps instead of only raw captured data.
- Intelligence outputs become more explainable and auditable.
- The implementation stays close to the current stack and avoids avoidable dependency risk.

Tradeoffs:

- Markdown remains the export format in this wave, so polished PDF/docx handover remains future work.
- SQLite/ad hoc schema update limitations remain accepted MVP constraints.
- The route/controller structure will improve incrementally, not through a full router/domain rewrite.

## Implementation Increments

1. Baseline hygiene:
   - ignore or remove unrelated `.playwright-mcp/` artifacts after human confirmation
   - update version/changelog/release metadata for this improvement wave

2. Review cockpit:
   - adjust dashboard information hierarchy
   - add action queue and readiness summaries
   - improve mobile table/navigation behavior

3. Structured outputs:
   - add evidence matrix, cost warnings, entitlement review, and reviewer checklist sections
   - preserve existing Markdown download routes

4. Intelligence traceability:
   - enrich signal/report content with source currency, rationale, review status, and evidence prompts
   - avoid final eligibility or bid conclusions

5. Verification:
   - add or update pytest coverage for report content, route smoke behavior, and intelligence traceability
   - run Docker build and full pytest suite

## Approval

Approved by human on 2026-06-07. G2 implementation may begin within the bounded scope of this ADR.

Requested decision:

- Approve this ADR and proceed to G2 implementation.
- Reject and request changes.
- Split this ADR into smaller ADRs before implementation.
