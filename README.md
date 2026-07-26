# R&D Claim Evidence Hub

MVP web application for a UK IT services provider delivering solutions to public sector and transport customers. It captures solution facts, R&D project assessments, competent professional opinions, evidence, costs, entitlement facts, AIF readiness, and audit-pack style summaries.

This is a decision-support and evidence-capture tool. It does not provide legal, tax, accounting, or HMRC submission advice. Outputs use terms such as R&D candidate, review required, blocked, and pending competent professional and tax review. Every decision-support output includes: "Requires competent professional and tax review."

## What It Does

- Captures company, customer, contract/SOW, solution, R&D project, evidence, cost, competent professional, entitlement, and claim-period submission data.
- Guides users through company setup, work context, R&D review, evidence and costs, and final review from one ordered overview.
- Captures people time with roles, periods, hours or days, internal rates, apportionment, and timesheet / PSA evidence links.
- Scores projects using configurable YAML-backed runtime rules.
- Tracks official HMRC/GOV.UK guidance through a Knowledge Agent source register and optional live source checks.
- Tracks public-sector framework and procurement opportunity sources through a guarded Framework Intelligence Agent.
- Flags blockers such as missing scientific/technological uncertainty, missing signed competent professional opinion, missing evidence, missing costs, blocked entitlement, and AIF sequencing risk.
- Calculates qualifying cost amounts from gross cost and apportionment percentage.
- Applies configurable Additional Information Form project-selection logic.
- Records local MVP audit events for key claim-data create, update, delete, and entitlement sync actions.
- Exports selected local data as a JSON backup bundle or review-friendly CSV files, and previews controlled JSON/CSV additions and updates before applying them.
- Identifies explicitly selected unused records for guarded cleanup across 7 of the 30 data areas. Whole-area purge is implemented but disabled by default.
- Generates HTML previews and downloadable Markdown for project memos, claim-period packs, and evidence indexes.
- Generates exportable Markdown framework intelligence summaries for bid, engineering, Finance, and Ayming review discussions.
- Seeds reference business units and a small set of non-demo reference customers on first run by default, ready for live project entry after exact customer/legal-entity review.

## What It Does Not Do

- It does not decide whether a claim is valid.
- It does not submit AIFs, CT600s, or claim notifications.
- It does not calculate Corporation Tax relief values or payable credits.
- It does not replace competent professional judgement, tax review, legal review, or advisor sign-off.
- It does not require cloud services for the MVP. Optional Knowledge Agent and Framework Intelligence checks call approved public official URLs only when a user runs them.
- The Knowledge Agent does not auto-update rule logic. It flags source-review work; rule changes remain controlled YAML updates.
- The Framework Intelligence Agent does not make autonomous bid/no-bid, procurement, RDEC, tax, or claim decisions.

## MVP Hardening Status

This branch hardens the MVP without changing the Docker Desktop workflow or adding cloud services:

- YAML files under `app/rules/` now drive scoring weights, blocker labels, AIF thresholds, cost-warning labels, claim notification timing, entitlement status labels, and customer-type Corporation Tax defaults.
- AIF readiness now implements the GOV.UK more-than-10-project top-10 fallback. If 50% qualifying expenditure coverage would require more than 10 project descriptions, the Hub selects the 10 largest projects and treats the selection rule as satisfied, while showing an informational note.
- Customer setup applies entitlement-rule defaults when Corporation Tax status is blank or unknown, while preserving explicit yes/no selections.
- High-risk forms now return a friendly 400 validation page for malformed dates or numbers rather than raising server errors.
- `/audit` shows the latest local audit events for key claim-data changes.
- `/healthz` returns a simple JSON health check for local runtime verification. `/health` returns the same status plus the loaded rule-file versions.

## Version Baseline

The version marker in [`VERSION`](VERSION) is authoritative for this repository. It currently reads:

```text
intelligence-effectiveness 0.1
```

`live-demo-version-1.0` is an earlier release tag, retained as history. It marked the local Telent / M Group demonstration baseline assessed in [`docs/live_demo_version_1_0_baseline.md`](docs/live_demo_version_1_0_baseline.md): the Telent-styled dashboard, reference business-unit and reference-customer setup, YAML-backed runtime rules, AIF top-10 fallback logic, local audit logging, Knowledge Agent source checks, Framework Intelligence Agent source tracking, and Markdown exports for Finance / Ayming handover discussions.

Development has continued past that tag. `VERSION` was moved on to `intelligence-effectiveness 0.1`, and the current branch carries further unreleased changes that no tag covers. Read the demo baseline document as a record of that earlier review, not as a description of the code you are running. Release notes are in [`CHANGELOG.md`](CHANGELOG.md).

