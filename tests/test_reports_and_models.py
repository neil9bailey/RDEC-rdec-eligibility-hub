import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.models import (
    AccountingPeriod,
    Activity,
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
    AuditEvent,
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
from app.reports import generate_claim_period_pack_markdown, generate_project_memo_markdown
from app.services import (
    ENTITLEMENT_LABEL_FALLBACK,
    ENTITLEMENT_STATUS_LABELS,
    calculate_project_score,
    entitlement_label,
)
from tests.test_rules_engine import project_by_title


# --- Schema regression guard ----------------------------------------------------------------
# Expected names below are literals on purpose. Deriving them from `Model.__tablename__` (as the
# previous version of this test did) makes the assertion true by construction: the same import
# that builds the expectation also populates the metadata, so it can never fail and says nothing
# about columns, keys or constraints. ADR-0002 line 54 and ADR-0004 both forbid schema migration
# for this baseline, so the expected shape is pinned exactly rather than as a subset.

EXPECTED_TABLES_BY_MODEL = {
    AccountingPeriod: "accountingperiod",
    Activity: "activity",
    AuditEvent: "auditevent",
    BusinessUnit: "businessunit",
    BuyerPortalInstance: "buyerportalinstance",
    ClaimPeriodSubmissionStatus: "claimperiodsubmissionstatus",
    Company: "company",
    CompetentProfessionalOpinion: "competentprofessionalopinion",
    Contract: "contract",
    CostLine: "costline",
    Customer: "customer",
    CustomerWatchProfile: "customerwatchprofile",
    EntitlementAssessment: "entitlementassessment",
    EvidenceItem: "evidenceitem",
    ExtractedQualityQuestion: "extractedqualityquestion",
    ExtractedRequirement: "extractedrequirement",
    FrameworkAgentRun: "frameworkagentrun",
    FrameworkOpportunity: "frameworkopportunity",
    FrameworkSource: "frameworksource",
    IntelligenceReport: "intelligencereport",
    KnowledgeSourceCheck: "knowledgesourcecheck",
    OpportunityDocument: "opportunitydocument",
    PortalRetrievalRun: "portalretrievalrun",
    ProcurementPlatform: "procurementplatform",
    RDECOpportunitySignal: "rdecopportunitysignal",
    RDProject: "rdproject",
    ReviewDecision: "reviewdecision",
    Solution: "solution",
    SourceCheckSnapshot: "sourcechecksnapshot",
    TechnicalUncertainty: "technicaluncertainty",
}

EXPECTED_COLUMNS = {
    "rdproject": {
        "id",
        "solution_id",
        "accounting_period_id",
        "project_title",
        "field_of_science_or_technology",
        "baseline_knowledge",
        "advance_sought",
        "wider_field_explanation",
        "scientific_or_technological_uncertainties",
        "competent_professionals_could_not_resolve",
        "system_uncertainty_explanation",
        "alternatives_considered",
        "experiments_prototypes_tests",
        "failed_attempts",
        "outcome",
        "rd_start_date",
        "rd_end_date",
        "boundary_explanation",
        "non_qualifying_delivery_activities",
        "supplier_initiated_rd",
        "uncertainty_discovered_during_delivery",
        "contract_specified_technical_uncertainty",
        "another_party_could_claim",
        "grant_funded_or_subsidised",
        "company_role",
        "described_in_aif",
    },
    "costline": {
        "id",
        "project_id",
        "activity_id",
        "activity",
        "cost_input_type",
        "cost_category",
        "person_or_supplier_name",
        "person_role",
        "time_period_start",
        "time_period_end",
        "hours",
        "hourly_rate",
        "days",
        "day_rate",
        "gross_cost",
        "apportionment_percentage",
        "qualifying_amount",
        "paid_status",
        "uk_or_overseas",
        "connected_party_status",
        "paye_nic_notes",
        "evidence_link",
        "notes",
    },
    "evidenceitem": {
        "id",
        "project_id",
        "source_system",
        "source_reference",
        "url_or_file_path",
        "date_created",
        "evidence_type",
        "relevance_tag",
        "strength",
        "notes",
    },
    "competentprofessionalopinion": {
        "id",
        "project_id",
        "professional_name",
        "role",
        "qualifications",
        "years_relevant_experience",
        "relevant_field_expertise",
        "opinion_text",
        "signoff_status",
        "signoff_date",
        "reviewer_comments",
    },
    "entitlementassessment": {
        "id",
        "project_id",
        "customer_type",
        "customer_corporation_tax_status",
        "customer_intended_or_contemplated_rd",
        "supplier_initiated_rd",
        "uncertainty_discovered_during_delivery",
        "contract_specified_technical_uncertainty",
        "another_party_could_claim",
        "grant_funded_or_subsidised",
        "company_role",
        "status",
        "rationale",
        "updated_at",
    },
    "claimperiodsubmissionstatus": {
        "id",
        "accounting_period_id",
        "ct600_submitted",
        "ct600_submission_date",
        "aif_submitted",
        "aif_submission_date",
        "notes",
    },
    "businessunit": {"id", "name", "parent_id", "description", "active", "created_at"},
    "auditevent": {
        "id",
        "created_at",
        "actor",
        "entity_type",
        "entity_id",
        "action",
        "summary",
        "before_json",
        "after_json",
    },
}

EXPECTED_FOREIGN_KEYS = {
    "rdproject": {("solution_id", "solution", "id"), ("accounting_period_id", "accountingperiod", "id")},
    "costline": {("project_id", "rdproject", "id"), ("activity_id", "activity", "id")},
    "evidenceitem": {("project_id", "rdproject", "id")},
    "competentprofessionalopinion": {("project_id", "rdproject", "id")},
    "entitlementassessment": {("project_id", "rdproject", "id")},
    "claimperiodsubmissionstatus": {("accounting_period_id", "accountingperiod", "id")},
    "businessunit": {("parent_id", "businessunit", "id")},
    # Audit history survives every purge scope (ADR-0002 line 39), so it must never reference a
    # working-data table. A foreign key here would break purge once enforcement is switched on.
    "auditevent": set(),
}

EXPECTED_UNIQUE_COLUMNS = {
    "entitlementassessment": "project_id",
    "claimperiodsubmissionstatus": "accounting_period_id",
    "businessunit": "name",
}

EXPECTED_REQUIRED_COLUMNS = {
    "rdproject": {"solution_id", "project_title"},
    "costline": {"project_id", "gross_cost", "apportionment_percentage", "qualifying_amount"},
    "evidenceitem": {"project_id"},
    "competentprofessionalopinion": {"project_id"},
    "entitlementassessment": {"project_id"},
    "claimperiodsubmissionstatus": {"accounting_period_id"},
    "auditevent": {"entity_type", "action"},
}


def unique_column_groups(inspector, table_name: str) -> set[frozenset[str]]:
    """Columns guaranteed unique, whether declared as a constraint or as a unique index."""
    groups = {
        frozenset(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table_name)
    }
    groups |= {
        frozenset(index["column_names"])
        for index in inspector.get_indexes(table_name)
        if index["unique"]
    }
    return groups


def test_database_creates_exactly_the_expected_tables(session):
    inspector = inspect(session.get_bind())

    assert set(inspector.get_table_names()) == set(EXPECTED_TABLES_BY_MODEL.values())
    assert {model: model.__tablename__ for model in EXPECTED_TABLES_BY_MODEL} == EXPECTED_TABLES_BY_MODEL


@pytest.mark.parametrize("table_name", sorted(EXPECTED_COLUMNS))
def test_core_tables_keep_their_columns(session, table_name):
    inspector = inspect(session.get_bind())
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}

    assert set(columns) == EXPECTED_COLUMNS[table_name]
    assert inspector.get_pk_constraint(table_name)["constrained_columns"] == ["id"]
    for required in EXPECTED_REQUIRED_COLUMNS.get(table_name, set()):
        assert columns[required]["nullable"] is False, f"{table_name}.{required} must stay NOT NULL"


