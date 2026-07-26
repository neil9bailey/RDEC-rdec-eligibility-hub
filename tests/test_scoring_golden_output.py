"""Golden-output invariant for the decision engine (Epic 7, E7-SC2), tiered under ADR-0002 R9.

Why a byte-identical golden rather than behavioural assertions
-------------------------------------------------------------
The Epic 7 performance work replaces per-project queries with batched loads. A batched loader that
subtly changes row ORDER (blockers and warnings are ordered lists), NULL handling (a missing
solution, contract or submission status), or which row wins a ``.first()`` would change published
scores with no other test in the suite failing. That is the highest-risk silent regression in the
programme: a governance decision is only auditable if the same inputs keep producing the same
output. So the expected values below are hard-coded LITERALS captured from the seeded dataset, and
the comparison is string equality on canonical JSON, not a field-by-field assertion.

The two tiers, and which half you are allowed to touch (ADR-0002 R9)
-------------------------------------------------------------------
**Tier 1 -- decision-bearing. Byte-for-byte, immutable without an EA ruling.** The score, the
rating *value* (``green``/``amber``/``weak``/``red`` -- a CSS class and a ``dashboard_metrics``
key), the blocker set and its exact strings, warning-set membership and count, the entitlement
status values inside the AIF result, and the ordering and structure of all of these. These encode
what the Hub asserts. A change here is a change of meaning and returns to G1. The expected values
are deliberately NOT computed from the system under test; regenerating them to make a red test
green is the failure this file exists to prevent.

**Tier 2 -- advisory prose.** Recommended next actions, cap explanations and the band text a
reviewer reads. Still asserted, so accidental drift still fails a test, but:

* where the sentence is derived from a rule file, the assertion is on the DERIVATION -- the
  sentence rendered from the current ``description`` -- not on a frozen literal, so an operator
  editing ``eligibility_weights.yml`` gets consistent copy instead of a red suite;
* a copy change that is itself approved may re-baseline the Tier 2 literal in the same commit, by
  the owning role, without an EA ruling, provided the commit message names the approval; and
* Tier 2 additionally carries invariants that do not depend on exact words and therefore survive
  every re-baseline: the caveat is present, no rating band is named by its stored value, and no
  verdict vocabulary appears.

R9's reason for the split, in EA's words: *a golden that pins everything equally teaches its
readers that no part of it may be touched, and the first person who needs to change a comma either
stops the line or quietly weakens the invariant.*

Splitting the file lost nothing. Tier 1 elides only the sentences Tier 2 asserts by derivation, and
the pre-split literal is reconstructable from (Tier 1 + Tier 2 + the derivations) byte-for-byte.
"""

from __future__ import annotations

import json
import re

from sqlmodel import Session, col, select

from app.models import AccountingPeriod, EntitlementAssessment, RDProject
from app.rules_engine import get_rules
from app.schemas import AIFSelectionResult, ScoreResult
from app.services import (
    CAVEAT,
    aif_readiness_for_period,
    bulk_project_contexts,
    calculate_project_score,
    dashboard_metrics,
    get_project_context,
)


SEEDED_PROJECT_COUNT = 3
SEEDED_PERIOD_COUNT = 1


