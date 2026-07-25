from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
import csv
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from io import BytesIO, StringIO
import json
from typing import Any, get_args
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import ValidationError
from sqlmodel import SQLModel, Session, select

from app.audit import compact_snapshot, log_event
from app.models import (
    AccountingPeriod,
    Activity,
    AuditEvent,
    BusinessUnit,
    BuyerPortalInstance,
    ClaimPeriodSubmissionStatus,
    Company,
    CompetentProfessionalOpinion,
    Contract,
    CostLine,
    Customer,
    CustomerWatchProfile,
    EntitlementAssessment,
    EvidenceItem,
    ExtractedQualityQuestion,
    ExtractedRequirement,
    FrameworkAgentRun,
    FrameworkOpportunity,
    FrameworkSource,
    IntelligenceReport,
    KnowledgeSourceCheck,
    OpportunityDocument,
    PortalRetrievalRun,
    ProcurementPlatform,
    RDProject,
    RDECOpportunitySignal,
    ReviewDecision,
    Solution,
    SourceCheckSnapshot,
    TechnicalUncertainty,
)
from app.services import CAVEAT


MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000


class DataOperationError(ValueError):
    pass


@dataclass(frozen=True)
class ImportIssue:
    """One problem with one uploaded row (ADR-0004 D7).

    ``code`` is the stable contract a caller may branch on. ``message`` is display
    only and must never be parsed. ``field`` carries a human field label, never a
    database column name.
    """

    field: str
    message: str
    code: str


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    label: str
    group: str
    model: type[SQLModel]
    natural_key: tuple[str, ...] = ()
    foreign_keys: tuple[tuple[str, str], ...] = ()
    importable: bool = True
    # ADR-0004 D7. Additive: overrides for columns the generic humaniser renders badly.
    # compare=False keeps DatasetSpec hashable despite the mutable default.
    field_labels: dict[str, str] = field(default_factory=dict, compare=False)


