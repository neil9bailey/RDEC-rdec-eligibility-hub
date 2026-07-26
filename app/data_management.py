from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
import csv
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO, StringIO
import json
import re
import secrets
import time
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
from app.services import CAVEAT, calculate_qualifying_amount, sync_entitlement_for_project


MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000

# ADR-0002 line 30 as amended by ADR-0004 D1. "restore_by_identifier" is the only mode that
# treats an uploaded identifier as identity. It is disabled by default and every entry point
# that accepts it requires an explicit operator enablement, exactly like purge.
DEFAULT_IMPORT_MODES = ("add_only", "add_update")
RESTORE_BY_IDENTIFIER_MODE = "restore_by_identifier"
IMPORT_MODES = DEFAULT_IMPORT_MODES + (RESTORE_BY_IDENTIFIER_MODE,)
CREATE_ONLY_NOTICE = (
    "Records in this area are always added as new. Importing the same file twice will create duplicates."
)
CHANGED_VALUE_LIMIT = 120
# ADR-0004 D1.4 as amended 2026-07-26: a parent that this file will create is labelled as
# new, because it cannot be looked up in the Hub by the operator checking the preview.
NEW_PARENT_SUFFIX = " (new in this file)"


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
            # ADR-0004 D1.6: the link and the free-text column both humanised to "Activity",
            # so the preview could not tell an operator which of the two had moved, and the
            # D1.6 matcher -- which keys on the label -- could have accepted a disclosed text
            # change as cover for an undisclosed relink. Labels must be unique per dataset;
            # test_every_foreign_key_has_a_label_of_its_own holds the line.
            "activity_id": "Linked activity",
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


# ADR-0002 line 27 as amended by ADR-0004 D3. A whole-cell, fully anchored match: a value
# satisfying it contains no letters and none of ! ( ) , : ; \ | + = @ " so it cannot carry
# formula syntax, a DDE payload or a cell reference.
#
# BINDING GUARDRAIL (ADR-0004 D3 residual risk, reviewed at G5): this pattern is fully
# anchored and must never be changed to a search; it must never permit leading or trailing
# whitespace, thousands separators, currency symbols, or Unicode minus signs.
#
# G5 finding L1: the end anchor was `$`, which in Python also matches immediately BEFORE a
# trailing newline, so `-500\n` and `-1.5e3\n` satisfied the exemption and exported
# un-neutralised. Not exploitable -- nothing can follow the LF and still match, and
# `-500\n=1+1` was always neutralised -- but it contradicted this guardrail and re-admitted
# precisely the LF that D3 ADDED to CSV_FORMULA_LEADS below, so it is repaired rather than
# accepted. `\Z` matches only at the true end of the string.
#
# Repaired on both sides on purpose, and each side is pinned by its own test: `\Z` makes the
# pattern object safe under any call style, and `.fullmatch` at the call site makes the call
# safe whatever the pattern says. Neither narrows the exemption: the set of admitted values is
# unchanged except for the trailing-line-feed forms, which were never intended to be in it.
PLAIN_SIGNED_NUMBER = re.compile(r"^-(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?\Z")

# Always neutralised, whatever follows. LF is ADDED by the amendment: it was missing before,
# so narrowing the "-" rule is not a net reduction in coverage.
CSV_FORMULA_LEADS = ("=", "+", "@", "\t", "\r", "\n")

# Minus-like characters that are not the ASCII hyphen-minus. The exemption above is decided
# by an ASCII-only pattern, so these can never satisfy it; neutralising them explicitly keeps
# the named "-5 with a Unicode minus" case covered rather than relying on that coincidence.
CSV_UNICODE_MINUS_LEADS = ("−", "–", "—", "－")


def _safe_csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text.startswith(CSV_FORMULA_LEADS) or text.startswith(CSV_UNICODE_MINUS_LEADS):
        return "'" + text
    if text.startswith("-") and not PLAIN_SIGNED_NUMBER.fullmatch(text):
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


def _annotation_contains(annotation: Any, value_type: type) -> bool:
    return annotation is value_type or value_type in get_args(annotation)


def _clean_row(
    spec: DatasetSpec, row: dict[str, Any], keep_identifier: bool = False
) -> tuple[dict[str, Any], list[ImportIssue]]:
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
        if name == "id" and not keep_identifier:
            # ADR-0004 D1.2. An identifier in an uploaded file asserts identity in the
            # EXPORTING database's namespace and is read in the live one, so it is dropped
            # here at source: it can then never reach model_validate, never reach
            # session.add, and never be inserted as an explicit primary key.
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


