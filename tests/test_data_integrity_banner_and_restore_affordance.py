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

import json
import re
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import select

from app import data_integrity, main
from app.data_management import DATASETS
from app.database import get_session
from app.main import app
from app.models import BusinessUnit, Contract, Customer
from app.settings import get_settings

CAVEAT = "Requires competent professional and tax review."

BANNER_TITLE = "Some record links need attention"
BANNER_MARKER = 'class="integrity-banner"'

#: The exact sentence app/data_integrity.py:orphan_warning_text produces for two orphans.
#: Built by the module under test rather than pasted, so the two cannot drift apart.
#: ``child_dataset`` and ``parent_dataset`` are DatasetSpec *keys*, which is what
#: ``scan_orphans`` emits; ``test_the_scan_really_emits_the_keys_the_page_maps`` holds that to
#: the code. They were dataset labels when this fixture only fed the warning sentence, which
#: reads neither field.
ORPHAN = data_integrity.OrphanRecord(
    child_dataset="contracts",
    child_display="Passenger Insight Framework - Work Order 7",
    child_id=1,
    field="customer_id",
    parent_dataset="customers",
    missing_parent_id=906,
)


@pytest.fixture(autouse=True)
def _hand_the_application_back():
    """Clear the session override this module installs on the shared ``app`` object.

    ``client_for`` cleared it on the *next* call, which is not the same thing: the last test
    in the module left an override bound to a session that had already been torn down. A later
    module that builds a plain ``TestClient(main.app)`` and relies on the real ``get_session``
    then wrote into that dead session instead of into its own database.

    Not hypothetical, and not introduced here. Reproduced with every file this increment
    touched reverted to HEAD inside the container: running this module immediately before
    tests/test_read_route_determinism.py made three of its tests fail, each reporting zero rows
    where one was expected. It is invisible in a full alphabetical run only because modules
    that sort in between install and clear overrides of their own, which is luck rather than
    isolation.
    """
    yield
    app.dependency_overrides.clear()


def client_for(session) -> TestClient:
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


@pytest.fixture()
def warned(monkeypatch):
    """The orphans-found case. It cannot be reached from data alone.

    ``INTEGRITY_WARNING`` is set once, at startup, by ``apply_foreign_key_policy``. A test
    that only seeded an orphan would still render nothing, because the module-level value is
    not recomputed per request -- which is exactly why this state had never been seen.

    Both module-level values are set, not just the sentence. Startup always writes them
    together, and since ADR-0005 D3.6 the banner reads both: a sentence about orphans with an
    empty report is a state the application cannot produce, and a fixture that produced it
    would be testing a screen no operator can reach.
    """
    warning = data_integrity.orphan_warning_text((ORPHAN, ORPHAN))
    assert warning, "orphan_warning_text returned nothing for a non-empty report"
    monkeypatch.setattr(data_integrity, "INTEGRITY_REPORT", (ORPHAN, ORPHAN))
    monkeypatch.setattr(data_integrity, "INTEGRITY_WARNING", warning)
    return warning


@pytest.fixture()
def disabled_on_a_clean_database(monkeypatch):
    """ADR-0005 D3.6: the operator switched link checking off and nothing is wrong with the data.

    The sentence is built by calling the producer rather than by pasting its output, so this
    fixture and the wording cannot drift apart.
    """
    warning = data_integrity.integrity_warning_text((), disabled_by_setting=True)
    assert warning, "integrity_warning_text returned nothing for an operator-disabled workspace"
    monkeypatch.setattr(data_integrity, "INTEGRITY_REPORT", ())
    monkeypatch.setattr(data_integrity, "INTEGRITY_WARNING", warning)
    return warning


@pytest.fixture()
def disabled_with_orphans(monkeypatch):
    """Both reasons at once. The composed sentence carries both, and there is still a list."""
    warning = data_integrity.integrity_warning_text((ORPHAN,), disabled_by_setting=True)
    assert warning
    monkeypatch.setattr(data_integrity, "INTEGRITY_REPORT", (ORPHAN,))
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