@pytest.mark.parametrize("table_name", sorted(EXPECTED_FOREIGN_KEYS))
def test_core_tables_keep_their_foreign_keys_and_uniqueness(session, table_name):
    inspector = inspect(session.get_bind())
    declared = {
        (key["constrained_columns"][0], key["referred_table"], key["referred_columns"][0])
        for key in inspector.get_foreign_keys(table_name)
    }

    assert declared == EXPECTED_FOREIGN_KEYS[table_name]
    if table_name in EXPECTED_UNIQUE_COLUMNS:
        expected_unique = frozenset({EXPECTED_UNIQUE_COLUMNS[table_name]})
        assert expected_unique in unique_column_groups(inspector, table_name)


def test_report_generation_smoke(seeded_session):
    project = project_by_title(seeded_session, "Passenger Flow")
    memo = generate_project_memo_markdown(seeded_session, project.id)
    pack = generate_claim_period_pack_markdown(seeded_session, project.accounting_period_id)

    assert "Project Eligibility Memo" in memo
    assert "Probabilistic Passenger Flow" in memo
    assert "Requires competent professional and tax review." in memo
    assert "Rule versions used" in memo
    assert "Generated at" in memo
    assert "Executive review summary" in memo
    assert "Evidence matrix" in memo
    assert "Reviewer checklist" in memo
    assert "Cost warnings for Finance review" in memo
    assert "| Relevance | Type | Source | Reference | Strength | Review note |" in memo
    assert "Qualifying expenditure captured for review" in memo
    assert "Contracted-out and irrelievable-client treatment requires tax review." in memo
    assert "Claim Period Pack" in pack
    assert "Project readiness matrix" in pack
    assert "Reviewer checklist" in pack
    assert "Cost warnings for Finance review" in pack
    assert "Total qualifying spend by category" in pack
    assert "Selection method" in pack
    assert "AIF project-selection notes" in pack
    assert "Requires competent professional and tax review." in pack


