# Framework Source And Portal Intelligence Extension

Implemented: 2026-05-16  
Scope: Framework Intelligence -> Opportunity Catalogue extension  
Design reference: `COF Design Draft.pdf`, focused on "Five sources. One pipeline. Documented." and "800 portals, four platforms."

## Purpose

This extension turns the existing Framework Intelligence area into a configurable procurement intelligence catalogue. It supports a single normalised opportunity pipeline for public-sector notice sources, plus a separate buyer-portal layer for ITT documents, quality questions, weightings, clarifications and attachments.

The feature is procurement and R&D candidate intelligence only. It does not make bid/no-bid, legal, tax, procurement, accounting, HMRC submission, or RDEC eligibility decisions. Requires competent professional and tax review.

## Official/Public Sources Checked

Checked on 2026-05-16:

- GOV.UK Open Contracting, confirming OCDS outputs for Find a Tender and Contracts Finder.
- GOV.UK Contracts Finder guidance, confirming Contracts Finder, Find a Tender, Public Contracts Scotland, Sell2Wales, eSourcing NI and eTendersNI as public-sector procurement discovery routes.
- Find a Tender data/API documentation.
- Contracts Finder API documentation.
- Public Contracts Scotland OCDS API help page.
- TED eForms / TED developer documentation.

## Implemented Capability

- Runtime YAML configuration for procurement source catalogue:
  - `app/rules/framework_sources.yml`
  - `app/rules/procurement_platforms.yml`
  - `app/rules/source_change_tracking.yml`
- Seeded source catalogue for:
  - Find a Tender
  - Contracts Finder
  - Public Contracts Scotland
  - Sell2Wales
  - TED eForms
  - Tenders Direct commercial backup, inactive by default
- Seeded portal platform families for:
  - ProContract
  - In-Tend
  - Jaggaer
  - Delta eSourcing
- Source change snapshots with hash comparison:
  - first seen
  - unchanged
  - changed
  - failed
- Buyer portal instance records mapped to customers and business units.
- Manual retrieval tasks for opportunity documents.
- Opportunity document capture with platform name, retrieval status, summary and permitted document text excerpts.
- Local extraction of ITT quality questions, weighting signals, requirement themes and RDEC candidate prompts for human review.
- Markdown framework intelligence reports now include source config versions, portal platform config versions, source changes, documents, and ITT quality question summaries.

## New Pages

- `/framework-intelligence/source-catalogue`
- `/framework-intelligence/source-changes`
- `/framework-intelligence/portal-platforms`
- `/framework-intelligence/opportunities/{id}/documents`

Existing pages remain available:

- `/framework-intelligence/sources`
- `/framework-intelligence/opportunities`
- `/framework-intelligence/requirements`
- `/framework-intelligence/reports`

## Guardrails

- Live checks require HTTPS and approved source domains.
- Commercial aggregator source is inactive by default and requires licence/approval.
- Portal platform capability is manual-assisted only.
- No portal credentials are stored by the MVP.
- No portal login, expression of interest, submission, or customer communication is automated.
- Document text extraction is only from permitted summaries, links, paths, or pasted excerpts supplied by a user.
- Extracted requirements and quality questions default to pending human review.
- RDEC candidate outputs are prompts only and retain: "Requires competent professional and tax review."

## Operating Pattern

1. Configure or review sources in `/framework-intelligence/source-catalogue`.
2. Run explicit guarded source checks from the catalogue or watch profiles.
3. Review source changes in `/framework-intelligence/source-changes`.
4. Manage buyer portal families and customer portal instances in `/framework-intelligence/portal-platforms`.
5. Open an opportunity and add document links, manual retrieval tasks, or permitted document excerpts.
6. Review extracted quality questions, weightings, requirement themes and RDEC candidate signals.
7. Generate a Framework Intelligence Report for bid, engineering, Finance and Ayming discussions.

## Future Controlled Enhancements

- Authenticated connector design with managed secrets outside the repo.
- Portal-specific browser automation after legal/procurement approval.
- SharePoint document storage integration.
- Immutable audit/event store.
- Role-based access control and SSO.
- Scheduler for approved source checks.
- Richer eForms and Sell2Wales normalisation.
- Human approval workflow for bid/no-bid and RDEC candidate escalation.
