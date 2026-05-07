# R&D Claim Evidence Hub

MVP web application for a UK IT services provider delivering solutions to public sector and transport customers. It captures solution facts, R&D project assessments, competent professional opinions, evidence, costs, entitlement facts, AIF readiness, and audit-pack style summaries.

This is a decision-support and evidence-capture tool. It does not provide legal, tax, accounting, or HMRC submission advice. Outputs use terms such as R&D candidate, review required, blocked, and pending competent professional and tax review. Every decision-support output includes: "Requires competent professional and tax review."

## What It Does

- Captures company, customer, contract/SOW, solution, R&D project, evidence, cost, competent professional, entitlement, and claim-period submission data.
- Scores projects using configurable weighted rules.
- Flags blockers such as missing scientific/technological uncertainty, missing signed competent professional opinion, missing evidence, missing costs, blocked entitlement, and AIF sequencing risk.
- Calculates qualifying cost amounts from gross cost and apportionment percentage.
- Applies configurable Additional Information Form project-selection logic.
- Generates HTML previews and downloadable Markdown for project memos, claim-period packs, and evidence indexes.
- Seeds only reference business units by default, ready for live customer and project entry.

## What It Does Not Do

- It does not decide whether a claim is valid.
- It does not submit AIFs, CT600s, or claim notifications.
- It does not calculate Corporation Tax relief values or payable credits.
- It does not replace competent professional judgement, tax review, legal review, or advisor sign-off.
- It does not call external APIs or require cloud services for the MVP.

## Official Guidance Checked

Initial rules were checked against official GOV.UK / HMRC sources on 2026-05-07 and encoded as versioned YAML under `app/rules/`.

- [CIRD81910 - DSIT Guidelines (2023)](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird81910)
- [CIRD81300 - Definition of R&D for tax purposes](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird81300)
- [Merged RDEC and ERIS guidance](https://www.gov.uk/guidance/research-and-development-rd-tax-relief-the-merged-scheme-and-enhanced-rd-intensive-support)
- [Additional information before claiming R&D tax relief](https://www.gov.uk/guidance/submit-detailed-information-before-you-claim-research-and-development-rd-tax-relief)
- [Claim notification guidance](https://www.gov.uk/guidance/tell-hmrc-that-youre-planning-to-claim-research-and-development-rd-tax-relief)
- [CIRD161000 - Contracted out R&D overview](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird161000)
- [CIRD163000 - Ineligible companies](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird163000)
- [CIRD150500 - Overseas restrictions](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird150500)
- [CIRD131000 - Reformed reliefs qualifying expenditure overview](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird131000)

## Docker Desktop Setup

From the repository root:

```powershell
docker compose up --build
```

Open:

```text
http://localhost:8080
```

Run tests:

```powershell
docker compose run --rm app pytest
```

Stop the app:

```powershell
docker compose down
```

## Reference Data

Reference business units are loaded automatically when the SQLite database is empty:

- Transport
  - Highways
  - Rail
  - Scada
  - TfL
- Network Services
- HPC / Hinckley Point C
- Nuclear Power
- Core Central Asset Management

Demo customers, contracts, solutions, projects, evidence, and costs are not seeded by default. To opt into the original demo data in a throwaway local instance:

```powershell
$env:SEED_DEMO_DATA="true"
docker compose up --build
```

## Data Reset

SQLite data is stored under `./data` and is ignored by Git.

```powershell
docker compose down
Remove-Item -Recurse -Force .\data
docker compose up --build
```

The reference business units will be recreated on the next startup.

## Architecture Overview

- `app/main.py` - FastAPI routes and Jinja rendering.
- `app/models.py` - SQLModel database models.
- `app/services.py` - scoring, entitlement, AIF readiness, cost validation, dashboard metrics.
- `app/reports.py` - Markdown report generation.
- `app/seed.py` - clean reference business units and optional demo transport data.
- `app/templates/` - server-rendered HTML.
- `app/static/` - CSS and vendored HTMX.
- `app/rules/` - versioned YAML rules.
- `tests/` - pytest coverage for rules, reports, costs, AIF logic, and models.

The app uses SQLite for MVP persistence and initialises tables automatically at startup. No secrets are required.

## Rule Configuration

Rules are loaded from YAML at startup:

- `eligibility_weights.yml`
- `blockers.yml`
- `cost_categories.yml`
- `claim_period_rules.yml`
- `aif_rules.yml`
- `entitlement_rules.yml`

Each file includes a version and source metadata. Update the YAML first when HMRC guidance changes, then adjust tests if the decision model intentionally changes.

## Main Pages

- `/`
- `/business-units`
- `/companies`
- `/customers`
- `/contracts`
- `/solutions`
- `/projects`
- `/projects/{id}`
- `/projects/{id}/assessment`
- `/projects/{id}/costs`
- `/projects/{id}/evidence`
- `/projects/{id}/competent-professional`
- `/projects/{id}/report`
- `/claim-periods/{id}/readiness`
- `/claim-periods/{id}/pack`

## Future Roadmap

- Jira import
- Azure DevOps import
- GitHub import
- ServiceNow evidence links
- SharePoint document indexing
- Confluence evidence links
- PSA / timesheet integration
- ERP / finance integration
- Azure / AWS / GCP cloud billing ingestion
- SSO
- Role-based access control
- Immutable audit log
- PDF exports
- Advisor review workflow