DATASETS = (
    DatasetSpec(
        "companies",
        "Companies",
        "Company and periods",
        Company,
        ("company_name",),
        field_labels={
            "utr": "Unique taxpayer reference",
            "vat_number": "VAT number",
            "sic_code": "SIC code",
        },
    ),
    DatasetSpec(
        "accounting_periods",
        "Accounting periods",
        "Company and periods",
        AccountingPeriod,
        ("company_id", "label"),
        (("company_id", "companies"),),
    ),
    DatasetSpec(
        "submission_statuses",
        "Final review status",
        "Company and periods",
        ClaimPeriodSubmissionStatus,
        ("accounting_period_id",),
        (("accounting_period_id", "accounting_periods"),),
    ),
    DatasetSpec("business_units", "Business units", "Work context", BusinessUnit, ("name",), (("parent_id", "business_units"),)),
    DatasetSpec(
        "customers",
        "Customers",
        "Work context",
        Customer,
        ("customer_name",),
        (("business_unit_id", "business_units"),),
    ),
    DatasetSpec(
        "contracts",
        "Contracts",
        "Work context",
        Contract,
        ("customer_id", "contract_name"),
        (("customer_id", "customers"),),
    ),
    DatasetSpec(
        "solutions",
        "Solutions",
        "Work context",
        Solution,
        ("customer_id", "solution_name"),
        (("customer_id", "customers"), ("contract_id", "contracts")),
    ),
    DatasetSpec(
        "projects",
        "R&D projects",
        "R&D review",
        RDProject,
        ("solution_id", "project_title"),
        (("solution_id", "solutions"), ("accounting_period_id", "accounting_periods")),
    ),
    DatasetSpec(
        "technical_uncertainties",
        "Technical uncertainties",
        "R&D review",
        TechnicalUncertainty,
        ("project_id", "summary"),
        (("project_id", "projects"),),
    ),
    DatasetSpec(
        "activities",
        "Project activities",
        "R&D review",
        Activity,
        ("project_id", "activity_name"),
        (("project_id", "projects"),),
    ),
    DatasetSpec(
        "professional_opinions",
        "Competent professional reviews",
        "Evidence, costs and review",
        CompetentProfessionalOpinion,
        ("project_id", "professional_name"),
        (("project_id", "projects"),),
    ),
    DatasetSpec(
        "evidence_items",
        "Evidence items",
        "Evidence, costs and review",
        EvidenceItem,
        ("project_id", "source_reference", "evidence_type"),
        (("project_id", "projects"),),
    ),
    DatasetSpec(
        "cost_lines",
        "Cost lines",
        "Evidence, costs and review",
        CostLine,
        (),
        (("project_id", "projects"), ("activity_id", "activities")),
        field_labels={
            "gross_cost": "Gross cost",
            "apportionment_percentage": "Apportionment percentage",
            "qualifying_amount": "Qualifying amount",
            "person_or_supplier_name": "Person or supplier name",
        },
    ),
    DatasetSpec(
        "entitlement_assessments",
        "Entitlement reviews",
        "Evidence, costs and review",
        EntitlementAssessment,
        ("project_id",),
        (("project_id", "projects"),),
    ),
    DatasetSpec(
        "review_decisions",
        "Review decisions",
        "Evidence, costs and review",
        ReviewDecision,
        (),
        (("project_id", "projects"),),
    ),
    DatasetSpec("framework_sources", "Opportunity sources", "Opportunities", FrameworkSource, ("name",)),
    DatasetSpec(
        "watch_profiles",
        "Opportunity searches",
        "Opportunities",
        CustomerWatchProfile,
        ("profile_name",),
        (("customer_id", "customers"), ("business_unit_id", "business_units")),
    ),
    DatasetSpec(
        "opportunities",
        "Opportunities",
        "Opportunities",
        FrameworkOpportunity,
        ("notice_identifier",),
        (("source_id", "framework_sources"), ("customer_id", "customers"), ("business_unit_id", "business_units")),
    ),
    DatasetSpec(
        "opportunity_documents",
        "Opportunity documents",
        "Opportunities",
        OpportunityDocument,
        ("opportunity_id", "title"),
        (("opportunity_id", "opportunities"),),
    ),
    DatasetSpec(
        "opportunity_requirements",
        "Opportunity requirements",
        "Opportunities",
        ExtractedRequirement,
        (),
        (("opportunity_id", "opportunities"),),
    ),
    DatasetSpec(
        "opportunity_signals",
        "R&D review prompts",
        "Opportunities",
        RDECOpportunitySignal,
        (),
        (("opportunity_id", "opportunities"), ("requirement_id", "opportunity_requirements")),
    ),
    DatasetSpec("procurement_platforms", "Procurement platforms", "Opportunities", ProcurementPlatform, ("name",)),
    DatasetSpec(
        "portal_instances",
        "Buyer portals",
        "Opportunities",
        BuyerPortalInstance,
        ("portal_name",),
        (("platform_id", "procurement_platforms"), ("customer_id", "customers"), ("business_unit_id", "business_units")),
    ),
    DatasetSpec(
        "quality_questions",
        "Quality questions",
        "Opportunities",
        ExtractedQualityQuestion,
        (),
        (("opportunity_id", "opportunities"), ("document_id", "opportunity_documents")),
    ),
    DatasetSpec("guidance_checks", "Guidance check history", "History", KnowledgeSourceCheck, (), importable=False),
    DatasetSpec("source_check_history", "Opportunity source history", "History", SourceCheckSnapshot, (), (("source_id", "framework_sources"),), False),
    DatasetSpec("opportunity_runs", "Opportunity search history", "History", FrameworkAgentRun, (), (("watch_profile_id", "watch_profiles"),), False),
    DatasetSpec("portal_runs", "Portal retrieval history", "History", PortalRetrievalRun, (), (("opportunity_id", "opportunities"), ("portal_instance_id", "portal_instances")), False),
    DatasetSpec("intelligence_reports", "Opportunity reports", "History", IntelligenceReport, (), (("customer_id", "customers"), ("business_unit_id", "business_units")), False),
    DatasetSpec("audit_events", "Change history", "History", AuditEvent, (), importable=False),
)

DATASET_BY_KEY = {spec.key: spec for spec in DATASETS}


