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
    EntitlementAssessment,
    EvidenceItem,
    KnowledgeSourceCheck,
    RDProject,
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
        CostLine.__tablename__,
        EntitlementAssessment.__tablename__,
        ReviewDecision.__tablename__,
        ClaimPeriodSubmissionStatus.__tablename__,
    }
    assert expected.issubset(set(SQLModel.metadata.tables.keys()))


def test_report_generation_smoke(seeded_session):
    project = project_by_title(seeded_session, "Passenger Flow")
    memo = generate_project_memo_markdown(seeded_session, project.id)
    pack = generate_claim_period_pack_markdown(seeded_session, project.accounting_period_id)

    assert "Project Eligibility Memo" in memo
    assert "Probabilistic Passenger Flow" in memo
    assert "Requires competent professional and tax review." in memo
    assert "Claim Period Pack" in pack
    assert "Total qualifying spend by category" in pack
