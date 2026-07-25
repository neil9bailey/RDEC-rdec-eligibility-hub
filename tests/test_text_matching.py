"""Regression tests for ADR-0003 term-matching precision.

Each case is named after the finding it closes. The two negative controls are the
runtime-proven narratives that were hard-blocked to RED before this change.
"""

from pathlib import Path

import pytest

from app import framework_intelligence, services
from app.rules_engine import get_rules
from app.text_matching import find_matches, matched_terms, normalise


# The two realistic R&D descriptions proven at runtime to be wrongly hard-blocked.
PROVEN_COMMERCIALLY_AVAILABLE = (
    "There was no commercially available product that met the latency requirement."
)
PROVEN_PROCUREMENT_AVAILABLE = (
    "The team needed a procurement-available option beyond existing catalogue items."
)
# Longer runtime-proven variants of the same two defects.
PROVEN_COMMERCIALLY_AVAILABLE_LONG = (
    "No commercially available product can reconcile the signalling telemetry feeds within "
    "the required latency budget, so a new reconciliation approach had to be attempted."
)
PROVEN_PROCUREMENT_AVAILABLE_LONG = (
    "Adaptive control had to operate beyond any procurement-available traffic controller, "
    "because no catalogue device exposed the interface timings the trial required."
)
# A realistic narrative that was correctly NOT blocked, retained as a control that
# the fix did not simply silence the matcher. The fourth adversarial-review
# narrative was not carried into this repository verbatim, so this equivalent
# clean narrative stands in for it.
CONTROL_CLEAN_NARRATIVE = (
    "The team sought to establish whether a distributed timing model could hold sub-50ms "
    "synchronisation across degraded trackside links, which competent professionals in the "
    "field could not resolve from published signalling literature."
)


def advance_terms() -> list[str]:
    return get_rules().negative_advance_terms()


def uncertainty_terms() -> list[str]:
    return get_rules().negative_uncertainty_terms()


def test_normalise_preserves_hyphens_and_pads_the_text():
    assert normalise("  Procurement-Available   Option \n") == " procurement-available option "


def test_commercially_available_is_not_a_commercial_advance_match():
    """Finding A1, proven case 1: 'commercially' must not fire the term 'commercial'."""
    assert matched_terms(PROVEN_COMMERCIALLY_AVAILABLE, advance_terms()) == []
    assert matched_terms(PROVEN_COMMERCIALLY_AVAILABLE_LONG, advance_terms()) == []


def test_procurement_available_is_not_a_procurement_advance_match():
    """Finding A1, proven case 2: a hyphen compound must not fire the bare term.

    This is the case that a plain \\b word boundary does NOT fix, because a hyphen
    is itself a word boundary.
    """
    assert matched_terms(PROVEN_PROCUREMENT_AVAILABLE, advance_terms()) == []
    assert matched_terms(PROVEN_PROCUREMENT_AVAILABLE_LONG, advance_terms()) == []


def test_word_boundary_alone_would_not_have_closed_the_second_finding():
    """Negative control on the cheap fix: \\b still matches 'procurement-available'."""
    import re

    assert re.search(r"\bprocurement\b", PROVEN_PROCUREMENT_AVAILABLE) is not None
    assert matched_terms(PROVEN_PROCUREMENT_AVAILABLE, ["procurement"]) == []


def test_clean_realistic_narrative_stays_unflagged():
    assert matched_terms(CONTROL_CLEAN_NARRATIVE, advance_terms()) == []
    assert matched_terms(CONTROL_CLEAN_NARRATIVE, uncertainty_terms()) == []


def test_seeded_red_project_uncertainty_terms_still_match():
    """True positives must survive the fix; 'budgetary' must not be invented."""
    text = "Commercial implementation planning, resourcing, and customer adoption risks."

    assert matched_terms(text, uncertainty_terms()) == [
        "commercial",
        "implementation planning",
        "resourcing",
        "customer adoption",
    ]
    assert "budgetary" not in matched_terms(text, uncertainty_terms())


def test_seeded_red_project_advance_terms_still_match():
    text = (
        "Internal learning and standard cloud migration delivery for a public sector "
        "reporting dashboard."
    )

    assert matched_terms(text, advance_terms()) == ["internal learning"]


