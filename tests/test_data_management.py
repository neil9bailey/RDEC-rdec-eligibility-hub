from base64 import urlsafe_b64decode, urlsafe_b64encode
import csv
from io import BytesIO, StringIO
import json
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import data_management
from app.data_management import (
    DataOperationError,
    ImportIssue,
    PLAIN_SIGNED_NUMBER,
    _safe_csv_value,
    apply_import,
    build_import_plan,
    cleanup_candidates,
    consume_import_payload,
    csv_export_zip_bytes,
    decode_import_payload,
    delete_unused_records,
    encode_import_payload,
    json_export_bytes,
    parse_import_file,
    purge_records,
)
from app.database import get_session
from app.main import app
from app.models import (
    AuditEvent,
    BusinessUnit,
    Company,
    Contract,
    CostLine,
    Customer,
    EntitlementAssessment,
    RDProject,
    Solution,
)


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_selected_json_and_csv_exports_are_review_safe(session):
    session.add(Company(company_name="=Formula Limited", utr="1234567890"))
    session.commit()

    exported = json.loads(json_export_bytes(session, ["companies"]))

    assert set(exported["datasets"]) == {"companies"}
    assert exported["datasets"]["companies"][0]["company_name"] == "=Formula Limited"
    assert exported["caveat"] == "Requires competent professional and tax review."

    archive_bytes = csv_export_zip_bytes(session, ["companies"])
    with ZipFile(BytesIO(archive_bytes)) as archive:
        csv_text = archive.read("companies.csv").decode("utf-8-sig")
        rows = list(csv.DictReader(StringIO(csv_text)))
        manifest = json.loads(archive.read("manifest.json"))

    assert rows[0]["company_name"] == "'=Formula Limited"
    assert manifest["datasets"][0]["records"] == 1
    assert "Use the JSON export" in manifest["purpose"]


@pytest.mark.parametrize(
    "value",
    [
        "-1+cmd|'/c calc'!A0",
        "-500,=1+1",
        "- 500",
        "-5-5",
        "-1e",
        "-",
        "−5",  # Unicode minus (U+2212), not the ASCII hyphen-minus.
    ],
)
def test_a_value_that_only_looks_like_a_negative_number_is_still_neutralised(value):
    """ADR-0004 D3: the seven named negative cases. Each must be neutralised."""
    assert _safe_csv_value(value) == "'" + value


@pytest.mark.parametrize("value", ["-500", "-1.5", "-0.25", "-1.5e3", "-.5"])
def test_a_plain_signed_number_exports_as_a_number(value):
    """ADR-0004 D3: the five named positive cases. Finance must be able to total these."""
    assert _safe_csv_value(value) == value


@pytest.mark.parametrize("value", ["=1+1", "+1", "@SUM(A1)", "\tcmd", "\rcmd", "\ncmd"])
def test_every_formula_lead_is_neutralised_including_the_newly_added_line_feed(value):
    """ADR-0004 D3: LF is ADDED by the amendment, so this is not a net loss of coverage."""
    assert _safe_csv_value(value) == "'" + value


def test_the_signed_number_exemption_is_a_whole_cell_anchored_match():
    """ADR-0004 D3 binding guardrail: never a search, never whitespace tolerant."""
    assert PLAIN_SIGNED_NUMBER.pattern.startswith("^")
    assert PLAIN_SIGNED_NUMBER.pattern.endswith("$")
    assert PLAIN_SIGNED_NUMBER.match("-500 ") is None
    assert PLAIN_SIGNED_NUMBER.match(" -500") is None
    assert PLAIN_SIGNED_NUMBER.match("-1,000") is None
    assert PLAIN_SIGNED_NUMBER.match("-£500") is None
    assert PLAIN_SIGNED_NUMBER.match("−500") is None


