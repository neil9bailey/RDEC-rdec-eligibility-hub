"""The operator gate on restore-by-identifier imports (ADR-0004 D1).

Restore by identifier is the one mode that treats an identifier in an uploaded file as identity,
so a file can overwrite a live record it never named by name. That is precisely how finding C2
destroyed a live contract, and the ADR requires the mode to exist but to be gated like purge:
disabled by default, and enabled only by a deliberate operator action.

``app/data_management.py`` already refused the mode at plan, decode and apply. It was unreachable
in the other direction too: nothing passed ``restore_by_identifier_enabled``, so no operator could
turn it on. These tests pin both halves -- the default refusal and the enabled path -- at the HTTP
boundary, because the flag being read in the route is the part that was missing.
"""

from base64 import urlsafe_b64decode, urlsafe_b64encode
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.main import app
from app.models import BusinessUnit, Contract, Customer
from app.settings import get_settings


RESTORE_REFUSAL = "Restore by identifier is turned off."


@pytest.fixture(autouse=True)
def _hand_the_application_back():
    """Clear the session override this module installs on the shared ``app`` object.

    Added at G3. ``client_for`` cleared the overrides when the *next* client was built, which
    is not the same as clearing them at teardown: the last test in this module left an
    override bound to a session that had already been torn down, and the next module to build
    a plain ``TestClient(main.app)`` against the real ``get_session`` then wrote into that
    dead session instead of its own database.

    The same fixture already guards tests/test_htmx_save_feedback.py and
    tests/test_data_integrity_banner_and_restore_affordance.py. This module was the last one
    still leaking: a session-end probe over every test module reported
    ``overrides_at_session_end=1 ['get_session']`` here and 0 everywhere else.

    Measured before this fixture existed, inside the container:

        pytest -q tests/test_restore_by_identifier_gate.py tests/test_read_route_determinism.py
        -> 3 failed, 11 passed   (each failure "0 == 1", zero rows created)

        pytest -q tests/test_read_route_determinism.py
        -> 7 passed

    A full alphabetical run hid it, because ``test_read_route_determinism`` sorts before
    ``test_restore_by_identifier_gate``. That ordering is luck, not isolation, and the module
    it poisons is the ADR-0006 D1/D4/D5.5 conformance proof.
    """
    yield
    app.dependency_overrides.clear()


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


@pytest.fixture()
def live_contract(session):
    """One customer and one contract, standing in for the sponsor's live records."""
    unit = BusinessUnit(name="Transport", description="Reference unit")
    session.add(unit)
    session.commit()
    session.refresh(unit)

    customer = Customer(customer_name="Transport for London", business_unit_id=unit.id)
    session.add(customer)
    session.commit()
    session.refresh(customer)

    contract = Contract(
        contract_name="Passenger Insight Framework - Work Order 7",
        customer_id=customer.id,
    )
    session.add(contract)
    session.commit()
    session.refresh(contract)
    return contract


def upload_for(contract_id: int, customer_id: int) -> dict:
    """A file whose only claim on the live record is an identifier.

    The contract name is deliberately unrelated to the live one, so under natural-key matching
    this file describes a new record and can never select the live contract. Only the identifier
    connects them, which is exactly what the restore mode makes authoritative.
    """
    payload = {
        "datasets": {
            "contracts": [
                {
                    "id": contract_id,
                    "contract_name": "TOTALLY DIFFERENT CONTRACT",
                    "customer_id": customer_id,
                }
            ]
        }
    }
    return {"import_file": ("restore.json", json.dumps(payload).encode("utf-8"), "application/json")}


def test_the_gate_is_off_by_default():
    """Binding: it must never be a release default. Same posture as purge."""
    assert get_settings().data_restore_by_identifier_enabled is False
    assert get_settings().data_purge_enabled is False


def test_the_preview_refuses_the_restore_mode_while_the_gate_is_shut(session, live_contract):
    response = client_for(session).post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "restore_by_identifier"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )

    assert response.status_code == 400
    assert RESTORE_REFUSAL in response.text

    session.expire_all()
    unchanged = session.get(Contract, live_contract.id)
    assert unchanged.contract_name == "Passenger Insight Framework - Work Order 7"


def test_the_default_modes_still_work_while_the_gate_is_shut(session, live_contract):
    """The gate refuses one mode, not imports. add_only must be unaffected."""
    response = client_for(session).post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "add_only"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )

    assert response.status_code == 200
    assert RESTORE_REFUSAL not in response.text


def test_an_operator_can_enable_the_restore_mode(session, live_contract, monkeypatch):
    """ADR-0004 D1 requires the mode to exist as an operator-enabled one, not to be absent."""
    monkeypatch.setattr(get_settings(), "data_restore_by_identifier_enabled", True)

    response = client_for(session).post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "restore_by_identifier"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )

    assert response.status_code == 200
    assert RESTORE_REFUSAL not in response.text
    # D1.4: the preview must name the live record the identifier would overwrite.
    assert "Passenger Insight Framework - Work Order 7" in response.text


def test_a_payload_issued_while_enabled_is_refused_once_the_gate_is_shut(
    session, live_contract, monkeypatch
):
    """The apply route re-reads the flag; it does not trust the payload that reached it."""
    client = client_for(session)
    monkeypatch.setattr(get_settings(), "data_restore_by_identifier_enabled", True)
    preview = client.post(
        "/data-management/import/preview",
        data={"data_area": "contracts", "import_mode": "restore_by_identifier"},
        files=upload_for(int(live_contract.id), int(live_contract.customer_id)),
    )
    assert preview.status_code == 200
    payload = preview.text.split('name="import_payload" value="', 1)[1].split('"', 1)[0]
    decoded = json.loads(urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    assert decoded["mode"] == "restore_by_identifier"

    monkeypatch.setattr(get_settings(), "data_restore_by_identifier_enabled", False)
    response = client.post("/data-management/import/apply", data={"import_payload": payload})

    assert response.status_code == 400
    assert RESTORE_REFUSAL in response.text

    session.expire_all()
    unchanged = session.get(Contract, live_contract.id)
    assert unchanged.contract_name == "Passenger Insight Framework - Work Order 7"


def test_a_hand_built_restore_payload_is_refused_at_apply(session, live_contract):
    """The apply route is reachable without a preview, so it decides the question itself."""
    forged = urlsafe_b64encode(
        json.dumps(
            {
                "mode": "restore_by_identifier",
                "nonce": "forged",
                "datasets": {
                    "contracts": [
                        {
                            "values": {"contract_name": "TOTALLY DIFFERENT CONTRACT"},
                            "declared_id": int(live_contract.id),
                            "existing_id": int(live_contract.id),
                        }
                    ]
                },
            }
        ).encode("utf-8")
    ).decode("ascii")

    response = client_for(session).post(
        "/data-management/import/apply", data={"import_payload": forged}
    )

    assert response.status_code == 400
    assert RESTORE_REFUSAL in response.text

    session.expire_all()
    unchanged = session.get(Contract, live_contract.id)
    assert unchanged.contract_name == "Passenger Insight Framework - Work Order 7"
    assert len(list(session.exec(select(Contract)))) == 1


def test_the_gate_state_is_published_to_the_import_screen(session):
    """The form can only offer the mode when an operator has turned it on."""
    response = client_for(session).get("/data-management")
    assert response.status_code == 200
    assert 'value="restore_by_identifier"' not in response.text
