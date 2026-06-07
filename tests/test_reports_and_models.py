from sqlmodel import SQLModel

from app.models import (
    AccountingPeriod,
    Activity,
    BusinessUnit,
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
    ExtractedRequirement,
    FrameworkAgentRun,
    FrameworkOpportunity,
    FrameworkSource,
    IntelligenceReport,
    KnowledgeSourceCheck,
    OpportunityDocument,
    RDProject,
    RDECOpportunitySignal,
    ReviewDecision,
    Solution,
    TechnicalUncertainty,
)
from app.reports import generate_claim_period_pack_markdown, generate_project_memo_markdown
from tests.test_rules_engine import project_by_title


def test_database_model_creation_metadata_contains_required_tables(session):
    expected = {
        Company.__tablename__,
        AccountingPeriod.__tablename__,
        Customer.__tablename__,
        Contract.__tablename__,
        Solution.__tablename__,
        RDProject.__tablename__,
        TechnicalUncertainty.__tablename__,
        Activity.__tablename__,
        BusinessUnit.__tablename__,
        CompetentProfessionalOpinion.__tablename__,
        EvidenceItem.__tablename__,
        KnowledgeSourceCheck.__tablename__,
        FrameworkSource.__tablename__,
        CustomerWatchProfile.__tablename__,
        FrameworkOpportunity.__tablename__,
        OpportunityDocument.__tablename__,
        ExtractedRequirement.__tablename__,
        RDECOpportunitySignal.__tablename__,
        FrameworkAgentRun.__tablename__,
        IntelligenceReport.__tablename__,
        CostLine.__tablename__,
        EntitlementAssessment.__tablename__,
        ReviewDecision.__tablename__,
        ClaimPeriodSubmissionStatus.__tablename__,
        AuditEvent.__tablename__,
    }
    assert expected.issubset(set(SQLModel.metadata.tables.keys()))


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
