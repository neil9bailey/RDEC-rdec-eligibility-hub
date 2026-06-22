# AGENTS.md

## Scope

This repository contains the R&D Claim Evidence Hub, a local FastAPI/SQLModel MVP for RDEC-style decision support, evidence capture, costs, claim-readiness triage, Knowledge Agent checks, and Framework Intelligence review.

The Hub is not legal, tax, accounting, HMRC submission, or bid/no-bid advice. Preserve the user-facing caveat: "Requires competent professional and tax review."

## Ground Rules

- Keep changes minimal, accurate, and grounded in the current checkout.
- Prefer existing FastAPI route, Jinja template, SQLModel, YAML-rule, and CSS patterns before adding new abstractions.
- Do not invent HMRC, GOV.UK, procurement, or customer policy. If a rule or source is uncertain, add a short TODO or review note rather than encoding unsupported behavior.
- Treat `data/` as local SQLite runtime state. Do not delete or replace it unless the user explicitly asks for a reset.
- For browser/user-flow testing, prefer a throwaway SQLite database via `DATABASE_URL` when the flow creates demo entities.
- Use the per-command safe-directory override if Git reports dubious ownership:

```powershell
git -c safe.directory=F:/code/RDEC/rdec-eligibility-hub status --short
```

## Local Commands

Run the app with Docker Desktop:

```powershell
docker compose up --build
```

Open the app:

```text
http://localhost:8080
```

Run the test suite:

```powershell
docker compose run --rm app pytest -q
```

Compile-check application code:

```powershell
docker compose run --rm app python -m compileall app
```

Stop the app:

```powershell
docker compose down
```

Use optional throwaway demo data only when the task explicitly needs the seeded demo scenario:

```powershell
$env:SEED_DEMO_DATA="true"
docker compose up --build
```

## App Map

- `app/main.py` - FastAPI routes and form handling.
- `app/models.py` - SQLModel persistence models.
- `app/services.py` - scoring, entitlement, AIF readiness, cost validation, and dashboard metrics.
- `app/rules_engine.py` and `app/rules/` - YAML-backed runtime decision rules.
- `app/templates/` - server-rendered HTML.
- `app/static/styles.css` - Telent / M Group visual styling.
- `app/reports.py` - Markdown report generation.
- `tests/` - route, rules, model, report, framework intelligence, and audit coverage.

## UI/UX Score Loop

Use this loop for browser-exercisable flows such as customer setup, solution/project creation, project assessment, evidence capture, cost capture, claim-period readiness, framework opportunity review, or report generation.

1. Choose the exact task, starting URL, success target, browser, clean-session rule, screen sizes, modes, meaningful screens to capture, and anything that must not change.
2. Start from fresh browser state with no saved login, cookies, local storage, session storage, or cached site data. If the flow mutates app data, use a throwaway SQLite database unless the user approves changes to `data/`.
3. Complete the task once without editing. Capture normal screens plus meaningful loading, validation, error, recovery, and success states.
4. Score every captured screen with the same checklist: task clarity, navigation/orientation, form usability, content hierarchy, feedback/error handling, accessibility basics, responsive fit, visual consistency, and trust/caveat clarity.
5. Improve the weakest safe area using the smallest template/CSS/route change that preserves the existing design system and decision-support boundaries.
6. Restart from fresh browser state and rerun the entire task under the same URL, screen sizes, modes, and scoring rubric.
7. Keep only changes that improve the target flow without making another important screen worse.

Stop on outcome verified, required approval, blocked access, missing evidence, or two full passes with no score gain.

Return the entry point, clean-session rule, viewport/mode set, scoring rubric, screenshots or paths, before/after scores, retained changes, verification commands, and stop reason.

## Docs Sweep

Use this workflow whenever implementation changes may have left README files, setup guides, API references, examples, or runbooks behind.

1. Review implementation changes since the last documentation pass. Use `git status`, current branch history, and source/test diffs before editing docs.
2. Compare documentation with the current code, configuration, commands, routes, seeded data, and shipped behavior.
3. Update only stale documentation. Preserve historical baseline notes unless they are presented as current behavior.
4. Verify commands, links, examples, and changed docs against the current repository.
5. Run proportional checks. For docs-only changes, at minimum run a diff check and any relevant tests already used to validate the implementation.
6. Open a reviewable pull request that explains the drift, the documentation fixes, and the verification performed.

Stop on outcome verified, required approval, missing evidence, or progress stalls.

## agentTeam Governance

When the `agentTeam` MCP is available and the work is more than a trivial edit, use its gates as lightweight delivery guardrails:

- G0 intake: confirm the user task and success criteria.
- G1 architecture: stop for approval if the change alters architecture or needs an ADR.
- G2 implementation: keep diffs small and scoped.
- G3 verification: run proportional tests or checks.
- G4 UAT: clearly distinguish synthetic browser verification from real end-user acceptance.
- G5 security: check for secrets, unsafe data handling, and compliance caveat regressions.
- G6 release: do not claim release approval without human signoff.

Call `team_validate_handoff` on role/gate transitions when using the MCP workflow.