DEPENDENCY_RULES = {
    Company: [(AccountingPeriod, AccountingPeriod.company_id, "accounting periods")],
    AccountingPeriod: [(RDProject, RDProject.accounting_period_id, "R&D projects")],
    BusinessUnit: [
        (BusinessUnit, BusinessUnit.parent_id, "child business units"),
        (Customer, Customer.business_unit_id, "customers"),
        (CustomerWatchProfile, CustomerWatchProfile.business_unit_id, "opportunity searches"),
        (FrameworkOpportunity, FrameworkOpportunity.business_unit_id, "opportunities"),
        (IntelligenceReport, IntelligenceReport.business_unit_id, "opportunity reports"),
        (BuyerPortalInstance, BuyerPortalInstance.business_unit_id, "buyer portals"),
    ],
    Customer: [
        (Contract, Contract.customer_id, "contracts"),
        (Solution, Solution.customer_id, "solutions"),
        (CustomerWatchProfile, CustomerWatchProfile.customer_id, "opportunity searches"),
        (FrameworkOpportunity, FrameworkOpportunity.customer_id, "opportunities"),
        (IntelligenceReport, IntelligenceReport.customer_id, "opportunity reports"),
        (BuyerPortalInstance, BuyerPortalInstance.customer_id, "buyer portals"),
    ],
    Contract: [(Solution, Solution.contract_id, "solutions")],
    Solution: [(RDProject, RDProject.solution_id, "R&D projects")],
    RDProject: [
        (TechnicalUncertainty, TechnicalUncertainty.project_id, "technical uncertainties"),
        (Activity, Activity.project_id, "activities"),
        (CostLine, CostLine.project_id, "cost lines"),
        (EvidenceItem, EvidenceItem.project_id, "evidence items"),
        (CompetentProfessionalOpinion, CompetentProfessionalOpinion.project_id, "competent professional reviews"),
        (ReviewDecision, ReviewDecision.project_id, "review decisions"),
    ],
    FrameworkSource: [
        (FrameworkOpportunity, FrameworkOpportunity.source_id, "opportunities"),
        (SourceCheckSnapshot, SourceCheckSnapshot.source_id, "source check history"),
    ],
    CustomerWatchProfile: [(FrameworkAgentRun, FrameworkAgentRun.watch_profile_id, "opportunity search history")],
    FrameworkOpportunity: [
        (OpportunityDocument, OpportunityDocument.opportunity_id, "opportunity documents"),
        (ExtractedRequirement, ExtractedRequirement.opportunity_id, "opportunity requirements"),
        (RDECOpportunitySignal, RDECOpportunitySignal.opportunity_id, "R&D review prompts"),
        (ExtractedQualityQuestion, ExtractedQualityQuestion.opportunity_id, "quality questions"),
        (PortalRetrievalRun, PortalRetrievalRun.opportunity_id, "portal retrieval history"),
    ],
    ExtractedRequirement: [(RDECOpportunitySignal, RDECOpportunitySignal.requirement_id, "R&D review prompts")],
    OpportunityDocument: [(ExtractedQualityQuestion, ExtractedQualityQuestion.document_id, "quality questions")],
    ProcurementPlatform: [(BuyerPortalInstance, BuyerPortalInstance.platform_id, "buyer portals")],
    BuyerPortalInstance: [(PortalRetrievalRun, PortalRetrievalRun.portal_instance_id, "portal retrieval history")],
}


def _records(session: Session, spec: DatasetSpec) -> list[SQLModel]:
    return list(session.exec(select(spec.model)))


def data_inventory(session: Session) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for spec in DATASETS:
        groups.setdefault(spec.group, []).append(
            {
                "key": spec.key,
                "label": spec.label,
                "count": len(_records(session, spec)),
                "importable": spec.importable,
            }
        )
    return groups


def _selected_specs(dataset_keys: list[str]) -> list[DatasetSpec]:
    selected = set(dataset_keys)
    unknown = selected.difference(DATASET_BY_KEY)
    if unknown:
        raise DataOperationError(f"Unknown data area: {', '.join(sorted(unknown))}.")
    specs = [spec for spec in DATASETS if spec.key in selected]
    if not specs:
        raise DataOperationError("Choose at least one data area.")
    return specs


def export_bundle(session: Session, dataset_keys: list[str]) -> dict[str, Any]:
    specs = _selected_specs(dataset_keys)
    return {
        "format_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "caveat": CAVEAT,
        "datasets": {
            spec.key: [item.model_dump(mode="json") for item in _records(session, spec)] for spec in specs
        },
    }


def json_export_bytes(session: Session, dataset_keys: list[str]) -> bytes:
    return json.dumps(export_bundle(session, dataset_keys), indent=2, ensure_ascii=True).encode("utf-8")


def _safe_csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def csv_export_zip_bytes(session: Session, dataset_keys: list[str]) -> bytes:
    specs = _selected_specs(dataset_keys)
    output = BytesIO()
    manifest = {
        "format_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": "Review-friendly CSV export. Use the JSON export for restore or re-import.",
        "caveat": CAVEAT,
        "datasets": [],
    }
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for spec in specs:
            rows = [item.model_dump(mode="json") for item in _records(session, spec)]
            fieldnames = list(spec.model.model_fields)
            stream = StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({name: _safe_csv_value(row.get(name)) for name in fieldnames})
            archive.writestr(f"{spec.key}.csv", stream.getvalue().encode("utf-8-sig"))
            manifest["datasets"].append({"key": spec.key, "label": spec.label, "records": len(rows)})
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=True))
    return output.getvalue()