# --- E5-EXPORT-1: the exports speak the reviewer's language ----------------------------------
# The G4 rejection: the eligibility panel was humanised and the export builders were not, so a
# UI-only walkthrough passed while `/claim-periods/{id}/pack?format=md` and `/projects/{id}/report`
# -- the artefacts handed to HMRC and to Ayming -- still printed `ambiguous_tax_review` and a bare
# `red / 7`. These tests pin the label SOURCE, not just the current output text: a label that the
# screen and the export take from two places will drift apart again the next time one is edited.

REPO_ROOT = Path(__file__).resolve().parents[1]
LABELS_TEMPLATE = REPO_ROOT / "app" / "templates" / "_labels.html"
SERVICES_SOURCE = REPO_ROOT / "app" / "services.py"
JINJA_ENTITLEMENT_LABELS = re.compile(
    r"\{%\s*set\s+ENTITLEMENT_LABELS\s*=\s*(\{.*?\})\s*%\}", re.DOTALL
)


def template_entitlement_labels() -> dict[str, str]:
    """The mapping ``app/templates/_labels.html`` actually renders, read out of the template."""
    match = JINJA_ENTITLEMENT_LABELS.search(LABELS_TEMPLATE.read_text(encoding="utf-8"))
    assert match, "ENTITLEMENT_LABELS is no longer declared as a literal in _labels.html"
    return ast.literal_eval(match.group(1))


def statuses_assess_entitlement_can_return() -> set[str]:
    """Every status literal reachable out of ``assess_entitlement``, read from its own source.

    Derived rather than listed, so a status added to the engine tomorrow is covered by this test
    on the day it is added instead of on the day someone remembers to extend a hardcoded list.
    """
    tree = ast.parse(SERVICES_SOURCE.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "assess_entitlement"
    )
    statuses: set[str] = set()
    for node in ast.walk(function):
        # EntitlementResult(status="supplier_likely", ...)
        if isinstance(node, ast.keyword) and node.arg == "status":
            for value in ast.walk(node.value):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    statuses.add(value.value)
        # status = "customer_likely" if ... else "ambiguous_tax_review"
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "status" for target in node.targets
        ):
            for value in ast.walk(node.value):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    statuses.add(value.value)
    assert statuses, "no status literals found; the AST walk has stopped matching the source"
    return statuses


