from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_name: str
    utr: str = ""
    paye_reference: str = ""
    vat_number: str = ""
    sic_code: str = ""
    registered_office_country: str = "United Kingdom"
    registered_office_region: str = "England"
    northern_ireland_registered: bool = False
    senior_rd_contact_name: str = ""
    senior_rd_contact_role: str = ""
    senior_rd_contact_email: str = ""
    senior_rd_contact_phone: str = ""
    agent_name: str = ""
    agent_reference: str = ""
    agent_email: str = ""
    agent_phone: str = ""
    agent_role: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class AccountingPeriod(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id")
    label: str
    start_date: date
    end_date: date
    period_of_account_start: date
    period_of_account_end: date
    claim_notification_submitted: bool = False
    claim_notification_date: Optional[date] = None
    prior_claim_within_3_years: bool = False
    scheme_notes: str = "Merged RDEC / ERIS review required for periods beginning on or after 1 April 2024."


class BusinessUnit(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="businessunit.id")
    description: str = ""
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_unit_id: Optional[int] = Field(default=None, foreign_key="businessunit.id")
    customer_name: str
    sector: str = "Public sector transport"
    transport_domain: str = "multimodal"
    customer_type: str = "transport authority"
    corporation_tax_status: str = "unknown"
    notes: str = ""


class Contract(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    contract_name: str
    customer_id: int = Field(foreign_key="customer.id")
    contract_type: str = "statement of work"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    customer_requested_rd: bool = False
    customer_intended_or_contemplated_rd: bool = False
    technical_uncertainty_described: bool = False
    ip_owner: str = ""
    funding_grant_notes: str = ""
    public_sector_procurement_notes: str = ""
    contract_evidence_links: str = ""


class Solution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    solution_name: str
    customer_id: int = Field(foreign_key="customer.id")
    contract_id: Optional[int] = Field(default=None, foreign_key="contract.id")
    solution_description: str = ""
    business_problem: str = ""
    technical_architecture_summary: str = ""
    transport_environment_constraints: str = ""
    initial_radar_status: str = "amber"
    radar_reason: str = ""


class RDProject(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    solution_id: int = Field(foreign_key="solution.id")
    accounting_period_id: Optional[int] = Field(default=None, foreign_key="accountingperiod.id")
    project_title: str
    field_of_science_or_technology: str = ""
    baseline_knowledge: str = ""
    advance_sought: str = ""
    wider_field_explanation: str = ""
    scientific_or_technological_uncertainties: str = ""
    competent_professionals_could_not_resolve: str = ""
    system_uncertainty_explanation: str = ""
    alternatives_considered: str = ""
    experiments_prototypes_tests: str = ""
    failed_attempts: str = ""
    outcome: str = "unresolved"
    rd_start_date: Optional[date] = None
    rd_end_date: Optional[date] = None
    boundary_explanation: str = ""
    non_qualifying_delivery_activities: str = ""
    supplier_initiated_rd: bool = False
    uncertainty_discovered_during_delivery: bool = False
    contract_specified_technical_uncertainty: bool = False
    another_party_could_claim: bool = False
    grant_funded_or_subsidised: bool = False
    company_role: str = "framework supplier"
    described_in_aif: bool = False


class TechnicalUncertainty(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="rdproject.id")
    summary: str
    why_not_readily_resolvable: str = ""
    status: str = "open"


class Activity(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="rdproject.id")
    activity_name: str
    activity_type: str = "experiment"
    description: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    qualifying_boundary_note: str = ""


class CompetentProfessionalOpinion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="rdproject.id")
    professional_name: str
    role: str = ""
    qualifications: str = ""
    years_relevant_experience: int = 0
    relevant_field_expertise: str = ""
    opinion_text: str = ""
    signoff_status: str = "draft"
    signoff_date: Optional[date] = None
    reviewer_comments: str = ""


class EvidenceItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="rdproject.id")
    source_system: str = "Manual upload / note"
    source_reference: str = ""
    url_or_file_path: str = ""
    date_created: Optional[date] = None
    evidence_type: str = "technical spike"
    relevance_tag: str = "uncertainty"
    strength: str = "moderate"
    notes: str = ""


class CostLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="rdproject.id")
    activity_id: Optional[int] = Field(default=None, foreign_key="activity.id")
    activity: str = ""
    cost_input_type: str = "direct_cost"
    cost_category: str = "staff"
    person_or_supplier_name: str = ""
    person_role: str = ""
    time_period_start: Optional[date] = None
    time_period_end: Optional[date] = None
    hours: float = 0
    hourly_rate: float = 0
    days: float = 0
    day_rate: float = 0
    gross_cost: float = 0
    apportionment_percentage: float = 0
    qualifying_amount: float = 0
    paid_status: str = "paid"
    uk_or_overseas: str = "UK"
    connected_party_status: str = "unconnected"
    paye_nic_notes: str = ""
    evidence_link: str = ""
    notes: str = ""


class EntitlementAssessment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="rdproject.id", unique=True)
    customer_type: str = ""
    customer_corporation_tax_status: str = "unknown"
    customer_intended_or_contemplated_rd: bool = False
    supplier_initiated_rd: bool = False
    uncertainty_discovered_during_delivery: bool = False
    contract_specified_technical_uncertainty: bool = False
    another_party_could_claim: bool = False
    grant_funded_or_subsidised: bool = False
    company_role: str = ""
    status: str = "ambiguous_tax_review"
    rationale: str = ""
    updated_at: datetime = Field(default_factory=utc_now)


class ReviewDecision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="rdproject.id")
    reviewer_name: str = ""
    decision_status: str = "pending"
    comments: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class ClaimPeriodSubmissionStatus(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    accounting_period_id: int = Field(foreign_key="accountingperiod.id", unique=True)
    ct600_submitted: bool = False
    ct600_submission_date: Optional[date] = None
    aif_submitted: bool = False
    aif_submission_date: Optional[date] = None
    notes: str = ""


class KnowledgeSourceCheck(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: str = Field(index=True)
    title: str
    url: str
    ok: bool = False
    status_code: int = 0
    last_modified_header: str = ""
    detected_last_updated: str = ""
    content_hash: str = ""
    checked_at: datetime = Field(default_factory=utc_now)
    notes: str = ""
