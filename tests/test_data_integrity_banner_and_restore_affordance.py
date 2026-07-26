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

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import data_integrity
from app.database import get_session
from app.main import app
from app.models import BusinessUnit, Contract, Customer
from app.settings import get_settings

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
