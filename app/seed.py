from datetime import date

from sqlmodel import Session, select

from app.models import (
    AccountingPeriod,
    Activity,
    ClaimPeriodSubmissionStatus,
    Company,
    CompetentProfessionalOpinion,
    Contract,
    CostLine,
    Customer,
    EvidenceItem,
    RDProject,
    ReviewDecision,
    Solution,
    TechnicalUncertainty,
)
from app.services import calculate_qualifying_amount, sync_entitlement_for_project


def seed_demo_data(session: Session) -> None:
    if session.exec(select(Company)).first():
        return

    company = Company(
        company_name="Northstar Digital Services Ltd",
        utr="1234567890",
        paye_reference="951/NA12345",
        vat_number="GB123456789",
        sic_code="62020",
        registered_office_country="United Kingdom",
        registered_office_region="England",
        northern_ireland_registered=False,
        senior_rd_contact_name="Amira Shah",
        senior_rd_contact_role="Chief Technology Officer",
        senior_rd_contact_email="amira.shah@example.local",
        senior_rd_contact_phone="020 7000 0000",
        agent_name="Evidence & Tax Review LLP",
        agent_reference="AGT-4422",
        agent_email="agent@example.local",
        agent_phone="020 7000 1111",
        agent_role="R&D claim preparation review",
    )
    session.add(company)
    session.commit()
    session.refresh(company)

    period = AccountingPeriod(
        company_id=company.id or 0,
        label="FY2025/26",
        start_date=date(2025, 4, 1),
        end_date=date(2026, 3, 31),
        period_of_account_start=date(2025, 4, 1),
        period_of_account_end=date(2026, 3, 31),
        claim_notification_submitted=False,
        prior_claim_within_3_years=True,
    )
    session.add(period)
    session.commit()
    session.refresh(period)

    session.add(
        ClaimPeriodSubmissionStatus(
            accounting_period_id=period.id or 0,
            ct600_submitted=False,
            aif_submitted=False,
            notes="Demo status: AIF preparation in progress; CT600 not submitted.",
        )
    )

    public_customer = Customer(
        customer_name="West Midlands Combined Transport Authority",
        sector="Public sector transport",
        transport_domain="multimodal",
        customer_type="mayoral / combined authority",
        corporation_tax_status="no",
        notes="Public authority customer; supplier entitlement needs contracted-out and irrelievable client review.",
    )
    private_customer = Customer(
        customer_name="MetroRail Operating Ltd",
        sector="Transport operator",
        transport_domain="rail",
        customer_type="private transport operator",
        corporation_tax_status="yes",
        notes="Private operator likely within Corporation Tax; customer/supplier entitlement needs fact review.",
    )
    council_customer = Customer(
        customer_name="Northshire County Council Highways",
        sector="Local government",
        transport_domain="highways",
        customer_type="local authority",
        corporation_tax_status="no",
        notes="Local authority customer for standard cloud delivery example.",
    )
    session.add_all([public_customer, private_customer, council_customer])
    session.commit()
    for item in [public_customer, private_customer, council_customer]:
        session.refresh(item)

    contract_a = Contract(
        contract_name="Passenger Insight Framework - Work Order 7",
        customer_id=public_customer.id or 0,
        contract_type="framework",
        start_date=date(2025, 4, 7),
        end_date=date(2026, 2, 27),
        customer_requested_rd=False,
        customer_intended_or_contemplated_rd=False,
        technical_uncertainty_described=False,
        ip_owner="Supplier foreground platform methods retained by Northstar; customer receives operational licence.",
        funding_grant_notes="No grant funding identified.",
        public_sector_procurement_notes="Framework call-off focused on passenger prediction outcomes, not defined R&D.",
        contract_evidence_links="SharePoint:/contracts/WMCTA-WO7.pdf",
    )
    contract_b = Contract(
        contract_name="MetroRail Ticketing Event SOW",
        customer_id=private_customer.id or 0,
        contract_type="statement of work",
        start_date=date(2025, 5, 1),
        end_date=date(2026, 1, 31),
        customer_requested_rd=False,
        customer_intended_or_contemplated_rd=True,
        technical_uncertainty_described=False,
        ip_owner="Mixed: integration adapters customer-specific; reusable event mapping retained by supplier.",
        funding_grant_notes="No grants identified.",
        public_sector_procurement_notes="Private operator procurement.",
        contract_evidence_links="SharePoint:/contracts/MR-ticketing-sow.docx",
    )
    contract_c = Contract(
        contract_name="Highways Reporting Cloud Migration",
        customer_id=council_customer.id or 0,
        contract_type="competitive tender",
        start_date=date(2025, 6, 1),
        end_date=date(2025, 12, 31),
        customer_requested_rd=False,
        customer_intended_or_contemplated_rd=False,
        technical_uncertainty_described=False,
        ip_owner="Customer owns migrated reports and configuration.",
        funding_grant_notes="No grant funding identified.",
        public_sector_procurement_notes="Standard migration procured on fixed requirements.",
        contract_evidence_links="SharePoint:/contracts/NCC-cloud-migration.pdf",
    )
    session.add_all([contract_a, contract_b, contract_c])
    session.commit()
    for item in [contract_a, contract_b, contract_c]:
        session.refresh(item)

    solution_a = Solution(
        solution_name="Real-time Passenger Prediction Platform",
        customer_id=public_customer.id or 0,
        contract_id=contract_a.id,
        solution_description="Predicts passenger disruption and crowding across inconsistent multi-operator feeds.",
        business_problem="Operators needed reliable real-time forecasts despite delayed, missing, and contradictory data.",
        technical_architecture_summary="Streaming ingestion, feature reconciliation, probabilistic prediction, and resilient APIs.",
        transport_environment_constraints="real-time operation, high availability, poor / inconsistent data, multi-operator data, passenger-facing service, disruption management",
        initial_radar_status="green",
        radar_reason="Strong indicators: system uncertainty, experiments, signed opinion, and cost/evidence traceability.",
    )
    solution_b = Solution(
        solution_name="Legacy Ticketing Event Integration",
        customer_id=private_customer.id or 0,
        contract_id=contract_b.id,
        solution_description="Integrates legacy ticketing events into a cloud event architecture.",
        business_problem="Legacy fare events needed normalisation for downstream revenue protection and passenger services.",
        technical_architecture_summary="Legacy pollers, event transformation, message bus, and replay pipeline.",
        transport_environment_constraints="legacy integration, fare / ticketing, high availability, poor / inconsistent data",
        initial_radar_status="amber",
        radar_reason="Some technical uncertainty exists, but evidence and wider-field explanation are incomplete.",
    )
    solution_c = Solution(
        solution_name="Highways Dashboard Cloud Migration",
        customer_id=council_customer.id or 0,
        contract_id=contract_c.id,
        solution_description="Migrates existing dashboards and reports to managed cloud hosting.",
        business_problem="Customer wanted lower infrastructure overhead and updated report access.",
        technical_architecture_summary="Standard lift-and-shift with managed database and dashboard tooling.",
        transport_environment_constraints="highways, high availability",
        initial_radar_status="red",
        radar_reason="No scientific or technological uncertainty identified.",
    )
    session.add_all([solution_a, solution_b, solution_c])
    session.commit()
    for item in [solution_a, solution_b, solution_c]:
        session.refresh(item)

    project_a = RDProject(
        solution_id=solution_a.id or 0,
        accounting_period_id=period.id,
        project_title="Probabilistic Passenger Flow Prediction Under Inconsistent Multi-Operator Data",
        field_of_science_or_technology="Software engineering: distributed streaming systems, probabilistic forecasting, and resilient data reconciliation.",
        baseline_knowledge="At project start, existing passenger prediction components assumed timely and internally consistent feeds from a single operator.",
        advance_sought="Create an appreciably improved real-time prediction method that remained reliable when multi-operator feeds arrived late, contradicted each other, or disappeared under disruption.",
        wider_field_explanation="The work sought an advance beyond Northstar's internal implementation knowledge because available transport prediction patterns did not readily explain how to preserve low-latency reliability with conflicting multi-operator data, live disruption feedback, and passenger-facing service-level constraints.",
        scientific_or_technological_uncertainties="Whether a streaming reconciliation and prediction architecture could infer credible passenger flows when source feeds had inconsistent timestamps, missing vehicle events, and contradictory operator identifiers.",
        competent_professionals_could_not_resolve="Competent professionals could not readily deduce the solution from public patterns because the uncertainty sat in the combined behaviour of stream ordering, probabilistic inference, stale-data decay, and transport-specific operational constraints.",
        system_uncertainty_explanation="The uncertainty was in how subsystems behaved together under live disruption, not in any single standard component.",
        alternatives_considered="Batch reconciliation, operator-specific models, fixed confidence thresholds, and full replacement of legacy feeds.",
        experiments_prototypes_tests="Built replay harnesses, latency simulators, stale-feed decay prototypes, confidence scoring models, and failure-mode tests against anonymised disruption windows.",
        failed_attempts="Initial fixed-window reconciliation produced unstable forecasts during service disruption and was replaced by adaptive confidence windows.",
        outcome="partially resolved",
        rd_start_date=date(2025, 4, 14),
        rd_end_date=date(2025, 12, 19),
        boundary_explanation="R&D started when the team began testing whether inconsistent multi-operator feeds could be reconciled in real time, and stopped when the adaptive model met agreed reliability thresholds.",
        non_qualifying_delivery_activities="Exclude routine UI polishing, standard deployment automation, service desk configuration, and customer training.",
        supplier_initiated_rd=True,
        uncertainty_discovered_during_delivery=True,
        contract_specified_technical_uncertainty=False,
        another_party_could_claim=False,
        grant_funded_or_subsidised=False,
        company_role="framework supplier",
        described_in_aif=True,
    )
    project_b = RDProject(
        solution_id=solution_b.id or 0,
        accounting_period_id=period.id,
        project_title="Legacy Ticketing Event Replay into Cloud Event Architecture",
        field_of_science_or_technology="Software engineering: event-driven integration and data consistency.",
        baseline_knowledge="Known adapters could move normalised events into cloud queues when source systems were stable.",
        advance_sought="Improve event replay reliability for old ticketing feeds with partial ordering and duplicate events.",
        wider_field_explanation="Needs more work to show this was not just Northstar learning the customer's legacy estate.",
        scientific_or_technological_uncertainties="Whether duplicate and out-of-order fare events could be reconstructed without corrupting downstream state.",
        competent_professionals_could_not_resolve="The team has not yet written a full competent professional explanation of why known integration patterns were insufficient.",
        system_uncertainty_explanation="Uncertainty arose from the combined behaviour of legacy polling, replay, idempotency, and event versioning.",
        alternatives_considered="Direct database replication, nightly batches, and bespoke point-to-point adapters.",
        experiments_prototypes_tests="Prototype replay adapter and duplicate-event tests.",
        failed_attempts="First adapter produced repeated tap-in events under replay.",
        outcome="partially resolved",
        rd_start_date=date(2025, 5, 20),
        rd_end_date=date(2025, 10, 10),
        boundary_explanation="R&D boundary is currently based on adapter prototype dates and needs stronger sprint evidence.",
        non_qualifying_delivery_activities="Exclude standard cloud environment setup, customer workshops, and dashboard configuration.",
        supplier_initiated_rd=True,
        uncertainty_discovered_during_delivery=True,
        contract_specified_technical_uncertainty=False,
        another_party_could_claim=False,
        grant_funded_or_subsidised=False,
        company_role="prime",
        described_in_aif=False,
    )
    project_c = RDProject(
        solution_id=solution_c.id or 0,
        accounting_period_id=period.id,
        project_title="Standard Dashboard Migration to Managed Cloud",
        field_of_science_or_technology="",
        baseline_knowledge="The team had existing cloud migration runbooks and dashboard deployment templates.",
        advance_sought="Internal learning and standard cloud migration delivery for a public sector reporting dashboard.",
        wider_field_explanation="No wider-field advance identified.",
        scientific_or_technological_uncertainties="Commercial implementation planning, resourcing, and customer adoption risks.",
        competent_professionals_could_not_resolve="",
        system_uncertainty_explanation="",
        alternatives_considered="Managed database and serverless hosting options.",
        experiments_prototypes_tests="",
        failed_attempts="",
        outcome="resolved",
        rd_start_date=date(2025, 6, 3),
        rd_end_date=date(2025, 9, 30),
        boundary_explanation="Delivery followed standard migration phases; no R&D boundary identified.",
        non_qualifying_delivery_activities="All captured activities appear to be standard delivery.",
        supplier_initiated_rd=False,
        uncertainty_discovered_during_delivery=False,
        contract_specified_technical_uncertainty=False,
        another_party_could_claim=False,
        grant_funded_or_subsidised=False,
        company_role="prime",
        described_in_aif=False,
    )
    session.add_all([project_a, project_b, project_c])
    session.commit()
    for item in [project_a, project_b, project_c]:
        session.refresh(item)

    session.add_all(
        [
            TechnicalUncertainty(
                project_id=project_a.id or 0,
                summary="Real-time reconciliation of delayed and contradictory multi-operator feeds.",
                why_not_readily_resolvable="Known single-operator prediction patterns did not address the combined failure modes.",
                status="partially resolved",
            ),
            Activity(
                project_id=project_a.id or 0,
                activity_name="Latency replay harness",
                activity_type="simulation",
                description="Replayed historic disruption windows with injected feed delay and drop-out patterns.",
                start_date=date(2025, 4, 21),
                end_date=date(2025, 6, 30),
                qualifying_boundary_note="Directly tested the feasibility of the proposed prediction approach.",
            ),
            Activity(
                project_id=project_a.id or 0,
                activity_name="Adaptive confidence model prototype",
                activity_type="prototype",
                description="Tested adaptive stale-data confidence windows against passenger prediction accuracy.",
                start_date=date(2025, 7, 1),
                end_date=date(2025, 11, 14),
                qualifying_boundary_note="Prototype work to resolve the system uncertainty.",
            ),
            Activity(
                project_id=project_b.id or 0,
                activity_name="Replay adapter spike",
                activity_type="technical spike",
                description="Prototype adapter for duplicate and out-of-order fare events.",
                start_date=date(2025, 6, 9),
                end_date=date(2025, 7, 18),
                qualifying_boundary_note="Potentially qualifying, but evidence is incomplete.",
            ),
        ]
    )

    session.add_all(
        [
            CompetentProfessionalOpinion(
                project_id=project_a.id or 0,
                professional_name="Dr Lena Morris",
                role="Principal Distributed Systems Engineer",
                qualifications="PhD Computer Science; AWS Certified Solutions Architect Professional",
                years_relevant_experience=18,
                relevant_field_expertise="Distributed stream processing, transport data platforms, and reliability engineering.",
                opinion_text="In my opinion, this project had strong indicators of seeking an advance in software engineering for real-time transport prediction under unresolved system uncertainty. The uncertainty was not readily resolvable by applying known streaming patterns without experimental work.",
                signoff_status="signed",
                signoff_date=date(2026, 1, 16),
                reviewer_comments="Signed for internal evidence pack; tax review still required.",
            ),
            CompetentProfessionalOpinion(
                project_id=project_b.id or 0,
                professional_name="Samir Patel",
                role="Integration Architect",
                qualifications="BSc Computer Science; TOGAF; Azure Solutions Architect Expert",
                years_relevant_experience=14,
                relevant_field_expertise="Transport ticketing integration and event-driven systems.",
                opinion_text="The replay adapter work may have involved technological uncertainty around event ordering and idempotency, but the evidence pack needs better comparison against available patterns before the project can be treated as a strong candidate.",
                signoff_status="signed",
                signoff_date=date(2025, 11, 3),
                reviewer_comments="Signed as amber: more evidence required.",
            ),
        ]
    )

    session.add_all(
        [
            EvidenceItem(
                project_id=project_a.id or 0,
                source_system="Azure DevOps",
                source_reference="ADO-2417",
                url_or_file_path="https://dev.azure.local/Northstar/_workitems/edit/2417",
                date_created=date(2025, 4, 22),
                evidence_type="technical spike",
                relevance_tag="uncertainty",
                strength="strong",
                notes="Spike records unresolved feed-ordering uncertainty.",
            ),
            EvidenceItem(
                project_id=project_a.id or 0,
                source_system="Confluence",
                source_reference="ADR-009",
                url_or_file_path="Confluence:/PassengerPrediction/ADR-009",
                date_created=date(2025, 6, 3),
                evidence_type="architecture decision",
                relevance_tag="advance",
                strength="strong",
                notes="Records adaptive confidence window decision.",
            ),
            EvidenceItem(
                project_id=project_a.id or 0,
                source_system="GitHub",
                source_reference="PR-812",
                url_or_file_path="https://github.local/northstar/passenger-prediction/pull/812",
                date_created=date(2025, 8, 8),
                evidence_type="prototype",
                relevance_tag="resolution activity",
                strength="moderate",
                notes="Prototype branch for stale-feed decay.",
            ),
            EvidenceItem(
                project_id=project_a.id or 0,
                source_system="Cloud billing",
                source_reference="AZ-2025-09-STREAM",
                url_or_file_path="Finance:/cloud-billing/2025-09-streaming.csv",
                date_created=date(2025, 9, 30),
                evidence_type="cost evidence",
                relevance_tag="cost",
                strength="strong",
                notes="Cloud simulation costs matched to replay harness.",
            ),
            EvidenceItem(
                project_id=project_b.id or 0,
                source_system="Jira",
                source_reference="TICK-118",
                url_or_file_path="https://jira.local/browse/TICK-118",
                date_created=date(2025, 6, 12),
                evidence_type="technical spike",
                relevance_tag="uncertainty",
                strength="weak",
                notes="Mentions duplicate events but lacks test detail.",
            ),
        ]
    )

    costs = [
        CostLine(
            project_id=project_a.id or 0,
            activity="Latency replay harness",
            cost_category="staff",
            person_or_supplier_name="Platform engineering team",
            gross_cost=142000,
            apportionment_percentage=72,
            paid_status="paid",
            uk_or_overseas="UK",
            connected_party_status="unconnected",
            paye_nic_notes="Employees paid through UK PAYE.",
            evidence_link="Timesheet / PSA: PP-2025-Q2",
            notes="Apportioned using sprint-level time records.",
        ),
        CostLine(
            project_id=project_a.id or 0,
            activity="Adaptive confidence model prototype",
            cost_category="cloud computing",
            person_or_supplier_name="Azure subscription",
            gross_cost=36000,
            apportionment_percentage=64,
            paid_status="paid",
            uk_or_overseas="UK",
            connected_party_status="unconnected",
            evidence_link="Cloud billing: AZ-2025-09-STREAM",
            notes="Simulation environment only.",
        ),
        CostLine(
            project_id=project_a.id or 0,
            activity="Latency replay harness",
            cost_category="data licences",
            person_or_supplier_name="Historic disruption data set",
            gross_cost=18000,
            apportionment_percentage=85,
            paid_status="paid",
            uk_or_overseas="UK",
            connected_party_status="unconnected",
            evidence_link="Finance: INV-DATA-2025-08",
            notes="Used directly in replay experiments.",
        ),
        CostLine(
            project_id=project_b.id or 0,
            activity="Replay adapter spike",
            cost_category="staff",
            person_or_supplier_name="Integration team",
            gross_cost=76000,
            apportionment_percentage=40,
            paid_status="paid",
            uk_or_overseas="UK",
            connected_party_status="unconnected",
            evidence_link="",
            notes="Timesheet export not yet linked.",
        ),
    ]
    for cost in costs:
        cost.qualifying_amount = calculate_qualifying_amount(cost.gross_cost, cost.apportionment_percentage)
    session.add_all(costs)

    session.add(
        ReviewDecision(
            project_id=project_a.id or 0,
            reviewer_name="Amira Shah",
            decision_status="internal R&D candidate approved",
            comments="Proceed to tax review; do not submit without advisor sign-off.",
        )
    )
    session.commit()

    for project in [project_a, project_b, project_c]:
        sync_entitlement_for_project(session, project.id or 0)
