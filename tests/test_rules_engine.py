from sqlmodel import select

from app.models import RDProject
from app.services import (
    aif_project_selection,
    assess_entitlement,
    calculate_project_score,
    calculate_people_time_gross,
    calculate_qualifying_amount,
    cost_validation_warnings,
)
from app.models import CostLine


def project_by_title(session, title_part: str) -> RDProject:
    return session.exec(select(RDProject).where(RDProject.project_title.contains(title_part))).first()


def test_score_calculation_for_seed_projects(seeded_session):
    strong = project_by_title(seeded_session, "Passenger Flow")
    amber = project_by_title(seeded_session, "Ticketing Event")
    red = project_by_title(seeded_session, "Dashboard Migration")

    strong_score = calculate_project_score(seeded_session, strong.id)
    amber_score = calculate_project_score(seeded_session, amber.id)
    red_score = calculate_project_score(seeded_session, red.id)

    assert strong_score.rating == "green"
    assert strong_score.score >= 80
    assert amber_score.rating == "amber"
    assert 60 <= amber_score.score <= 79
    assert red_score.rating == "red"


def test_automatic_blockers_for_red_project(seeded_session):
    red = project_by_title(seeded_session, "Dashboard Migration")
    score = calculate_project_score(seeded_session, red.id)

    assert "No field of science or technology." in score.blockers
    assert "No signed competent professional opinion." in score.blockers
    assert "No evidence linked to the project." in score.blockers
    assert "No linked costs for a claimed project." in score.blockers


def test_claimant_entitlement_statuses():
    supplier = assess_entitlement(
        customer_type="local authority",
        customer_corporation_tax_status="no",
        customer_intended_or_contemplated_rd=False,
        supplier_initiated_rd=True,
        uncertainty_discovered_during_delivery=True,
        contract_specified_technical_uncertainty=False,
        another_party_could_claim=False,
        grant_funded_or_subsidised=False,
        company_role="framework supplier",
    )
    customer = assess_entitlement(
        customer_type="private transport operator",
        customer_corporation_tax_status="yes",
        customer_intended_or_contemplated_rd=True,
        supplier_initiated_rd=False,
        uncertainty_discovered_during_delivery=False,
        contract_specified_technical_uncertainty=True,
        another_party_could_claim=False,
        grant_funded_or_subsidised=False,
        company_role="prime",
    )
    blocked = assess_entitlement(
        customer_type="private transport operator",
        customer_corporation_tax_status="yes",
        customer_intended_or_contemplated_rd=True,
        supplier_initiated_rd=False,
        uncertainty_discovered_during_delivery=False,
        contract_specified_technical_uncertainty=True,
        another_party_could_claim=True,
        grant_funded_or_subsidised=False,
        company_role="subcontractor",
    )

    assert supplier.status == "supplier_likely"
    assert customer.status == "customer_likely"
    assert blocked.status == "blocked"


def test_cost_apportionment_and_flags():
    assert calculate_qualifying_amount(1000, 37.5) == 375
    assert calculate_people_time_gross(hours=10, hourly_rate=75, days=2, day_rate=600) == 1950
    cost = CostLine(
        project_id=1,
        cost_category="subcontractors",
        gross_cost=1000,
        apportionment_percentage=120,
        paid_status="unpaid",
        uk_or_overseas="overseas",
        evidence_link="",
        activity="",
    )
    warnings = cost_validation_warnings(cost)
    assert any("not fully paid" in warning for warning in warnings)
    assert any("overseas contractor/EPW" in warning for warning in warnings)
    assert any("missing cost evidence" in warning for warning in warnings)
    assert any("over 100%" in warning for warning in warnings)
    assert any("activity link" in warning for warning in warnings)


def test_people_time_validation_flags_missing_time_and_rate():
    cost = CostLine(
        project_id=1,
        cost_input_type="people_time",
        cost_category="staff",
        person_or_supplier_name="",
        gross_cost=0,
        apportionment_percentage=50,
        paid_status="paid",
        uk_or_overseas="UK",
        activity="Prototype investigation",
        evidence_link="Timesheet: 123",
    )

    warnings = cost_validation_warnings(cost)

    assert any("person name" in warning for warning in warnings)
    assert any("no people time" in warning for warning in warnings)
    assert any("no people rate" in warning for warning in warnings)


def test_aif_project_selection_logic():
    all_projects = aif_project_selection({1: 100, 2: 75, 3: 25}, {1, 2, 3})
    assert all_projects.selected_project_ids == [1, 2, 3]
    assert all_projects.ready

    four_to_ten = aif_project_selection({1: 100, 2: 70, 3: 40, 4: 20}, {1, 2, 3})
    assert four_to_ten.selected_project_ids == [1, 2, 3]
    assert four_to_ten.coverage_percentage >= 50

    many = aif_project_selection({i: 1 for i in range(1, 31)}, set(range(1, 11)))
    assert len(many.selected_project_ids) == 10
    assert many.coverage_percentage < 50
    assert not many.ready