This remains a decision-support MVP at every version. It is not legal, tax, accounting, HMRC submission, or bid/no-bid advice. Requires competent professional and tax review.

## Security And Data Governance Warning

This remains an MVP for local evidence capture and decision support. It is not suitable for live public-sector evidence operations without additional controls, including SSO, role-based access control, formal audit-log review, backup/restore, deployment controls, encryption and retention policies, and evidence export governance.

The Hub has no authentication and no role-based access control. That is accepted only because it is a
single-user local tool, so `docker-compose.yml` publishes port 8080 on `127.0.0.1` rather than on all
interfaces. The app is reachable at `http://localhost:8080` from the machine running it, and is not
reachable from other machines on the network. Anyone who republishes it on `0.0.0.0`, or puts it behind
a reverse proxy or a tunnel, removes the only control that makes the missing authentication acceptable
and must add identity and access control first.

The current audit log is useful for local traceability, but it is not immutable or append-only. Import, export, cleanup, and purge controls are local MVP safeguards rather than production data governance. Production use should move to a controlled deployment model, stronger identity, managed backup/restore, retention controls, and a database platform such as Postgres with managed migrations.

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
docker compose build test
docker compose run --rm --no-deps test pytest -q
```

Tests run in a separate `test` image, not in the `app` image. The `app` image installs only
`requirements.txt`, so pytest and any other test-only tool is absent from what the Hub actually runs;
`requirements-dev.txt` adds them to the `test` image alone. The two images are built from the same
Dockerfile and run the same code and settings. `docker compose run --rm app pytest` no longer works,
and is expected not to: pytest is not installed there.

Stop the app:

```powershell
docker compose down
```

## Reference Data

Reference business units and customers are loaded once, on the first run against a database that has no reference-data seed marker. If you rename or delete a seeded record afterwards, the Hub respects that and does not recreate it.

The business units created by that first run are:

- Transport
  - Highways
  - Rail
  - SCADA
  - TfL
- Network Services
- HPC / Hinkley Point C
- Nuclear Power
- Core Central Asset Management

The default reference seed also creates customer records for common transport review contexts:

- Transport for London (TfL), assigned to the TfL business unit
- National Rail, assigned to Rail
- National Rail, assigned to SCADA

These are reference labels for local workflow setup, not final legal contracting-entity determinations. Confirm the exact customer/legal entity before live evidence capture.

The seed marker is a single audit event (`ReferenceData` / `seed_complete`) recorded in the local change history rather than a schema change or a file on disk. Audit history is preserved by purge, so a purge does not cause reference data to be re-created. Audit events are excluded from JSON import, so restoring a backup into an empty database does not restore the marker and that database is treated as a first run.

Seeding is controlled by two environment variables, both forwarded to the container by `docker-compose.yml`:

| Setting | Default | Effect |
| --- | --- | --- |
| `SEED_REFERENCE_DATA` | `true` | Runs the first-run reference business-unit and customer seed, and the framework source references. |
| `SEED_DEMO_DATA` | `false` | Additionally seeds the demo company, contracts, solutions, projects, evidence, and costs. |

Demo contracts, solutions, projects, evidence, and costs are not seeded by default. To opt into the original demo data in a throwaway local instance:

```powershell
$env:SEED_DEMO_DATA="true"
docker compose up --build
```

## Local Data Management And Reset

SQLite data is stored under `./data` and is ignored by Git.

Use `/data-management` for normal local administration:

- open the existing edit pages for individual records
- export selected data as a JSON backup bundle or ZIP of CSV files
- preview JSON or CSV additions and updates before applying them, matched on a conservative natural key rather than on any record identifier in the uploaded file
- remove selected records only when the Hub confirms they have no current links
- inspect purge scopes and record counts

Unused-record cleanup does not cover every data area. It offers 7 of the 30 data areas: companies, customers, contracts, solutions, customer watch profiles, framework opportunities, and buyer portal instances. Records in the other 23 areas, including projects, cost lines, evidence items, and competent professional opinions, are never listed for cleanup and must be removed from their own edit pages. Within those 7 areas the list is narrower still: a record appears only when it has no dependent records, and seeded reference customers, active watch profiles, opportunities that are not archived or rejected, and portal instances that are not retired are all withheld.

Import, export, and unused-record cleanup are enabled by default. Whole-area purge is disabled by default. To make purge available only in a controlled local session:

```powershell
$env:DATA_PURGE_ENABLED="true"
docker compose up --build
```

An enabled purge still requires a selected scope, backup acknowledgement, and the exact confirmation phrase shown in the UI. Reference catalogues and local change history are preserved. This does not replace a managed business backup and restore process.

The local data controls can be configured independently before startup. All six are forwarded to the container by `docker-compose.yml`, so setting one on the host before `docker compose up` is enough; compose only passes variables it names.

| Setting | Default | Effect |
| --- | --- | --- |
| `DATA_IMPORT_ENABLED` | `true` | Allows previewed JSON/CSV additions and updates. |
| `DATA_EXPORT_ENABLED` | `true` | Allows selected JSON and CSV downloads. |
| `DATA_CLEANUP_ENABLED` | `true` | Allows deletion of explicitly selected unused records in the 7 areas listed above. |
| `DATA_PURGE_ENABLED` | `false` | Makes guarded whole-area purge controls available. |
| `DATA_RESTORE_BY_IDENTIFIER_ENABLED` | `false` | Permits the separate restore-by-identifier import mode. That mode makes a record identifier in an uploaded file authoritative, so an uploaded row can overwrite a live record it never named. Leave it off unless an operator is deliberately restoring a backup. |
| `ENFORCE_FOREIGN_KEYS` | `true` | Rollback lever for referential-integrity enforcement. Setting it `false` starts the Hub with link checking switched off, so records can be saved pointing at records that no longer exist. The startup log says so loudly. |

A default import matches records on a conservative natural key. A record identifier in an uploaded file is treated as untrusted data: it never selects the record to update and is never written to the database. Data areas that have no natural key can only be added by a default import, never updated, so importing the same file twice creates duplicates in those areas.

`DATA_RESTORE_BY_IDENTIFIER_ENABLED` is the one setting that changes this, and it is the reason it is treated exactly like purge: off by default and never a release default. This branch gates the mode in the import routes and publishes the setting to the data-management screen, but does not yet render a control for selecting it.

Link checking is applied at startup. The Hub first scans for records pointing at records that no longer exist. If that scan is clean, database link enforcement is switched on. If it finds any, enforcement is left off and every affected record is written to the startup log; nothing is changed or removed automatically, and the records are corrected from their own edit pages. On this branch the startup log is the only place that report appears. Setting `ENFORCE_FOREIGN_KEYS` to `false` withholds enforcement the same way, without a code change or a redeploy, and is also recorded in the startup log.

For a deliberate full local SQLite reset outside the UI:

```powershell
docker compose down
Remove-Item -Recurse -Force .\data
docker compose up --build
```

That deletes the database and its seed marker, so the reference business units and reference customers are treated as a first run again and are recreated on the next startup.

## Knowledge Agent

The Knowledge Agent is available at `/knowledge-agent`.

It uses `app/rules/knowledge_sources.yml` as a curated register of official sources covering R&D definition, merged RDEC / ERIS, AIF, claim notification, qualifying costs, overseas restrictions, and contracted-out entitlement rules.

Normal app operation does not require internet access. When internet access is available, use **Check official sources** to fetch approved official domains and record:

- HTTP status
- detected GOV.UK updated date where available
- last-modified header where available
- content hash
- check timestamp

The agent is intentionally conservative: it never changes scoring, blockers, entitlement, AIF logic, or cost rules automatically. A user must review official changes, update the relevant YAML rule file, and keep the output caveat: "Requires competent professional and tax review."

## Framework Intelligence Agent

The Framework Intelligence Agent is available at `/framework-intelligence`.

It uses customer and business-unit data to create watch profiles for public-sector customers and domains such as National Highways, TfL, Network Services customers, SCADA, Highways, Rail, HPC / Hinkley Point C, Nuclear Power, and asset management. A user can run a guarded source check against configured official/public procurement sources. The MVP seeds references for Find a Tender, Contracts Finder, devolved public sources, portal platform families, and source-change tracking.

The agent records:

- source configuration and last check status
- configurable five-source public procurement pipeline metadata
- source-change snapshots and connector health
- buyer portal platform families and customer portal instances
- customer watch profiles, aliases, keywords, CPV codes, and domains
- captured framework or bid opportunities
- opportunity review classification, evidence gaps, and next-action prompts
- opportunity document links, retrieval tasks, quality-question extracts, and weighting signals
- extracted requirement themes
- RDEC candidate indicators for human review
- agent run history and audit events
- exportable Markdown framework intelligence reports

The agent is deliberately bounded. It only checks approved HTTPS official/public procurement domains, and runs are explicit and logged. It does not auto-bid, contact customers, alter claim rules, or decide RDEC eligibility. Outputs are prompts for bid, engineering, Finance, and Ayming discussion and retain: "Requires competent professional and tax review."

The source and portal intelligence extension is documented in:

- [`docs/framework_source_portal_intelligence_extension.md`](docs/framework_source_portal_intelligence_extension.md)

## Operating Procedure

The end-to-end Telent / M Group operating procedure is available at:

- [`docs/telent_m_group_rdec_evidence_hub_operating_procedure.md`](docs/telent_m_group_rdec_evidence_hub_operating_procedure.md)

It covers business-unit setup, company/accounting period setup, customer and contract capture, solution intake, R&D candidate assessment, competent professional sign-off, evidence capture, people time and cost capture, AIF readiness, Finance handover, Ayming handover, and a National Highways NRTS3 worked example.

## Architecture Overview

- `app/main.py` - FastAPI routes and Jinja rendering.
- `app/models.py` - SQLModel database models.
- `app/company_setup.py` - claimant company normalization, setup readiness, and accounting-period guardrails.
- `app/review_cockpit.py` - workflow stage status and prioritised next actions.
- `app/data_management.py` - selected exports, previewed imports, unused-record cleanup, purge scopes, and relationship safeguards.
- `app/data_integrity.py` - startup orphan scan, link-enforcement policy, and the plain-language integrity banner.
- `app/services.py` - scoring, entitlement, AIF readiness, cost validation, dashboard metrics.
- `app/rules_engine.py` - typed runtime accessors and validation for YAML rules.
- `app/form_utils.py` - safe form parsing helpers and validation responses.
- `app/audit.py` - compact MVP audit-event helpers.
- `app/reports.py` - Markdown report generation.
- `app/knowledge_agent.py` - official source registry, optional live checks, and rule coverage monitoring.
- `app/framework_intelligence.py` - guarded public-sector procurement/source checks, opportunity capture, opportunity review summaries, requirement extraction, RDEC candidate signals, and Markdown intelligence reports.
- `app/seed.py` - clean reference business units, reference transport customers, and optional demo transport data.
- `app/templates/` - server-rendered HTML.
- `app/static/` - CSS and vendored HTMX.
- `app/rules/` - versioned YAML rules.
- `tests/` - pytest coverage for rules, reports, costs, company setup readiness, data management, AIF logic, models, route smoke tests, validation, and audit logging.

The app uses SQLite for MVP persistence and initialises tables automatically at startup. No secrets are required.

## Rule Configuration

Rules are loaded from YAML at startup:

- `eligibility_weights.yml`
- `blockers.yml`
- `cost_categories.yml`
- `claim_period_rules.yml`
- `aif_rules.yml`
- `entitlement_rules.yml`
- `knowledge_sources.yml`
- `framework_sources.yml`
- `framework_intelligence_review.yml`
- `procurement_platforms.yml`
- `source_change_tracking.yml`

Each file includes a version and source metadata. The app validates required rule keys at startup and uses these files at runtime, not just as documentation. Update the YAML first when HMRC guidance changes, then adjust tests if the decision model intentionally changes.

The AIF selection thresholds are loaded from `aif_rules.yml`. For more than 10 projects, the Hub follows the GOV.UK top-10 fallback where reaching 50% qualifying expenditure would require more than 10 project descriptions.

## Main Pages

- `/`
- `/final-review`
- `/data-management`
- `/knowledge-agent`
- `/framework-intelligence`
- `/framework-intelligence/source-catalogue`
- `/framework-intelligence/source-changes`
- `/framework-intelligence/portal-platforms`
- `/framework-intelligence/sources`
- `/framework-intelligence/watch-profiles`
- `/framework-intelligence/opportunities`
- `/framework-intelligence/opportunities/{id}`
- `/framework-intelligence/opportunities/{id}/documents`
- `/framework-intelligence/requirements`
- `/framework-intelligence/agent-runs`
- `/framework-intelligence/reports`
- `/framework-intelligence/reports/{id}`
- `/business-units`
- `/companies`
- `/customers`
- `/contracts`
- `/solutions`
- `/projects`
- `/costs`
- `/evidence-index`
- `/audit`
- `/healthz`
- `/health`
- `/projects/{id}`
- `/projects/{id}/assessment`
- `/projects/{id}/costs`
- `/projects/{id}/evidence`
- `/projects/{id}/competent-professional`
- `/projects/{id}/report`
- `/claim-periods/{id}/readiness`
- `/claim-periods/{id}/pack`

## Future Roadmap

- Direct Jira connector
- Direct Azure DevOps connector
- Direct GitHub connector
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