def test_the_banner_leads_to_the_page_that_names_the_records(session, warned) -> None:
    """D3.4's second half, delivered by E5-INTEGRITY-PAGE.

    This assertion replaces ``test_the_banner_links_nowhere_until_the_page_it_needs_exists``,
    which pinned the gap while ``/data-integrity`` did not exist and was written to fail the
    day it did. The route exists now, so the pin is spent and the obligation it protected --
    the operator can find out *which* records the sentence is about -- is what is asserted
    instead. The link is followed, not read: a banner that pointed at a 404 would satisfy any
    assertion made on the markup alone.
    """
    banner = client_for(session).get("/").text.split(BANNER_MARKER, 1)[1].split("</div>", 1)[0]
    href = re.search(r'href="([^"]+)"', banner)
    assert href, f"the banner offers no way to see the affected records: {banner!r}"

    followed = client_for(session).get(href.group(1))
    assert followed.status_code == 200, (
        f"the banner links to {href.group(1)}, which answers {followed.status_code}"
    )
    assert CAVEAT in followed.text


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


# --------------------------------------------------------------------------
# ADR-0005 D3.6 -- the banner reports the reason, and does not overstate it
# --------------------------------------------------------------------------

SWITCHED_OFF_TITLE = "Link checking is switched off"


def banner_block(session) -> str:
    body = client_for(session).get("/").text
    assert BANNER_MARKER in body, "no banner rendered in a state that should show one"
    return body.split(BANNER_MARKER, 1)[1].split("</div>", 1)[0]


def test_the_banner_appears_when_the_operator_switched_checking_off(
    session, disabled_on_a_clean_database
) -> None:
    """D3.6 requires the banner in this state. Nothing else about it is in question.

    Compared on the visible text: the published sentence contains "The Hub's", and asserting
    on the raw response would be comparing against ``&#39;``, which is the template escaping
    correctly rather than a difference in the copy.
    """
    body = client_for(session).get("/").text
    assert BANNER_MARKER in body
    assert disabled_on_a_clean_database in visible_text(body)


def test_a_clean_database_is_never_told_its_records_need_attention(
    session, disabled_on_a_clean_database
) -> None:
    """The reason this increment exists.

    D3.6's "show the same banner" is about the operator never being unaware that link checking
    is inactive. It is not licence to assert a data problem: here the scan found nothing, and a
    title reading "Some record links need attention" would be the Hub telling a Finance
    reviewer something untrue about their records.
    """
    banner = banner_block(session)
    assert SWITCHED_OFF_TITLE in banner, banner
    assert BANNER_TITLE not in banner, (
        "the banner claims records need attention on a database where the scan found none"
    )


def test_a_clean_database_is_not_offered_a_list_of_nothing(
    session, disabled_on_a_clean_database
) -> None:
    """A link to a report with no rows is a promise the page cannot keep."""
    banner = banner_block(session)
    assert "href=" not in banner, f"the banner offers a link with nothing to show: {banner!r}"


def test_the_orphan_wording_and_link_are_unchanged(session, warned) -> None:
    """The state that was already correct must be byte-identical. D3.6 changes nothing here."""
    banner = banner_block(session)
    assert BANNER_TITLE in banner
    assert SWITCHED_OFF_TITLE not in banner
    assert 'href="/data-integrity"' in banner
    assert "See which records are affected" in banner


def test_both_reasons_at_once_keep_the_list_and_the_orphan_title(
    session, disabled_with_orphans
) -> None:
    """The composed sentence carries both reasons; the title must not contradict it."""
    banner = banner_block(session)
    assert BANNER_TITLE in banner, "there are records to review, so the title must say so"
    assert 'href="/data-integrity"' in banner
    assert data_integrity.ENFORCEMENT_SETTING in banner, (
        "the operator's own reason has vanished from the sentence"
    )


@pytest.mark.parametrize(
    "state", ["warned", "disabled_on_a_clean_database", "disabled_with_orphans"]
)
def test_no_banner_state_claims_a_repair_or_passes_a_verdict(session, request, state) -> None:
    """The word lists are asserted on the backend side too; they must not diverge here."""
    request.getfixturevalue(state)
    visible = " ".join(re.sub(r"<[^>]+>", " ", banner_block(session)).split()).lower()
    for claim in ("repair", "fixed", "corrected", "cleaned up", "we will", "invalid", "corrupt", "damaged"):
        assert claim not in visible, f"[{state}] banner copy claims {claim!r}: {visible!r}"
    for verdict in ("not eligible", "rejected", "fails", "approved", "qualifies"):
        assert verdict not in visible, f"[{state}] banner copy reads as a verdict: {verdict!r}"
    assert visible


