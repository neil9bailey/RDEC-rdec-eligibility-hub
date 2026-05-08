# Live Demo Version 1.0 Baseline

Prepared: 2026-05-08  
Baseline name: `live-demo-version 1.0`  
Git tag to use: `live-demo-version-1.0`  
Branch prepared on: `codex/framework-intelligence-agent`

## Purpose

This baseline captures the current local-demo-ready state of the R&D Claim Evidence Hub for Telent / M Group review. It is intended to support a controlled demonstration of RDEC-style evidence capture, claim-readiness triage, public-sector framework intelligence, cost capture, and Finance / Ayming handover preparation.

The Hub remains a decision-support and evidence-capture MVP. It does not provide legal, tax, accounting, HMRC submission, or bid/no-bid advice. Requires competent professional and tax review.

## Included Capability

- Telent / M Group enterprise dashboard with sticky no-wrap navigation and live-demo visual direction.
- Clean default database state with reference business units only, ready for live customer and project entry.
- Business-unit structure covering Transport, Highways, Rail, SCADA, TfL, Network Services, HPC / Hinkley Point C, Nuclear Power, and Core Central Asset Management.
- Customer, contract, solution, R&D project, competent professional, evidence, people-time, cost, entitlement, AIF readiness, and claim-pack capture.
- YAML-backed runtime rules for eligibility scoring, blockers, AIF thresholds, claim-period timing, cost-warning labels, and entitlement defaults.
- Guarded Knowledge Agent for official GOV.UK / HMRC source monitoring.
- Guarded Framework Intelligence Agent for public-sector framework and bid-opportunity source tracking.
- Local audit page for key claim-data events.
- HTML and Markdown report generation for project memos, claim-period packs, evidence indexes, and framework intelligence summaries.

## Verification Baseline

The following local checks are part of the v1.0 baseline release gate:

- `docker compose config -q`
- `docker compose build`
- `docker compose run --rm app pytest -q`
- `docker compose run --rm app python -m compileall app`
- static repository scans for old `TemplateResponse` usage, obvious unresolved-work markers, and committed credential-like strings
- local runtime health check at `/healthz`

Latest assessment before tagging found:

- Docker Compose configuration valid.
- Docker image builds successfully.
- Pytest suite passes with 41 tests.
- Python files compile successfully.
- No old-style `templates.TemplateResponse("template.html", context)` calls detected.
- No actionable unresolved-work markers detected in application, tests, docs, or README scans.
- No committed credential values detected in scanned application, test, README, and Markdown documentation paths.

## Demo Operation

Run locally with Docker Desktop:

```powershell
docker compose up --build
```

Open:

```text
http://localhost:8080
```

Run tests:

```powershell
docker compose run --rm app pytest -q
```

Stop:

```powershell
docker compose down
```

Optional throwaway demo data remains controlled by:

```powershell
$env:SEED_DEMO_DATA="true"
docker compose up --build
```

## Known MVP Constraints

- No SSO or Entra ID integration.
- No role-based access control.
- SQLite is used for local MVP persistence.
- Audit events are useful for local traceability, but are not immutable or append-only.
- No formal backup, restore, retention, or encryption model.
- No managed production deployment controls.
- No live Jira, Azure DevOps, GitHub, ServiceNow, SharePoint, PSA, ERP, or cloud billing integrations yet.
- Optional Knowledge Agent and Framework Intelligence source checks require internet access at the point of checking.
- Production use should move to Postgres, Alembic-managed migrations, SSO/RBAC, controlled audit storage, and formal evidence governance before live public-sector evidence handling.

## Release Hygiene

Generated Word/PDF draft artefacts under `docs/` are ignored from the Git baseline to keep the repository source-focused. Markdown source documents remain tracked.

The v1.0 baseline should be tagged as:

```powershell
git tag -a live-demo-version-1.0 -m "Live demo version 1.0 baseline"
```
