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