def _derived_values(spec: DatasetSpec, values: dict[str, Any]) -> dict[str, Any]:
    """Recompute the figures the Hub owns, so an uploaded file can never assert them.

    B4: qualifying_amount was written verbatim, so a file claiming 999999 against a gross
    cost of 1000 at 50% stored 999999 and that figure flowed into report totals and CSV
    exports. It is derived data, and the same function the rest of the Hub uses derives it.
    """
    if spec.model is CostLine:
        values["qualifying_amount"] = calculate_qualifying_amount(
            values.get("gross_cost") or 0,
            values.get("apportionment_percentage") or 0,
        )
    return values


def _find_existing(
    session: Session,
    spec: DatasetSpec,
    values: dict[str, Any],
    *,
    identifier: int | None = None,
    allow_identifier_match: bool = False,
) -> SQLModel | None:
    """Select the live record an uploaded row refers to (ADR-0002 line 30 as amended).

    Natural key first. The identifier branch below is the only place ``session.get`` may be
    called on an uploaded identifier, and it is reachable only from the operator-enabled
    restore-by-identifier mode.
    """
    if allow_identifier_match and identifier is not None:
        existing = session.get(spec.model, identifier)
        if existing:
            return existing
    if not spec.natural_key or any(values.get(name) in (None, "") for name in spec.natural_key):
        return None
    query = select(spec.model)
    for name in spec.natural_key:
        query = query.where(getattr(spec.model, name) == values[name])
    return session.exec(query).first()


def _display_fields(spec: DatasetSpec) -> tuple[str, ...]:
    """Natural-key parts worth showing a human, preferring names over link identifiers."""
    named = tuple(name for name in spec.natural_key if not name.endswith("_id"))
    return named or spec.natural_key


def _incoming_display(spec: DatasetSpec, values: dict[str, Any]) -> str:
    """The natural-key display of an UPLOADED row (ADR-0004 D1.4).

    Deliberately separate from _existing_display. One function taking either an uploaded
    row or a live record is what let the preview show the uploaded name while a different
    live record was overwritten.
    """
    for name in _display_fields(spec):
        value = values.get(name)
        if value not in (None, ""):
            return str(value)
    return "New record"


def _existing_display(spec: DatasetSpec, record: SQLModel) -> str:
    """The natural-key display of the LIVE record being changed (ADR-0004 D1.4)."""
    for name in _display_fields(spec):
        value = getattr(record, name, None)
        if value not in (None, ""):
            return str(value)
    item_id = getattr(record, "id", None)
    return f"Record {item_id}" if item_id is not None else "Existing record"


def _live_ids(session: Session) -> dict[str, set[int]]:
    """Identifiers already committed in this database.

    ADR-0004 D2.1 invariant: this may contain only identifiers already committed at the
    time the plan is built. Identifiers merely declared by the uploaded file used to be
    added here, which let a child row's foreign key pass validation against an identifier
    that was then discarded when its parent matched by natural key -- the child dangled.
    """
    return {
        spec.key: {int(item.id) for item in _records(session, spec) if getattr(item, "id", None) is not None}
        for spec in DATASETS
    }


def _declared_identifier(raw_row: Any) -> int | None:
    if not isinstance(raw_row, dict):
        return None
    value = raw_row.get("id")
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _declared_identifier_index(datasets: dict[str, list[dict[str, Any]]]) -> dict[tuple[str, int], list[str]]:
    """Identifiers declared by rows in the uploaded file, with each row's display name.

    ADR-0004 D2.1 L1. These are never valid foreign keys in their own right: they belong
    to the exporting database's namespace. They resolve only through ``id_remap``, to
    whatever the parent row itself resolves to in this database.
    """
    index: dict[tuple[str, int], list[str]] = {}
    for spec in DATASETS:
        if not spec.importable:
            continue
        for raw_row in datasets.get(spec.key, []) or []:
            declared = _declared_identifier(raw_row)
            if declared is None:
                continue
            index.setdefault((spec.key, declared), []).append(_incoming_display(spec, raw_row))
    return index


def _readable_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return text if len(text) <= CHANGED_VALUE_LIMIT else text[: CHANGED_VALUE_LIMIT - 1] + "…"


def _live_link_display(session: Session, target_key: str, target_id: int) -> str:
    spec = DATASET_BY_KEY[target_key]
    record = session.get(spec.model, target_id)
    return _existing_display(spec, record) if record is not None else f"Record {target_id}"


def _stored_link_display(session: Session, target_key: str, value: Any) -> str:
    """Render a foreign key already committed in this database as its parent's name.

    Used for the BEFORE side of a link change. The value is a live identifier by
    construction -- it came off the stored record -- so resolving it against the live
    database is correct here, and only here.
    """
    if value in (None, ""):
        return ""
    try:
        target_id = int(value)
    except (TypeError, ValueError):
        return _readable_value(value)
    return _readable_value(_live_link_display(session, target_key, target_id))