def test_the_switched_off_title_detector_is_not_vacuous(session, warned) -> None:
    """Non-vacuity: a template that hard-coded the new title would fail the orphan state.

    Both titles are asserted in both directions above, so neither can be satisfied by a
    template that renders one string unconditionally.
    """
    assert SWITCHED_OFF_TITLE not in banner_block(session)


# --------------------------------------------------------------------------
# ADR-0005 D3.4 -- the /data-integrity page the banner leads to
# --------------------------------------------------------------------------

#: Orphans shaped the way ``scan_orphans`` really produces them: ``child_dataset`` and
#: ``parent_dataset`` are DatasetSpec *keys*, not labels. The module-level ORPHAN above
#: predates the page and carries labels, which would silently exercise the fallback screen
#: and prove nothing about the mapping.
#: ``test_the_scan_really_emits_the_keys_the_page_maps`` holds this assumption to the code.
REAL_ORPHANS = (
    data_integrity.OrphanRecord(
        child_dataset="contracts",
        child_id=41,
        child_display="Passenger Insight Framework - Work Order 7",
        field="customer_id",
        parent_dataset="customers",
        missing_parent_id=906,
    ),
    data_integrity.OrphanRecord(
        child_dataset="cost_lines",
        child_id=88,
        child_display="Cost lines #88",
        field="project_id",
        parent_dataset="projects",
        missing_parent_id=57,
    ),
)


@pytest.fixture()
def reported(monkeypatch):
    """The startup scan's published report, driven the only way it can be reached."""
    monkeypatch.setattr(data_integrity, "INTEGRITY_REPORT", REAL_ORPHANS)
    monkeypatch.setattr(
        data_integrity, "INTEGRITY_WARNING", data_integrity.orphan_warning_text(REAL_ORPHANS)
    )
    return REAL_ORPHANS


def test_the_page_names_every_orphan_by_its_display_name(session, reported) -> None:
    """ADR-0005 Verification item 5."""
    body = client_for(session).get("/data-integrity").text
    for orphan in reported:
        assert orphan.child_display in body, f"{orphan.child_display!r} is not listed"
        assert str(orphan.missing_parent_id) in body


def visible_text(html_body: str) -> str:
    """Tags stripped and entities resolved: what the operator actually reads.

    Asserting on the raw response would compare against ``R&amp;D``, which is the template
    doing its job, not a difference in the copy.
    """
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html_body)).split())


def test_the_page_gives_each_orphan_its_specific_remedy(session, reported) -> None:
    """D3.4 asks for the remedy in the ADR's own words: "open this contract and choose a customer"."""
    body = visible_text(client_for(session).get("/data-integrity").text)
    assert "Open this contract and choose the customer it should link to." in body
    assert "Open this cost line and choose the R&D project it should link to." in body


def test_each_listed_orphan_offers_a_way_to_reach_the_record(session, reported) -> None:
    body = client_for(session).get("/data-integrity").text
    rows = re.findall(r"<tr>(.*?)</tr>", body, re.S)[1:]
    assert len(rows) == len(reported), f"expected one row per orphan, got {len(rows)}"
    client = client_for(session)
    for row in rows:
        href = re.search(r'href="([^"]+)"', row)
        assert href, f"a listed record offers no link to the screen that owns it: {row!r}"
        assert client.get(href.group(1)).status_code == 200, (
            f"the row links to {href.group(1)}, which does not answer 200"
        )


