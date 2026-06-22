# Changelog

## Unreleased - 2026-06-22

Documentation and UX sweep for the current framework-intelligence and assessment-review branch.

- Documented default reference customer seeding alongside the existing business-unit reference seed.
- Documented Framework Intelligence opportunity workbench, source-change, portal-platform, requirement-review, RDEC signal, and next-action review surfaces.
- Added repo-local `AGENTS.md` guidance for future coding agents, including UI/UX score-loop and docs-sweep workflows.
- Improved mobile project-assessment review ordering so the eligibility score, blockers, warnings, and entitlement summary appear before the long assessment form on narrow screens.

Requires competent professional and tax review.

## intelligence-effectiveness 0.1 - 2026-06-07

Improvement wave for the approved RDEC review cockpit, structured outputs, and traceable intelligence epic.

- Added governance ADR approval for the bounded architecture change.
- Planned review-cockpit UI refinements, stronger Markdown review packs, and traceable intelligence enhancements.
- Keeps the local Docker Desktop MVP stack, GOV.UK/HMRC rule guardrails, and decision-support wording.
- No cloud services, secrets, authenticated portal automation, HMRC submission automation, or final claim decisions are added.

Requires competent professional and tax review.

## live-demo-version 1.0 - 2026-05-08

Baseline for the local Telent / M Group live demo.

- Enterprise Telent-styled dashboard and navigation refresh.
- Clean default reference setup for live customer and project entry.
- Business-unit hierarchy for Transport, Network Services, HPC / Hinkley Point C, Nuclear Power, and Core Central Asset Management.
- YAML-backed runtime rules for scoring, blockers, AIF selection, entitlement defaults, cost warnings, and claim-period timing.
- AIF top-10 fallback behaviour for more-than-10-project claim periods.
- Guarded Knowledge Agent for official GOV.UK / HMRC source checks.
- Guarded Framework Intelligence Agent for public-sector customer framework and bid-opportunity tracking.
- Local MVP audit log for key claim-data changes.
- Safer form parsing for common malformed date, number, and enum inputs.
- Markdown reports with rule traceability, cost caveats, entitlement caveats, and review caveats.

This version remains a decision-support MVP. It does not provide legal, tax, accounting, HMRC submission, or bid/no-bid advice. Requires competent professional and tax review.
