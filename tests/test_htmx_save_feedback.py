"""The htmx cost and evidence save path: a refused save is visible, a saved one is current.

Epic EPIC-RDEC-2026-07-VERIFIED-FIXES, increment E1-HTMX.

Two faults on one code path, both reproduced by driving real requests before any of this was
written, both of the same kind: the screen and the database disagreed and the screen looked
fine.

*Fault 1 -- a rejected save looked successful.* ``validation_error_response`` returns a whole
HTML document with status 400. htmx 2.0.4 ships ``responseHandling`` defaults that map
``[45]..`` to ``swap:false``, so the response was received and thrown away. Posting the
add-cost form with ``HX-Request: true`` and a gross cost of ``"1,2OO"`` returned 400, changed
nothing on the page, showed no message anywhere, and stored no cost line. The user had every
reason to believe the figure went in.

*Fault 2 -- the eligibility panel went stale.* A successful save returned only the row list,
so the panel kept the rating the page had been loaded with. Measured on demo project 3: after
a valid GBP 12,000 cost was added, the page still read ``35/100`` with the blocker "No linked
costs for a claimed project" while a fresh GET of the same URL returned ``45/100`` without it.
Two contradictory ratings for one project, on the screen whose whole job is the number.

Everything here goes through the application. A template-source assertion cannot tell whether
htmx would have shown the markup, and that gap is precisely what let both faults ship.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.main import app
from app.models import CostLine, EvidenceItem, RDProject
from app.settings import BASE_DIR

HTMX = {"HX-Request": "true"}
SAVE_ERROR_REGION = '<div id="save-errors" role="alert"></div>'
CAVEAT = "Requires competent professional and tax review."

#: An unparseable money value. Not a boundary case and not a policy rejection -- a figure the
#: parser cannot read at all, so the rejection cannot be argued away as a rule change.
UNPARSEABLE_GROSS = "1,2OO"

VALID_COST = {
    "cost_input_type": "direct_cost",
    "cost_category": "consumables",
    "activity": "Rig calibration",
    "person_or_supplier_name": "Aerodyne Ltd",
    "gross_cost": "12000",
    "apportionment_percentage": "80",
    "paid_status": "paid",
    "uk_or_overseas": "UK",
    "connected_party_status": "unconnected",
    "evidence_link": "INV-2291",
}

INVALID_COST = dict(VALID_COST, gross_cost=UNPARSEABLE_GROSS)

VALID_EVIDENCE = {
    "source_system": "Manual upload / note",
    "source_reference": "SPIKE-114",
    "url_or_file_path": "//evidence/spike-114.md",
    "date_created": "2026-02-11",
    "evidence_type": "technical spike",
    "relevance_tag": "uncertainty",
    "strength": "strong",
    "notes": "Rig calibration spike write-up.",
}

INVALID_EVIDENCE = dict(VALID_EVIDENCE, date_created="the third of never")


@pytest.fixture(autouse=True)
def _hand_the_application_back():
    """Clear the session override this module installs on the shared ``app`` object.

    Clearing it when the *next* client is built is not the same as clearing it at teardown:
    the last test in the module would otherwise leave an override bound to a session that has
    already been torn down, and the next module that builds a plain ``TestClient(main.app)``
    against the real ``get_session`` writes into that dead session instead of its own database.
    Measured: without this, tests/test_read_route_determinism.py loses three tests to "zero
    rows created" when it happens to run after this module.
    """
    yield
    app.dependency_overrides.clear()


def client_for(session) -> TestClient:
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def project_without_costs(session) -> RDProject:
    for project in session.exec(select(RDProject)):
        if not list(session.exec(select(CostLine).where(CostLine.project_id == project.id))):
            return project
    raise AssertionError("the demo data no longer contains a project with no cost lines")


def cost_count(session, project_id: int) -> int:
    return len(list(session.exec(select(CostLine).where(CostLine.project_id == project_id))))


def evidence_count(session, project_id: int) -> int:
    return len(list(session.exec(select(EvidenceItem).where(EvidenceItem.project_id == project_id))))


def score_panel(html: str) -> str:
    """The visible text of the eligibility panel, or "" when the response has none."""
    match = re.search(
        r'<section class="panel" id="eligibility-score"[^>]*>(.*?)</section>', html, re.S
    )
    if not match:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())


# --------------------------------------------------------------------------
# The reason a refused htmx save returns 200 rather than 400
# --------------------------------------------------------------------------


def test_the_vendored_htmx_really_does_discard_a_4xx_response() -> None:
    """The premise of the whole increment, pinned against the file that is actually served.

    If a later htmx upgrade changes this default, returning 200 for a refused save stops being
    necessary and this design should be revisited rather than inherited.
    """
    source = (BASE_DIR / "static" / "htmx.min.js").read_text(encoding="utf-8")
    assert 'version:"2.0.4"' in source, "htmx was upgraded; re-check the responseHandling default"
    # Anchored on "}]" rather than "]": the entries themselves contain a bracket, because the
    # status codes are regular expressions ("[23]..", "[45]..").
    handling = re.search(r"responseHandling:\[(.*?)\}\]", source)
    assert handling, "htmx no longer declares a responseHandling default"
    assert '{code:"[45]..",swap:false' in handling.group(1), (
        "htmx now swaps 4xx responses by default, so a 400 would reach the page: "
        f"got {handling.group(1)!r}"
    )


# --------------------------------------------------------------------------
# Fault 1 -- a refused save is visible, and still refuses
# --------------------------------------------------------------------------


def test_a_refused_htmx_cost_save_returns_something_htmx_will_show(seeded_session) -> None:
    project = project_without_costs(seeded_session)
    client = client_for(seeded_session)

    response = client.post(f"/projects/{project.id}/costs", data=INVALID_COST, headers=HTMX)

    assert 200 <= response.status_code < 300, (
        f"htmx discards {response.status_code}; the user would see no change at all"
    )
    assert response.headers.get("HX-Retarget") == "#save-errors"
    assert response.headers.get("HX-Reswap", "").startswith("innerHTML"), (
        "the live region must survive the swap for the message to be announced"
    )


def test_a_refused_htmx_cost_save_says_what_was_wrong(seeded_session) -> None:
    project = project_without_costs(seeded_session)
    response = client_for(seeded_session).post(
        f"/projects/{project.id}/costs", data=INVALID_COST, headers=HTMX
    )
    assert "This was not saved" in response.text
    assert "Gross cost" in response.text, (
        f"the message does not name the field that was refused: {response.text!r}"
    )


def test_a_refused_htmx_cost_save_stores_nothing(seeded_session) -> None:
    """The visible half of the fix must not have loosened the refusal."""
    project = project_without_costs(seeded_session)
    before = cost_count(seeded_session, project.id)
    client_for(seeded_session).post(
        f"/projects/{project.id}/costs", data=INVALID_COST, headers=HTMX
    )
    assert cost_count(seeded_session, project.id) == before


def test_a_refused_htmx_evidence_save_is_visible_and_stores_nothing(seeded_session) -> None:
    project = project_without_costs(seeded_session)
    before = evidence_count(seeded_session, project.id)
    response = client_for(seeded_session).post(
        f"/projects/{project.id}/evidence", data=INVALID_EVIDENCE, headers=HTMX
    )
    assert 200 <= response.status_code < 300
    assert response.headers.get("HX-Retarget") == "#save-errors"
    assert "This was not saved" in response.text
    assert evidence_count(seeded_session, project.id) == before


def test_the_refused_save_message_carries_no_markup_from_the_submitted_value(
    seeded_session,
) -> None:
    """ADR-0004 D7, held forward rather than proven backwards.

    Today's parsers name the field and never echo the value, so nothing attacker-shaped
    reaches this fragment. The point of the assertion is the day one of them starts quoting
    the offending value, as the import preview already does: this fragment is Jinja-rendered
    rather than string-built, so escaping is the environment's job and stays correct, and this
    fails loudly if someone replaces it with an interpolated string.
    """
    project = project_without_costs(seeded_session)
    payload = dict(VALID_COST, gross_cost='<script>alert("x")</script>')
    response = client_for(seeded_session).post(
        f"/projects/{project.id}/costs", data=payload, headers=HTMX
    )
    assert "<script>" not in response.text


# --------------------------------------------------------------------------
# Progressive enhancement: the full-page POST is untouched
# --------------------------------------------------------------------------


def test_the_full_page_post_still_returns_the_400_validation_page(seeded_session) -> None:
    project = project_without_costs(seeded_session)
    response = client_for(seeded_session).post(f"/projects/{project.id}/costs", data=INVALID_COST)
    assert response.status_code == 400
    assert response.text.startswith("<!doctype html>")
    assert "Check the submitted values" in response.text
    assert "Go back and correct the form" in response.text


def test_both_paths_refuse_the_same_value_for_the_same_stated_reason(seeded_session) -> None:
    """A user on a browser without htmx must not be told a different story."""
    project = project_without_costs(seeded_session)
    client = client_for(seeded_session)
    full_page = client.post(f"/projects/{project.id}/costs", data=INVALID_COST).text
    partial = client.post(f"/projects/{project.id}/costs", data=INVALID_COST, headers=HTMX).text

    messages = re.findall(r"<li>(.*?)</li>", full_page, re.S)
    assert messages, f"the full-page validation document listed no messages: {full_page!r}"
    for message in messages:
        assert message in partial, (
            f"the htmx path omits the message the full-page path gives: {message!r}"
        )


def test_the_full_page_post_still_saves_and_redirects(seeded_session) -> None:
    project = project_without_costs(seeded_session)
    before = cost_count(seeded_session, project.id)
    response = client_for(seeded_session).post(
        f"/projects/{project.id}/costs", data=VALID_COST, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/projects/{project.id}/costs"
    assert cost_count(seeded_session, project.id) == before + 1


# --------------------------------------------------------------------------
# Fault 2 -- the score panel a save leaves on screen is the current one
# --------------------------------------------------------------------------


def test_a_valid_htmx_cost_save_returns_the_current_score_panel(seeded_session) -> None:
    project = project_without_costs(seeded_session)
    client = client_for(seeded_session)

    stale = score_panel(client.get(f"/projects/{project.id}/costs").text)
    fragment = client.post(f"/projects/{project.id}/costs", data=VALID_COST, headers=HTMX).text
    fresh = score_panel(client.get(f"/projects/{project.id}/costs").text)

    assert score_panel(fragment), "the htmx save response carries no score panel"
    assert score_panel(fragment) == fresh, (
        "the swapped panel does not match a fresh full-page GET of the same URL"
    )
    assert stale != fresh, (
        "this fixture no longer changes the score, so the test proves nothing; "
        "pick a project whose rating moves when a cost is added"
    )


def test_the_blocker_the_save_answers_is_gone_from_the_swapped_panel(seeded_session) -> None:
    """The exact contradiction that was observed on screen, driven end to end."""
    project = project_without_costs(seeded_session)
    client = client_for(seeded_session)
    blocker = "No linked costs for a claimed project"

    assert blocker in score_panel(client.get(f"/projects/{project.id}/costs").text), (
        "the fixture project does not start with the blocker this test is about"
    )
    fragment = client.post(f"/projects/{project.id}/costs", data=VALID_COST, headers=HTMX).text
    assert blocker not in score_panel(fragment), (
        "the panel swapped in still says the project has no costs, immediately after one "
        "was added"
    )


def test_a_valid_htmx_evidence_save_returns_the_current_score_panel(seeded_session) -> None:
    project = project_without_costs(seeded_session)
    client = client_for(seeded_session)

    stale = score_panel(client.get(f"/projects/{project.id}/evidence").text)
    fragment = client.post(
        f"/projects/{project.id}/evidence", data=VALID_EVIDENCE, headers=HTMX
    ).text
    fresh = score_panel(client.get(f"/projects/{project.id}/evidence").text)

    assert score_panel(fragment) == fresh
    assert stale != fresh, "the fixture project's rating does not move when evidence is added"


def test_the_swapped_panel_is_marked_for_an_out_of_band_swap(seeded_session) -> None:
    """Without this attribute htmx would swap the panel into the row list instead."""
    project = project_without_costs(seeded_session)
    fragment = client_for(seeded_session).post(
        f"/projects/{project.id}/costs", data=VALID_COST, headers=HTMX
    ).text
    panel = re.search(r'<section class="panel" id="eligibility-score"([^>]*)>', fragment)
    assert panel, fragment
    assert 'hx-swap-oob="true"' in panel.group(1)


def test_a_full_page_render_carries_no_out_of_band_attribute(seeded_session) -> None:
    """The same partial serves both, so the full page must be unchanged by this increment."""
    project = project_without_costs(seeded_session)
    for path in ("costs", "evidence", "competent-professional", "assessment"):
        body = client_for(seeded_session).get(f"/projects/{project.id}/{path}").text
        assert 'id="eligibility-score"' in body, f"/{path} lost its score panel"
        assert "hx-swap-oob" not in body, (
            f"/{path} renders an out-of-band marker on a full page load"
        )


def test_a_valid_htmx_save_clears_a_message_left_by_an_earlier_refusal(seeded_session) -> None:
    """Otherwise the refusal stays on screen contradicting the save that just worked."""
    project = project_without_costs(seeded_session)
    client = client_for(seeded_session)
    client.post(f"/projects/{project.id}/costs", data=INVALID_COST, headers=HTMX)
    fragment = client.post(f"/projects/{project.id}/costs", data=VALID_COST, headers=HTMX).text
    assert '<div id="save-errors" hx-swap-oob="innerHTML"></div>' in fragment, (
        "the save response does not empty the error region"
    )


def test_the_save_still_swaps_the_row_list_it_always_swapped(seeded_session) -> None:
    """hx-target and hx-swap on the forms are unchanged, so the main swap must still fit."""
    project = project_without_costs(seeded_session)
    fragment = client_for(seeded_session).post(
        f"/projects/{project.id}/costs", data=VALID_COST, headers=HTMX
    ).text
    assert fragment.lstrip().startswith('<div id="cost-lines"'), (
        "the element hx-target names is no longer the main swap content"
    )
    assert "Rig calibration" in fragment


# --------------------------------------------------------------------------
# Non-vacuity: both detectors must fail against the shape that shipped
# --------------------------------------------------------------------------


def test_the_staleness_detector_fires_on_the_response_that_shipped(seeded_session) -> None:
    """The pre-fix response was the row partial alone. It must fail ``score_panel``."""
    project = project_without_costs(seeded_session)
    client = client_for(seeded_session)
    client.post(f"/projects/{project.id}/costs", data=VALID_COST, headers=HTMX)
    pre_fix = client.get(f"/projects/{project.id}/costs").text
    rows = re.search(r'(<div id="cost-lines".*?)\n</section>', pre_fix, re.S)
    assert rows, "could not isolate the row list from the rendered page"
    assert score_panel(rows.group(1)) == "", (
        "the pre-fix response shape is expected to carry no score panel; if this passes, "
        "the equality assertions above would pass vacuously"
    )


def test_the_visibility_detector_fires_on_the_response_that_shipped() -> None:
    """The pre-fix refusal was a 400, which the assertions above reject."""
    pre_fix_status = 400
    assert not 200 <= pre_fix_status < 300


# --------------------------------------------------------------------------
# The live region itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/customers", "/projects", "/costs", "/audit"])
def test_every_page_carries_the_empty_live_region(seeded_session, path: str) -> None:
    """A live region has to be in the DOM before its content arrives to be announced."""
    body = client_for(seeded_session).get(path).text
    assert body.count(SAVE_ERROR_REGION) == 1, (
        f"{path} does not render exactly one empty #save-errors region"
    )
    assert CAVEAT in body


def test_the_live_region_adds_no_tab_stop_and_no_control() -> None:
    source = Path(BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    block = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    region = re.search(r'<div id="save-errors"[^>]*>.*?</div>', block, re.S)
    assert region, "base.html no longer declares the #save-errors region"
    assert not re.search(r"<(a|button|input|select|textarea)\b", region.group(0)), (
        "the live region must carry no focusable element"
    )
    assert "tabindex" not in region.group(0)