def test_every_screen_the_page_can_link_to_actually_exists(session) -> None:
    """The standing "0 broken internal links" invariant, applied to the whole mapping.

    Only two of the twenty-five destinations are reachable from any fixture, so following the
    links a fixture happens to produce is not enough: the rest would break unnoticed until a
    real operator hit that record type, which is exactly the audience this page has.
    """
    client = client_for(session)
    for key, (_noun, href, screen) in main.ORPHAN_RECORD_SCREENS.items():
        assert client.get(href).status_code == 200, f"{key} links to {href}, which is broken"
        assert screen and screen[0].isupper(), f"{key} has no screen name a user would recognise"
    assert client.get(main.ORPHAN_FALLBACK_SCREEN[1]).status_code == 200


def test_every_dataset_that_can_orphan_has_a_screen(session) -> None:
    """A new foreign key must not silently fall back to a generic destination.

    ``scan_orphans`` derives its checks from ``DatasetSpec.foreign_keys``, so the set of
    datasets that can appear on this page is decided there, not here. This fails the day the
    two lists diverge.
    """
    can_orphan = {spec.key for spec in DATASETS if spec.foreign_keys}
    parents = {parent for spec in DATASETS for _field, parent in spec.foreign_keys}
    missing = sorted((can_orphan | parents) - set(main.ORPHAN_RECORD_SCREENS))
    assert not missing, f"these datasets can appear on /data-integrity with no screen: {missing}"


def test_the_scan_really_emits_the_keys_the_page_maps(session) -> None:
    """Non-vacuity for REAL_ORPHANS: the fixture's shape is the scan's shape.

    A fixture that used dataset *labels* would exercise the fallback screen for every row and
    every assertion above would still pass, while a real operator's report rendered "record"
    for the type and pointed everything at Data management.
    """
    unit = BusinessUnit(name="Structures", description="Reference unit")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    customer = Customer(customer_name="Orphaned Customer Ltd", business_unit_id=unit.id)
    session.add(customer)
    session.commit()
    session.refresh(customer)
    session.add(Contract(contract_name="Dangling Work Order", customer_id=customer.id))
    session.commit()
    session.delete(customer)
    session.commit()

    found = data_integrity.scan_orphans(session)
    assert found, "the deliberate orphan was not found, so this proves nothing"
    for orphan in found:
        assert orphan.child_dataset in main.ORPHAN_RECORD_SCREENS, (
            f"the scan reports child_dataset={orphan.child_dataset!r}, which the page cannot map"
        )
        assert orphan.parent_dataset in main.ORPHAN_RECORD_SCREENS
    assert any(o.child_dataset == "contracts" for o in found)


def test_the_page_offers_no_repair_of_any_kind(session, reported) -> None:
    """ADR-0005 D3.5: no auto-delete, no auto-nulling, no auto-reparenting. Report only.

    Tested as "offers no action", not as "avoids a word list". A bare word check is the wrong
    detector here and proved it on first run: it fired on the banner's own sentence "Nothing
    has been changed or removed", which is a promise that nothing was done, not an offer to
    do it. So the structural half is what carries the weight -- the page has no form and no
    button, so it has nothing to submit -- and the copy half looks for offers.
    """
    body = client_for(session).get("/data-integrity").text
    # From the page heading down: the page's own content, without the shared banner above it.
    page = body.split('<div class="page-head">', 1)[1]
    assert "<form" not in page, "the report page offers a form, so it can submit something"
    assert "<button" not in page
    assert "method=" not in page and "hx-post" not in page
    visible = visible_text(page).lower()
    for offer in ("repair", "quarantine", "clean up", "delete this", "remove this", "fix this", "we will"):
        assert offer not in visible, f"the page offers to {offer!r} on the operator's records"


def test_the_page_never_calls_the_operators_records_wrong(session, reported) -> None:
    """ADR-0005 Guardrails and ADR-0002 line 59: operational information, not a verdict."""
    page = client_for(session).get("/data-integrity").text.split("<main", 1)[1]
    visible = " ".join(re.sub(r"<[^>]+>", " ", page).split()).lower()
    for word in ("invalid", "corrupt", "damaged", "not eligible", "rejected", "fails", "approved"):
        assert word not in visible, f"the page reads as a verdict: {word!r}"