def canonical_json(payload: object) -> str:
    """Deterministic JSON: dict keys sorted, list order preserved, ASCII only.

    List order is preserved on purpose -- the order of blockers and warnings is part of the
    published output a reviewer reads, so a reordering must fail this test.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)


def ordered_projects(session: Session) -> list[RDProject]:
    """Projects in a stable order that does not depend on insertion or query plan."""
    projects = list(session.exec(select(RDProject)))
    return sorted(projects, key=lambda project: (project.project_title, project.id or 0))


def score_payload(score: ScoreResult) -> dict:
    """Both tiers together. Used only where actual is compared with actual, never with a literal."""
    return {
        "score": score.score,
        "rating": score.rating,
        "rating_label": score.rating_label,
        "blockers": list(score.blockers),
        "warnings": list(score.warnings),
        "positive_indicators": list(score.positive_indicators),
        "recommended_next_actions": list(score.recommended_next_actions),
    }


def per_project_payload(
    session: Session,
    builder,
    key: str,
    scores_by_id: dict[int, ScoreResult] | None = None,
) -> list[dict]:
    """One payload per project, keyed by title so it survives id renumbering."""
    payload: list[dict] = []
    for project in ordered_projects(session):
        project_id = project.id or 0
        score = (
            scores_by_id[project_id]
            if scores_by_id is not None
            else calculate_project_score(session, project_id)
        )
        payload.append({"project_title": project.project_title, key: builder(score)})
    return payload


def project_scores_payload(session: Session, scores_by_id: dict[int, ScoreResult] | None = None) -> list[dict]:
    return per_project_payload(session, score_payload, "score", scores_by_id)


# ---------------------------------------------------------------------------
# The Tier 2 derivations.
#
# These live here, above both tiers, because Tier 1 needs to know which strings
# to elide and Tier 2 needs to assert they were rendered from the rule file.
# They are TIER 2: the band word comes from ``eligibility_weights.yml`` through
# the same accessor ``app/services.py`` uses (ADR-0002 R8), so an operator
# editing a description changes the sentence and this file follows.
# ---------------------------------------------------------------------------

def cap_explanation() -> str:
    """The warning the engine adds when warnings hold a would-be top-band project down."""
    amber_description = str(get_rules().score_bands()["amber"]["description"])
    return f'Warnings keep this project at "{amber_description}" until the review points are resolved.'


def competent_professional_next_action() -> str:
    """The next action offered when no signed competent professional opinion is present."""
    green_description = str(get_rules().score_bands()["green"]["description"])
    return f'Obtain a signed competent professional opinion before treating this project as "{green_description}".'


def derived_advisory_sentences() -> set[str]:
    return {cap_explanation(), competent_professional_next_action()}


# ===========================================================================
# TIER 1 -- DECISION-BEARING OUTPUT.
#
# Byte-for-byte. What the Hub asserts about a project. Immutable without an EA
# ruling: a change here is a change of meaning and returns to G1.
# ===========================================================================

# Stands in for a sentence Tier 2 asserts by derivation. Its presence pins that the sentence is
# THERE, at that position, in a list of that length; its wording is Tier 2's business.
DERIVED_ADVISORY = "<derived advisory prose: asserted by the Tier 2 derivation tests>"


def decision_payload(score: ScoreResult) -> dict:
    """TIER 1. Everything the Hub asserts, with the derived advisory sentences elided."""
    advisory = derived_advisory_sentences()
    return {
        "score": score.score,
        "rating": score.rating,
        "blockers": list(score.blockers),
        "warnings": [DERIVED_ADVISORY if warning in advisory else warning for warning in score.warnings],
        "warning_count": len(score.warnings),
        "positive_indicators": list(score.positive_indicators),
        "recommended_next_action_count": len(score.recommended_next_actions),
    }


def project_decisions_payload(
    session: Session, scores_by_id: dict[int, ScoreResult] | None = None
) -> list[dict]:
    return per_project_payload(session, decision_payload, "decision", scores_by_id)


def selection_payload(selection: AIFSelectionResult) -> dict:
    return {
        "project_count": selection.project_count,
        "total_qualifying_expenditure": selection.total_qualifying_expenditure,
        "selected_project_ids": list(selection.selected_project_ids),
        "selected_qualifying_expenditure": selection.selected_qualifying_expenditure,
        "coverage_percentage": selection.coverage_percentage,
        "minimum_described_projects": selection.minimum_described_projects,
        "selection_method": selection.selection_method,
        "notes": list(selection.notes),
        "ready": selection.ready,
        "warnings": list(selection.warnings),
    }


def aif_readiness_payload(session: Session) -> list[dict]:
    """TIER 1. The full AIF readiness result for every accounting period."""
    periods = list(session.exec(select(AccountingPeriod).order_by(col(AccountingPeriod.start_date))))
    payload: list[dict] = []
    for period in periods:
        readiness = aif_readiness_for_period(session, period.id or 0)
        submission = readiness["submission"]
        payload.append(
            {
                "period_label": readiness["period"].label,
                "company_name": readiness["company"].company_name if readiness["company"] else None,
                # Project order is part of the output: AIF selection ties are broken by id.
                "project_titles": [project.project_title for project in readiness["projects"]],
                "selection": selection_payload(readiness["selection"]),
                "submission_recorded": submission is not None,
                "warnings": list(readiness["warnings"]),
                "ready": readiness["ready"],
            }
        )
    return payload


GOLDEN_PROJECT_DECISIONS = r"""[
  {
    "decision": {
      "blockers": [],
      "positive_indicators": [
        "Accounting period linked.",
        "Project boundary has start and narrative support.",
        "Field of science or technology captured.",
        "Advance sought recorded beyond ordinary delivery language.",
        "Scientific or technological uncertainty captured.",
        "Signed competent professional opinion present.",
        "1 evidence item(s) linked."
      ],
      "rating": "amber",
      "recommended_next_action_count": 2,
      "score": 79,
      "warning_count": 5,
      "warnings": [
        "Resolution activity exists but needs stronger activity-level evidence.",
        "Evidence exists but no item is marked strong.",
        "Cost line 4: Flag missing evidence.",
        "Claim entitlement is ambiguous and needs tax review.",
        "<derived advisory prose: asserted by the Tier 2 derivation tests>"
      ]
    },
    "project_title": "Legacy Ticketing Event Replay into Cloud Event Architecture"
  },
  {
    "decision": {
      "blockers": [],
      "positive_indicators": [
        "Accounting period linked.",
        "Project boundary has start and narrative support.",
        "Field of science or technology captured.",
        "Advance sought recorded beyond ordinary delivery language.",
        "Scientific or technological uncertainty captured.",
        "Resolution activity is supported by activities and technical narrative.",
        "Signed competent professional opinion present.",
        "4 evidence item(s) linked.",
        "Cost lines have evidence and valid apportionment.",
        "Supplier entitlement indicators are present."
      ],
      "rating": "green",
      "recommended_next_action_count": 1,
      "score": 100,
      "warning_count": 0,
      "warnings": []
    },
    "project_title": "Probabilistic Passenger Flow Prediction Under Inconsistent Multi-Operator Data"
  },
  {
    "decision": {
      "blockers": [
        "No field of science or technology.",
        "No signed competent professional opinion.",
        "No evidence linked to the project.",
        "No linked costs for a claimed project."
      ],
      "positive_indicators": [
        "Accounting period linked.",
        "Project boundary has start and narrative support.",
        "Advance sought recorded beyond ordinary delivery language.",
        "Scientific or technological uncertainty captured.",
        "Supplier entitlement indicators are present."
      ],
      "rating": "red",
      "recommended_next_action_count": 5,
      "score": 35,
      "warning_count": 5,
      "warnings": [
        "Advance wording may describe commercial, aesthetic, project management, procurement, or internal-learning work. Review with a competent professional. Matched terms: \"internal learning\" in \"internal learning and standard cloud migration delivery f\"",
        "Explain more clearly why the advance is in the wider field, not only internal knowledge.",
        "Uncertainty wording may describe commercial, budgetary, resourcing, customer-adoption, or implementation-planning risk. Review with a competent professional. Matched terms: \"commercial\" in \"commercial implementation planning, resourcing, an\"; \"implementation planning\" in \"commercial implementation planning, resourcing, and customer adoption risk\"; \"resourcing\" in \"commercial implementation planning, resourcing, and customer adoption risks.\"; \"customer adoption\" in \"mplementation planning, resourcing, and customer adoption risks.\"",
        "Competent professional uncertainty explanation is thin.",
        "Experiments, prototypes, tests, or iterations are not yet described."
      ]
    },
    "project_title": "Standard Dashboard Migration to Managed Cloud"
  }
]"""

GOLDEN_AIF_READINESS = r"""[
  {
    "company_name": "Northstar Digital Services Ltd",
    "period_label": "FY2025/26",
    "project_titles": [
      "Probabilistic Passenger Flow Prediction Under Inconsistent Multi-Operator Data",
      "Legacy Ticketing Event Replay into Cloud Event Architecture",
      "Standard Dashboard Migration to Managed Cloud"
    ],
    "ready": false,
    "selection": {
      "coverage_percentage": 100.0,
      "minimum_described_projects": 3,
      "notes": [],
      "project_count": 3,
      "ready": false,
      "selected_project_ids": [
        1,
        2,
        3
      ],
      "selected_qualifying_expenditure": 170980.0,
      "selection_method": "describe all projects",
      "total_qualifying_expenditure": 170980.0,
      "warnings": [
        "Selected project descriptions missing for: 2, 3."
      ]
    },
    "submission_recorded": true,
    "warnings": [
      "Selected project descriptions missing for: 2, 3."
    ]
  }
]"""


def test_tier_1_project_decisions_match_the_pinned_golden(seeded_session):
    actual = canonical_json(project_decisions_payload(seeded_session))
    assert actual == GOLDEN_PROJECT_DECISIONS


def test_tier_1_aif_readiness_matches_the_pinned_golden(seeded_session):
    actual = canonical_json(aif_readiness_payload(seeded_session))
    assert actual == GOLDEN_AIF_READINESS


def test_tier_1_the_dashboard_publishes_the_same_decision_as_the_project_pages(seeded_session):
    """The dashboard read path must publish the identical decision to the project read path.

    Both sides are compared against the same pinned literal rather than against each other, so
    this cannot pass by both paths drifting together. ADR-0005 D6 removes the write-on-GET from
    the dashboard render; this is what stops that removal from quietly turning into "the dashboard
    scores a project differently from its own project page".
    """
    metrics = dashboard_metrics(seeded_session)
    dashboard_scores = {int(project_id): score for project_id, score in metrics["scores"].items()}
    actual = canonical_json(project_decisions_payload(seeded_session, dashboard_scores))
    assert actual == GOLDEN_PROJECT_DECISIONS


def test_tier_1_the_advisory_elision_hides_nothing_but_the_derived_sentences(seeded_session):
    """The elision must narrow Tier 1, not blind it.

    Two ways this could go wrong and both are asserted here: the placeholder standing for a
    sentence that is no longer produced at all (so Tier 1 pins the absence of an assertion), and
    the placeholder text itself arriving from the engine as real prose.
    """
    elided = 0
    for project in ordered_projects(seeded_session):
        score = calculate_project_score(seeded_session, project.id or 0)
        emitted = list(score.warnings) + list(score.recommended_next_actions)
        assert DERIVED_ADVISORY not in emitted, "the placeholder is being emitted as real prose"
        elided += sum(1 for line in emitted if line in derived_advisory_sentences())

    assert elided == len(derived_advisory_sentences()), (
        f"the seeded dataset emits {elided} derived advisory sentence(s); Tier 1 elides "
        f"{len(derived_advisory_sentences())}, so at least one is no longer produced and Tier 1 "
        f"is pinning a hole rather than a sentence"
    )


# ===========================================================================
# TIER 2 -- ADVISORY PROSE.
#
# Reviewer-facing sentences. Asserted so accidental drift fails, but on the
# DERIVATION where the rule file supplies the words, and re-baseline-able by
# the owning role when the copy change is itself approved and the commit
# message names the approval. The word-independent invariants at the end
# survive every re-baseline.
# ===========================================================================

# The vocabulary a decision-support tool must never use about a project. ADR-0002 line 59 and
# Ruling R2: wording must not read as an approval, a rejection, or a verdict.
VERDICT_VOCABULARY = ("not eligible", "rejected", "fails", "approved", "qualifies")


def advisory_payload(score: ScoreResult) -> dict:
    """TIER 2. The sentences a reviewer reads, minus anything Tier 1 already pins."""
    advisory = derived_advisory_sentences()
    return {
        "advisory_warnings": [warning for warning in score.warnings if warning in advisory],
        "recommended_next_actions": list(score.recommended_next_actions),
    }


def project_advisory_payload(
    session: Session, scores_by_id: dict[int, ScoreResult] | None = None
) -> list[dict]:
    return per_project_payload(session, advisory_payload, "advisory", scores_by_id)


GOLDEN_ADVISORY_PROSE = r"""[
  {
    "advisory": {
      "advisory_warnings": [
        "Warnings keep this project at \"review required\" until the review points are resolved."
      ],
      "recommended_next_actions": [
        "Resolve blockers and warnings before including in an audit-ready claim pack.",
        "Requires competent professional and tax review."
      ]
    },
    "project_title": "Legacy Ticketing Event Replay into Cloud Event Architecture"
  },
  {
    "advisory": {
      "advisory_warnings": [],
      "recommended_next_actions": [
        "Requires competent professional and tax review."
      ]
    },
    "project_title": "Probabilistic Passenger Flow Prediction Under Inconsistent Multi-Operator Data"
  },
  {
    "advisory": {
      "advisory_warnings": [],
      "recommended_next_actions": [
        "Obtain a signed competent professional opinion before treating this project as \"strong candidate\".",
        "Add evidence for advance, uncertainty, resolution activity, cost, and boundaries.",
        "Add cost lines with activity links, evidence, and apportionment.",
        "Resolve blockers and warnings before including in an audit-ready claim pack.",
        "Requires competent professional and tax review."
      ]
    },
    "project_title": "Standard Dashboard Migration to Managed Cloud"
  }
]"""


def test_tier_2_advisory_prose_matches_the_pinned_copy(seeded_session):
    """Accidental drift still fails. An approved copy change re-baselines this literal.

    Re-baselining is the owning role's call, in the same commit as the approved copy change, with
    the approval named in the commit message. It is not an EA matter and it is not a licence to
    edit Tier 1.
    """
    actual = canonical_json(project_advisory_payload(seeded_session))
    assert actual == GOLDEN_ADVISORY_PROSE


def test_tier_2_the_cap_explanation_is_rendered_from_the_current_rule_file(seeded_session):
    """ADR-0002 R8: the band is named by its rule-file description, not by a literal in the code.

    Asserted as a derivation, so an operator editing ``eligibility_weights.yml`` gets consistent
    copy on the panel, in the memo and here, instead of a red suite.
    """
    amber_description = str(get_rules().score_bands()["amber"]["description"])
    capped = [
        score
        for score in (
            calculate_project_score(seeded_session, project.id or 0)
            for project in ordered_projects(seeded_session)
        )
        if cap_explanation() in score.warnings
    ]

    assert capped, "no seeded project reaches the warning-cap branch; this assertion is vacuous"
    for score in capped:
        assert cap_explanation() in score.warnings
        assert amber_description in cap_explanation()
        assert score.rating == "amber", "the cap explanation appeared on a project it does not describe"


def test_tier_2_the_competent_professional_next_action_is_rendered_from_the_current_rule_file(seeded_session):
    """ADR-0002 R8, second sentence. Same derivation, same reason."""
    green_description = str(get_rules().score_bands()["green"]["description"])
    offered = [
        score
        for score in (
            calculate_project_score(seeded_session, project.id or 0)
            for project in ordered_projects(seeded_session)
        )
        if competent_professional_next_action() in score.recommended_next_actions
    ]

    assert offered, "no seeded project lacks a signed opinion; this assertion is vacuous"
    assert green_description in competent_professional_next_action()


def test_tier_2_the_rating_label_is_the_rule_file_description_for_the_rating(seeded_session):
    """The band text is the rule file's, so panel, memo, pack and dashboard cannot describe one
    score two ways. The rating VALUE that selects it is Tier 1 and is pinned above."""
    bands = get_rules().score_bands()
    seen = set()
    for project in ordered_projects(seeded_session):
        score = calculate_project_score(seeded_session, project.id or 0)
        assert score.rating_label == str(bands[score.rating]["description"]), (
            f"{project.project_title}: rating_label {score.rating_label!r} is not the "
            f"{score.rating!r} band description"
        )
        seen.add(score.rating)

    assert len(seen) > 1, f"only one band is exercised ({seen}); this assertion is nearly vacuous"


def test_tier_2_every_project_still_carries_the_review_caveat(seeded_session):
    """ADR-0002 line 58. A word-independent invariant: it survives every Tier 2 re-baseline."""
    for entry in json.loads(GOLDEN_ADVISORY_PROSE):
        assert CAVEAT in entry["advisory"]["recommended_next_actions"]
    for project in ordered_projects(seeded_session):
        score = calculate_project_score(seeded_session, project.id or 0)
        assert CAVEAT in score.recommended_next_actions


def test_tier_2_advisory_prose_names_no_rating_band_and_returns_no_verdict(seeded_session):
    """Word-independent invariants. These survive every re-baseline, including one that reintroduces
    the wording ADR-0002 R8 removed.

    The band vocabulary is derived from the rule file rather than listed, so a band renamed there
    is forbidden here the same day.
    """
    bands = {str(band["label"]) for band in get_rules().score_bands().values()}
    assert bands >= {"green", "amber", "red", "weak"}, f"band vocabulary no longer derives: {bands}"

    leaks = []
    for entry in project_advisory_payload(seeded_session):
        lines = entry["advisory"]["advisory_warnings"] + entry["advisory"]["recommended_next_actions"]
        for line in lines:
            for word in sorted(bands):
                if re.search(rf"\b{re.escape(word)}\b", line, re.IGNORECASE):
                    leaks.append(f"{entry['project_title']}: band {word!r} named in {line!r}")
            for phrase in VERDICT_VOCABULARY:
                if re.search(rf"\b{re.escape(phrase)}\b", line, re.IGNORECASE):
                    leaks.append(f"{entry['project_title']}: verdict {phrase!r} in {line!r}")

    assert not leaks, "advisory prose broke a word-independent invariant:\n" + "\n".join(leaks)


# ===========================================================================
# BOTH TIERS -- determinism, read-path agreement and the fixture guards.
#
# These compare actual with actual, or assert a precondition the literals above
# depend on. They belong to neither tier because they are about the engine's
# behaviour rather than about its published words.
# ===========================================================================


def test_the_seeded_dataset_is_not_empty(seeded_session):
    """Guard: an empty dataset would make every equality above trivially satisfiable."""
    projects = ordered_projects(seeded_session)
    periods = list(seeded_session.exec(select(AccountingPeriod)))
    assert len(projects) == SEEDED_PROJECT_COUNT
    assert len(periods) == SEEDED_PERIOD_COUNT


def test_both_tiers_are_stable_when_repeated_within_a_session(seeded_session):
    """Same inputs, same output: no dependence on evaluation order, caches or accumulated state."""
    first = canonical_json(project_scores_payload(seeded_session))
    second = canonical_json(project_scores_payload(seeded_session))
    assert first == second
    first_readiness = canonical_json(aif_readiness_payload(seeded_session))
    second_readiness = canonical_json(aif_readiness_payload(seeded_session))
    assert first_readiness == second_readiness


def test_the_batched_context_loader_matches_the_per_project_loader(seeded_session):
    """The batching in E7-1b must be a pure query optimisation, not a change of shape.

    Compared field by field, including the ORDER of every related-row list and the identity of the
    single-row lookups, because a reordered cost line or a different ``.first()`` winner would
    change a published score with nothing else failing.
    """
    projects = ordered_projects(seeded_session)
    batched = bulk_project_contexts(seeded_session, projects)
    assert sorted(batched) == sorted(project.id or 0 for project in projects)

    populated: dict[str, int] = {"activities": 0, "opinions": 0, "evidence": 0, "costs": 0}
    for project in projects:
        project_id = project.id or 0
        one = get_project_context(seeded_session, project_id)
        many = batched[project_id]
        assert many.project is one.project
        assert many.solution is one.solution
        assert many.customer is one.customer
        assert many.contract is one.contract
        assert many.period is one.period
        assert many.company is one.company
        assert many.submission_status is one.submission_status
        assert many.entitlement is one.entitlement
        for field in populated:
            batched_rows = [row.id for row in getattr(many, field)]
            single_rows = [row.id for row in getattr(one, field)]
            assert batched_rows == single_rows, f"{field} differs for project {project_id}"
            populated[field] += len(batched_rows)

    # Guard against a vacuous comparison: empty lists match empty lists. Every related-row type
    # must actually carry rows somewhere in the dataset for the ordering check above to mean
    # anything. (Individual projects legitimately have none -- that is what a red rating is made of.)
    assert all(count > 0 for count in populated.values()), f"nothing to compare: {populated}"


def test_a_project_with_no_stored_entitlement_scores_the_same_on_both_read_paths(seeded_session):
    """ADR-0005 D6: the dashboard must not write, and must not disagree with the project page.

    ``seed_demo_data`` runs ``sync_entitlement_for_project`` for every seeded project, so the
    golden dataset alone can never reach the branch D6 changes. This test removes one assessment to
    reach it deliberately. Order matters: the dashboard is rendered first, while the assessment is
    genuinely absent, because ``sync=True`` would otherwise recreate it and make the assertion pass
    for the wrong reason.

    Compared on the FULL payload -- both tiers -- because this is actual against actual.
    """
    project = ordered_projects(seeded_session)[0]
    project_id = project.id or 0
    stored = seeded_session.exec(
        select(EntitlementAssessment).where(EntitlementAssessment.project_id == project_id)
    ).first()
    assert stored is not None, "seed no longer creates assessments; this test needs revisiting"
    seeded_session.delete(stored)
    seeded_session.commit()

    dashboard_score = dashboard_metrics(seeded_session)["scores"][project_id]

    remaining = seeded_session.exec(
        select(EntitlementAssessment).where(EntitlementAssessment.project_id == project_id)
    ).first()
    assert remaining is None, "the dashboard render created an EntitlementAssessment on a GET"

    page_score = calculate_project_score(seeded_session, project_id)
    assert canonical_json(score_payload(dashboard_score)) == canonical_json(score_payload(page_score))