def _live_link_target(value: Any) -> tuple[Any, ...]:
    """The parent a live foreign key points at (ADR-0004 D1.4 as amended 2026-07-26).

    Used for the stored side of a comparison, which always resolves to
    ``("live", <stored id>)``, and for an incoming value that apply will write unchanged.
    """
    if value in (None, ""):
        return ("none",)
    try:
        return ("live", int(value))
    except (TypeError, ValueError):
        return ("raw", str(value))


def _incoming_link_target(link: dict[str, str] | None, target_key: str, value: Any) -> tuple[Any, ...]:
    """The parent an uploaded foreign key will actually point at once written.

    ADR-0004 D1.4 as amended: the comparison is on the RESOLVED target, never on the
    declared integer, because the declared integer belongs to the exporting database's
    namespace. Two equal integers that resolve to different parents are a change; two
    different integers that resolve to the same parent are not.

    A parent this file has yet to create resolves to a ``new_in_file`` marker, which can
    never equal a live link and is therefore always a change. The marker carries the
    declared identifier rather than the parent's row number: inside ``_plan_links``'s
    ``len(declared_parents) == 1`` branch exactly one in-file row declares that identifier,
    so the two name the same row, and this avoids widening the link shape the preview
    payload and template already consume.

    With no plan entry the value is either inherited from the record being updated or a
    link the preview could not resolve -- an error row, which never applies. Either way
    ``_resolve_links`` writes it unchanged, so it is compared as a live link.
    """
    if link is None:
        return _live_link_target(value)
    resolved = link.get("resolved_id") or ""
    if resolved:
        return _live_link_target(resolved)
    return ("new_in_file", target_key, str(value))


def _incoming_link_display(link: dict[str, str] | None, value: Any) -> str:
    """The AFTER side of a link change, as the parent's name (ADR-0004 D1.4)."""
    parent = str(link.get("parent") or "") if link is not None else ""
    if not parent:
        return _readable_value(value)
    if link is not None and not link.get("resolved_id"):
        return _new_parent_display(parent)
    return _readable_value(parent)


def _new_parent_display(name: str) -> str:
    """Label a parent that this file will create, e.g. ``Acme Rail Ltd (new in this file)``.

    The suffix is fitted INSIDE the 120-character budget rather than appended after it, so
    an uploaded parent name long enough to reach the truncation limit cannot push the marker
    off the end of the disclosure and make a new parent read like an existing one.
    """
    limit = CHANGED_VALUE_LIMIT - len(NEW_PARENT_SUFFIX)
    text = str(name)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text + NEW_PARENT_SUFFIX


