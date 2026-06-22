# MVP Hardening Notes

Branch: `hardening/rules-aif-validation-audit`  
Date checked: 2026-05-07  

This note summarises the MVP hardening work for the R&D Claim Evidence Hub. The app remains a decision-support and evidence-capture tool only. It does not provide legal, tax, accounting, or HMRC submission advice.

Every decision-support output must continue to carry:

> Requires competent professional and tax review.

## Baseline Checks

Baseline checks before code changes:

- `docker compose build` passed.
- `docker compose run --rm app pytest -q` passed with `16 passed`.

No pre-existing app or test failure was found during baseline. During later verification, Docker Desktop produced one transient image-export snapshot error; rerunning `docker compose build app` succeeded.

Final verification on this branch:

- `docker compose build app` passed.
- `docker compose run --rm app pytest -q` passed with `36 passed`.

## Issues Fixed

- Fixed AIF more-than-10-project readiness logic so the GOV.UK top-10 fallback is treated as satisfied when reaching 50% qualifying expenditure would require more than 10 project descriptions.
- Added an informational AIF note when the top-10 fallback applies.
- Preserved warnings when selected AIF project descriptions are missing.
- Added `app/rules_engine.py` so YAML files drive runtime behaviour rather than acting only as documentation.
- Added startup validation for required rule-file keys.
- Refactored scoring blockers, negative terms, AIF thresholds, cost-warning labels, claim-notification timing, and entitlement defaults to use YAML-backed accessors.
- Applied entitlement customer-type Corporation Tax defaults from `entitlement_rules.yml` when the submitted status is blank or unknown.
- Preserved explicit yes/no Corporation Tax status selections.
- Added safer form parsing for high-risk numeric and date fields to return friendly 400 validation responses instead of 500 errors.
- Added compact MVP audit events for create, update, delete, and entitlement-sync actions on key claim-data entities.
- Added `/audit` to review recent local audit events.
- Added `/healthz` for a simple local runtime health check.
- Enhanced Markdown reports with generated timestamp, rule version summary, AIF selection method/notes, cost caveat, and entitlement caveat.
- Added route smoke tests, AIF regression tests, YAML runtime-rule tests, validation tests, audit tests, report traceability tests, and health check tests.

## Official Sources Checked

Official GOV.UK / HMRC sources checked on 2026-05-07:

- Additional Information Form: https://www.gov.uk/guidance/submit-detailed-information-before-you-claim-research-and-development-rd-tax-relief
- Merged RDEC / ERIS: https://www.gov.uk/guidance/research-and-development-rd-tax-relief-the-merged-scheme-and-enhanced-rd-intensive-support
- Contracted-out R&D overview: https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird161000
- Ineligible / irrelievable clients: https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird163000
- Qualifying expenditure overview: https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird131000
- Overseas restrictions: https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird150500
- Claim notification: https://www.gov.uk/guidance/tell-hmrc-that-youre-planning-to-claim-research-and-development-rd-tax-relief

Implementation notes:

- AIF selection follows the GOV.UK rule that 1 to 3 projects require all descriptions; 4 to 10 require at least 3 and enough to cover at least 50% of qualifying expenditure; more than 10 uses the same rule unless more than 10 descriptions would be needed, in which case the 10 largest projects are selected.
- Cost outputs remain captured expenditure for review only. Relief value and payable credit are not calculated by this MVP.
- Contracted-out and irrelievable-client treatment remains a tax-review matter.

## Remaining Production Gaps

Before live production use, especially for public-sector evidence, the system needs:

- SSO / Entra ID.
- Role-based access control.
- Immutable audit log or append-only event store.
- Audit review workflow and monitoring.
- Encryption and retention model.
- Evidence export governance.
- Backup and restore process.
- Alembic migrations and Postgres or another managed production database.
- Deployment controls and environment separation.
- Advisor workflow.
- PDF exports.
- Live integrations for Jira, Azure DevOps, GitHub, ServiceNow, SharePoint, Confluence, PSA/timesheets, ERP/finance, and cloud billing.
