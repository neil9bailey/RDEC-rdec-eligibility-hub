# R&D Claim Evidence Hub

## Executive Summary for Mick Mohan, Group Engineering Director

The R&D Claim Evidence Hub is a local, secure, evidence-capture and decision-support application designed to help Telent / M Group identify, assess, evidence, and package potential UK R&D tax relief / RDEC-style claim candidates across engineering and technology delivery work.

The tool is not designed to replace tax advice, competent professional judgement, Finance review, or external advisor review by Ayming. Its purpose is to create a disciplined operational process that captures the right technical, contractual, evidence, and cost information early enough and consistently enough to support robust review.

Every decision-support output remains caveated:

> Requires competent professional and tax review.

## Why This Matters

Telent / M Group delivers technically complex services across transport, network services, nuclear, asset management, and critical infrastructure environments. Some delivery work may involve genuine scientific or technological uncertainty, especially where teams are developing or adapting systems under constraints such as legacy integration, operational technology, real-time performance, cyber security, high availability, constrained devices, inconsistent data, or safety-critical environments.

The business challenge is that potentially eligible work is often identified too late, described inconsistently, or separated from the evidence and costs needed by Finance and external advisors. This creates avoidable friction, rework, missed opportunities, and risk during claim preparation.

The R&D Claim Evidence Hub addresses this by creating a standard internal pathway from engineering activity to review-ready claim evidence.

## What The Hub Does

The application captures and organises:

- Company and accounting period details
- Business units and customer ownership
- Customer sector, transport domain, and public-sector classification
- Contract and statement-of-work facts
- Solution intake and transport / infrastructure constraints
- R&D project assessments
- Competent professional opinions
- Technical uncertainties and resolution activity
- Evidence from engineering and delivery systems
- Cost lines with apportionment and evidence links
- Customer / supplier entitlement indicators
- Additional Information Form readiness
- Claim-period pack summaries
- Project eligibility memos
- Evidence indexes
- Framework intelligence source checks, opportunity workbench summaries, requirement themes, quality questions, and RDEC candidate signals for human review

It creates a structured record that can be reviewed internally before being passed to Finance and Ayming.

## Business Units Included

The current clean instance is configured with the following business-unit reference structure:

- Transport
  - Highways
  - Rail
  - SCADA
  - TfL
- Network Services
- HPC / Hinkley Point C
- Nuclear Power
- Core Central Asset Management

The clean seed also includes reference customer labels for Transport for London (TfL) and National Rail in Rail and SCADA contexts. These are setup aids only; live evidence capture still requires confirmation of the exact customer/legal contracting entity.

Customers can be assigned to these business units as live data is added.

## How It Aligns To Current RDEC / HMRC Guidance

The Hub uses versioned rule files rather than hard-coded advice. These rule files reflect official GOV.UK / HMRC guidance areas including:

- R&D definition
- Advance in science or technology
- Scientific or technological uncertainty
- Competent professional judgement
- Merged RDEC and ERIS from accounting periods beginning on or after 1 April 2024
- Additional Information Form requirements
- Claim notification requirements
- Qualifying cost categories
- Contracted-out R&D and entitlement
- Public-sector / irrelievable customer indicators
- Overseas contractor and externally provided worker restrictions

The Hub also includes a Knowledge Agent that tracks official HMRC / GOV.UK source pages and maps them to the local rule files. The Knowledge Agent can run an optional live check of official sources, record whether they are reachable, detect update markers where possible, and flag when guidance should be reviewed.

The Knowledge Agent does not automatically change the rules. It creates governance around rule maintenance so changes can be reviewed properly by competent professionals, Finance, Tax, and Ayming.

## How The Scoring Works

Each project is assessed using weighted categories:

- Qualifying project boundary
- Field of science or technology
- Advance sought
- Scientific or technological uncertainty
- Resolution activity
- Competent professional support
- Cost traceability
- Claim entitlement

The output is not a claim decision. It is a triage and readiness indicator:

- Green: strong R&D candidate
- Amber: review required
- Weak: weak candidate
- Red: not currently supportable

The system also applies blockers, including:

- No field of science or technology
- No advance sought
- No scientific or technological uncertainty
- No signed competent professional opinion
- No evidence
- No linked costs
- Customer entitlement blocked
- Missing accounting period
- Additional Information Form timing risk

A project cannot become green without a signed competent professional opinion.

## Benefits To Telent / M Group

### 1. Earlier Identification Of R&D Candidates

Engineering teams can record technical uncertainty and resolution activity while the work is happening, instead of trying to reconstruct it months later.

### 2. Better Evidence Quality

The Hub provides an evidence ledger that links each project to source references such as Jira, Azure DevOps, GitHub, SharePoint, Confluence, ServiceNow, Teams, email, timesheets, cloud billing, finance systems, and manual notes.

This helps demonstrate:

- What advance was sought
- What uncertainty existed
- Why it was not readily resolvable
- What experiments, prototypes, tests, or iterations were performed
- What failed or changed
- When R&D started and stopped
- Which costs relate to qualifying activity

### 3. Stronger Finance Handover

Finance can receive structured cost lines rather than unstructured engineering narratives. Each cost line includes:

- Project
- Activity
- Cost category
- Person or supplier
- Gross cost
- Apportionment percentage
- Calculated qualifying amount
- Paid status
- UK / overseas flag
- Connected-party status
- PAYE / NIC notes
- Evidence link

The Hub flags missing evidence, unpaid costs, overseas contractor / EPW indicators, apportionment over 100%, and missing activity links.