def parse_import_file(filename: str, content: bytes, selected_dataset: str = "") -> dict[str, list[dict[str, Any]]]:
    if not content:
        raise DataOperationError("The selected file is empty.")
    if len(content) > MAX_IMPORT_BYTES:
        raise DataOperationError("The file is larger than the 5 MB import limit.")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataOperationError("Use a UTF-8 JSON or CSV file.") from exc

    if suffix == "csv":
        if selected_dataset not in DATASET_BY_KEY:
            raise DataOperationError("Choose the data area contained in the CSV file.")
        spec = DATASET_BY_KEY[selected_dataset]
        if not spec.importable:
            raise DataOperationError(f"{spec.label} can be exported but not imported.")
        reader = csv.DictReader(StringIO(text))
        if not reader.fieldnames:
            raise DataOperationError("The CSV file does not contain a header row.")
        datasets: dict[str, list[dict[str, Any]]] = {selected_dataset: [dict(row) for row in reader]}
    elif suffix == "json":
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DataOperationError(f"The JSON file could not be read: {exc.msg}.") from exc
        if isinstance(decoded, list):
            if selected_dataset not in DATASET_BY_KEY:
                raise DataOperationError("Choose the data area contained in this JSON list.")
            datasets = {selected_dataset: decoded}
        elif isinstance(decoded, dict) and isinstance(decoded.get("datasets"), dict):
            datasets = decoded["datasets"]
        elif isinstance(decoded, dict) and selected_dataset in DATASET_BY_KEY:
            datasets = {selected_dataset: [decoded]}
        else:
            raise DataOperationError("Use a Hub JSON export, a JSON list, or one JSON record with a selected data area.")
    else:
        raise DataOperationError("Choose a .json or .csv file.")

    total_rows = 0
    for key, rows in datasets.items():
        spec = DATASET_BY_KEY.get(key)
        if not spec:
            raise DataOperationError(f"Unknown data area in the file: {key}.")
        if not spec.importable:
            raise DataOperationError(f"{spec.label} can be exported but not imported.")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DataOperationError(f"{spec.label} must contain a list of records.")
        total_rows += len(rows)
    if total_rows > MAX_IMPORT_ROWS:
        raise DataOperationError(f"The file contains more than the {MAX_IMPORT_ROWS:,} record import limit.")
    return {spec.key: datasets[spec.key] for spec in DATASETS if spec.key in datasets}


_LABEL_WORDS = {
    "rd": "R&D",
    "utr": "UTR",
    "uk": "UK",
    "paye": "PAYE",
    "nic": "NIC",
    "vat": "VAT",
    "sic": "SIC",
    "ip": "IP",
    "url": "web address",
    "id": "identifier",
}


def _field_label(spec: DatasetSpec, name: str) -> str:
    """A human field label for an uploaded column (ADR-0004 D7).

    Database column names must never reach a user-facing message, so every label is
    either an explicit override on the dataset or a humanised rendering of the column.
    """
    override = spec.field_labels.get(name)
    if override:
        return override
    words = [part for part in str(name).split("_") if part]
    if not words:
        return "Value"
    if len(words) > 1 and words[-1] == "id":
        words = words[:-1]
    rendered = [_LABEL_WORDS.get(word, word) for word in words]
    first = rendered[0]
    if first == first.lower():
        rendered[0] = first[:1].upper() + first[1:]
    return " ".join(rendered)