def test_the_page_still_answers_when_there_is_nothing_to_report(session, monkeypatch) -> None:
    """It is a bookmarkable URL, and a clean workspace is the state the sponsor is in."""
    monkeypatch.setattr(data_integrity, "INTEGRITY_REPORT", ())
    monkeypatch.setattr(data_integrity, "INTEGRITY_WARNING", None)
    response = client_for(session).get("/data-integrity")
    assert response.status_code == 200
    assert "Nothing to review" in response.text
    assert BANNER_MARKER not in response.text
    assert CAVEAT in response.text
    # ...and it does not describe a list that is not there. The first version of this page kept
    # the "every record below stores a broken link" wording in both states, which is a false
    # statement about an empty table on the very page that exists to stop false statements.
    visible = visible_text(response.text.split('<div class="page-head">', 1)[1])
    assert "Every record below" not in visible, visible


def test_the_report_page_writes_nothing(session, reported) -> None:
    """ADR-0006 D1: no HTTP GET may commit. This route takes no session at all."""
    commits = 0

    def count_commit(_connection):
        nonlocal commits
        commits += 1

    engine = session.get_bind()
    event.listen(engine, "commit", count_commit)
    try:
        response = client_for(session).get("/data-integrity")
    finally:
        event.remove(engine, "commit", count_commit)
    assert response.status_code == 200
    assert commits == 0, f"the read-only integrity report issued {commits} COMMIT(s)"


def test_the_page_does_not_mutate_the_report_it_renders(session, reported) -> None:
    """ADR-0005 D3.5 and Verification item 6: the scan's output is read, never edited."""
    client_for(session).get("/data-integrity")
    assert data_integrity.INTEGRITY_REPORT == REAL_ORPHANS


# --------------------------------------------------------------------------
# ADR-0004 D1 -- the restore-by-identifier affordance
# --------------------------------------------------------------------------

RESTORE_VALUE = 'value="restore_by_identifier"'
RESTORE_CONTROL = 'class="restore-option"'
LIVE_CONTRACT_NAME = "Passenger Insight Framework - Work Order 7"
UPLOADED_CONTRACT_NAME = "TOTALLY DIFFERENT CONTRACT"


@pytest.fixture()
def live_contract(session):
    """The record finding C2 destroyed, rebuilt so the affordance is exercised on it."""
    unit = BusinessUnit(name="Transport", description="Reference unit")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    customer = Customer(customer_name="Transport for London", business_unit_id=unit.id)
    session.add(customer)
    session.commit()
    session.refresh(customer)
    contract = Contract(contract_name=LIVE_CONTRACT_NAME, customer_id=customer.id)
    session.add(contract)
    session.commit()
    session.refresh(contract)
    return contract


@pytest.fixture()
def restore_enabled(monkeypatch):
    """The operator action ADR-0004 D1 requires. Off by default, never a release default."""
    monkeypatch.setattr(get_settings(), "data_restore_by_identifier_enabled", True)


def upload_for(contract_id: int, customer_id: int) -> dict:
    """A file whose only claim on the live contract is its Hub reference."""
    payload = {
        "datasets": {
            "contracts": [
                {
                    "id": contract_id,
                    "contract_name": UPLOADED_CONTRACT_NAME,
                    "customer_id": customer_id,
                }
            ]
        }
    }
    return {
        "import_file": ("restore.json", json.dumps(payload).encode("utf-8"), "application/json")
    }


def test_the_import_form_offers_nothing_while_the_gate_is_shut(session) -> None:
    """The purge precedent: the control is not rendered at all when it is off.

    ``get_settings().data_restore_by_identifier_enabled`` is False by default, so this is
    the state of every workspace that has not deliberately been changed.
    """
    assert get_settings().data_restore_by_identifier_enabled is False
    body = client_for(session).get("/data-management").text
    assert RESTORE_VALUE not in body
    assert RESTORE_CONTROL not in body
    assert "Restoring a backup" not in body
    assert "with-restore-mode" not in body, (
        "the fieldset takes its full-width span even with the feature off, so the "
        "default import layout has moved"
    )
    # ...while the two default modes are untouched.
    assert 'value="add_only"' in body
    assert 'value="add_update"' in body


