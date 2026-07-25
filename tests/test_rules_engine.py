from datetime import date

import pytest
from sqlmodel import select

from app import rule_loader
from app.models import RDProject
from app.rules_engine import clear_rules_cache, get_rules, validate_all_rules
from app.services import (
    aif_project_selection,
    assess_entitlement,
    claim_notification_deadline,
    deadline_warning,
    calculate_project_score,
    calculate_people_time_gross,
    calculate_qualifying_amount,
    cost_validation_warnings,
)
from app.models import AccountingPeriod, CostLine


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
    # ADR-0003 D7/D8. Wording judgements are review flags, never blockers.
    assert len(score.blockers) == 4
    assert not any("Advance appears only" in b for b in score.blockers)
    assert not any("Uncertainty appears only" in b for b in score.blockers)
    assert any("internal learning" in w.lower() for w in score.warnings)
    assert any("customer adoption" in w.lower() for w in score.warnings)
    assert score.rating == "red"
    assert score.score < 40
    # The flag must name the terms that actually fired, and only those.
    matched_detail = " ".join(
        warning.split("Matched terms:", 1)[1]
        for warning in score.warnings
        if "Matched terms:" in warning
    )
    for term in ["internal learning", "commercial", "implementation planning", "resourcing", "customer adoption"]:
        assert f'"{term}"' in matched_detail
    assert '"budgetary"' not in matched_detail
    assert "Explain more clearly why the advance is in the wider field, not only internal knowledge." in score.warnings
    assert "Competent professional uncertainty explanation is thin." in score.warnings
    assert "Experiments, prototypes, tests, or iterations are not yet described." in score.warnings
    assert "Requires competent professional and tax review." in score.recommended_next_actions


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
    assert any("unpaid costs" in warning for warning in warnings)
    assert any("overseas contractor or EPW" in warning for warning in warnings)
    assert any("missing evidence" in warning for warning in warnings)
    assert any("over 100%" in warning for warning in warnings)
    assert any("missing activity link" in warning for warning in warnings)


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
    assert many.ready
    assert "Top 10 fallback applied" in many.notes[0]


def test_aif_top_10_fallback_ready_for_equal_spend_projects():
    selection = aif_project_selection({i: 1 for i in range(1, 31)}, set(range(1, 11)))

    assert selection.selected_project_ids == list(range(1, 11))
    assert selection.coverage_percentage == 33.33
    assert selection.ready
    assert selection.selection_method == "top 10 fallback"
    assert any("Top 10 fallback applied" in note for note in selection.notes)


def test_aif_top_10_fallback_requires_selected_descriptions():
    selection = aif_project_selection({i: 1 for i in range(1, 31)}, {1, 2, 3})

    assert selection.selected_project_ids == list(range(1, 11))
    assert not selection.ready
    assert any("Selected project descriptions missing" in warning for warning in selection.warnings)


def test_aif_more_than_10_selects_projects_that_reach_50_percent():
    spend = {1: 20, 2: 15, 3: 10, 4: 10}
    spend.update({i: 5 for i in range(5, 14)})

    selection = aif_project_selection(spend, {1, 2, 3, 4})

    assert selection.selected_project_ids == [1, 2, 3, 4]
    assert selection.coverage_percentage >= 50
    assert selection.ready
    assert not selection.notes


def test_aif_four_to_ten_not_ready_without_required_descriptions():
    selection = aif_project_selection({1: 40, 2: 30, 3: 20, 4: 10}, {1, 2})

    assert selection.selected_project_ids == [1, 2, 3]
    assert not selection.ready
    assert any("Selected project descriptions missing" in warning for warning in selection.warnings)


def test_aif_four_to_ten_not_ready_when_selected_projects_fail_50_percent():
    selection = aif_project_selection({1: 0, 2: 0, 3: 0, 4: 0}, {1, 2, 3})

    assert selection.coverage_percentage == 0
    assert not selection.ready
    assert any("50%" in warning for warning in selection.warnings)


@pytest.mark.parametrize("project_count", [1, 2, 3, 4])
def test_aif_zero_qualifying_expenditure_is_never_ready(project_count):
    """Proven defect: 1-3 zero-spend projects returned ready=True at 0% coverage."""
    spend = {index: 0.0 for index in range(1, project_count + 1)}

    selection = aif_project_selection(spend, set(spend))

    assert selection.total_qualifying_expenditure == 0
    assert selection.coverage_percentage == 0
    assert not selection.ready
    assert any("No qualifying expenditure is recorded" in warning for warning in selection.warnings)


def test_aif_zero_spend_warning_does_not_fire_for_a_funded_period():
    selection = aif_project_selection({1: 100.0, 2: 75.0, 3: 25.0}, {1, 2, 3})

    assert selection.ready
    assert not any("No qualifying expenditure is recorded" in warning for warning in selection.warnings)


