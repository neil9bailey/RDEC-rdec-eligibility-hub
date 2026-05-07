# Telent / M Group R&D Claim Evidence Hub Operating Procedure

Version: 1.0  
Prepared for: Telent / M Group Engineering, Delivery, Finance, and advisor review teams  
Last official guidance check: 2026-05-07  
System: R&D Claim Evidence Hub  

This procedure explains how to use the R&D Claim Evidence Hub to identify R&D candidates, capture evidence, capture people time and other costs, assess entitlement indicators, prepare AIF readiness data, and produce review packs for Finance and Ayming.

This document is an operating guide only. It is not legal, tax, accounting, or HMRC submission advice. Every project, cost item, entitlement position, and claim-period pack must remain marked:

> Requires competent professional and tax review.

## 1. Purpose

The Hub exists to help Telent / M Group capture consistent, review-ready information for potential UK R&D tax relief / RDEC-style claims across transport, network services, nuclear, asset management, and critical infrastructure delivery.

The aim is to move from late, inconsistent claim reconstruction to earlier evidence capture while work is being designed, delivered, tested, changed, and reviewed.

The Hub should help the business:

- Identify stronger R&D candidates earlier.
- Separate genuine scientific or technological uncertainty from ordinary delivery work.
- Capture competent professional reasoning in a structured way.
- Link technical evidence to each candidate project.
- Link people time, supplier, software, cloud, data, and other costs to the right project.
- Give Finance a cleaner cost and evidence pack.
- Give Ayming a more complete, structured starting point for review.
- Keep public-sector and contracted-out entitlement questions visible.

## 2. Scope

Use this procedure for solution work where there may be technical advancement or uncertainty, especially in:

- Transport - Highways
- Transport - Rail
- Transport - SCADA
- Transport - TfL
- Network Services
- HPC / Hinkley Point C
- Nuclear Power
- Core Central Asset Management

The Hub is suitable for candidate identification, evidence capture, readiness review, and pack generation. It is not a claim submission tool and it does not replace Finance, Tax, competent professional, legal, or Ayming review.

## 3. Operating Principles

Use conservative wording throughout the system:

- R&D candidate
- strong indicators
- review required
- weak candidate
- blocked
- pending competent professional and tax review

Do not write that a project "qualifies" or "is eligible" unless this is part of an externally reviewed, approved position. Inside the Hub, the safer wording is that a project has indicators, blockers, warnings, or requires review.

Do not treat a whole customer contract as R&D by default. Large delivery contracts may contain specific R&D candidate projects, but normal delivery, rollout, support, procurement, project management, routine integration, and acceptance testing should be excluded unless they directly support the resolution of a scientific or technological uncertainty.

## 4. Roles And Responsibilities

Business Unit Owner:
Owns the customer and solution pipeline for the BU. Ensures candidate projects are created early enough and that accountable delivery leads are named.

Engineering / Technical Lead:
Describes the field of science or technology, baseline capability, advance sought, uncertainties, experiments, failed attempts, boundaries, and non-qualifying delivery work.

Competent Professional:
Provides the technical opinion. They must have relevant knowledge, experience, and field expertise. A project cannot be treated as a green candidate in the Hub without a signed competent professional opinion.

Delivery / Project Manager:
Provides contract, SOW, change request, delivery boundary, activity, and evidence references. Helps distinguish planned delivery from uncertainty-resolution activity.

Finance:
Confirms claimant company details, UTR, PAYE, VAT, accounting periods, paid status, payroll cost sources, cost categories, apportionment approach, and AIF / CT600 sequencing. Finance owns handover quality to Ayming.

Ayming:
Reviews the technical, tax, entitlement, cost, and submission position. The Hub should make Ayming review easier, but it does not replace that review.

System Owner:
Maintains rule YAML files, Knowledge Agent source checks, app access, local data backup/reset process, and version control.

## 5. Current Guidance Basis

Before each claim cycle, use the Knowledge Agent and official source links to check whether the rules need review. The current MVP rule files are versioned YAML under `app/rules/`.

Current source areas to monitor:

| Area | Official source |
| --- | --- |
| R&D definition and project boundaries | [CIRD81300 - definition of R&D for tax purposes](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird81300) |
| DSIT Guidelines and competent professional expectations | [CIRD81910 - DSIT Guidelines 2023](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird81910) |
| Merged RDEC and ERIS | [GOV.UK merged R&D expenditure credit and ERIS guidance](https://www.gov.uk/guidance/research-and-development-rd-tax-relief-the-merged-scheme-and-enhanced-rd-intensive-support) |
| Additional Information Form | [GOV.UK additional information before claiming R&D tax relief](https://www.gov.uk/guidance/submit-detailed-information-before-you-claim-research-and-development-rd-tax-relief) |
| Claim notification | [GOV.UK claim notification guidance](https://www.gov.uk/guidance/tell-hmrc-that-youre-planning-to-claim-research-and-development-rd-tax-relief) |
| Contracted-out R&D entitlement | [CIRD161000 - contracted out R&D overview](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird161000) |
| Overseas contractor and EPW restrictions | [CIRD150500 - overseas restrictions overview](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird150500) |
| Qualifying expenditure categories | [CIRD131000 - qualifying expenditure overview](https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird131000) |

Key operating interpretation for the Hub:

- R&D requires an advance in science or technology and scientific or technological uncertainty.
- The advance must be in the wider field, not only internal learning.
- The uncertainty must not be readily deducible by a competent professional.
- A competent professional opinion must explain the reasoning, not just assert that work is R&D.
- For accounting periods beginning on or after 1 April 2024, the merged RDEC and ERIS rules are relevant.
- AIF and claim notification timing must be checked before submission.
- Contracted-out and public-sector entitlement facts must be captured and reviewed.
- Overseas contractor and externally provided worker costs must be flagged for review.

## 6. Local App Operation

Start the app:

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

Stop:

```powershell
docker compose down
```

Reset local data:

```powershell
docker compose down
Remove-Item -Recurse -Force .\data
docker compose up --build
```

After reset, only reference business units are recreated. Demo customer, contract, project, evidence, and cost data is not seeded by default.

## 7. Page Map

| Page | Use |
| --- | --- |
| `/` | Dashboard showing counts, ratings, missing opinions, missing evidence, AIF readiness, and timing warnings. |
| `/knowledge-agent` | Check official source register and identify rule review actions. |
| `/business-units` | Maintain Telent / M Group business units and child units. |
| `/companies` | Capture claimant company and accounting period details. |
| `/customers` | Capture live customer records and business-unit ownership. |
| `/contracts` | Capture contract, SOW, bid, procurement, funding, and entitlement facts. |
| `/solutions` | Capture technical solution context and initial R&D radar status. |
| `/projects` | Create and maintain R&D candidate projects. |
| `/projects/{id}/assessment` | Capture detailed technical assessment and accounting period. |
| `/projects/{id}/competent-professional` | Capture competent professional opinion and sign-off. |
| `/projects/{id}/evidence` | Capture evidence ledger entries. |
| `/projects/{id}/costs` | Capture people time and other cost lines. |
| `/costs` | Review cost lines across all projects. |
| `/claim-periods/{id}/readiness` | Review AIF readiness for an accounting period. |
| `/claim-periods/{id}/pack` | Generate claim-period pack. |
| `/evidence-index` | Review evidence grouped by project and relevance. |

## 8. End-To-End Operating Procedure

### Step 1 - Check Knowledge Agent

Go to `/knowledge-agent`.

For live claim preparation, use the official source check before relying on rule outputs. Review any changed source status or update indicators. If official guidance has changed, do not alter claim positions directly in the database. Update the relevant YAML rule file, review with Finance and Ayming, run tests, and commit the controlled rule change.

Minimum action:

- Confirm source register is current.
- Confirm the caveat remains active.
- Confirm rule files have a version and review status.
- Record any guidance review actions.

### Step 2 - Confirm Business Units

Go to `/business-units`.

Expected reference structure:

- Transport
  - Highways
  - Rail
  - SCADA
  - TfL
- Network Services
- HPC / Hinkley Point C
- Nuclear Power
- Core Central Asset Management

Add child units only where this helps operational ownership. Do not create a customer as a business unit unless that is genuinely how the organisation manages work.

### Step 3 - Add Claimant Company Details

Go to `/companies`.

This page should normally contain the Telent / M Group claimant company details, not the customer details. For example, National Highways should be entered under `/customers`, not as the claimant company.

Finance should provide:

| Field | Source / instruction |
| --- | --- |
| Company name | Exact legal claimant entity name. Do not guess from trading name. |
| UTR | Finance / Corporation Tax records. Must match CT600. |
| PAYE reference | Payroll / Finance. |
| VAT number | Finance. |
| SIC code | Companies House / Finance. |
| Registered office country / region | Companies House / Finance. |
| Northern Ireland flag | Tick only if the registered office position requires it. |
| Senior internal R&D contact | Named accountable senior internal contact. |
| Agent details | Include Ayming and any other agent involved in advice, cost analysis, forms, or CT return support. |

### Step 4 - Add Accounting Periods

Accounting periods are added on `/companies`.

Add one accounting period per claim period. These dates must align to the Corporation Tax return position and should be confirmed by Finance.

Required fields:

- Label, for example `FY2026` or `AP ending 31 March 2026`.
- Accounting period start.
- Accounting period end.
- Period of account start.
- Period of account end.
- Claim notification submitted, if applicable.
- Claim notification date, if applicable.
- Scheme notes.

Important: The scoring blocker "Missing accounting period" clears only when the accounting period is selected on the R&D project. Creating an accounting period on the company page is step one. Linking it to the project is step two.

### Step 5 - Add Customers

Go to `/customers`.

Each live customer should be assigned to the correct business unit. For transport customers, capture the transport domain and customer type carefully because these facts feed entitlement review.

Minimum fields:

- Customer name.
- Business unit.
- Sector.
- Transport domain.
- Customer type.
- Whether the customer is likely chargeable to Corporation Tax.
- Notes.

Use `unknown` for Corporation Tax status unless Finance or Ayming has confirmed the position. For public-sector and arm's-length bodies, avoid assuming the answer.

### Step 6 - Add Contracts / SOWs

Go to `/contracts`.

Create a contract record for each contract, framework, direct award, statement of work, change request, innovation partnership, subcontract, or grant-funded project that may contain R&D candidate work.

Capture:

- Contract name.
- Customer.
- Contract type.
- Start and end dates.
- Whether customer explicitly requested R&D.
- Whether customer intended or contemplated R&D.
- Whether technical uncertainty was described in bid, contract, SOW, or change request.
- IP / foreground knowledge owner.
- Funding / grant notes.
- Public-sector procurement notes.
- Contract evidence links.

How to populate the four commonly missed fields:

| Field | What to enter |
| --- | --- |
| IP / foreground knowledge owner | Who owns newly developed foreground knowledge, technical designs, reusable assets, software, methods, or inventions. If contract wording is unclear, write `Unknown - contract review required`. |
| Funding / grant notes | Any grant, subsidy, public funding, innovation funding, customer contribution, or payment mechanism relevant to entitlement or cost review. If none known, write `No grant or subsidy identified from current evidence - Finance/Ayming review required`. |
| Public-sector procurement notes | Procurement route, public notice reference, framework/direct award/tender basis, public authority status, and any text showing innovation, renewal, obsolescence, or uncertainty. |
| Contract evidence links | Links or file paths to signed contract, SOW, bid response, award notice, change request, procurement notice, clarification log, or internal contract review note. |

### Step 7 - Add Solutions

Go to `/solutions`.

Create a solution record for the business or technical solution delivered under the customer and contract. This should describe the operational problem and technical architecture clearly enough for later project assessment.

Capture:

- Solution name.
- Customer.
- Contract.
- Solution description.
- Business problem.
- Technical architecture summary.
- Transport environment constraints.
- Initial R&D radar status.
- Reason for radar status.

Use `amber` where a solution looks technically complex but has not yet been broken into specific R&D candidate projects with evidence, costs, and competent professional opinion. Reserve `green` for strong early indicators, but remember the project itself cannot score green without signed competent professional support.

### Step 8 - Create R&D Candidate Projects

Go to `/projects`.

One solution can contain multiple R&D candidate projects. Do not create one large project called "the whole contract" unless the whole contract is genuinely focused on resolving a defined scientific or technological uncertainty.

For each candidate project, capture:

- Project title.
- Solution.
- Accounting period.
- Field of science or technology.
- Baseline level of knowledge/capability at project start.
- Advance sought.
- Why the advance is in the wider field, not just Telent / M Group internal knowledge.
- Scientific or technological uncertainties.
- Why competent professionals could not readily resolve the uncertainty.
- System uncertainty explanation, if relevant.
- Alternatives considered.
- Experiments, prototypes, models, simulations, tests, and iterations.
- Failed attempts.
- Outcome.
- R&D start and end dates.
- Boundary explanation.
- Non-qualifying delivery activities to exclude.

Good project titles are specific:

- `Resolving latency and reliability uncertainty in multi-operator passenger prediction`
- `IP translator approach for legacy roadside operational technology`
- `High-availability telemetry ingestion under constrained edge connectivity`

Weak project titles are broad:

- `National Highways contract`
- `Cloud migration`
- `Dashboard implementation`
- `System upgrade`

### Step 9 - Add Competent Professional Opinion

Open the project and go to `Competent Professional`.

Capture:

- Professional name.
- Role.
- Qualifications.
- Years of relevant experience.
- Relevant field expertise.
- Opinion text.
- Sign-off status.
- Sign-off date.
- Reviewer comments.

The opinion text should explain:

- The relevant field of science or technology.
- The baseline capability at project start.
- The advance sought.
- Why the advance is wider than internal company learning.
- The uncertainties.
- Why they were not readily deducible by a competent professional.
- What work was done to resolve them.
- The project boundary.
- Any exclusions or limitations.

A project cannot be green unless the sign-off status is `signed`.

### Step 10 - Add Evidence

Open the project and go to `Evidence`.

Evidence should be linked to the project as soon as possible. Do not wait until year-end. Evidence is strongest when it is contemporaneous and comes from normal delivery systems.

Good evidence sources include:

- Jira
- Azure DevOps
- GitHub
- Azure Repos
- ServiceNow
- SharePoint
- Confluence
- Teams
- Email
- Finance system
- Timesheet / PSA
- Cloud billing
- Manual upload / note

Use relevance tags consistently:

- `advance`
- `uncertainty`
- `resolution activity`
- `failure`
- `cost`
- `entitlement`
- `project boundary`
- `sign-off`

Evidence strength guidance:

| Strength | Use when |
| --- | --- |
| Strong | Contemporaneous technical evidence directly shows uncertainty, experiment, failure, decision, test result, boundary, or cost. |
| Moderate | Evidence supports the story but needs explanation or is partly indirect. |
| Weak | Evidence is late, generic, incomplete, or only loosely connected. |

Minimum evidence expectation for a strong candidate:

- One item supporting baseline / field position.
- One item supporting advance sought.
- One item supporting uncertainty.
- One item showing experiment, prototype, simulation, test, or iteration.
- One item showing failed attempt or technical learning, where applicable.
- One item supporting boundary dates.
- One item supporting costs.
- Signed competent professional opinion.

### Step 11 - Add Costs And People Time

Open the project and go to `Costs`, or use the top-level `/costs` page for review.

The Hub supports both direct cost lines and people time. For people time, capture the person, role, period, hours or days, rate, activity, apportionment, and evidence link.

Cost categories:

- Staff.
- Externally provided workers.
- Subcontractors.
- Software.
- Cloud computing.
- Data licences.
- Consumables.
- Qualifying indirect activities.
- Other.

People time capture should include:

- Project.
- Activity.
- Person name.
- Person role.
- Time period start and end.
- Hours and hourly rate, or days and day rate.
- Gross cost.
- Apportionment percentage.
- Qualifying amount.
- PAYE / NIC notes.
- Evidence link to timesheet, PSA, payroll report, delivery plan, or activity record.

The Hub calculates:

```text
Qualifying amount = gross cost x apportionment percentage
```

Finance must review:

- Payroll source.
- Rate calculation.
- Paid status.
- Apportionment method.
- UK or overseas location.
- Connected-party status.
- EPW versus subcontractor classification.
- Evidence availability.

The Hub flags:

- Unpaid costs.
- Overseas contractor / EPW costs.
- Missing evidence.
- Apportionment over 100%.
- Missing activity link.

### Step 12 - Review Claimant Entitlement

The entitlement assessment is generated from project, customer, and contract facts. It is a decision-support signal only.

Statuses:

- `supplier_likely`
- `customer_likely`
- `ambiguous_tax_review`
- `blocked`

Facts to capture and review:

- Customer type.
- Customer Corporation Tax status.
- Whether the customer intended or contemplated R&D.
- Whether Telent / M Group initiated the R&D.
- Whether uncertainty was discovered during delivery.
- Whether the contract specified the technical uncertainty.
- Whether another party could claim.
- Whether the work is grant-funded or subsidised.
- Whether Telent / M Group is prime, subcontractor, consortium member, or framework supplier.

Public-sector and arm's-length company work should normally remain `ambiguous_tax_review` until Finance and Ayming have reviewed the contract and customer position.

### Step 13 - Review Scoring And Blockers

Open the project assessment page and review the score panel.

Score categories:

| Category | Weight |
| --- | ---: |
| Qualifying project boundary | 10 |
| Field of science or technology | 10 |
| Advance sought | 20 |
| Scientific / technological uncertainty | 20 |
| Resolution activity | 15 |
| Competent professional support | 10 |
| Cost traceability | 10 |
| Claim entitlement | 5 |

Score bands:

| Score | Rating | Meaning |
| ---: | --- | --- |
| 80 to 100 | Green | Strong candidate |
| 60 to 79 | Amber | Review required |
| 40 to 59 | Weak | Weak candidate |
| Below 40 | Red | Not currently supportable |

Automatic blockers:

- No field of science or technology.
- No advance sought.
- Advance appears only commercial, aesthetic, project management, procurement, or internal learning.
- No scientific or technological uncertainty.
- Uncertainty appears only commercial, budgetary, resourcing, customer adoption, or implementation planning.
- No signed competent professional opinion.
- No evidence linked to the project.
- No linked costs for a claimed project.
- Customer/supplier entitlement is blocked.
- Missing accounting period.
- Additional Information Form timing risk.

Clearing the common blockers:

| Blocker | Where to fix |
| --- | --- |
| Missing accounting period | Add period on `/companies`, then select it on project `/assessment`. |
| No signed competent professional opinion | Project `Competent Professional` tab. |
| No evidence linked to the project | Project `Evidence` tab. |
| No linked costs for a claimed project | Project `Costs` tab. |
| Customer entitlement blocked | Review customer, contract, entitlement facts with Finance/Ayming. |

### Step 14 - Review AIF Readiness

Go to `/claim-periods/{id}/readiness`.

Review:

- Company details.
- Contact details.
- Agent details.
- Accounting period start and end.
- Project count.
- Required project descriptions.
- Project-level field, baseline, advance, uncertainty, method, and costs.
- CT600 submitted status.
- AIF submitted status.
- AIF submission date.
- CT600 submission date.

If CT600 was submitted before the AIF, the Hub shows a red warning. Finance and Ayming must review immediately.

Current configurable AIF project selection logic:

- 1 to 3 projects: describe all.
- 4 to 10 projects: describe at least 3 and enough to cover at least 50% of qualifying expenditure.
- More than 10 projects: describe at least 3 and enough to cover at least 50% of qualifying expenditure. If more than 10 projects would be required, choose the 10 largest by qualifying expenditure.

### Step 15 - Generate Reports

For project-level review, open:

```text
/projects/{id}/report
```

For accounting-period review, open:

```text
/claim-periods/{id}/pack
```

For evidence review, open:

```text
/evidence-index
```

Expected outputs:

- Project Eligibility Memo.
- Claim Period Pack.
- Evidence Index.

These outputs can be downloaded as Markdown and used as working papers for Finance, Ayming, and internal review.

## 9. Finance Handover Standard

Before sending a pack to Finance, each candidate project should have:

- Accounting period selected.
- Solution and customer linked.
- Contract linked.
- Field of science or technology.
- Baseline.
- Advance sought.
- Wider-field explanation.
- Uncertainties.
- Resolution activity.
- Boundary explanation.
- Non-qualifying activities excluded.
- Signed competent professional opinion or explicit reason why pending.
- Evidence items.
- Cost lines.
- Entitlement assessment.
- Known blockers and warnings reviewed.

Finance handover pack should include:

- Claim Period Pack.
- Project Eligibility Memos for candidate projects.
- Evidence Index.
- Cost export or cost page summary.
- AIF readiness screen.
- List of open questions for Finance and Ayming.

Do not hand over a project as a strong candidate if the Hub still shows missing accounting period, no signed competent professional opinion, no evidence, or no costs.

## 10. Ayming Handover Standard

For Ayming review, provide:

- Claimant company and accounting period details.
- Contract/SOW/public procurement references.
- Customer type and Corporation Tax status assumption.
- Entitlement assessment with open questions.
- Project memo for each candidate.
- Competent professional opinion.
- Evidence index.
- Cost lines with apportionment and evidence.
- AIF readiness position.
- CT600/AIF submission dates, if known.
- Rule version and official guidance check date.

Ask Ayming to review:

- Whether each candidate project is technically supportable.
- Whether the boundary is appropriate.
- Whether costs are properly categorised and apportioned.
- Whether entitlement is supplier, customer, ambiguous, or blocked.
- Whether public-sector or contracted-out facts change the position.
- Whether AIF project selection and descriptions are sufficient.
- Whether any costs should be removed, reduced, or reclassified.

## 11. National Highways NRTS3 Example

This example is for demonstrating how to populate the Hub. It is not a claim position.

### Customer Record

| Field | Suggested entry |
| --- | --- |
| Customer name | `National Highways Limited` |
| Business unit | `Highways` |
| Sector | `Public Sector Transport` |
| Transport domain | `highways` |
| Customer type | `arm's-length company` or `other`, pending Finance/Ayming review |
| Likely chargeable to Corporation Tax | `unknown` |
| Notes | `Government-owned strategic highways company responsible for operating, maintaining and improving the Strategic Road Network in England. Companies House number 09346363. Formerly Highways England Company Limited. Public-sector/customer entitlement position requires Finance, competent professional and tax review.` |

Public reference details:

- Legal name: `NATIONAL HIGHWAYS LIMITED`.
- Company number: `09346363`.
- Registered office: `Three Snowhill, Snow Hill Queensway, Birmingham, England, B4 6GA`.
- SIC codes: `43999`, `49390`, `71129`, `84110`.
- General contact: `info@nationalhighways.co.uk`.

Sources:

- [Companies House - National Highways Limited](https://find-and-update.company-information.service.gov.uk/company/09346363)
- [National Highways About Us](https://nationalhighways.co.uk/about-us/)
- [Office of Rail and Road - Holding National Highways to account](https://www.orr.gov.uk/monitoring-and-regulation/roads-monitoring/holding-national-highways-to-account)

### Contract Record

| Field | Suggested entry |
| --- | --- |
| Contract name | `National Roads Telecommunications Service 3 (NRTS3)` |
| Customer | `National Highways Limited` |
| Contract type | Use actual route from the signed contract/procurement record. Public notices show competitive flexible procedure and later direct-award status, so mark carefully and explain in notes. |
| Start date | Public tender notice states estimated contract start around `2027-09-16`; use signed contract date once known. |
| End date | Public tender notice states estimated end around `2035-03-15`, with possible extension to `2037-03-15`; use signed contract date once known. |
| Customer explicitly requested R&D | `No` unless a signed contract, SOW, change request, or innovation project explicitly requires defined R&D. |
| Customer intended or contemplated R&D | `Unknown` or `Yes - review required` where the contract explicitly provides for innovation projects or development/enhancement activity. |
| Technical uncertainty described | `Yes - review required` only where bid/SOW wording identifies uncertainty, not just complex delivery. |
| IP / foreground knowledge owner | `Unknown - contract review required` unless contract clauses identify ownership. |
| Funding / grant notes | `Public-sector funded contract. No separate grant/subsidy identified from current public notice evidence - Finance/Ayming review required.` |
| Public-sector procurement notes | `Find a Tender notices identify National Highways as the contracting authority and describe NRTS3 as provision, operation, maintenance, renewal, development and enhancement of roadside telecommunications/connectivity services for the Strategic Road Network.` |
| Contract evidence links | Link signed contract, SOW, bid response, internal bid pack, Find a Tender notice, direct award notice, clarification log, and any innovation project documentation. |

Public sources:

- [Find a Tender - NRTS3 tender notice 2025/S 000-016468](https://www.find-tender.service.gov.uk/Notice/016468-2025)
- [Find a Tender - NRTS3 direct award notice 2025/S 000-074068](https://www.find-tender.service.gov.uk/Notice/074068-2025?origin=SearchResults&p=1)
- [Find a Tender - NRTS3 prior information notice 2024/S 000-005877](https://www.find-tender.service.gov.uk/Notice/005877-2024)

### Solution Record

| Field | Suggested entry |
| --- | --- |
| Solution name | `NRTS3 Critical Roadside Telecommunications and Operational Connectivity Service` |
| Solution description | `Provision, operation, maintenance, development and enhancement of telecommunications services between roadside operational technology and central locations for National Highways' Strategic Road Network.` |
| Business problem | `National Highways requires resilient, secure and sustainable telecommunications connectivity for roadside operational technology, including CCTV, stopped vehicle detection, variable message signs, signals and emergency roadside telephones.` |
| Technical architecture summary | `National roadside telecommunications environment spanning optical fibre, legacy copper-based services, roadside device connectivity, operational network services, service management, security controls, renewals, deployment services and potential innovation projects.` |
| Constraints to select | `real-time operation`, `safety criticality`, `cyber security`, `legacy integration`, `operational technology / IoT`, `high availability`, `constrained devices`, `traffic optimisation`, `disruption management` |
| Initial R&D radar status | `amber` until specific projects, evidence, costs, and competent professional opinion are added. |
| Reason for radar status | `Complex operational technology and legacy integration environment with possible R&D candidate areas, but whole-contract eligibility is not assumed. Specific uncertainty-resolution projects require evidence, costs, and review.` |

### Possible R&D Candidate Projects

Use these as candidate templates only. They need internal technical evidence and competent professional review.

| Candidate | Why it may be worth assessing |
| --- | --- |
| Internet Protocol Translator for legacy roadside operational technology | Potential system uncertainty around translating between legacy RS485/POTS-dependent roadside technology and IP-based services under operational, latency, reliability, and supportability constraints. |
| Critical obsolescence renewal strategy | Potential uncertainty if replacement strategy requires new technical methods to maintain service continuity and compatibility across constrained roadside assets, not just routine refresh planning. |
| Operational/corporate network convergence option | Potential uncertainty if convergence creates unresolved security, availability, segregation, latency, or operational control problems beyond standard network design. |
| NRTS service management system enhancement | Potential uncertainty if new monitoring, automation, resilience, or predictive capability is developed under non-routine operational constraints. |
| Digital CCTV migration support | Likely delivery unless there is clear uncertainty beyond installation and commissioning of free-issue equipment. |

For each candidate, the technical lead must answer:

- What is the field of science or technology?
- What was known at project start?
- What advance was sought?
- Why is this wider than Telent / M Group internal capability?
- What uncertainty could not be readily resolved?
- What experiments, prototypes, tests, simulations, or failed attempts show the resolution activity?
- When did R&D start and stop?
- Which delivery activities must be excluded?

## 12. Quality Gates

### Gate 1 - Candidate Intake

Minimum:

- BU, customer, contract, and solution exist.
- Candidate project title is specific.
- Accounting period is selected.
- Initial field, advance, uncertainty, and boundary notes are present.

Outcome:

- Red, weak, or amber triage accepted.
- Do not treat as green.

### Gate 2 - Technical Review

Minimum:

- Full assessment completed.
- Competent professional identified.
- Evidence items added.
- Non-qualifying delivery activity excluded.

Outcome:

- Candidate may remain amber pending sign-off.

### Gate 3 - Cost Review

Minimum:

- People time and supplier/direct costs captured.
- Apportionment percentages entered.
- Qualifying amounts calculated.
- Paid status checked.
- Evidence links added.
- Overseas/EPW/subcontractor flags reviewed.

Outcome:

- Finance can reconcile and challenge costs.

### Gate 4 - Competent Professional Sign-Off

Minimum:

- Opinion is signed.
- Opinion explains reasoning clearly.
- Reviewer comments captured.

Outcome:

- Project can become green only if other blockers are cleared.

### Gate 5 - Finance / Ayming Review

Minimum:

- Claim-period pack generated.
- Project memos generated.
- Evidence index generated.
- AIF readiness reviewed.
- Entitlement notes reviewed.

Outcome:

- Pack is ready for external review, not automatic claim submission.

## 13. Data Quality Rules

Use exact legal names for companies and customers.

Use exact accounting period dates from Finance.

Use `unknown` rather than guessing.

Use evidence links that another reviewer can find.

Avoid generic phrases such as:

- `complex project`
- `innovative solution`
- `new to us`
- `difficult integration`
- `business-critical`

Prefer specific phrases such as:

- `uncertain whether the latency target could be met with inconsistent telemetry feeds`
- `uncertain whether legacy roadside protocols could be translated reliably under operational constraints`
- `prototype failed because message ordering could not be preserved under peak load`
- `R&D stopped when the design pattern was validated and remaining work became rollout`

## 14. Common Errors To Avoid

- Adding the customer as the claimant company.
- Creating an accounting period but not linking it to the project.
- Calling the whole contract R&D.
- Describing commercial uncertainty as technical uncertainty.
- Treating standard cloud migration as R&D without a scientific or technological uncertainty.
- Marking a project green before competent professional sign-off.
- Adding costs without evidence.
- Using 100% apportionment without activity evidence.
- Ignoring overseas contractor or EPW flags.
- Treating public-sector entitlement as obvious without tax review.
- Submitting CT600 before AIF without resolving the warning.

## 15. Monthly Operating Cadence

Monthly:

- BU owners review new candidate solutions.
- Engineering leads add or update project assessments.
- Delivery managers add evidence links.
- Finance refreshes cost lines and paid status.
- System owner runs Knowledge Agent source check where internet is available.

Quarterly:

- Review amber and weak candidates.
- Remove or mark blocked projects that no longer have support.
- Confirm cost apportionment methods.
- Confirm competent professional coverage.
- Review AIF readiness for open accounting periods.

Before Finance / Ayming handover:

- Run dashboard review.
- Clear or document blockers.
- Generate claim-period pack.
- Generate project memos.
- Generate evidence index.
- Export or review costs.
- Agree open questions.

## 16. Completion Checklist For A Claim Period

Use this checklist before sending material to Finance or Ayming.

| Check | Complete |
| --- | --- |
| Knowledge Agent checked and rule versions confirmed. |  |
| Claimant company details confirmed by Finance. |  |
| Accounting period dates confirmed by Finance. |  |
| Claim notification status reviewed. |  |
| AIF readiness reviewed. |  |
| CT600/AIF sequencing reviewed. |  |
| Customers assigned to business units. |  |
| Contracts/SOWs linked and evidence added. |  |
| Solutions created and scoped. |  |
| Candidate projects created. |  |
| Each project has accounting period selected. |  |
| Each candidate has field, baseline, advance, uncertainty, and boundary. |  |
| Non-qualifying delivery activities documented. |  |
| Competent professional opinions captured. |  |
| Evidence items added and tagged. |  |
| Costs and people time added. |  |
| Cost evidence links added. |  |
| Overseas/EPW/subcontractor flags reviewed. |  |
| Entitlement status reviewed. |  |
| Blockers and warnings reviewed. |  |
| Project memos generated. |  |
| Claim-period pack generated. |  |
| Evidence index generated. |  |
| Open questions listed for Finance and Ayming. |  |

## 17. Final Governance Statement

The R&D Claim Evidence Hub is a disciplined evidence and workflow tool for Telent / M Group. It improves the quality, consistency, and timing of R&D candidate information, but it does not make final eligibility decisions.

Every output should be treated as:

> Requires competent professional and tax review.