def test_the_export_and_the_screen_read_entitlement_labels_from_one_source():
    """ADR-0002 Ruling R2: one mapping, two surfaces. Divergence here is the G4 defect itself."""
    assert ENTITLEMENT_STATUS_LABELS == template_entitlement_labels(), (
        "app/services.py ENTITLEMENT_STATUS_LABELS and app/templates/_labels.html "
        "ENTITLEMENT_LABELS must stay byte-identical: the Markdown pack and the eligibility "
        "panel describe the same stored status and must not word it differently"
    )


def test_every_entitlement_status_the_engine_can_produce_has_a_label():
    """The ``.get`` fallback must be unreachable for a status the engine can actually return.

    Without this, a new status would silently render as the fallback wording on both surfaces --
    a degrade nobody sees, on a document sent to HMRC.
    """
    unlabelled = statuses_assess_entitlement_can_return() - set(ENTITLEMENT_STATUS_LABELS)

    assert not unlabelled, (
        f"entitlement statuses with no reviewer-facing label: {sorted(unlabelled)}. "
        f"Add each to ENTITLEMENT_STATUS_LABELS and to _labels.html rather than letting it "
        f"fall back to {ENTITLEMENT_LABEL_FALLBACK!r}"
    )
    assert EntitlementAssessment.model_fields["status"].default in ENTITLEMENT_STATUS_LABELS


def test_an_unknown_entitlement_status_still_gets_the_template_fallback():
    """Parity with the macro is what stops the two surfaces disagreeing on an unknown value."""
    assert entitlement_label("not_a_status") == ENTITLEMENT_LABEL_FALLBACK
    assert entitlement_label("") == ENTITLEMENT_LABEL_FALLBACK
    assert entitlement_label(None) == ENTITLEMENT_LABEL_FALLBACK


def test_the_memo_states_the_position_in_words_and_keeps_the_stored_rating(seeded_session):
    project = project_by_title(seeded_session, "Passenger Flow")
    score = calculate_project_score(seeded_session, project.id, sync=False)

    memo = generate_project_memo_markdown(seeded_session, project.id)

    # ADR-0002 Ruling R2: the VALUE is untouched -- it is a CSS class and a dashboard_metrics key.
    assert score.rating in {"green", "amber", "weak", "red"}
    assert f"- Rating: {score.rating_label}" in memo
    assert f"- Rating: {score.rating}" not in memo
    assert f"- Current status: {score.rating_label}." in memo
    assert "- Status: Supplier indicators" in memo
    assert "- Status: supplier_likely" not in memo
    assert "entitlement position is Supplier indicators" in memo
    assert "entitlement status is supplier_likely" not in memo
    # ADR-0002 line 58 preserve-clause is untouched by the copy change.
    assert "Requires competent professional and tax review." in memo


def test_the_pack_states_each_project_in_words_and_keeps_the_stored_rating(seeded_session):
    project = project_by_title(seeded_session, "Passenger Flow")
    score = calculate_project_score(seeded_session, project.id, sync=False)

    pack = generate_claim_period_pack_markdown(seeded_session, project.accounting_period_id)

    assert f"{project.project_title}: {score.rating_label}, score {score.score}/100" in pack
    assert f"{project.project_title}: {score.rating} /" not in pack
    assert f"rating {score.rating_label}, blockers" in pack
    assert f"rating {score.rating}, blockers" not in pack
    assert "AIF described yes" in pack
    assert "AIF described True" not in pack
    assert "- Ready: no" in pack
    assert "ambiguous_tax_review" not in pack
    assert "Requires competent professional and tax review." in pack
