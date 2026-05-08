import json

from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.framework_intelligence import (
    FetchResult,
    create_intelligence_report,
    generate_framework_intelligence_report_markdown,
    official_framework_source_allowed,
    run_framework_agent_for_profile,
    seed_framework_sources,
)
from app.main import app
from app.models import (
    AuditEvent,
    BusinessUnit,
    Customer,
    CustomerWatchProfile,
    ExtractedRequirement,
    FrameworkOpportunity,
    FrameworkSource,
    IntelligenceReport,
    RDECOpportunitySignal,
)


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_framework_source_allowlist():
    assert official_framework_source_allowed("https://www.gov.uk/contracts-finder")
    assert official_framework_source_allowed("https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages")
    assert not official_framework_source_allowed("http://www.gov.uk/contracts-finder")
    assert not official_framework_source_allowed("https://example.com/contracts")


def test_seed_framework_sources_are_official_public_sources(session):
    seed_framework_sources(session)

    sources = list(session.exec(select(FrameworkSource)))

    assert len(sources) >= 4
    assert all(official_framework_source_allowed(source.query_url) for source in sources)
    assert any(source.active and source.source_type == "ocds_api" for source in sources)


def test_framework_agent_run_extracts_opportunity_requirements_signals_and_audit(session):
    unit = BusinessUnit(name="Highways")
    customer = Customer(
        customer_name="National Highways",
        business_unit_id=None,
        sector="Public sector transport",
        transport_domain="highways",
        customer_type="UK Government department",
        corporation_tax_status="no",
    )
    source = FrameworkSource(
        name="Contracts Finder test source",
        source_type="ocds_api",
        base_url="https://www.contractsfinder.service.gov.uk",
        query_url="https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?limit=1",
        active=True,
    )
    session.add(unit)
    session.add(customer)
    session.add(source)
    session.commit()
    session.refresh(unit)
    session.refresh(customer)
    customer.business_unit_id = unit.id
    profile = CustomerWatchProfile(
        profile_name="National Highways NRTS watch",
        customer_id=customer.id,
        business_unit_id=unit.id,
        buyer_aliases="National Highways; Highways England",
        keywords="SCADA, cyber security, roadside technology, real-time, asset management",
        domains="highways, operational technology",
        active=True,
    )
    session.add(customer)
    session.add(profile)
    session.commit()
    session.refresh(profile)

    payload = {
        "releases": [
            {
                "id": "notice-1",
                "ocid": "ocds-test-1",
                "date": "2026-05-08T12:00:00Z",
                "tag": ["tender"],
                "buyer": {"name": "National Highways"},
                "tender": {
                    "title": "National Highways SCADA cyber security roadside technology framework",
                    "description": (
                        "Provision of real-time operational technology, network resilience, asset management "
                        "and cyber security services for roadside systems."
                    ),
                    "status": "active",
                    "value": {"amount": 5000000, "currency": "GBP"},
                    "tenderPeriod": {"endDate": "2026-07-01T12:00:00Z"},
                    "items": [
                        {
                            "classification": {
                                "id": "72000000",
                                "description": "IT services: consulting, software development, Internet and support",
                            }
                        }
                    ],
                },
                "links": {"self": "https://www.contractsfinder.service.gov.uk/notice-1"},
            }
        ]
    }

    def fake_fetcher(url):
        assert "contractsfinder.service.gov.uk" in url
        return FetchResult(
            ok=True,
            status_code=200,
            url=url,
            text=json.dumps(payload),
            content_type="application/json",
        )

    run = run_framework_agent_for_profile(session, profile.id, fetcher=fake_fetcher)

    opportunity = session.exec(select(FrameworkOpportunity)).first()
    requirements = list(session.exec(select(ExtractedRequirement)))
    signals = list(session.exec(select(RDECOpportunitySignal)))
    events = list(session.exec(select(AuditEvent)))
    assert run.status == "completed"
    assert opportunity is not None
    assert opportunity.buyer_name == "National Highways"
    assert opportunity.relevance_score > 0
    assert requirements
    assert any(requirement.requirement_theme == "operational technology / SCADA" for requirement in requirements)
    assert signals
    assert any("R&D candidate indicators" in signal.signal_text for signal in signals)
    assert any(event.entity_type == "FrameworkAgentRun" for event in events)


def test_framework_intelligence_report_contains_guardrails_and_finance_ayming_use(session):
    source = FrameworkSource(
        name="Find a Tender test source",
        source_type="ocds_api",
        base_url="https://www.find-tender.service.gov.uk",
        query_url="https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit=1",
        active=True,
    )
    opportunity = FrameworkOpportunity(
        title="Transport real-time data platform opportunity",
        buyer_name="Transport Authority",
        summary="Real-time data, resilience, cyber security and legacy integration requirements.",
        source_url="https://www.find-tender.service.gov.uk/Notice/123",
        relevance_score=80,
        status="watching",
    )
    session.add(source)
    session.add(opportunity)
    session.commit()

    markdown = generate_framework_intelligence_report_markdown(session, "Test framework summary")
    report = create_intelligence_report(session, "Stored framework summary")

    assert "Test framework summary" in markdown
    assert "Finance And Ayming Use" in markdown
    assert "not bid, legal, tax, accounting, procurement, or HMRC submission advice" in markdown
    assert "Requires competent professional and tax review." in markdown
    assert report.id is not None
    assert session.exec(select(IntelligenceReport)).first() is not None


def test_framework_intelligence_report_routes(session):
    report = create_intelligence_report(session, "Route report")
    client = client_for(session)
    try:
        index_response = client.get("/framework-intelligence/reports")
        detail_response = client.get(f"/framework-intelligence/reports/{report.id}")
        markdown_response = client.get(f"/framework-intelligence/reports/{report.id}?format=md")
    finally:
        app.dependency_overrides.clear()

    assert index_response.status_code == 200
    assert detail_response.status_code == 200
    assert markdown_response.status_code == 200
    assert "text/markdown" in markdown_response.headers["content-type"]