def test_negative_money_survives_a_csv_export_as_a_number(session):
    """The finding: every negative figure in a Finance or Ayming review pack was text."""
    company = Company(company_name="Cost Co", utr="1111111111")
    customer = Customer(customer_name="Cost Customer")
    session.add_all([company, customer])
    session.commit()
    session.refresh(customer)
    solution = Solution(solution_name="Cost Solution", customer_id=customer.id)
    session.add(solution)
    session.commit()
    session.refresh(solution)
    project = RDProject(project_title="Cost Project", solution_id=solution.id)
    session.add(project)
    session.commit()
    session.refresh(project)
    session.add(CostLine(project_id=project.id, activity="Credit note", gross_cost=-500.0))
    session.commit()

    archive_bytes = csv_export_zip_bytes(session, ["cost_lines"])
    with ZipFile(BytesIO(archive_bytes)) as archive:
        rows = list(csv.DictReader(StringIO(archive.read("cost_lines.csv").decode("utf-8-sig"))))
        manifest = json.loads(archive.read("manifest.json"))

    assert rows[0]["gross_cost"] == "-500.0"
    assert float(rows[0]["gross_cost"]) == -500.0
    assert "Use the JSON export for restore or re-import" in manifest["purpose"]
    assert manifest["caveat"] == "Requires competent professional and tax review."


def test_previewed_import_adds_and_updates_matching_records(session):
    """Matching is by natural key (ADR-0002 line 30 as amended by ADR-0004 D1).

    This fixture previously identified the record to update with the uploaded ``id``. That
    is the behaviour finding C2 proved destructive, so the fixture now matches the way the
    amended line 30 requires: the company name is the natural key, and the other fields move.
    """
    existing = Company(company_name="Original Limited", utr="1111111111")
    session.add(existing)
    session.commit()
    session.refresh(existing)
    datasets = {
        "companies": [
            {"company_name": "Original Limited", "utr": "2222222222"},
            {"company_name": "New Limited", "utr": "3333333333"},
        ]
    }

    preview = build_import_plan(session, datasets, "add_update")
    encoded = encode_import_payload(preview)
    mode, approved_datasets = decode_import_payload(encoded)
    result = apply_import(session, approved_datasets, mode)

    assert preview["summary"] == {"create": 1, "update": 1, "skip": 0, "error": 0}
    assert result == {
        "created": 1,
        "updated": 1,
        "skipped": 0,
        "entitlement_reviews": 0,
        "entitlement_reviews_failed": 0,
    }
    updated = session.get(Company, existing.id)
    assert updated.company_name == "Original Limited"
    assert updated.utr == "2222222222"
    assert session.exec(select(Company).where(Company.company_name == "New Limited")).first() is not None
    actions = set(session.exec(select(AuditEvent.action)).all())
    assert {"import_create", "import_update"}.issubset(actions)


def test_add_only_import_leaves_a_matching_record_unchanged(session):
    company = Company(company_name="Keep Limited", utr="1111111111")
    session.add(company)
    session.commit()
    session.refresh(company)

    preview = build_import_plan(
        session,
        {"companies": [{"company_name": "Keep Limited", "utr": "9999999999"}]},
        "add_only",
    )
    mode, approved_datasets = decode_import_payload(encode_import_payload(preview))
    result = apply_import(session, approved_datasets, mode)

    assert preview["summary"]["skip"] == 1
    assert result["skipped"] == 1
    assert session.get(Company, company.id).utr == "1111111111"


def test_import_preview_rejects_a_missing_parent_link(session):
    preview = build_import_plan(
        session,
        {
            "accounting_periods": [
                {
                    "company_id": 999,
                    "label": "FY2026/27",
                    "start_date": "2026-04-01",
                    "end_date": "2027-03-31",
                    "period_of_account_start": "2026-04-01",
                    "period_of_account_end": "2027-03-31",
                }
            ]
        },
        "add_only",
    )

    assert preview["has_errors"] is True
    assert "does not match a record in this file or in the Hub" in " ".join(preview["rows"][0]["errors"])


def forged_payload(mode, datasets):
    """Build an apply payload directly, exactly as the runtime finding did.

    This deliberately bypasses parse_import_file and build_import_plan, which is the
    whole point: it is the shape an operator can hand-craft and POST to the apply route.
    """
    body = json.dumps({"mode": mode, "datasets": datasets}, separators=(",", ":"))
    return urlsafe_b64encode(body.encode("utf-8")).decode("ascii")


FORGED_AUDIT_ROW = {
    "entity_type": "Company",
    "entity_id": 1,
    "action": "forged",
    "summary": "written by a forged import payload",
    "before": "",
    "after": "",
}


def test_a_non_importable_dataset_is_refused_at_the_parse_layer():
    """ADR-0004 D5 layer 1."""
    bundle = json.dumps({"datasets": {"audit_events": [FORGED_AUDIT_ROW]}}).encode("utf-8")
    with pytest.raises(DataOperationError) as exc:
        parse_import_file("bundle.json", bundle)
    assert "Change history can be exported but not imported." in str(exc.value)


