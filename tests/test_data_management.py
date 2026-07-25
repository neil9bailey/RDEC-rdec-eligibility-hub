import csv
from io import BytesIO, StringIO
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlmodel import select

from app.data_management import (
    apply_import,
    build_import_plan,
    cleanup_candidates,
    csv_export_zip_bytes,
    decode_import_payload,
    delete_unused_records,
    encode_import_payload,
    json_export_bytes,
    purge_records,
)
from app.database import get_session
from app.main import app
from app.models import AuditEvent, Company, Contract, Customer, RDProject


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


def test_previewed_import_adds_and_updates_matching_records(session):
    existing = Company(company_name="Original Limited", utr="1111111111")
    session.add(existing)
    session.commit()
    session.refresh(existing)
    datasets = {
        "companies": [
            {"id": existing.id, "company_name": "Updated Limited", "utr": "2222222222"},
            {"company_name": "New Limited", "utr": "3333333333"},
        ]
    }

    preview = build_import_plan(session, datasets, "add_update")
    encoded = encode_import_payload(preview)
    mode, approved_datasets = decode_import_payload(encoded)
    result = apply_import(session, approved_datasets, mode)

    assert preview["summary"] == {"create": 1, "update": 1, "skip": 0, "error": 0}
    assert result == {"created": 1, "updated": 1, "skipped": 0}
    assert session.get(Company, existing.id).company_name == "Updated Limited"
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
        {"companies": [{"id": company.id, "company_name": "Changed Limited", "utr": "9999999999"}]},
        "add_only",
    )
    mode, approved_datasets = decode_import_payload(encode_import_payload(preview))
    result = apply_import(session, approved_datasets, mode)

    assert preview["summary"]["skip"] == 1
    assert result["skipped"] == 1
    assert session.get(Company, company.id).company_name == "Keep Limited"


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
    assert "does not match an existing or included record" in " ".join(preview["rows"][0]["errors"])


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
