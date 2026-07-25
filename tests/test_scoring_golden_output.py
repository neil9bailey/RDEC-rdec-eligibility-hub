"""Golden-output invariant for the decision engine (Epic 7, E7-SC2).

This module pins the *exact* decision output of the scoring engine against the seeded dataset:
for every project the score, rating, rating label, blockers, warnings, positive indicators and
recommended next actions; and for every accounting period the full AIF readiness result.

Why a byte-identical golden rather than behavioural assertions
-------------------------------------------------------------
The Epic 7 performance work replaces per-project queries with batched loads. A batched loader that
subtly changes row ORDER (blockers and warnings are ordered lists), NULL handling (a missing
solution, contract or submission status), or which row wins a ``.first()`` would change published
scores with no other test in the suite failing. That is the highest-risk silent regression in the
programme: a governance decision is only auditable if the same inputs keep producing the same
output. So the expected values below are hard-coded LITERALS captured before any optimisation, and
the comparison is string equality on canonical JSON, not a field-by-field assertion.

The expected values are deliberately NOT computed from the system under test. If a change is
genuinely intended to alter published scores, that is design-altering: it needs an ADR, and the
literal below is then updated in the same commit as the approved change, never to make a red test
green.

``test_the_dashboard_publishes_the_same_scores_as_the_project_pages`` additionally pins that the
two independent read paths agree. ADR-0005 D6 removes the write-on-GET from the dashboard render;
this test is what stops that removal from quietly turning into "the dashboard scores a project
differently from its own project page", which would make a score depend on render history rather
than on the project's facts.
"""

from __future__ import annotations

import json

from sqlmodel import Session, col, select

from app.models import AccountingPeriod, EntitlementAssessment, RDProject
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


def score_payload(score: ScoreResult) -> dict:
    return {
        "score": score.score,
        "rating": score.rating,
        "rating_label": score.rating_label,
        "blockers": list(score.blockers),
        "warnings": list(score.warnings),
        "positive_indicators": list(score.positive_indicators),
        "recommended_next_actions": list(score.recommended_next_actions),
    }


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


def ordered_projects(session: Session) -> list[RDProject]:
    """Projects in a stable order that does not depend on insertion or query plan."""
    projects = list(session.exec(select(RDProject)))
    return sorted(projects, key=lambda project: (project.project_title, project.id or 0))


def project_scores_payload(session: Session, scores_by_id: dict[int, ScoreResult] | None = None) -> list[dict]:
    """The published score for every project, keyed by title so it survives id renumbering."""
    payload: list[dict] = []
    for project in ordered_projects(session):
        project_id = project.id or 0
        score = (
            scores_by_id[project_id]
            if scores_by_id is not None
            else calculate_project_score(session, project_id)
        )
        payload.append({"project_title": project.project_title, "score": score_payload(score)})
    return payload


def aif_readiness_payload(session: Session) -> list[dict]:
    """The full AIF readiness result for every accounting period."""
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


# ---------------------------------------------------------------------------
# Pinned expected output. Captured from the seeded dataset before any Epic 7
# optimisation. Do not regenerate to make a test pass.
# ---------------------------------------------------------------------------

GOLDEN_PROJECT_SCORES = r"""[
  {
    "project_title": "Legacy Ticketing Event Replay into Cloud Event Architecture",
    "score": {
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
      "rating_label": "review required",
      "recommended_next_actions": [
        "Resolve blockers and warnings before including in an audit-ready claim pack.",
        "Requires competent professional and tax review."
      ],
      "score": 79,
      "warnings": [
        "Resolution activity exists but needs stronger activity-level evidence.",
        "Evidence exists but no item is marked strong.",
        "Cost line 4: Flag missing evidence.",
        "Claim entitlement is ambiguous and needs tax review.",
        "Warnings cap the current rating at amber until review points are resolved."
      ]
    }
  },
  {
    "project_title": "Probabilistic Passenger Flow Prediction Under Inconsistent Multi-Operator Data",
    "score": {
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
      "rating_label": "strong candidate",
      "recommended_next_actions": [
        "Requires competent professional and tax review."
      ],
      "score": 100,
      "warnings": []
    }
  },
  {
    "project_title": "Standard Dashboard Migration to Managed Cloud",
    "score": {
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
      "rating_label": "not currently supportable",
      "recommended_next_actions": [
        "Obtain signed competent professional opinion before treating the project as green.",
        "Add evidence for advance, uncertainty, resolution activity, cost, and boundaries.",
        "Add cost lines with activity links, evidence, and apportionment.",
        "Resolve blockers and warnings before including in an audit-ready claim pack.",
        "Requires competent professional and tax review."
      ],
      "score": 35,
      "warnings": [
        "Advance wording may describe commercial, aesthetic, project management, procurement, or internal-learning work. Review with a competent professional. Matched terms: \"internal learning\" in \"internal learning and standard cloud migration delivery f\"",
        "Explain more clearly why the advance is in the wider field, not only internal knowledge.",
        "Uncertainty wording may describe commercial, budgetary, resourcing, customer-adoption, or implementation-planning risk. Review with a competent professional. Matched terms: \"commercial\" in \"commercial implementation planning, resourcing, an\"; \"implementation planning\" in \"commercial implementation planning, resourcing, and customer adoption risk\"; \"resourcing\" in \"commercial implementation planning, resourcing, and customer adoption risks.\"; \"customer adoption\" in \"mplementation planning, resourcing, and customer adoption risks.\"",
        "Competent professional uncertainty explanation is thin.",
        "Experiments, prototypes, tests, or iterations are not yet described."
      ]
    }
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


def test_the_seeded_dataset_is_not_empty(seeded_session):
    """Guard: an empty dataset would make every equality below trivially satisfiable."""
    projects = ordered_projects(seeded_session)
    periods = list(seeded_session.exec(select(AccountingPeriod)))
    assert len(projects) == SEEDED_PROJECT_COUNT
    assert len(periods) == SEEDED_PERIOD_COUNT


def test_project_scores_match_the_pinned_golden_output(seeded_session):
    actual = canonical_json(project_scores_payload(seeded_session))
    assert actual == GOLDEN_PROJECT_SCORES


def test_aif_readiness_matches_the_pinned_golden_output(seeded_session):
    actual = canonical_json(aif_readiness_payload(seeded_session))
    assert actual == GOLDEN_AIF_READINESS


def test_the_dashboard_publishes_the_same_scores_as_the_project_pages(seeded_session):
    """The dashboard read path must publish the identical score to the project read path.

    Both sides are compared against the same pinned literal rather than against each other, so
    this cannot pass by both paths drifting together.
    """
    metrics = dashboard_metrics(seeded_session)
    dashboard_scores = {int(project_id): score for project_id, score in metrics["scores"].items()}
    actual = canonical_json(project_scores_payload(seeded_session, dashboard_scores))
    assert actual == GOLDEN_PROJECT_SCORES


def test_scoring_is_stable_when_repeated_within_a_session(seeded_session):
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


def test_every_project_still_carries_the_review_caveat(seeded_session):
    """The caveat is a standing product guardrail, not an incidental string."""
    for entry in json.loads(GOLDEN_PROJECT_SCORES):
        assert CAVEAT in entry["score"]["recommended_next_actions"]
    for project in ordered_projects(seeded_session):
        score = calculate_project_score(seeded_session, project.id or 0)
        assert CAVEAT in score.recommended_next_actions