def test_the_import_form_offers_the_restore_mode_once_an_operator_enables_it(
    session, restore_enabled
) -> None:
    body = client_for(session).get("/data-management").text
    assert RESTORE_VALUE in body
    assert RESTORE_CONTROL in body
    assert "Restoring a backup" in body
    control = body.split(RESTORE_CONTROL, 1)[1].split("</label>", 1)[0]
    assert 'name="import_mode"' in control, control
    assert "checked" not in control, "the restore mode must never be preselected"


def test_the_rendered_warning_states_what_the_mode_actually_does(
    session, restore_enabled
) -> None:
    """The copy has to earn its place: this is how finding C2 destroyed a live contract."""
    body = client_for(session).get("/data-management").text
    control = body.split(RESTORE_CONTROL, 1)[1].split("</label>", 1)[0]
    visible = " ".join(re.sub(r"<[^>]+>", " ", control).split())
    assert "Hub reference" in visible
    assert "replace a live record it never names" in visible, visible
    assert "backup" in visible.lower()
    assert "References are not shared between workspaces" in visible, visible
    # Not a verdict, and not a promise the Hub will look after it (ADR-0002 line 59).
    for word in ("approved", "rejected", "qualifies", "safely", "automatically"):
        assert word not in visible.lower(), f"restore copy contains {word!r}: {visible!r}"


def test_the_offered_value_is_the_one_the_backend_accepts(
    session, live_contract, restore_enabled
) -> None:
    """Drive the affordance: read the value out of the rendered form and post it.

    A control that offers a value the route refuses is worse than no control. The value
    is taken from the page rather than written into the test, so a typo in the template
    cannot pass.
    """
    client = client_for(session)
    body = client.get("/data-management").text
    offered = re.findall(r'<input[^>]*name="import_mode"[^>]*value="([^"]+)"', body)
    assert "restore_by_identifier" in offered, offered

    response = client.post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "restore_by_identifier"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )
    assert response.status_code == 200, response.text[:500]
    assert "Restore by identifier is turned off." not in response.text


def test_the_preview_names_the_live_record_and_says_which_mode_produced_it(
    session, live_contract, restore_enabled
) -> None:
    """ADR-0004 D1.4. The mode is the one fact the rows themselves cannot carry."""
    response = client_for(session).post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "restore_by_identifier"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )
    assert response.status_code == 200
    assert "restore-preview-note" in response.text, (
        "the preview does not disclose that it was produced in restore mode"
    )
    note = response.text.split("restore-preview-note", 1)[1].split("</div>", 1)[0]
    assert "matched by the Hub reference in the file, not by its name" in note, note
    # D1.4: both names on the row, so the substitution is visible.
    assert LIVE_CONTRACT_NAME in response.text
    assert UPLOADED_CONTRACT_NAME in response.text


def test_the_confirmation_describes_replacement_not_addition(
    session, live_contract, restore_enabled
) -> None:
    """The last control before the write must name the act it authorises."""
    response = client_for(session).post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "restore_by_identifier"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )
    assert response.status_code == 200
    confirm = response.text.split('class="confirm-action"', 1)[1]
    assert "deliberately restoring a backup over them" in confirm, confirm[:600]
    assert "Replace the records listed above" in confirm
    assert "I have reviewed the additions and updates shown above." not in confirm
    assert "danger-button" in confirm, "the submit control is not marked as destructive"


def test_a_default_import_preview_carries_none_of_the_restore_copy(
    session, live_contract, restore_enabled
) -> None:
    """Enabling the mode must not colour every preview. Only the restore one changes."""
    response = client_for(session).post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "add_update"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )
    assert response.status_code == 200
    assert "restore-preview-note" not in response.text
    assert "I have reviewed the additions and updates shown above." in response.text
    assert "Replace the records listed above" not in response.text
    # The affordance itself is still offered on the same page.
    assert RESTORE_VALUE in response.text


def test_the_restore_affordance_never_writes_by_being_rendered(
    session, live_contract, restore_enabled
) -> None:
    """Preview changes nothing: the live contract is untouched by everything above."""
    client = client_for(session)
    client.get("/data-management")
    client.post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "restore_by_identifier"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )
    session.expire_all()
    assert session.get(Contract, live_contract.id).contract_name == LIVE_CONTRACT_NAME
    assert len(list(session.exec(select(Contract)))) == 1
