"""E5-DELETED-MSG: a record that is no longer there says so, in words a reviewer can read.

Epic EPIC-RDEC-2026-07-VERIFIED-FIXES.

E3-D1/E3-D2 stopped a request for a deleted record raising HTTP 500 and made it
redirect to the list instead. G4 confirmed all twelve routes return 303 with zero
orphan rows -- and found the redirect silent: the user presses a project tab and
simply arrives somewhere else, with nothing on the screen to say why.

The redirect now carries a reason code that ``friendly_query_error`` turns into a
sentence. What the user actually sees, measured in Chrome on the rendered page:

    That project is no longer in this workspace. It may have been deleted, or this
    link may be out of date. Nothing has been changed, and you are now on the R&D
    Project Register page.

rendered at 8.2:1 text contrast, inside <main> ahead of the page heading, with no
focusable element of its own (so the pinned "one tab stop reaches <main>" metric is
unmoved and the skip link is still the first focusable element on the page).

What these tests hold:

  * the guard still returns 303 and still writes nothing -- the reason travels in
    the query string, not in the database (ADR-0006 P1). Asserted here as well as
    in tests/test_edit_delete_routes.py because a message test that did not check
    it would happily pass on a route that had started committing on a GET.
  * the reason reaches the user as a rendered sentence, compared through the
    template engine's own escaper rather than ``html.escape`` (MarkupSafe spells
    an apostrophe differently, and comparing this way also proves the sentence is
    delivered as data rather than as markup).
  * the sentence is plain English. This epic exists because machine vocabulary
    leaked to users, so "404", "not found" and the ORM class names are refused
    here by name.
  * every code in the table names a destination that is really a route, so a
    message can never send a reviewer to a page that does not exist.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from app.database import get_session
from app.main import MISSING_RECORD_COPY, app, friendly_query_error, missing_record_message
from tests.test_routes_validation_audit import table_row_counts

MISSING_ID = 999999

VALID_COST_FORM = {
    "cost_input_type": "people_time",
    "cost_category": "staff",
    "hours": "10",
    "hourly_rate": "100",
    "apportionment_percentage": "50",
}
VALID_EVIDENCE_FORM = {
    "source_system": "Jira",
    "date_created": "2025-06-01",
    "evidence_type": "experiment",
    "relevance_tag": "uncertainty",
    "strength": "strong",
}
VALID_PROFESSIONAL_FORM = {
    "professional_name": "Reviewer",
    "years_relevant_experience": "12",
    "signoff_status": "draft",
}
VALID_READINESS_FORM = {
    "aif_submitted": "on",
    "aif_submission_date": "2026-03-31",
    "notes": "Readiness note.",
}

#: The journeys G4 walked, plus the update and delete routes that share the guard. Each is
#: (method, path, form, the record the user asked for).
MISSING_RECORD_JOURNEYS = [
    ("GET", f"/projects/{MISSING_ID}", None, "project"),
    ("GET", f"/projects/{MISSING_ID}/assessment", None, "project"),
    ("GET", f"/projects/{MISSING_ID}/costs", None, "project"),
    ("GET", f"/projects/{MISSING_ID}/evidence", None, "project"),
    ("GET", f"/projects/{MISSING_ID}/competent-professional", None, "project"),
    ("GET", f"/projects/{MISSING_ID}/report", None, "project"),
    ("GET", f"/projects/{MISSING_ID}/report?format=md", None, "project"),
    ("GET", f"/claim-periods/{MISSING_ID}/readiness", None, "claim_period"),
    ("GET", f"/claim-periods/{MISSING_ID}/pack", None, "claim_period"),
    ("GET", f"/claim-periods/{MISSING_ID}/pack?format=md", None, "claim_period"),
    ("POST", f"/projects/{MISSING_ID}/costs", VALID_COST_FORM, "project"),
    ("POST", f"/projects/{MISSING_ID}/evidence", VALID_EVIDENCE_FORM, "project"),
    ("POST", f"/projects/{MISSING_ID}/competent-professional", VALID_PROFESSIONAL_FORM, "project"),
    ("POST", f"/claim-periods/{MISSING_ID}/readiness", VALID_READINESS_FORM, "claim_period"),
    ("POST", f"/projects/{MISSING_ID}/delete", None, "project"),
    ("POST", f"/companies/{MISSING_ID}/delete", None, "company"),
    ("POST", f"/customers/{MISSING_ID}/delete", None, "customer"),
    ("POST", f"/contracts/{MISSING_ID}/delete", None, "contract"),
    ("POST", f"/solutions/{MISSING_ID}/delete", None, "solution"),
    ("POST", f"/business-units/{MISSING_ID}/delete", None, "business_unit"),
    ("POST", f"/accounting-periods/{MISSING_ID}/delete", None, "claim_period"),
]

#: Machine vocabulary that must never reach a Finance reviewer.
JARGON = [
    "404",
    "not found",
    "does not exist",
    "entity",
    "null",
    "none",
    "internal server error",
    "traceback",
    "rdproject",
    "accountingperiod",
    "businessunit",
    "invalid",
]


@pytest.fixture()
def client(seeded_session):
    def override_session():
        yield seeded_session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


# --- the guard still behaves, and now explains itself -----------------------------------------


@pytest.mark.parametrize(
    "method, path, form, kind",
    MISSING_RECORD_JOURNEYS,
    ids=[f"{m} {p}" for m, p, _f, _k in MISSING_RECORD_JOURNEYS],
)
def test_a_missing_record_redirects_writes_nothing_and_says_why(
    client, seeded_session, method, path, form, kind
):
    before = table_row_counts(seeded_session)
    response = client.request(method, path, data=form, follow_redirects=False)

    assert response.status_code == 303, f"{method} {path} returned {response.status_code}"
    assert table_row_counts(seeded_session) == before, f"{method} {path} changed stored data"
    location = response.headers["location"]
    assert location == f"{MISSING_RECORD_COPY[kind][1]}?error=missing_{kind}", location


@pytest.mark.parametrize(
    "method, path, form, kind",
    MISSING_RECORD_JOURNEYS,
    ids=[f"{m} {p}" for m, p, _f, _k in MISSING_RECORD_JOURNEYS],
)
def test_the_reason_is_rendered_to_the_user_on_the_page_they_land_on(
    client, method, path, form, kind
):
    landing = client.request(method, path, data=form, follow_redirects=True)
    assert landing.status_code == 200
    # Through the template engine's escaper, not html.escape: MarkupSafe spells "&" the same but
    # an apostrophe differently, and this is also the stronger assertion -- it pins that the
    # sentence is delivered as data rather than as markup.
    assert str(escape(missing_record_message(kind))) in landing.text


def test_the_message_is_not_the_generic_fallback():
    """Non-vacuity: the fallback is a real sentence too, so a broken code would look fine."""
    generic = friendly_query_error("something_unmapped")
    for kind in MISSING_RECORD_COPY:
        assert friendly_query_error(f"missing_{kind}") != generic
    assert friendly_query_error("missing_not_a_record") == generic


# --- the words themselves ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(MISSING_RECORD_COPY))
def test_the_message_is_plain_english(kind):
    message = missing_record_message(kind).lower()
    for token in JARGON:
        assert token not in message, f"{kind!r} message uses machine vocabulary {token!r}"
    assert "_" not in message, f"{kind!r} message leaks an identifier: {message!r}"
    assert not re.search(r"\b[a-z]+[A-Z]", missing_record_message(kind)), "camelCase leaked"


@pytest.mark.parametrize("kind", sorted(MISSING_RECORD_COPY))
def test_the_message_names_the_record_and_where_the_user_has_landed(kind):
    noun, _path, heading = MISSING_RECORD_COPY[kind]
    message = missing_record_message(kind)
    assert noun in message, f"the message does not say what was missing: {message!r}"
    assert heading in message, f"the message does not say where the user is: {message!r}"
    # The one thing a user who has just pressed Delete or Save most needs to know.
    assert "Nothing has been changed" in message


def test_every_destination_is_a_real_route():
    """A message may not send a reviewer to a page that does not exist."""
    get_paths = {
        route.path for route in app.routes if hasattr(route, "methods") and "GET" in route.methods
    }
    for kind, (_noun, path, _heading) in MISSING_RECORD_COPY.items():
        assert path in get_paths, f"{kind} sends the user to {path}, which is not a GET route"


def test_the_landing_page_renders_the_message_without_a_tab_stop(client):
    """The banner sits inside <main> and carries no link, so it costs no keyboard user anything."""
    body = client.get("/projects?error=missing_project").text
    main = body.split("<main", 1)[1]
    flash = re.search(r'<div class="flash-message error" role="alert">(.*?)</div>', main, re.S)
    assert flash, "the message is not rendered inside <main>"
    assert "<a" not in flash.group(1) and "<button" not in flash.group(1)