# ADR-0004 D7. Keyed on the Pydantic error ``type``, never on its English text, so no raw
# Pydantic wording and no column name can reach a user. Unmapped types fall back to the
# safe generic message below.
_PYDANTIC_ISSUE_MESSAGES: dict[str, tuple[str, str]] = {
    "missing": ("{label} is required.", "required_value"),
    "none_not_allowed": ("{label} is required.", "required_value"),
    "string_type": ("{label} must be text.", "invalid_value"),
    "string_too_long": ("{label} is longer than this area allows.", "invalid_value"),
    "int_parsing": ("{label} must be a whole number.", "invalid_value"),
    "int_type": ("{label} must be a whole number.", "invalid_value"),
    "int_from_float": ("{label} must be a whole number.", "invalid_value"),
    "float_parsing": ("{label} must be a number.", "invalid_value"),
    "float_type": ("{label} must be a number.", "invalid_value"),
    "decimal_parsing": ("{label} must be a number.", "invalid_value"),
    "bool_parsing": ("{label} must be true or false.", "invalid_value"),
    "bool_type": ("{label} must be true or false.", "invalid_value"),
    "date_parsing": ("{label} must be a date in YYYY-MM-DD format.", "invalid_value"),
    "date_type": ("{label} must be a date in YYYY-MM-DD format.", "invalid_value"),
    "date_from_datetime_parsing": ("{label} must be a date in YYYY-MM-DD format.", "invalid_value"),
    "date_from_datetime_inexact": ("{label} must be a date in YYYY-MM-DD format.", "invalid_value"),
    "datetime_parsing": ("{label} must be a date and time.", "invalid_value"),
    "datetime_type": ("{label} must be a date and time.", "invalid_value"),
    "datetime_from_date_parsing": ("{label} must be a date and time.", "invalid_value"),
    "greater_than": ("{label} is outside the range this area accepts.", "invalid_value"),
    "greater_than_equal": ("{label} is outside the range this area accepts.", "invalid_value"),
    "less_than": ("{label} is outside the range this area accepts.", "invalid_value"),
    "less_than_equal": ("{label} is outside the range this area accepts.", "invalid_value"),
    "enum": ("{label} is not one of the accepted values.", "invalid_value"),
    "literal_error": ("{label} is not one of the accepted values.", "invalid_value"),
}
_PYDANTIC_FALLBACK = ("{label} could not be understood.", "invalid_value")


def _validation_issues(spec: DatasetSpec, error: ValidationError) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for item in error.errors():
        location = [str(part) for part in item.get("loc", ())]
        column = location[0] if location else ""
        label = _field_label(spec, column) if column else spec.label
        template, code = _PYDANTIC_ISSUE_MESSAGES.get(str(item.get("type", "")), _PYDANTIC_FALLBACK)
        issues.append(ImportIssue(field=label, message=template.format(label=label), code=code))
    return issues


def _issue_messages(issues: list[ImportIssue]) -> list[str]:
    return [issue.message for issue in issues]


def _annotation_contains(annotation: Any, value_type: type) -> bool:
    return annotation is value_type or value_type in get_args(annotation)


def _clean_row(spec: DatasetSpec, row: dict[str, Any]) -> tuple[dict[str, Any], list[ImportIssue]]:
    fields = spec.model.model_fields
    unknown = sorted(set(row).difference(fields))
    issues = [
        ImportIssue(
            field="",
            message=f"This file has a column called '{name}' that {spec.label.lower()} do not use.",
            code="unknown_column",
        )
        for name in unknown
    ]
    cleaned: dict[str, Any] = {}
    for name, value in row.items():
        if name not in fields:
            continue
        annotation = fields[name].annotation
        if isinstance(value, str) and value == "":
            if name == "id":
                continue
            cleaned[name] = "" if _annotation_contains(annotation, str) else None
            continue
        if isinstance(value, str) and _annotation_contains(annotation, bool):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                cleaned[name] = True
                continue
            if lowered in {"false", "0", "no", "off"}:
                cleaned[name] = False
                continue
        cleaned[name] = value
    return cleaned, issues


def _find_existing(session: Session, spec: DatasetSpec, values: dict[str, Any]) -> SQLModel | None:
    item_id = values.get("id")
    if item_id not in (None, ""):
        try:
            existing = session.get(spec.model, int(item_id))
        except (TypeError, ValueError):
            existing = None
        if existing:
            return existing
    if not spec.natural_key or any(values.get(name) in (None, "") for name in spec.natural_key):
        return None
    query = select(spec.model)
    for name in spec.natural_key:
        query = query.where(getattr(spec.model, name) == values[name])
    return session.exec(query).first()


def _display_name(spec: DatasetSpec, values: dict[str, Any]) -> str:
    for name in spec.natural_key:
        value = values.get(name)
        if value not in (None, ""):
            return str(value)
    item_id = values.get("id")
    return f"Record {item_id}" if item_id not in (None, "") else "New record"


def _known_ids(session: Session, datasets: dict[str, list[dict[str, Any]]]) -> dict[str, set[int]]:
    ids: dict[str, set[int]] = {}
    for spec in DATASETS:
        ids[spec.key] = {
            int(item.id) for item in _records(session, spec) if getattr(item, "id", None) is not None
        }
        for row in datasets.get(spec.key, []):
            try:
                if row.get("id") not in (None, ""):
                    ids[spec.key].add(int(row["id"]))
            except (TypeError, ValueError):
                continue
    return ids


def _rejected_dataset_issue(key: str) -> ImportIssue | None:
    """ADR-0004 D5: the one place that decides whether a dataset key may be written."""
    spec = DATASET_BY_KEY.get(key)
    if spec is None:
        return ImportIssue(
            field="",
            message=f"This file has a data area called '{key}' that the Hub does not use.",
            code="unknown_dataset",
        )
    if not spec.importable:
        return ImportIssue(
            field="",
            message=f"{spec.label} can be exported but not imported.",
            code="not_importable",
        )
    return None