def _changed_fields(
    session: Session,
    spec: DatasetSpec,
    existing: SQLModel,
    values: dict[str, Any],
    links: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Every field that actually moves on the live record (ADR-0004 D1.4).

    Link fields are disclosed by the parent's NAME. A raw ``Customer: 4 -> 5`` tells a
    Finance reviewer nothing about which customer, which defeats the whole point of the
    D1.3/D1.4 disclosure that exists to make finding C2 visible. Everything else renders
    literally, because for a stored value such as ``ambiguous_tax_review`` the reviewer
    needs to see exactly the text that will be written, not a prettier rendering of it.

    The AFTER name is taken from ``links``, never re-resolved here: ``_plan_links`` is what
    applied D2.1's L1-over-L3 precedence, so it already knows whether the parent is a row
    from this file or a live record. Looking the number up again would resolve an in-file
    identifier against the live database and name the wrong parent -- the exact C2 confusion
    this disclosure exists to prevent. A link the preview could not resolve has no entry, so
    its raw value is shown beside the issue that explains it.

    A link field is COMPARED on the same basis it is rendered: the resolved target, not the
    declared integer (D1.4 as amended 2026-07-26). Comparing raw values here let a child row
    declaring ``customer_id=4`` against a stored ``customer_id=4`` report no change while
    ``_resolve_links`` rewrote that link to an in-file parent at apply time -- an undisclosed
    re-parenting, finding C2 arriving through the link path.
    """
    before_values = existing.model_dump(mode="json")
    link_entries = {str(link["field"]): link for link in links}
    link_targets = dict(spec.foreign_keys)
    changed: list[dict[str, str]] = []
    for name in spec.model.model_fields:
        if name == "id" or name not in values:
            continue
        before = before_values.get(name)
        after = values.get(name)
        if name in link_targets:
            target_key = link_targets[name]
            link = link_entries.get(name)
            if _live_link_target(before) == _incoming_link_target(link, target_key, after):
                continue
            rendered_before = _stored_link_display(session, target_key, before)
            rendered_after = _incoming_link_display(link, after)
        else:
            if before == after:
                continue
            rendered_before = _readable_value(before)
            rendered_after = _readable_value(after)
        changed.append(
            {
                "field": _field_label(spec, name),
                "before": rendered_before,
                "after": rendered_after,
            }
        )
    return changed


def _plan_links(
    session: Session,
    spec: DatasetSpec,
    values: dict[str, Any],
    declared_index: dict[tuple[str, int], list[str]],
    live_ids: dict[str, set[int]],
    plan_remap: dict[tuple[str, int], int | None],
) -> tuple[list[dict[str, str]], list[ImportIssue]]:
    """Decide, and disclose, which parent every foreign key points at (ADR-0004 D2.1)."""
    links: list[dict[str, str]] = []
    issues: list[ImportIssue] = []
    for field_name, target_key in spec.foreign_keys:
        raw_value = values.get(field_name)
        if raw_value in (None, ""):
            continue
        label = _field_label(spec, field_name)
        unresolved = ImportIssue(
            field=label,
            message=f"{label} does not match a record in this file or in the Hub.",
            code="unknown_link",
        )
        try:
            target_id = int(raw_value)
        except (TypeError, ValueError):
            issues.append(unresolved)
            continue
        declared_parents = declared_index.get((target_key, target_id), [])
        if len(declared_parents) == 1:
            # L1, and L3's precedence rule: an identifier declared in this file wins over a
            # live record that happens to carry the same number. ``resolved`` is the parent's
            # live identifier when the parent already matched one; empty while the parent is
            # still to be created, in which case only apply time can complete the link.
            resolved = plan_remap.get((target_key, target_id))
            links.append(
                {
                    "field": field_name,
                    "label": label,
                    "source": "this file",
                    "parent": declared_parents[0],
                    "resolved_id": "" if resolved is None else str(resolved),
                }
            )
        elif len(declared_parents) > 1:
            issues.append(
                ImportIssue(
                    field=label,
                    message=f"{label} matches more than one record in this file.",
                    code="ambiguous_link",
                )
            )
        elif target_id in live_ids.get(target_key, set()):
            # L3: referencing an existing parent is safe, and is disclosed by name.
            links.append(
                {
                    "field": field_name,
                    "label": label,
                    "source": "the Hub",
                    "parent": _live_link_display(session, target_key, target_id),
                    "resolved_id": str(target_id),
                }
            )
        else:
            issues.append(unresolved)  # L2: never dangle.
    return links, issues


def _resolve_links(
    session: Session,
    spec: DatasetSpec,
    values: dict[str, Any],
    links: list[dict[str, str]],
    id_remap: dict[tuple[str, int], int],
) -> None:
    """ADR-0004 D2.1 L4: re-resolve every foreign key immediately before the write."""
    sources = {str(link.get("field")): str(link.get("source")) for link in links}
    for field_name, target_key in spec.foreign_keys:
        raw_value = values.get(field_name)
        if raw_value in (None, ""):
            continue
        label = _field_label(spec, field_name)
        try:
            declared = int(raw_value)
        except (TypeError, ValueError):
            raise DataOperationError(f"{label} could not be linked to a record that exists. Preview the file again.")
        if sources.get(field_name) == "this file":
            if (target_key, declared) not in id_remap:
                raise DataOperationError(
                    f"{label} points at a record that appears later in this file. "
                    "Put parent records before the records that use them, then preview the file again."
                )
            resolved = id_remap[(target_key, declared)]
        else:
            # L3: a live reference, or a value inherited from the record being updated. The
            # remap must NOT apply here -- an unrelated in-file row may have declared this
            # same number, and re-pointing a live link through it would silently move the
            # record to a different parent.
            resolved = declared
        if session.get(DATASET_BY_KEY[target_key].model, resolved) is None:
            raise DataOperationError(f"{label} could not be linked to a record that exists. Preview the file again.")
        values[field_name] = resolved


def _assert_link_changes_disclosed(
    spec: DatasetSpec,
    existing: SQLModel,
    values: dict[str, Any],
    changed_fields: list[dict[str, str]],
) -> None:
    """ADR-0004 D1.6: no foreign key may move unless the preview named it.

    Called after ``_resolve_links`` and before the write, on the values that will actually
    be stored, so it compares the outcome against the disclosure rather than one intention
    against another. The disclosure path (``_changed_fields``) and the write path
    (``_resolve_links``) are separate pieces of code that can drift apart -- they had, which
    is how an operator who reviewed one renamed field also re-parented an RDEC record. When
    they diverge this refuses the whole import instead of letting the write win in silence:
    a disclosure control the write path can diverge from is a display feature, not a control.

    This is to links what D1.5 is to identity.
    """
    disclosed = {str(change.get("field")) for change in changed_fields}
    for field_name, _target_key in spec.foreign_keys:
        stored = _live_link_target(getattr(existing, field_name, None))
        writing = _live_link_target(values.get(field_name))
        if stored == writing:
            continue
        if _field_label(spec, field_name) not in disclosed:
            raise DataOperationError(UNDISCLOSED_WRITE_MESSAGE)


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


def build_import_plan(
    session: Session,
    datasets: dict[str, list[dict[str, Any]]],
    mode: str,
    *,
    restore_by_identifier_enabled: bool = False,
) -> dict[str, Any]:
    if mode not in IMPORT_MODES:
        raise DataOperationError("Choose whether to add only or add and update matching records.")
    restore_by_identifier = mode == RESTORE_BY_IDENTIFIER_MODE
    if restore_by_identifier and not restore_by_identifier_enabled:
        raise DataOperationError(
            "Restore by identifier is turned off. An operator must enable it before a file "
            "can overwrite records by identifier."
        )
    live_ids = _live_ids(session)
    declared_index = _declared_identifier_index(datasets)
    # Parent rows are planned before their children (DATASETS is in dependency order), so by
    # the time a child is planned its parent's live identifier is known when the parent
    # matched one. That is what lets a child whose natural key contains a link column be
    # matched against the right live record instead of against an exported identifier.
    plan_remap: dict[tuple[str, int], int | None] = {}
    notices: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for spec in DATASETS:
        if not spec.importable:
            # ADR-0004 D5 layer 3. Rows of a dataset that may not be written are never
            # planned, so they can never surface as a "create" that hides the rejection.
            # The dataset-level error row below is what the operator sees.
            continue
        spec_rows = datasets.get(spec.key, []) or []
        if spec_rows and not spec.natural_key and not restore_by_identifier:
            # ADR-0004 D1.3: no natural key means it can never be matched, so it is
            # create-only and the operator must be told before applying.
            notices.append(f"{spec.label}: {CREATE_ONLY_NOTICE}")
        for row_number, raw_row in enumerate(spec_rows, start=1):
            declared_id = _declared_identifier(raw_row)
            cleaned, issues = _clean_row(spec, raw_row, keep_identifier=restore_by_identifier)

            links, link_issues = _plan_links(session, spec, cleaned, declared_index, live_ids, plan_remap)
            match_values = dict(cleaned)
            for link in links:
                if link.get("resolved_id"):
                    match_values[link["field"]] = int(link["resolved_id"])
            existing = _find_existing(
                session,
                spec,
                match_values,
                identifier=declared_id,
                allow_identifier_match=restore_by_identifier,
            )
            plan_remap[(spec.key, declared_id)] = existing.id if existing is not None else None

            merged = existing.model_dump(mode="json") if existing else {}
            merged.update(cleaned)
            values: dict[str, Any] = cleaned
            try:
                candidate = spec.model.model_validate(merged)
                values = _derived_values(spec, candidate.model_dump(mode="json"))
            except ValidationError as exc:
                issues.extend(_validation_issues(spec, exc))
            if not restore_by_identifier:
                # Belt and braces on D1.2: whatever the merge produced, no identifier from an
                # uploaded file travels on in the values that get written or re-posted.
                values.pop("id", None)

            identity: tuple[Any, ...] | None = None
            if spec.natural_key and all(values.get(name) not in (None, "") for name in spec.natural_key):
                identity = (spec.key, "key", *(values.get(name) for name in spec.natural_key))
            elif restore_by_identifier and declared_id is not None:
                identity = (spec.key, "id", declared_id)
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

            issues.extend(link_issues)

            changed_fields = (
                _changed_fields(session, spec, existing, values, links) if existing is not None else []
            )
            if issues:
                status = "error"
            elif existing is None:
                status = "create"
            elif mode == "add_only" or not changed_fields:
                # ADR-0004 D1.4: an update that moves nothing is not an update.
                status = "skip"
            else:
                status = "update"
            # ADR-0004 D7 preview row shape. ``issues`` carries ImportIssue and is the single
            # source of truth for what is wrong with a row: there is deliberately no parallel
            # list of plain strings, because two shapes for one fact is how a caller ends up
            # branching on display text instead of on the stable ``code``.
            rows.append(
                {
                    "dataset_key": spec.key,
                    "dataset_label": spec.label,
                    "row_number": row_number,
                    "display": _incoming_display(spec, values),
                    "existing_id": existing.id if existing is not None else None,
                    "existing_display": _existing_display(spec, existing) if existing is not None else "",
                    "changed_fields": changed_fields,
                    "no_change": bool(existing is not None and not changed_fields),
                    "declared_id": declared_id,
                    "links": links,
                    "status": status,
                    "issues": issues,
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
                "existing_id": None,
                "existing_display": "",
                "changed_fields": [],
                "no_change": False,
                "declared_id": None,
                "links": [],
                "status": "error",
                "issues": [issue],
                "values": {},
            }
        )
    summary = {status: sum(1 for row in rows if row["status"] == status) for status in ("create", "update", "skip", "error")}
    return {
        "mode": mode,
        "rows": rows,
        "summary": summary,
        "notices": notices,
        "has_errors": bool(summary["error"]),
    }


# ADR-0004 D4. A preview is single use. The payload itself is unauthenticated base64 posted
# back as a form field, so re-posting it re-applied the import: one preview applied three
# times created three rows. The used-set cannot be a table (ADR-0002 line 54 forbids schema
# migration) and cannot be a signature (ADR-0001 line 110 forbids a persisted secret), so it
# is an in-process nonce set with a content hash and an expiry. A restart invalidates
# outstanding previews, which degrades fail-closed.
_ISSUED_PREVIEWS: dict[str, tuple[int, str]] = {}
_CONSUMED_PREVIEWS: dict[str, tuple[int, str]] = {}
_PREVIEW_TTL_SECONDS = 30 * 60
_MAX_TRACKED_PREVIEWS = 32
EXPIRED_PREVIEW_MESSAGE = "The import preview has expired or is too large. Preview the file again."
APPLIED_PREVIEW_MESSAGE = "This import preview has already been applied. Preview the file again."
UNREADABLE_PREVIEW_MESSAGE = "The import preview could not be read. Preview the file again."
# ADR-0004 D1.5 and D1.6 share one plain-language refusal, because the operator's position is
# the same either way: a write was about to happen that the preview never showed them.
UNDISCLOSED_WRITE_MESSAGE = (
    "This import would change a record that the preview did not show. "
    "Preview the file again and check the records listed before applying."
)


def _bound_preview_store(store: dict[str, tuple[int, str]], now: int) -> None:
    for nonce, (stamped_at, _) in list(store.items()):
        if now - stamped_at > _PREVIEW_TTL_SECONDS:
            store.pop(nonce, None)
    while len(store) > _MAX_TRACKED_PREVIEWS:
        store.pop(next(iter(store)), None)


def _prune_previews(now: int) -> None:
    _bound_preview_store(_ISSUED_PREVIEWS, now)
    _bound_preview_store(_CONSUMED_PREVIEWS, now)


def encode_import_payload(plan: dict[str, Any]) -> str:
    if plan["has_errors"]:
        return ""
    datasets: dict[str, list[dict[str, Any]]] = {}
    for row in plan["rows"]:
        if row["status"] in {"create", "update", "skip"}:
            # ADR-0004 D1.5: the identifier the operator was SHOWN travels with the row, so
            # apply can refuse to write a record the preview never disclosed.
            datasets.setdefault(row["dataset_key"], []).append(
                {
                    "values": row["values"],
                    "declared_id": row["declared_id"],
                    "existing_id": row["existing_id"],
                }
            )
    issued_at = int(time.time())
    nonce = secrets.token_hex(16)
    payload = json.dumps(
        {"mode": plan["mode"], "nonce": nonce, "issued_at": issued_at, "datasets": datasets},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    body = payload.encode("utf-8")
    _prune_previews(issued_at)
    _ISSUED_PREVIEWS[nonce] = (issued_at, sha256(body).hexdigest())
    _bound_preview_store(_ISSUED_PREVIEWS, issued_at)
    return urlsafe_b64encode(body).decode("ascii")


def _payload_entry(entry: Any) -> tuple[dict[str, Any], int | None, int | None]:
    """Read one payload row as (values, declared identifier, disclosed existing identifier).

    A hand-crafted payload of bare value dictionaries is still readable, and still fails
    closed: with no disclosed identifier it can only ever create, never update.
    """
    if isinstance(entry, dict) and isinstance(entry.get("values"), dict):
        declared = entry.get("declared_id")
        existing = entry.get("existing_id")
        return (
            entry["values"],
            int(declared) if isinstance(declared, int) else None,
            int(existing) if isinstance(existing, int) else None,
        )
    if isinstance(entry, dict):
        return entry, _declared_identifier(entry), None
    raise DataOperationError("The import preview is not valid. Preview the file again.")


def _read_import_payload(
    encoded: str, *, restore_by_identifier_enabled: bool = False
) -> tuple[str, dict[str, list[dict[str, Any]]], bytes, str]:
    """Decode and structurally validate a payload. Performs no writes and consumes nothing.

    The dataset checks below are ADR-0004 D5 layer 2 and deliberately run before the
    single-use check, so a rejected dataset key still produces its own specific message.
    A payload that fails here can never match an issued content hash, so no replay window
    is opened by leaving its nonce outstanding.
    """
    if not encoded or len(encoded) > MAX_IMPORT_BYTES * 2:
        raise DataOperationError(EXPIRED_PREVIEW_MESSAGE)
    try:
        body = urlsafe_b64decode(encoded.encode("ascii"))
        decoded = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataOperationError(UNREADABLE_PREVIEW_MESSAGE) from exc
    mode = decoded.get("mode")
    datasets = decoded.get("datasets")
    if mode not in IMPORT_MODES or not isinstance(datasets, dict):
        raise DataOperationError("The import preview is not valid. Preview the file again.")
    if mode == RESTORE_BY_IDENTIFIER_MODE and not restore_by_identifier_enabled:
        raise DataOperationError(
            "Restore by identifier is turned off. An operator must enable it before a file "
            "can overwrite records by identifier."
        )
    # ADR-0004 D5 layer 2. A payload posted straight to the apply route never passes through
    # parse_import_file, so this boundary re-decides the dataset question itself rather than
    # trusting that an earlier layer already did.
    for key, rows in datasets.items():
        issue = _rejected_dataset_issue(str(key))
        if issue is not None:
            raise DataOperationError(issue.message)
        if not isinstance(rows, list):
            raise DataOperationError("The import preview is not valid. Preview the file again.")
    nonce = decoded.get("nonce")
    return mode, datasets, body, nonce if isinstance(nonce, str) else ""


def consume_import_payload(
    encoded: str, *, restore_by_identifier_enabled: bool = False
) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """Read a preview payload exactly once (ADR-0004 D4).

    The nonce is popped unconditionally, before any write and before the content hash and
    expiry are checked, so a preview is spent whether or not the import that follows it
    succeeds. Restoring the nonce on failure would reopen the replay window and is
    prohibited.
    """
    mode, datasets, body, nonce = _read_import_payload(
        encoded, restore_by_identifier_enabled=restore_by_identifier_enabled
    )
    now = int(time.time())
    _prune_previews(now)
    issued = _ISSUED_PREVIEWS.pop(nonce, None) if nonce else None
    if issued is None:
        if nonce and nonce in _CONSUMED_PREVIEWS:
            raise DataOperationError(APPLIED_PREVIEW_MESSAGE)
        raise DataOperationError(EXPIRED_PREVIEW_MESSAGE)
    issued_at, digest = issued
    _CONSUMED_PREVIEWS[nonce] = (now, digest)
    _bound_preview_store(_CONSUMED_PREVIEWS, now)
    if sha256(body).hexdigest() != digest:
        raise DataOperationError(UNREADABLE_PREVIEW_MESSAGE)
    if now - issued_at > _PREVIEW_TTL_SECONDS:
        raise DataOperationError(EXPIRED_PREVIEW_MESSAGE)
    return mode, datasets


def decode_import_payload(
    encoded: str, *, restore_by_identifier_enabled: bool = False
) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """Deprecated alias for consume_import_payload.

    ADR-0004 D4 replaces the apply route's call with consume_import_payload. Until
    app/main.py is wired to the new name this alias consumes as well, so the apply route is
    fail-closed now rather than after the wiring. Delete it once the route has moved.
    """
    return consume_import_payload(encoded, restore_by_identifier_enabled=restore_by_identifier_enabled)


ENTITLEMENT_RESYNC_PHRASE = "following previewed import"
# The datasets whose facts feed assess_entitlement. A project's entitlement is derived from
# the project, and from the customer and contract reached through its solution.
ENTITLEMENT_FACT_DATASETS = ("projects", "solutions", "customers", "contracts")


def _projects_touched_by_import(session: Session, touched: dict[str, set[int]]) -> list[int]:
    """ADR-0004 D6 guardrail 3: confined to what the import touched, never the whole table."""
    project_ids: set[int] = set(touched.get("projects", set()))
    solution_ids: set[int] = set(touched.get("solutions", set()))
    for key, column in (("customers", Solution.customer_id), ("contracts", Solution.contract_id)):
        parent_ids = touched.get(key, set())
        if parent_ids:
            solution_ids.update(
                int(item.id)
                for item in session.exec(select(Solution).where(column.in_(parent_ids)))
                if item.id is not None
            )
    if solution_ids:
        project_ids.update(
            int(item.id)
            for item in session.exec(select(RDProject).where(RDProject.solution_id.in_(solution_ids)))
            if item.id is not None
        )
    return sorted(project_ids)


def _resync_entitlements(session: Session, project_ids: list[int]) -> tuple[int, int]:
    """Recalculate the derived entitlement reviews an import invalidated (ADR-0004 D6).

    This applies unchanged ``assess_entitlement`` to changed facts. It does not change RDEC
    eligibility logic, so it does not breach ADR-0002 line 40: the clause governs logic, not
    facts, and the alternative is a Hub that knowingly shows an assessment computed from
    facts that no longer exist.

    Guardrail 1: this runs after the import has committed, so nothing here can make an import
    partially apply. A project that cannot be recalculated is counted and reported, never
    silently skipped.
    """
    resynced = 0
    failed = 0
    for project_id in project_ids:
        try:
            assessment = sync_entitlement_for_project(session, project_id)
            project = session.get(RDProject, project_id)
            log_event(
                session,
                entity_type="EntitlementAssessment",
                entity_id=assessment.id,
                action="import_entitlement_resync",
                summary=(
                    f"Recalculated the entitlement review for "
                    f"{project.project_title if project else f'project {project_id}'} "
                    f"{ENTITLEMENT_RESYNC_PHRASE}"
                ),
                after=assessment,
            )
            session.commit()
            resynced += 1
        except Exception:
            session.rollback()
            failed += 1
    return resynced, failed


def apply_import(
    session: Session,
    datasets: dict[str, list[dict[str, Any]]],
    mode: str,
    *,
    restore_by_identifier_enabled: bool = False,
) -> dict[str, int]:
    # The payload carries what the operator was shown. Re-planning uses the values only; the
    # disclosed identifiers are held aside and checked against the fresh plan below.
    plan_input: dict[str, list[dict[str, Any]]] = {}
    disclosed: dict[str, list[int | None]] = {}
    for key, entries in datasets.items():
        if not isinstance(entries, list):
            raise DataOperationError("The import preview is not valid. Preview the file again.")
        plan_input[key] = []
        disclosed[key] = []
        for entry in entries:
            values, declared_id, existing_id = _payload_entry(entry)
            row = dict(values)
            if declared_id is not None:
                row["id"] = declared_id
            plan_input[key].append(row)
            disclosed[key].append(existing_id)
    datasets = plan_input
    plan = build_import_plan(
        session, plan_input, mode, restore_by_identifier_enabled=restore_by_identifier_enabled
    )
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
    # ADR-0004 D1.5: disclosure is enforced, not merely displayed. A row may only update the
    # record the preview named, so an undisclosed or changed target is refused outright.
    for row in plan["rows"]:
        if row["status"] != "update":
            continue
        shown = disclosed.get(row["dataset_key"], [])
        index = row["row_number"] - 1
        declared_target = shown[index] if 0 <= index < len(shown) else None
        if declared_target is None or declared_target != row["existing_id"]:
            raise DataOperationError(UNDISCLOSED_WRITE_MESSAGE)
    applied = {"created": 0, "updated": 0, "skipped": plan["summary"]["skip"]}
    # ADR-0004 D2.1: (dataset key, identifier declared in the file) -> identifier in THIS
    # database. Built as rows are written, in dependency order, so a child's link resolves
    # to whatever its parent actually became here.
    id_remap: dict[tuple[str, int], int] = {}
    touched: dict[str, set[int]] = {}
    try:
        for row in plan["rows"]:
            spec = DATASET_BY_KEY[row["dataset_key"]]
            declared_id = row.get("declared_id")
            existing_id = row.get("existing_id")
            if row["status"] == "skip":
                # A skipped parent still resolves: it is the record the file matched.
                if declared_id is not None and existing_id is not None:
                    id_remap[(spec.key, int(declared_id))] = int(existing_id)
                continue
            values = dict(row["values"])
            _resolve_links(session, spec, values, row.get("links") or [], id_remap)
            # The record the preview named, and only that record.
            existing = session.get(spec.model, existing_id) if existing_id is not None else None
            if existing is not None:
                # ADR-0004 D1.6, immediately after link resolution and before any write.
                _assert_link_changes_disclosed(spec, existing, values, row.get("changed_fields") or [])
            candidate = spec.model.model_validate(values)
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
                written_id = existing.id
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
                written_id = candidate.id
            if declared_id is not None and written_id is not None:
                id_remap[(spec.key, int(declared_id))] = int(written_id)
            if written_id is not None and spec.key in ENTITLEMENT_FACT_DATASETS:
                touched.setdefault(spec.key, set()).add(int(written_id))
        session.commit()
    except Exception:
        session.rollback()
        raise
    # ADR-0004 D6 guardrails 1 and 4: a separate, audited step after the import transaction
    # has committed, whose count is returned so the recalculation is never silent.
    resynced, resync_failures = _resync_entitlements(session, _projects_touched_by_import(session, touched))
    applied["entitlement_reviews"] = resynced
    applied["entitlement_reviews_failed"] = resync_failures
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
            candidates.append(
                {
                    "token": f"{key}:{item_id}",
                    "dataset_label": spec.label,
                    "display": _existing_display(spec, item),
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