def test_aif_one_to_three_require_all_descriptions():
    selection = aif_project_selection({1: 100, 2: 75, 3: 25}, {1, 2})

    assert selection.selected_project_ids == [1, 2, 3]
    assert not selection.ready
    assert any("Selected project descriptions missing" in warning for warning in selection.warnings)


def test_rule_accessors_expose_customer_defaults_and_blocker_labels():
    rules = get_rules()

    assert rules.default_corporation_tax_status("local authority") == "no"
    assert rules.default_corporation_tax_status("private transport operator") == "yes"
    assert rules.default_corporation_tax_status("public corporation") == "unknown"
    assert rules.blocker_label("missing_field") == "No field of science or technology."


def test_score_blocker_labels_are_read_from_yaml(tmp_path, monkeypatch, seeded_session):
    original_rules_dir = rule_loader.RULES_DIR
    for path in original_rules_dir.glob("*.yml"):
        target = tmp_path / path.name
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    blockers_path = tmp_path / "blockers.yml"
    text = blockers_path.read_text(encoding="utf-8")
    text = text.replace("No field of science or technology", "Custom field blocker")
    blockers_path.write_text(text, encoding="utf-8")

    red = project_by_title(seeded_session, "Dashboard Migration")
    monkeypatch.setattr(rule_loader, "RULES_DIR", tmp_path)
    clear_rules_cache()
    try:
        score = calculate_project_score(seeded_session, red.id)
        assert "Custom field blocker." in score.blockers
    finally:
        monkeypatch.setattr(rule_loader, "RULES_DIR", original_rules_dir)
        clear_rules_cache()


def test_optional_review_flag_keys_are_not_required_at_startup(tmp_path, monkeypatch, seeded_session):
    """An operator removing the optional ADR-0003 keys must not brick the app."""
    original_rules_dir = rule_loader.RULES_DIR
    for path in original_rules_dir.glob("*.yml"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    weights_path = tmp_path / "eligibility_weights.yml"
    weights_path.write_text(
        weights_path.read_text(encoding="utf-8").split("review_flag_stop_phrases:")[0],
        encoding="utf-8",
    )
    blockers_path = tmp_path / "blockers.yml"
    blockers_path.write_text(
        blockers_path.read_text(encoding="utf-8").split("review_flags:")[0],
        encoding="utf-8",
    )

    red = project_by_title(seeded_session, "Dashboard Migration")
    monkeypatch.setattr(rule_loader, "RULES_DIR", tmp_path)
    clear_rules_cache()
    try:
        validate_all_rules()
        rules = get_rules()
        assert "review_flag_stop_phrases" not in rules.eligibility
        assert "review_flags" not in rules.blockers
        assert rules.review_flag_stop_phrases("advance") == []
        assert rules.review_flag_label("non_technical_advance_review") == "Non Technical Advance Review."
        score = calculate_project_score(seeded_session, red.id)
        assert score.rating == "red"
        assert any("Matched terms:" in warning for warning in score.warnings)
    finally:
        monkeypatch.setattr(rule_loader, "RULES_DIR", original_rules_dir)
        clear_rules_cache()


def test_review_flag_keys_are_not_startup_required_keys():
    from app.rules_engine import REQUIRED_RULE_KEYS

    assert "review_flag_stop_phrases" not in REQUIRED_RULE_KEYS["eligibility_weights.yml"]
    assert "review_flags" not in REQUIRED_RULE_KEYS["blockers.yml"]
    assert "negative_advance_terms" in REQUIRED_RULE_KEYS["eligibility_weights.yml"]
    assert "negative_uncertainty_terms" in REQUIRED_RULE_KEYS["eligibility_weights.yml"]


def test_claim_notification_warning_window_uses_yaml_value():
    rules = get_rules()
    period = AccountingPeriod(
        company_id=1,
        label="AP",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        period_of_account_start=date(2025, 1, 1),
        period_of_account_end=date(2025, 12, 31),
    )
    deadline = claim_notification_deadline(period)
    today = deadline.replace(day=deadline.day - 1)

    assert rules.claim_notification_warning_days() == 60
    assert deadline_warning(period, today=today) is not None


def test_changing_aif_threshold_config_changes_behaviour(tmp_path, monkeypatch):
    original_rules_dir = rule_loader.RULES_DIR
    for path in original_rules_dir.glob("*.yml"):
        target = tmp_path / path.name
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    aif_path = tmp_path / "aif_rules.yml"
    text = aif_path.read_text(encoding="utf-8")
    text = text.replace("minimum_qualifying_expenditure_percentage: 50", "minimum_qualifying_expenditure_percentage: 80")
    aif_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(rule_loader, "RULES_DIR", tmp_path)
    clear_rules_cache()
    try:
        selection = aif_project_selection({1: 40, 2: 20, 3: 15, 4: 10, 5: 10, 6: 5}, {1, 2, 3, 4})
        assert selection.selected_project_ids == [1, 2, 3, 4]
        assert selection.coverage_percentage >= 80
    finally:
        monkeypatch.setattr(rule_loader, "RULES_DIR", original_rules_dir)
        clear_rules_cache()