### 4. Cleaner External Advisor Review With Ayming

Ayming can be provided with consistent claim pack material rather than fragmented spreadsheets, emails, and technical write-ups.

Outputs include:

- Project Eligibility Memo
- Claim Period Pack
- Evidence Index
- AIF readiness summary
- Entitlement notes
- Cost summary by category
- Blockers, warnings, and recommended actions

This should help Ayming focus their time on technical and tax review rather than data chasing.

### 5. Better Governance And Audit Readiness

The Hub creates a repeatable internal process for review, sign-off, and evidence capture. It makes gaps visible before submission pressure starts.

It also supports internal challenge by showing why a project is green, amber, weak, red, blocked, or pending review.

### 6. Improved Public-Sector Contract Handling

Because much of the business works with public sector and transport customers, entitlement can be complex. The Hub captures facts such as:

- Customer type
- Whether the customer is likely chargeable to Corporation Tax
- Whether the customer intended or contemplated R&D
- Whether the supplier discovered uncertainty during delivery
- Whether the contract specified technical uncertainty
- Whether the work was grant-funded or subsidised
- Whether another party could claim
- Whether the company was prime, subcontractor, consortium member, or framework supplier

The system then assigns an entitlement status:

- supplier_likely
- customer_likely
- ambiguous_tax_review
- blocked

This gives Finance and Ayming a clearer starting point for entitlement review.

## How It Would Be Used Operationally

### Step 1: Set Up Business Context

Add or confirm:

- Business unit
- Company details
- Accounting period
- Customer
- Contract / SOW
- Solution

### Step 2: Capture Candidate Projects

Engineering or delivery leads add potential R&D projects linked to a solution and accounting period.

They capture:

- Baseline knowledge
- Advance sought
- Wider-field explanation
- Scientific or technological uncertainties
- Why competent professionals could not readily resolve the uncertainty
- Experiments, prototypes, simulations, tests, iterations
- Failed attempts
- Outcome
- R&D start and stop boundaries
- Non-qualifying delivery activity to exclude

### Step 3: Add Competent Professional Opinion

A competent professional records their opinion and sign-off status. A project remains blocked from green status until this is signed.

### Step 4: Add Evidence

Evidence is linked to each project and tagged by relevance:

- Advance
- Uncertainty
- Resolution activity
- Failure
- Cost
- Entitlement
- Project boundary
- Sign-off

### Step 5: Add Costs

Costs are captured and apportioned. The Hub calculates qualifying amounts and flags review issues.

### Step 6: Review Scoring And Gaps

The dashboard and project pages show:

- Candidate status
- Blockers
- Warnings
- Missing evidence
- Missing competent professional opinion
- Cost traceability issues
- Entitlement risk

### Step 7: Prepare Finance And Ayming Pack

For each accounting period, the Hub generates:

- Claim Period Pack
- Project Eligibility Memos
- Evidence Index
- AIF readiness summary
- Cost category summaries

These can be downloaded as Markdown and converted into the required internal or external working papers.

## How This Helps Finance

Finance receives a cleaner and more traceable dataset:

- Costs linked to named R&D projects
- Costs linked to activities
- Costs categorised consistently
- Apportionment percentages visible
- Qualifying amounts calculated
- Evidence links attached
- Review flags surfaced early
- Claim-period summaries prepared

This should reduce manual reconciliation and help Finance challenge weak or unsupported costs before they reach Ayming.

## How This Helps Ayming

Ayming can review structured evidence packs containing:

- Technical rationale
- Competent professional support
- Contract and entitlement facts
- Evidence index
- Cost apportionment logic
- AIF project selection support
- Known blockers and unresolved questions

This can make the external review process more efficient and more robust, especially where customer contracts and public-sector entitlement need careful judgement.

## Controls And Caveats

The Hub is deliberately conservative:

- It does not provide legal or tax advice.
- It does not submit claims.
- It does not decide eligibility.
- It does not auto-update rules based on web content.
- It marks outputs as requiring competent professional and tax review.
- It treats missing evidence, missing costs, and missing sign-off as blockers.

The tool should be used as a controlled evidence and workflow layer feeding Finance and Ayming, not as an automated claim engine.

## Suggested Rollout

1. Confirm business-unit ownership and user group.
2. Select one pilot accounting period.
3. Add a small number of live candidate projects from Transport, Network Services, HPC, Nuclear Power, or Asset Management.
4. Ask competent professionals to complete opinions for candidate projects.
5. Add cost and evidence links with Finance support.
6. Generate packs and ask Ayming to review format, completeness, and usefulness.
7. Refine rules, fields, and exports based on Finance and Ayming feedback.
8. Scale to wider business-unit usage.

## Future Opportunities

The current MVP is local and does not require cloud services or secrets. Future enhancements could include:

- Jira integration
- Azure DevOps integration
- GitHub integration
- ServiceNow integration
- SharePoint and Confluence evidence search
- PSA / timesheet integration
- ERP / finance integration
- Azure / AWS / GCP cloud billing imports
- SSO
- Role-based access control
- Immutable audit log
- PDF exports
- Advisor review workflow

## Bottom Line

The R&D Claim Evidence Hub gives Telent / M Group a practical way to turn engineering delivery knowledge into structured, review-ready R&D claim evidence.

It should help the business identify stronger candidates earlier, reduce unsupported claims, improve Finance handover, and give Ayming a clearer, more complete evidence pack to support their review and claim process.

It is a governance and evidence-quality tool, not a tax advice engine.