def build_import_plan(session: Session, datasets: dict[str, list[dict[str, Any]]], mode: str) -> dict[str, Any]:
    if mode not in {"add_only", "add_update"}:
        raise DataOperationError("Choose whether to add only or add and update matching records.")
    known_ids = _known_ids(session, datasets)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for spec in DATASETS:
        if not spec.importable:
            # ADR-0004 D5 layer 3. Rows of a dataset that may not be written are never
            # planned, so they can never surface as a "create" that hides the rejection.
            # The dataset-level error row below is what the operator sees.
            continue
        for row_number, raw_row in enumerate(datasets.get(spec.key, []), start=1):
            cleaned, issues = _clean_row(spec, raw_row)
            existing = _find_existing(session, spec, cleaned)
            merged = existing.model_dump(mode="json") if existing else {}
            merged.update(cleaned)
            values: dict[str, Any] = cleaned
            try:
                candidate = spec.model.model_validate(merged)
                values = candidate.model_dump(mode="json")
            except ValidationError as exc:
                issues.extend(_validation_issues(spec, exc))

            identity: tuple[Any, ...] | None = None
            if values.get("id") not in (None, ""):
                identity = (spec.key, "id", values["id"])
            elif spec.natural_key and all(values.get(name) not in (None, "") for name in spec.natural_key):
                identity = (spec.key, "key", *(values.get(name) for name in spec.natural_key))
            if identity and identity in seen:
                issues.append(
                    ImportIssue(
                        field="",
                        message="This record appears more than once in the import file.",
                        code="duplicate_record",
                    )
                )
            elif identity:
                seen.add(identity)

            if not issues:
                for field_name, target_key in spec.foreign_keys:
                    target_id = values.get(field_name)
                    if target_id in (None, ""):
                        continue
                    try:
                        valid_link = int(target_id) in known_ids[target_key]
                    except (TypeError, ValueError):
                        valid_link = False
                    if not valid_link:
                        label = _field_label(spec, field_name)
                        issues.append(
                            ImportIssue(
                                field=label,
                                message=f"{label} does not match a record in this file or in the Hub.",
                                code="unknown_link",
                            )
                        )

            if issues:
                status = "error"
            elif existing and mode == "add_only":
                status = "skip"
            elif existing:
                status = "update"
            else:
                status = "create"
            rows.append(
                {
                    "dataset_key": spec.key,
                    "dataset_label": spec.label,
                    "row_number": row_number,
                    "display": _display_name(spec, values),
                    "status": status,
                    "issues": issues,
                    # Transitional mirror of ``issues`` so the preview template keeps rendering
                    # readable text until app/main.py and data_management.html are wired to the
                    # ratified shape. Derived, never authored: one source of truth.
                    "errors": _issue_messages(issues),
                    "values": values,
                }
            )
    # ADR-0004 D5 layer 3, and the defect it records: the loop above walks DATASETS, so a
    # dataset key the Hub does not import -- or one the operator mistyped -- was previously
    # dropped in silence and the import reported success having written nothing. Every key
    # the payload actually carries is now accounted for, as a visible preview error rather
    # than an exception, so the operator sees the rejection.
    for key, raw_rows in datasets.items():
        if not raw_rows:
            continue
        issue = _rejected_dataset_issue(key)
        if issue is None:
            continue
        spec = DATASET_BY_KEY.get(key)
        rows.append(
            {
                "dataset_key": key,
                "dataset_label": spec.label if spec else str(key),
                "row_number": 0,
                "display": spec.label if spec else str(key),
                "status": "error",
                "issues": [issue],
                "errors": [issue.message],
                "values": {},
            }
        )
    summary = {status: sum(1 for row in rows if row["status"] == status) for status in ("create", "update", "skip", "error")}
    return {"mode": mode, "rows": rows, "summary": summary, "has_errors": bool(summary["error"])}