def test_a_non_importable_dataset_is_refused_at_the_decode_layer():
    """ADR-0004 D5 layer 2, called directly rather than through HTTP."""
    with pytest.raises(DataOperationError) as exc:
        decode_import_payload(forged_payload("add_only", {"audit_events": [FORGED_AUDIT_ROW]}))
    assert "Change history can be exported but not imported." in str(exc.value)


def test_a_mistyped_dataset_key_is_refused_at_the_decode_layer():
    """ADR-0004 D5: a mistyped key used to import nothing and report success."""
    with pytest.raises(DataOperationError) as exc:
        decode_import_payload(forged_payload("add_only", {"companiez": [{"company_name": "Typo Ltd"}]}))
    assert "companiez" in str(exc.value)


def test_a_rejected_dataset_key_becomes_a_visible_preview_error_not_an_exception(session):
    """ADR-0004 D5 layer 3: the preview shows the rejection instead of returning a 500."""
    preview = build_import_plan(
        session,
        {"audit_events": [FORGED_AUDIT_ROW], "companiez": [{"company_name": "Typo Ltd"}]},
        "add_only",
    )

    assert preview["has_errors"] is True
    assert preview["summary"]["error"] == 2
    codes = {issue.code for row in preview["rows"] for issue in row["issues"]}
    assert codes == {"not_importable", "unknown_dataset"}
    assert encode_import_payload(preview) == ""


def test_apply_refuses_a_forged_non_importable_payload_and_writes_nothing(session):
    """ADR-0004 D5 layer 4, plus the proven finding: audit_events is what purge preserves."""
    before = len(list(session.exec(select(AuditEvent))))

    with pytest.raises(DataOperationError) as exc:
        apply_import(session, {"audit_events": [FORGED_AUDIT_ROW]}, "add_only")

    assert "Change history can be exported but not imported." in str(exc.value)
    assert len(list(session.exec(select(AuditEvent)))) == before