@pytest.mark.parametrize(
    "text,term",
    [
        ("Commercial implementation planning was the only driver.", "commercial"),
        ("Procurement of standard catalogue items covered the work.", "procurement"),
    ],
)
def test_true_positive_whole_token_matches_are_preserved(text, term):
    assert term in matched_terms(text, advance_terms())


def test_stop_phrases_suppress_a_true_whole_token_hit():
    stop_phrases = get_rules().review_flag_stop_phrases("advance")
    text = "No commercial solution existed for the trackside reconciliation problem."

    assert "commercial" in matched_terms(text, advance_terms())
    assert matched_terms(text, advance_terms(), stop_phrases) == []


def test_stop_phrases_only_suppress_hits_inside_their_own_span():
    stop_phrases = get_rules().review_flag_stop_phrases("advance")
    text = "No commercial option existed, and commercial pressure drove the delivery plan."

    matches = find_matches(text, ["commercial"], stop_phrases)

    assert len(matches) == 1
    assert "commercial pressure" in matches[0].excerpt


def test_matches_carry_the_term_and_a_quoted_excerpt():
    matches = find_matches("Commercial implementation planning risks.", uncertainty_terms())

    assert [match.term for match in matches] == ["commercial", "implementation planning"]
    assert all(match.excerpt for match in matches)
    assert matches[0].start < matches[1].start


def test_matching_is_deterministic_and_does_not_mutate_the_input():
    text = "Commercial implementation planning, resourcing, and customer adoption risks."
    first = find_matches(text, uncertainty_terms())
    second = find_matches(text, uncertainty_terms())

    assert first == second
    assert text == "Commercial implementation planning, resourcing, and customer adoption risks."


def test_services_and_framework_intelligence_resolve_to_the_same_matcher():
    """Anti-drift: one matcher, no parallel implementation (ADR-0003 D4)."""
    assert services.find_matches is find_matches
    assert framework_intelligence.find_matches is find_matches


def test_both_call_paths_produce_identical_match_spans():
    text = "Commercial platform procurement-available implementation planning."
    terms = ["commercial", "platform", "procurement", "implementation planning"]

    via_services = services.find_matches(text, terms)
    via_framework = framework_intelligence.find_matches(text, terms)

    normalised = normalise(text)
    expected = [
        (term, normalised.index(term), normalised.index(term) + len(term))
        for term in ["commercial", "platform", "implementation planning"]
    ]

    assert via_services == via_framework
    assert [(match.term, match.start, match.end) for match in via_services] == expected


PROVEN_STATION_PLATFORM = "Station platform resurfacing and drainage works"


def test_station_platform_resurfacing_is_not_corroborated_software_development():
    """Finding E6-4, proven case: unanchored theme matching classified civil works as software.

    ADR-0003 D5.3/D8 conformance outcome: the theme is still surfaced for a human,
    at low confidence, with zero corroboration and therefore zero R&D signals.
    """
    themes = framework_intelligence.requirement_themes_for_text(PROVEN_STATION_PLATFORM)
    matches = framework_intelligence.requirement_theme_matches(PROVEN_STATION_PLATFORM)

    assert themes == ["software development"]
    assert len(matches) == 1
    assert matches[0].matched_patterns == ("platform",)
    assert matches[0].corroborating_patterns == ()
    assert matches[0].corroborated is False
    assert matches[0].confidence == "low"


def test_genuine_software_development_text_is_still_corroborated():
    text = "Digital service platform for bespoke software development and application integration."

    matches = {match.theme: match for match in framework_intelligence.requirement_theme_matches(text)}

    assert matches["software development"].corroborated is True
    assert matches["software development"].confidence == "medium"
    assert len(matches["software development"].corroborating_patterns) > 1


def test_a_lone_generic_pattern_never_corroborates_a_theme():
    matches = {
        match.theme: match
        for match in framework_intelligence.requirement_theme_matches("A new data feed is required.")
    }

    assert matches["data and analytics"].matched_patterns == ("data",)
    assert matches["data and analytics"].corroborated is False


def test_contains_any_is_deleted_from_the_application_package():
    """ADR-0003 D4 conformance proof: no surviving private substring matcher."""
    app_dir = Path(services.__file__).resolve().parent
    offenders = [
        path.name
        for path in sorted(app_dir.rglob("*.py"))
        if "contains_any" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
