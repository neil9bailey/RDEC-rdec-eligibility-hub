"""Two published context keys that no template consumed, driven through real requests.

Epic EPIC-RDEC-2026-07-VERIFIED-FIXES, increment E5-BANNER.

``app/main.py`` already injected ``data_integrity_warning`` into every template context
(ADR-0005 D3.4) and already published ``data_features.restore_by_identifier`` to the
data-management screen (ADR-0004 D1). Neither was read by any template, so the ADR clause
each of them exists to satisfy was undelivered: the integrity report was visible only in a
startup log line, and the restore mode was selectable only by a hand-crafted POST.

``tests/test_template_accessibility.py`` asserts the *markup*. This module asserts the
*rendered page*, because on the sponsor's live database ``INTEGRITY_WARNING`` is ``None``
and a banner nobody has ever seen rendered is not a delivered banner. Every assertion here
runs against a response produced by the application, in both states of the condition.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import data_integrity
from app.database import get_session
from app.main import app

CAVEAT = "Requires competent professional and tax review."

BANNER_TITLE = "Some record links need attention"
BANNER_MARKER = 'class="integrity-banner"'

#: The exact sentence app/data_integrity.py:orphan_warning_text produces for two orphans.
#: Built by the module under test rather than pasted, so the two cannot drift apart.
ORPHAN = data_integrity.OrphanRecord(
    child_dataset="Contracts",
    child_display="Passenger Insight Framework - Work Order 7",
    child_id=1,
    field="customer_id",
    parent_dataset="Customers",
    missing_parent_id=906,
)


def client_for(session) -> TestClient:
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


@pytest.fixture()
def warned(monkeypatch):
    """The non-None case. It cannot be reached from data alone.

    ``INTEGRITY_WARNING`` is set once, at startup, by ``apply_foreign_key_policy``. A test
    that only seeded an orphan would still render nothing, because the module-level value is
    not recomputed per request -- which is exactly why this state had never been seen.
    """
    warning = data_integrity.orphan_warning_text((ORPHAN, ORPHAN))
    assert warning, "orphan_warning_text returned nothing for a non-empty report"
    monkeypatch.setattr(data_integrity, "INTEGRITY_WARNING", warning)
    return warning


# --------------------------------------------------------------------------
# ADR-0005 D3.4 -- the banner
# --------------------------------------------------------------------------

#: One page from each area of the workflow. The banner is injected from
#: ``template_context``, so "persistent" means every page, not the dashboard.
PAGES = ["/", "/customers", "/projects", "/costs", "/data-management", "/audit"]


@pytest.mark.parametrize("path", PAGES)
def test_the_banner_renders_on_every_page_while_the_condition_stands(
    session, warned, path
) -> None:
    response = client_for(session).get(path)
    assert response.status_code == 200
    assert BANNER_MARKER in response.text, f"{path} does not render the integrity banner"
    assert BANNER_TITLE in response.text
    assert warned in response.text, (
        f"{path} renders the banner container but not the injected sentence"
    )


@pytest.mark.parametrize("path", PAGES)
def test_no_banner_renders_on_a_clean_workspace(session, monkeypatch, path) -> None:
    """The sponsor's live database is currently clean, so this is the state they see."""
    monkeypatch.setattr(data_integrity, "INTEGRITY_WARNING", None)
    response = client_for(session).get(path)
    assert response.status_code == 200
    assert BANNER_MARKER not in response.text
    assert BANNER_TITLE not in response.text
    assert "None" not in response.text.split("<main", 1)[1][:400], (
        "an unguarded banner would print the string 'None' at the top of every page"
    )


def test_the_banner_says_what_was_found_what_stopped_and_what_to_do(session, warned) -> None:
    """ADR-0005 D3.4 requires all three, plainly.

    The operator has to learn that some records point at records that are gone, that link
    checking is therefore off, and what to do about it. Asserted on the rendered page, so a
    change to either the wording function or the template is caught here.
    """
    body = client_for(session).get("/").text
    banner = body.split(BANNER_MARKER, 1)[1].split("</div>", 1)[0]
    assert "no longer in the Hub" in banner, banner
    assert "link checking is switched off" in banner, banner
    assert "Open each listed record" in banner, banner


def test_the_banner_never_claims_the_hub_changed_anything(session, warned) -> None:
    """ADR-0005 D3.5: report and withhold, never mutate -- and never imply a mutation."""
    banner = client_for(session).get("/").text.split(BANNER_MARKER, 1)[1].split("</div>", 1)[0]
    assert "Nothing has been changed or removed" in banner
    for claim in ("repaired", "fixed", "we have corrected", "will be cleaned"):
        assert claim not in banner.lower(), f"the banner claims {claim!r}"


def test_the_banner_links_nowhere_until_the_page_it_needs_exists(session, warned) -> None:
    """D3.4 also specifies a read-only /data-integrity page. It has no route yet.

    Creating it means adding a route to app/main.py, which is outside this increment. A link
    to a 404 would be a worse signal than no link, and would break the standing "0 broken
    internal links" invariant. This test pins the gap so it cannot be forgotten: it fails the
    day the route exists, which is the moment the banner should gain its link.
    """
    banner = client_for(session).get("/").text.split(BANNER_MARKER, 1)[1].split("</div>", 1)[0]
    assert "href=" not in banner, (
        "the banner now carries a link; if /data-integrity exists, point it there and "
        "replace this assertion with one that follows the link"
    )
    assert client_for(session).get("/data-integrity").status_code == 404, (
        "the /data-integrity route now exists, so ADR-0005 D3.4's link is no longer "
        "outstanding: link the banner to it and update this test"
    )


def test_the_banner_does_not_displace_the_preserved_caveat(session, warned) -> None:
    """ADR-0002 line 58 as narrowed by Ruling R2."""
    for path in PAGES:
        response = client_for(session).get(path)
        assert CAVEAT in response.text, f"{path} lost the caveat"


def test_the_banner_does_not_suppress_a_flash_message(session, warned) -> None:
    """Both render at the top of <main>; the standing condition must not hide the event."""
    response = client_for(session).get("/customers?notice=Customer+saved.")
    assert response.status_code == 200
    assert BANNER_MARKER in response.text
    assert "Customer saved." in response.text
    assert response.text.index(BANNER_MARKER) < response.text.index("Customer saved."), (
        "the persistent banner is expected first; if this order is changed, change it "
        "deliberately rather than by accident"
    )