def test_the_apply_route_refuses_a_forged_non_importable_payload(session):
    """The runtime-proven exploit: forged base64 posted straight to the apply route."""
    before = len(list(session.exec(select(AuditEvent))))
    client = client_for(session)
    try:
        response = client.post(
            "/data-management/import/apply",
            data={"import_payload": forged_payload("add_only", {"audit_events": [FORGED_AUDIT_ROW]})},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "can be exported but not imported" in response.text
    assert len(list(session.exec(select(AuditEvent)))) == before
    assert session.exec(select(AuditEvent).where(AuditEvent.action == "forged")).first() is None


BANNED_ISSUE_TEXT = ("Input should be", "validation error", "Value error", "value_error", "pydantic")


def _all_issues(preview):
    return [issue for row in preview["rows"] for issue in row["issues"]]


def test_import_issues_are_business_language_with_stable_codes(session):
    """ADR-0004 D7: no raw Pydantic text and no database column name reaches a user."""
    customer = Customer(customer_name="Link Customer")
    session.add(customer)
    session.commit()
    session.refresh(customer)

    preview = build_import_plan(
        session,
        {
            "contracts": [
                # str, int, date and bool failures on one dataset, plus a missing required value.
                {
                    "contract_name": 12345,
                    "customer_id": "not-a-number",
                    "start_date": "31/12/2026",
                    "customer_requested_rd": "perhaps",
                },
                {"customer_id": customer.id},
                {"contract_name": "Column Check", "customer_id": customer.id, "made_up_column": "x"},
            ],
            # float failure, on the dataset that carries the money fields.
            "cost_lines": [{"project_id": 1, "gross_cost": "abc"}],
        },
        "add_update",
    )

    issues = _all_issues(preview)
    assert issues, "the deliberately broken rows produced no issues"
    for issue in issues:
        assert isinstance(issue, ImportIssue)
        assert issue.message.endswith("."), issue.message
        if issue.code != "unknown_column":
            # No snake_case identifier can survive this. The one exemption is the
            # unknown-column message, which quotes the operator's OWN column back to
            # them so they can find it; ADR-0004 D7 keeps that message and requires it
            # to render escaped instead (asserted in the injection test below).
            assert "_" not in issue.message, f"a database column name leaked: {issue.message}"
        assert "_" not in issue.field, f"a database column name leaked into a field label: {issue.field}"
        assert issue.code and issue.code == issue.code.lower().replace(" ", "_")
        for banned in BANNED_ISSUE_TEXT:
            assert banned not in issue.message, f"raw validation text leaked: {issue.message}"

    messages = {issue.message for issue in issues}
    assert "Contract name must be text." in messages
    assert "Customer must be a whole number." in messages
    assert "Start date must be a date in YYYY-MM-DD format." in messages
    assert "Customer requested R&D must be true or false." in messages
    assert "Contract name is required." in messages
    assert "Gross cost must be a number." in messages
    assert {issue.code for issue in issues} >= {"invalid_value", "required_value", "unknown_column"}


def test_an_uploaded_column_name_cannot_inject_html_into_the_preview(session):
    """ADR-0004 D7: issue messages carry uploaded content, so they must render escaped."""
    client = client_for(session)
    payload = [{"company_name": "Injection Limited", "<img src=x onerror=alert(1)>": "x"}]
    try:
        response = client.post(
            "/data-management/import/preview",
            data={"data_area": "companies", "import_mode": "add_only"},
            files={"import_file": ("companies.json", json.dumps(payload), "application/json")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "<img src=x onerror=alert(1)>" not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text


def test_an_uploaded_identifier_cannot_overwrite_an_unrelated_live_record(session):
    """C2, the proven file: a CSV carrying id=1 and a different contract name.

    ADR-0004 Verification 2 asserts on the live record, not on the preview.
    """
    customer = Customer(customer_name="Passenger Insight Customer")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    victim = Contract(contract_name="Passenger Insight Framework - Work Order 7", customer_id=customer.id)
    session.add(victim)
    session.commit()
    session.refresh(victim)
    assert victim.id == 1

    csv_row = {"id": "1", "contract_name": "TOTALLY DIFFERENT CONTRACT", "customer_id": str(customer.id)}
    preview = build_import_plan(session, {"contracts": [csv_row]}, "add_update")
    mode, approved = decode_import_payload(encode_import_payload(preview))
    result = apply_import(session, approved, mode)

    assert session.get(Contract, victim.id).contract_name == "Passenger Insight Framework - Work Order 7"
    assert result["created"] == 1
    created = session.exec(
        select(Contract).where(Contract.contract_name == "TOTALLY DIFFERENT CONTRACT")
    ).first()
    assert created is not None and created.id != victim.id


def test_the_preview_names_the_live_record_it_would_change(session):
    """ADR-0004 D1.4: existing_display comes from the live record, never the upload."""
    customer = Customer(customer_name="Disclosure Customer", sector="Rail", corporation_tax_status="unknown")
    session.add(customer)
    session.commit()
    session.refresh(customer)

    preview = build_import_plan(
        session,
        {"customers": [{"customer_name": "Disclosure Customer", "sector": "Bus", "corporation_tax_status": "yes"}]},
        "add_update",
    )
    row = preview["rows"][0]

    assert row["status"] == "update"
    assert row["existing_id"] == customer.id
    assert row["existing_display"] == "Disclosure Customer"
    moved = {change["field"]: (change["before"], change["after"]) for change in row["changed_fields"]}
    assert moved["Sector"] == ("Rail", "Bus")
    assert moved["Corporation tax status"] == ("unknown", "yes")
    assert "Customer name" not in moved


def test_a_renaming_update_shows_the_victims_name_not_the_uploaded_one(session):
    """The non-disclosure that made C2 silent, asserted directly (ADR-0004 Verification 3)."""
    customer = Customer(customer_name="Rename Customer")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    contract = Contract(contract_name="Original Work Order", customer_id=customer.id)
    session.add(contract)
    session.commit()
    session.refresh(contract)

    preview = build_import_plan(
        session,
        {
            "contracts": [
                {
                    "id": contract.id,
                    "contract_name": "Original Work Order",
                    "customer_id": customer.id,
                    "ip_owner": "Renamed Owner",
                }
            ]
        },
        "add_update",
    )
    row = preview["rows"][0]

    assert row["status"] == "update"
    assert row["existing_display"] == "Original Work Order"
    assert row["existing_id"] == contract.id
    assert [change["field"] for change in row["changed_fields"]] == ["IP owner"]


def test_an_update_that_moves_nothing_is_not_counted_as_an_update(session):
    """ADR-0004 D1.4: an update row with no changed fields renders as no change."""
    session.add(Company(company_name="Static Limited", utr="1111111111"))
    session.commit()

    preview = build_import_plan(
        session,
        {"companies": [{"company_name": "Static Limited", "utr": "1111111111"}]},
        "add_update",
    )
    row = preview["rows"][0]

    assert row["changed_fields"] == []
    assert row["no_change"] is True
    assert row["status"] == "skip"
    assert preview["summary"]["update"] == 0


def test_apply_refuses_an_update_the_preview_never_disclosed(session):
    """ADR-0004 D1.5: disclosure is enforced, not merely displayed."""
    session.add(Company(company_name="Disclosed Limited", utr="1111111111"))
    session.commit()

    # Hand-crafted rows carrying no disclosed identifier: they match a live record by
    # natural key, so the re-plan wants to update, but the operator was shown nothing.
    undisclosed = {"companies": [{"company_name": "Disclosed Limited", "utr": "9999999999"}]}

    with pytest.raises(DataOperationError) as exc:
        apply_import(session, undisclosed, "add_update")

    assert "the preview did not show" in str(exc.value)
    assert session.exec(select(Company).where(Company.company_name == "Disclosed Limited")).first().utr == "1111111111"


def test_a_preview_payload_can_only_be_applied_once(session):
    """C3, the proven defect: one preview applied three times created three rows."""
    preview = build_import_plan(
        session,
        {"companies": [{"company_name": "Replay Limited", "utr": "1111111111"}]},
        "add_only",
    )
    payload = encode_import_payload(preview)

    mode, approved = consume_import_payload(payload)
    first = apply_import(session, approved, mode)
    assert first["created"] == 1

    for _ in range(2):
        with pytest.raises(DataOperationError) as exc:
            consume_import_payload(payload)
        assert "already been applied" in str(exc.value)

    companies = list(session.exec(select(Company).where(Company.company_name == "Replay Limited")))
    assert len(companies) == 1


def test_the_apply_route_applies_one_preview_once(session):
    """The runtime-proven route path: the same payload POSTed three times."""
    preview = build_import_plan(
        session,
        {"companies": [{"company_name": "Route Replay Import", "utr": "3333333333"}]},
        "add_only",
    )
    payload = encode_import_payload(preview)

    client = client_for(session)
    statuses = []
    try:
        for _ in range(3):
            response = client.post(
                "/data-management/import/apply",
                data={"import_payload": payload},
                follow_redirects=False,
            )
            statuses.append(response.status_code)
            last = response
    finally:
        app.dependency_overrides.clear()

    assert statuses[0] in (302, 303)
    assert statuses[1:] == [400, 400]
    assert "already been applied" in last.text
    created = list(session.exec(select(Company).where(Company.company_name == "Route Replay Import")))
    assert len(created) == 1


def test_a_spent_preview_stays_spent_even_when_the_import_it_carried_failed(session):
    """ADR-0004 D4: the pop is unconditional; restoring it on failure is prohibited."""
    session.add(Customer(customer_name="Race Customer"))
    session.commit()
    preview = build_import_plan(
        session,
        {"contracts": [{"contract_name": "Race Contract", "customer_id": 1}]},
        "add_only",
    )
    payload = encode_import_payload(preview)

    mode, approved = consume_import_payload(payload)
    # The parent disappears between preview and apply, so the import itself fails.
    session.delete(session.get(Customer, 1))
    session.commit()
    with pytest.raises(DataOperationError):
        apply_import(session, approved, mode)

    with pytest.raises(DataOperationError) as exc:
        consume_import_payload(payload)
    assert "already been applied" in str(exc.value)


def test_a_tampered_payload_is_refused(session):
    """The content hash: the nonce alone must not authenticate edited contents."""
    preview = build_import_plan(
        session,
        {"companies": [{"company_name": "Honest Limited", "utr": "1111111111"}]},
        "add_only",
    )
    payload = encode_import_payload(preview)
    body = json.loads(urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    body["datasets"]["companies"][0]["values"]["company_name"] = "Tampered Limited"
    tampered = urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8")).decode("ascii")

    with pytest.raises(DataOperationError) as exc:
        consume_import_payload(tampered)

    assert "could not be read" in str(exc.value)
    assert session.exec(select(Company).where(Company.company_name == "Tampered Limited")).first() is None


def test_a_preview_from_before_a_restart_is_refused(session):
    """ADR-0004 D4: an in-process nonce set does not survive a restart, and fails closed."""
    preview = build_import_plan(
        session,
        {"companies": [{"company_name": "Restart Limited", "utr": "1111111111"}]},
        "add_only",
    )
    payload = encode_import_payload(preview)
    data_management._ISSUED_PREVIEWS.clear()
    data_management._CONSUMED_PREVIEWS.clear()

    with pytest.raises(DataOperationError) as exc:
        consume_import_payload(payload)

    assert "expired" in str(exc.value)


def test_the_issued_preview_set_stays_bounded(session):
    """ADR-0004 ARB checklist: the nonce set is bounded at 32 entries."""
    data_management._ISSUED_PREVIEWS.clear()
    for index in range(40):
        preview = build_import_plan(
            session,
            {"companies": [{"company_name": f"Bounded {index}", "utr": "1111111111"}]},
            "add_only",
        )
        encode_import_payload(preview)

    assert len(data_management._ISSUED_PREVIEWS) <= 32


def test_restore_by_identifier_is_off_by_default_at_every_entry_point(session):
    """ADR-0004 D1 and the ADR-0002 guardrail: never a release default."""
    datasets = {"companies": [{"id": 1, "company_name": "Restored Limited", "utr": "1111111111"}]}

    with pytest.raises(DataOperationError) as plan_exc:
        build_import_plan(session, datasets, "restore_by_identifier")
    assert "Restore by identifier is turned off" in str(plan_exc.value)

    with pytest.raises(DataOperationError) as decode_exc:
        decode_import_payload(forged_payload("restore_by_identifier", datasets))
    assert "Restore by identifier is turned off" in str(decode_exc.value)

    with pytest.raises(DataOperationError) as apply_exc:
        apply_import(session, datasets, "restore_by_identifier")
    assert "Restore by identifier is turned off" in str(apply_exc.value)


def test_restore_by_identifier_names_the_record_it_would_overwrite_when_enabled(session):
    """ADR-0004 D1: the mode survives, with mandatory disclosure."""
    customer = Customer(customer_name="Restore Customer")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    victim = Contract(contract_name="Passenger Insight Framework - Work Order 7", customer_id=customer.id)
    session.add(victim)
    session.commit()
    session.refresh(victim)

    preview = build_import_plan(
        session,
        {"contracts": [{"id": victim.id, "contract_name": "RESTORED CONTRACT", "customer_id": customer.id}]},
        "restore_by_identifier",
        restore_by_identifier_enabled=True,
    )
    row = preview["rows"][0]

    assert row["status"] == "update"
    assert row["existing_id"] == victim.id
    assert row["existing_display"] == "Passenger Insight Framework - Work Order 7"
    assert row["display"] == "RESTORED CONTRACT"
    assert row["existing_display"] != row["display"]


def project_for(session, customer_name, project_title):
    customer = Customer(customer_name=customer_name, customer_type="transport authority")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    solution = Solution(solution_name=f"{project_title} Solution", customer_id=customer.id)
    session.add(solution)
    session.commit()
    session.refresh(solution)
    project = RDProject(project_title=project_title, solution_id=solution.id)
    session.add(project)
    session.commit()
    session.refresh(project)
    return customer, project


def test_an_import_that_changes_the_facts_recalculates_the_entitlement_review(session):
    """ADR-0004 D6: resync is required, and is confined to what the import touched."""
    changed_customer, changed_project = project_for(session, "Resync Customer", "Resync Project")
    _, untouched_project = project_for(session, "Untouched Customer", "Untouched Project")

    preview = build_import_plan(
        session,
        {"customers": [{"customer_name": "Resync Customer", "corporation_tax_status": "no"}]},
        "add_update",
    )
    mode, approved = consume_import_payload(encode_import_payload(preview))
    result = apply_import(session, approved, mode)

    assert result["updated"] == 1
    assert result["entitlement_reviews"] == 1
    assert result["entitlement_reviews_failed"] == 0

    assessments = {
        assessment.project_id: assessment for assessment in session.exec(select(EntitlementAssessment))
    }
    assert changed_project.id in assessments
    assert assessments[changed_project.id].customer_corporation_tax_status == "no"
    assert untouched_project.id not in assessments, "a project the import never touched was recalculated"

    resync_events = list(
        session.exec(select(AuditEvent).where(AuditEvent.action == "import_entitlement_resync"))
    )
    assert len(resync_events) == 1
    assert "following previewed import" in resync_events[0].summary
    assert "Resync Project" in resync_events[0].summary
    assert session.get(Customer, changed_customer.id).corporation_tax_status == "no"


def test_an_import_that_touches_no_entitlement_facts_recalculates_nothing(session):
    """Guardrail 3, from the other side: no fact change, no recalculation."""
    _, project = project_for(session, "Quiet Customer", "Quiet Project")

    preview = build_import_plan(
        session,
        {"companies": [{"company_name": "Unrelated Limited", "utr": "1111111111"}]},
        "add_only",
    )
    mode, approved = consume_import_payload(encode_import_payload(preview))
    result = apply_import(session, approved, mode)

    assert result["entitlement_reviews"] == 0
    assert list(session.exec(select(EntitlementAssessment))) == []
    assert list(session.exec(select(AuditEvent).where(AuditEvent.action == "import_entitlement_resync"))) == []


def orphan_contracts(session):
    """Contracts whose customer_id has no customer row. The invariant, asserted directly."""
    customer_ids = {customer.id for customer in session.exec(select(Customer))}
    return [
        contract
        for contract in session.exec(select(Contract))
        if contract.customer_id not in customer_ids
    ]


@pytest.mark.parametrize("mode", ["add_only", "add_update"])
def test_an_in_file_identifier_never_satisfies_a_foreign_key_on_its_own(session, mode):
    """C1a, the proven file: a contract referencing in-file customer 906.

    The customer row matches a live record by natural key, so its declared identifier 906
    is discarded. Previously the preview reported zero errors and apply created a contract
    referencing customer 906, which does not exist.
    """
    live = Customer(customer_name="Existing Customer")
    session.add(live)
    session.commit()
    session.refresh(live)
    assert live.id != 906

    datasets = {
        "customers": [{"id": 906, "customer_name": "Existing Customer", "sector": "Rail"}],
        "contracts": [{"contract_name": "Dangling Contract", "customer_id": 906}],
    }

    preview = build_import_plan(session, datasets, mode)
    assert preview["has_errors"] is False, [row["errors"] for row in preview["rows"]]

    mode_out, approved = decode_import_payload(encode_import_payload(preview))
    apply_import(session, approved, mode_out)

    contract = session.exec(select(Contract).where(Contract.contract_name == "Dangling Contract")).first()
    assert contract is not None
    assert orphan_contracts(session) == []
    assert contract.customer_id == live.id
    assert session.get(Customer, 906) is None


@pytest.mark.parametrize("mode", ["add_only", "add_update"])
def test_a_link_to_an_identifier_in_neither_the_file_nor_the_hub_errors_at_preview(session, mode):
    """ADR-0004 D2.1 L2: never dangle. The error is raised at preview, in both modes."""
    preview = build_import_plan(
        session,
        {"contracts": [{"contract_name": "Dangling Contract", "customer_id": 906}]},
        mode,
    )

    assert preview["has_errors"] is True
    issue = preview["rows"][0]["issues"][0]
    assert issue.code == "unknown_link"
    assert issue.message == "Customer does not match a record in this file or in the Hub."
    assert encode_import_payload(preview) == ""


def test_a_bundle_restores_its_own_parent_child_links(session):
    """ADR-0004 D2.1 L1: a new parent and its child in one file still link up."""
    datasets = {
        "customers": [{"id": 906, "customer_name": "Bundle Customer", "sector": "Rail"}],
        "contracts": [{"contract_name": "Bundle Contract", "customer_id": 906}],
    }

    preview = build_import_plan(session, datasets, "add_only")
    assert preview["has_errors"] is False
    link = preview["rows"][1]["links"][0]
    assert link["source"] == "this file"
    assert link["parent"] == "Bundle Customer"

    mode, approved = decode_import_payload(encode_import_payload(preview))
    apply_import(session, approved, mode)

    customer = session.exec(select(Customer).where(Customer.customer_name == "Bundle Customer")).first()
    contract = session.exec(select(Contract).where(Contract.contract_name == "Bundle Contract")).first()
    assert customer is not None and contract is not None
    assert contract.customer_id == customer.id
    assert orphan_contracts(session) == []


def test_a_live_parent_reference_is_disclosed_by_name(session):
    """ADR-0004 D2.1 L3: referencing an existing parent is safe, and is named in the preview."""
    live = Customer(customer_name="Named Customer")
    session.add(live)
    session.commit()
    session.refresh(live)

    preview = build_import_plan(
        session,
        {"contracts": [{"contract_name": "Referencing Contract", "customer_id": live.id}]},
        "add_only",
    )

    assert preview["has_errors"] is False
    assert preview["rows"][0]["links"] == [
        {
            "field": "customer_id",
            "label": "Customer",
            "source": "the Hub",
            "parent": "Named Customer",
            "resolved_id": str(live.id),
        }
    ]


def test_apply_refuses_an_in_file_link_whose_parent_is_not_written_yet(session):
    """ADR-0004 D2.1 L4: the apply-time re-check, rather than silently using a live row."""
    datasets = {
        "business_units": [
            {"id": 2, "name": "Child Unit", "parent_id": 3},
            {"id": 3, "name": "Parent Unit"},
        ]
    }

    preview = build_import_plan(session, datasets, "add_only")
    assert preview["has_errors"] is False

    mode, approved = decode_import_payload(encode_import_payload(preview))
    with pytest.raises(DataOperationError) as exc:
        apply_import(session, approved, mode)

    assert "appears later in this file" in str(exc.value)
    assert list(session.exec(select(BusinessUnit))) == []


def test_cleanup_only_removes_selected_unlinked_records(session):
    unused = Customer(customer_name="Unused Customer")
    linked = Customer(customer_name="Linked Customer")
    session.add_all([unused, linked])
    session.commit()
    session.refresh(unused)
    session.refresh(linked)
    session.add(Contract(contract_name="Live Contract", customer_id=linked.id))
    session.commit()

    candidates = cleanup_candidates(session)
    tokens = {item["token"] for item in candidates}

    assert f"customers:{unused.id}" in tokens
    assert f"customers:{linked.id}" not in tokens
    assert delete_unused_records(session, [f"customers:{unused.id}"]) == 1
    assert session.get(Customer, unused.id) is None
    assert session.get(Customer, linked.id) is not None


def test_startup_reference_customers_are_not_offered_for_cleanup(seeded_session):
    candidate_tokens = {item["token"] for item in cleanup_candidates(seeded_session)}
    reference_customers = list(
        seeded_session.exec(
            select(Customer).where(Customer.customer_name.in_(["Transport for London (TfL)", "National Rail"]))
        )
    )

    assert reference_customers
    assert all(f"customers:{customer.id}" not in candidate_tokens for customer in reference_customers)


def test_claim_work_purge_preserves_customers_and_change_history(seeded_session):
    customer_count = len(list(seeded_session.exec(select(Customer))))

    counts = purge_records(seeded_session, "claim_work", "PURGE CLAIM WORK")

    assert counts["RDProject"] > 0
    assert list(seeded_session.exec(select(RDProject))) == []
    assert len(list(seeded_session.exec(select(Customer)))) == customer_count
    purge_event = seeded_session.exec(select(AuditEvent).where(AuditEvent.action == "purge")).first()
    assert purge_event is not None


def test_data_management_and_workflow_routes_use_plain_language(seeded_session):
    client = client_for(seeded_session)
    try:
        data_page = client.get("/data-management")
        dashboard = client.get("/")
        final_review = client.get("/final-review")
        export_response = client.post(
            "/data-management/export",
            data={"data_areas": "companies", "export_format": "json"},
        )
    finally:
        app.dependency_overrides.clear()

    assert data_page.status_code == 200
    assert "Edit records" in data_page.text
    assert "Preview import" in data_page.text
    assert "Purge is not available in this workspace" in data_page.text
    assert dashboard.status_code == 200
    assert "See what needs attention" in dashboard.text
    assert "Your review workflow" in dashboard.text
    assert final_review.status_code == 200
    assert "Final review and working papers" in final_review.text
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("application/json")
    assert "rdec-hub-data.json" in export_response.headers["content-disposition"]


def test_import_preview_shows_plain_change_counts(session):
    client = client_for(session)
    try:
        response = client.post(
            "/data-management/import/preview",
            data={"data_area": "companies", "import_mode": "add_update"},
            files={
                "import_file": (
                    "companies.json",
                    json.dumps([{"company_name": "Preview Limited", "utr": "1234567890"}]),
                    "application/json",
                )
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "1 add" in response.text
    assert "0 update" in response.text
    assert "built-in method" not in response.text
