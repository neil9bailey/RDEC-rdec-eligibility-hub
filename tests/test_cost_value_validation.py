"""Finding B1: cost value validation.

Runtime-proven stored values before this change: gross 1000 @ 500% -> qualifying
5000; @ -50% -> -500; gross -1000 -> -1000; 1e308 -> inf; "nan" -> HTTP 500.

``parse_float`` is frozen by ADR-0002 Ruling R3, so these tests pin the new
additive helpers plus the arithmetic guard, not a change to ``parse_float``.
"""

from math import isfinite, isinf, isnan

import pytest

from app.form_utils import (
    MAX_MONETARY_AMOUNT,
    parse_decimal_amount,
    parse_float,
    parse_money,
    parse_percentage,
)
from app.models import CostLine
from app.services import calculate_qualifying_amount, cost_validation_warnings, project_qualifying_spend


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_parse_float_still_admits_non_finite_values(raw):
    """Negative control: the frozen helper is unchanged, which is why B1 exists."""
    errors: list[str] = []

    parsed = parse_float(raw, "Gross cost", errors)

    assert errors == []
    assert isnan(parsed) or isinf(parsed)


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity", "1e309"])
def test_parse_money_rejects_non_finite_values(raw):
    errors: list[str] = []

    parsed = parse_money(raw, "Gross cost", errors)

    assert parsed == 0.0
    assert errors == ["Gross cost must be a real number. Values such as nan, inf and -inf are not accepted."]


def test_parse_money_rejects_negative_values():
    errors: list[str] = []

    assert parse_money("-1000", "Gross cost", errors) == 0.0
    assert errors == ["Gross cost cannot be negative."]


def test_parse_money_allows_negative_when_explicitly_permitted():
    errors: list[str] = []

    assert parse_money("-1000", "Adjustment", errors, allow_negative=True) == -1000.0
    assert errors == []


def test_parse_money_rejects_absurd_magnitudes_that_overflow_totals():
    errors: list[str] = []

    assert parse_money("1e308", "Gross cost", errors) == 0.0
    assert errors == ["Gross cost must be 1,000,000,000,000 or less."]
    assert parse_money(str(MAX_MONETARY_AMOUNT), "Gross cost", []) == MAX_MONETARY_AMOUNT


def test_parse_money_rejects_underscore_separators():
    errors: list[str] = []

    assert parse_money("1_000", "Gross cost", errors) == 0.0
    assert errors == ["Gross cost must be a plain number without underscore separators."]


def test_parse_money_accepts_a_normal_figure_and_an_empty_field():
    errors: list[str] = []

    assert parse_money("1000.55", "Gross cost", errors) == 1000.55
    assert parse_money("", "Gross cost", errors) == 0.0
    assert parse_money("", "Gross cost", errors, default=12.0) == 12.0
    assert errors == []


@pytest.mark.parametrize(
    "raw,message",
    [
        ("500", "Apportionment percentage must be 100 or less."),
        ("-50", "Apportionment percentage cannot be negative."),
        ("nan", "Apportionment percentage must be a real number. Values such as nan, inf and -inf are not accepted."),
    ],
)
def test_parse_percentage_rejects_the_proven_bad_values(raw, message):
    errors: list[str] = []

    assert parse_percentage(raw, "Apportionment percentage", errors) == 0.0
    assert errors == [message]


def test_parse_percentage_accepts_the_full_valid_range():
    errors: list[str] = []

    assert parse_percentage("0", "Apportionment percentage", errors) == 0.0
    assert parse_percentage("37.5", "Apportionment percentage", errors) == 37.5
    assert parse_percentage("100", "Apportionment percentage", errors) == 100.0
    assert errors == []


def test_parse_decimal_amount_reports_every_rejection_in_the_errors_list():
    errors: list[str] = []

    parse_decimal_amount("-5", "Hours", errors)
    parse_decimal_amount("nan", "Day rate", errors)

    assert errors == [
        "Hours cannot be negative.",
        "Day rate must be a real number. Values such as nan, inf and -inf are not accepted.",
    ]


@pytest.mark.parametrize(
    "gross,percentage",
    [(float("nan"), 50), (float("inf"), 50), (1000, float("nan")), (1e308, 500)],
)
def test_calculate_qualifying_amount_never_returns_a_non_finite_number(gross, percentage):
    amount = calculate_qualifying_amount(gross, percentage)

    assert isfinite(amount)
    assert amount == 0.0


def test_calculate_qualifying_amount_arithmetic_is_unchanged_for_valid_input():
    assert calculate_qualifying_amount(1000, 37.5) == 375
    assert calculate_qualifying_amount(1000, 100) == 1000
    assert calculate_qualifying_amount(0, 50) == 0


def test_non_finite_cost_line_is_flagged_and_excluded_from_totals():
    good = CostLine(
        project_id=1,
        cost_category="staff",
        gross_cost=1000,
        apportionment_percentage=50,
        qualifying_amount=500,
        paid_status="paid",
        uk_or_overseas="UK",
        evidence_link="Timesheet 1",
        activity="Prototype",
    )
    poisoned = CostLine(
        project_id=1,
        cost_category="staff",
        gross_cost=float("inf"),
        apportionment_percentage=50,
        paid_status="paid",
        uk_or_overseas="UK",
        evidence_link="Timesheet 2",
        activity="Prototype",
    )

    warnings = cost_validation_warnings(poisoned)
    total = project_qualifying_spend([good, poisoned])

    assert any("not a real number" in warning for warning in warnings)
    assert isfinite(total)
    assert total == 500


def test_negative_gross_cost_is_flagged_for_review():
    cost = CostLine(
        project_id=1,
        cost_category="staff",
        gross_cost=-1000,
        apportionment_percentage=100,
        paid_status="paid",
        uk_or_overseas="UK",
        evidence_link="Invoice 9",
        activity="Prototype",
    )

    warnings = cost_validation_warnings(cost)

    assert any("negative gross cost" in warning for warning in warnings)