def encode_import_payload(plan: dict[str, Any]) -> str:
    if plan["has_errors"]:
        return ""
    datasets: dict[str, list[dict[str, Any]]] = {}
    for row in plan["rows"]:
        if row["status"] in {"create", "update", "skip"}:
            datasets.setdefault(row["dataset_key"], []).append(row["values"])
    payload = json.dumps({"mode": plan["mode"], "datasets": datasets}, separators=(",", ":"), ensure_ascii=True)
    return urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_import_payload(encoded: str) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    if not encoded or len(encoded) > MAX_IMPORT_BYTES * 2:
        raise DataOperationError("The import preview has expired or is too large. Preview the file again.")
    try:
        decoded = json.loads(urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataOperationError("The import preview could not be read. Preview the file again.") from exc
    mode = decoded.get("mode")
    datasets = decoded.get("datasets")
    if mode not in {"add_only", "add_update"} or not isinstance(datasets, dict):
        raise DataOperationError("The import preview is not valid. Preview the file again.")
    # ADR-0004 D5 layer 2. A payload posted straight to the apply route never passes through
    # parse_import_file, so this boundary re-decides the dataset question itself rather than
    # trusting that an earlier layer already did.
    for key, rows in datasets.items():
        issue = _rejected_dataset_issue(str(key))
        if issue is not None:
            raise DataOperationError(issue.message)
        if not isinstance(rows, list):
            raise DataOperationError("The import preview is not valid. Preview the file again.")
    return mode, datasets


def apply_import(session: Session, datasets: dict[str, list[dict[str, Any]]], mode: str) -> dict[str, int]:
    plan = build_import_plan(session, datasets, mode)
    # ADR-0004 D5 layer 4. Reaching this line with a rejected dataset key means layers 1 to 3
    # were bypassed, so it fails loudly and specifically rather than behind the generic
    # revalidation message below.
    for key, rows in datasets.items():
        if not rows:
            continue
        issue = _rejected_dataset_issue(str(key))
        if issue is not None:
            raise DataOperationError(issue.message)
    if plan["has_errors"]:
        raise DataOperationError("The data changed after preview or no longer passes validation. Preview the file again.")
    applied = {"created": 0, "updated": 0, "skipped": plan["summary"]["skip"]}
    try:
        for row in plan["rows"]:
            if row["status"] == "skip":
                continue
            spec = DATASET_BY_KEY[row["dataset_key"]]
            candidate = spec.model.model_validate(row["values"])
            existing = _find_existing(session, spec, row["values"])
            if existing:
                before = compact_snapshot(existing)
                for field_name in spec.model.model_fields:
                    if field_name != "id":
                        setattr(existing, field_name, getattr(candidate, field_name))
                session.add(existing)
                session.flush()
                log_event(
                    session,
                    entity_type=spec.model.__name__,
                    entity_id=existing.id,
                    action="import_update",
                    summary=f"Updated {spec.label.lower()} through previewed import",
                    before=before,
                    after=existing,
                )
                applied["updated"] += 1
            else:
                session.add(candidate)
                session.flush()
                log_event(
                    session,
                    entity_type=spec.model.__name__,
                    entity_id=candidate.id,
                    action="import_create",
                    summary=f"Added {spec.label.lower()} through previewed import",
                    after=candidate,
                )
                applied["created"] += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return applied


def _has_dependencies(session: Session, model: type[SQLModel], item_id: int) -> bool:
    return any(
        session.exec(select(child_model).where(child_field == item_id)).first() is not None
        for child_model, child_field, _ in DEPENDENCY_RULES.get(model, [])
    )


CLEANUP_DATASET_KEYS = ("companies", "customers", "contracts", "solutions", "watch_profiles", "opportunities", "portal_instances")
SEEDED_REFERENCE_CUSTOMERS = {"Transport for London (TfL)", "National Rail"}


def cleanup_candidates(session: Session) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in CLEANUP_DATASET_KEYS:
        spec = DATASET_BY_KEY[key]
        for item in _records(session, spec):
            item_id = int(item.id or 0)
            if not item_id or _has_dependencies(session, spec.model, item_id):
                continue
            if isinstance(item, Customer) and item.business_unit_id and item.customer_name in SEEDED_REFERENCE_CUSTOMERS:
                continue
            if isinstance(item, CustomerWatchProfile) and item.active:
                continue
            if isinstance(item, FrameworkOpportunity) and item.status not in {"archived", "rejected"}:
                continue
            if isinstance(item, BuyerPortalInstance) and item.access_status != "retired":
                continue
            values = item.model_dump(mode="json")
            candidates.append(
                {
                    "token": f"{key}:{item_id}",
                    "dataset_label": spec.label,
                    "display": _display_name(spec, values),
                }
            )
    return candidates


def delete_unused_records(session: Session, selections: list[str]) -> int:
    available = {item["token"]: item for item in cleanup_candidates(session)}
    requested = list(dict.fromkeys(selections))
    if not requested:
        raise DataOperationError("Choose at least one unused record to remove.")
    unavailable = [token for token in requested if token not in available]
    if unavailable:
        raise DataOperationError("One or more records are now in use or are no longer available. Refresh and try again.")
    try:
        for token in requested:
            key, item_id_text = token.split(":", 1)
            spec = DATASET_BY_KEY[key]
            item = session.get(spec.model, int(item_id_text))
            before = compact_snapshot(item)
            log_event(
                session,
                entity_type=spec.model.__name__,
                entity_id=item.id,
                action="cleanup_delete",
                summary=f"Removed unused {spec.label.lower()} record",
                before=before,
                after="",
            )
            session.delete(item)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return len(requested)


@dataclass(frozen=True)
class PurgeScope:
    key: str
    label: str
    description: str
    confirmation: str
    models: tuple[type[SQLModel], ...]


CLAIM_WORK_MODELS = (
    ReviewDecision,
    EntitlementAssessment,
    CompetentProfessionalOpinion,
    EvidenceItem,
    CostLine,
    TechnicalUncertainty,
    Activity,
    RDProject,
    ClaimPeriodSubmissionStatus,
    AccountingPeriod,
    Solution,
    Contract,
    Company,
)
OPPORTUNITY_WORK_MODELS = (
    RDECOpportunitySignal,
    ExtractedQualityQuestion,
    ExtractedRequirement,
    OpportunityDocument,
    PortalRetrievalRun,
    FrameworkOpportunity,
    FrameworkAgentRun,
    IntelligenceReport,
    BuyerPortalInstance,
    CustomerWatchProfile,
)
ACTIVITY_HISTORY_MODELS = (SourceCheckSnapshot, FrameworkAgentRun, PortalRetrievalRun, KnowledgeSourceCheck, IntelligenceReport)
ALL_WORKING_DATA_MODELS = tuple(dict.fromkeys(OPPORTUNITY_WORK_MODELS + CLAIM_WORK_MODELS + (Customer,) + ACTIVITY_HISTORY_MODELS))

PURGE_SCOPES = {
    scope.key: scope
    for scope in (
        PurgeScope(
            "claim_work",
            "Claim work and claimant setup",
            "Removes projects, evidence, costs, reviews, periods, companies, contracts and solutions. Customers remain.",
            "PURGE CLAIM WORK",
            CLAIM_WORK_MODELS,
        ),
        PurgeScope(
            "opportunity_work",
            "Opportunity working data",
            "Removes saved searches, opportunities, documents, prompts, reports and portal working records. Source catalogues remain.",
            "PURGE OPPORTUNITY WORK",
            OPPORTUNITY_WORK_MODELS,
        ),
        PurgeScope(
            "activity_history",
            "Automated check history",
            "Removes previous guidance checks, source checks, search runs, retrieval runs and generated opportunity reports.",
            "PURGE ACTIVITY HISTORY",
            ACTIVITY_HISTORY_MODELS,
        ),
        PurgeScope(
            "all_working_data",
            "All working data",
            "Removes claim, customer, opportunity and automated check working data. Reference catalogues and change history remain.",
            "PURGE ALL WORKING DATA",
            ALL_WORKING_DATA_MODELS,
        ),
    )
}


def _purge_items(session: Session, model: type[SQLModel]) -> list[SQLModel]:
    items = list(session.exec(select(model)))
    if model is Customer:
        return [
            item
            for item in items
            if not (item.business_unit_id and item.customer_name in SEEDED_REFERENCE_CUSTOMERS)
        ]
    return items


def purge_scope_cards(session: Session) -> list[dict[str, Any]]:
    cards = []
    for scope in PURGE_SCOPES.values():
        counts = {model.__name__: len(_purge_items(session, model)) for model in scope.models}
        cards.append(
            {
                "key": scope.key,
                "label": scope.label,
                "description": scope.description,
                "confirmation": scope.confirmation,
                "count": sum(counts.values()),
            }
        )
    return cards


def purge_records(session: Session, scope_key: str, confirmation: str) -> dict[str, int]:
    scope = PURGE_SCOPES.get(scope_key)
    if not scope:
        raise DataOperationError("Choose what to purge.")
    if confirmation.strip() != scope.confirmation:
        raise DataOperationError(f"Type {scope.confirmation} exactly to continue.")
    counts: dict[str, int] = {}
    try:
        for model in scope.models:
            items = _purge_items(session, model)
            counts[model.__name__] = len(items)
            for item in items:
                session.delete(item)
            session.flush()
        log_event(
            session,
            entity_type="DataPurge",
            entity_id=None,
            action="purge",
            summary=f"Purged {scope.label.lower()}",
            before=json.dumps(counts, sort_keys=True),
            after="",
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return counts
