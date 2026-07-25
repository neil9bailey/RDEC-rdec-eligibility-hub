import json

from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.framework_intelligence import (
    FetchResult,
    QUESTION_TEXT_MAX_LENGTH,
    configured_framework_source_types,
    create_portal_retrieval_run,
    create_intelligence_report,
    extract_document_intelligence,
    extract_quality_questions_from_text,
    generate_framework_intelligence_report_markdown,
    normalise_document_lines,
    official_framework_source_allowed,
    run_framework_agent_for_profile,
    run_single_source_check,
    seed_framework_sources,
)
from app.main import app
from app.models import (
    AuditEvent,
    BusinessUnit,
    Customer,
    CustomerWatchProfile,
    ExtractedQualityQuestion,
    ExtractedRequirement,
    FrameworkOpportunity,
    FrameworkSource,
    IntelligenceReport,
    OpportunityDocument,
    PortalRetrievalRun,
    ProcurementPlatform,
    RDECOpportunitySignal,
    SourceCheckSnapshot,
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
    platforms = list(session.exec(select(ProcurementPlatform)))

    assert len(sources) >= 6
    assert all(official_framework_source_allowed(source.query_url) for source in sources)
    assert any(source.active and source.source_type == "ocds_api" for source in sources)
    assert any(source.name == "Public Contracts Scotland OCDS API" for source in sources)
    assert any(source.name == "TED eForms notice data" for source in sources)
    assert "eforms_api" in configured_framework_source_types()
    assert {platform.name for platform in platforms} >= {"ProContract", "In-Tend", "Jaggaer", "Delta eSourcing"}


def test_source_change_snapshot_tracks_first_seen_unchanged_and_changed(session):
    source = FrameworkSource(
        name="Find a Tender snapshot source",
        source_type="ocds_api",
        base_url="https://www.find-tender.service.gov.uk",
        query_url="https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit=1",
        active=True,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    payloads = iter([
        '{"releases":[]}',
        '{"releases":[]}',
        '{"releases":[{"id":"changed"}]}',
    ])

    def fake_fetcher(url):
        return FetchResult(ok=True, status_code=200, url=url, text=next(payloads), content_type="application/json")

    first = run_single_source_check(session, source.id, fetcher=fake_fetcher)
    second = run_single_source_check(session, source.id, fetcher=fake_fetcher)
    third = run_single_source_check(session, source.id, fetcher=fake_fetcher)

    snapshots = list(session.exec(select(SourceCheckSnapshot)))
    assert [first.change_type, second.change_type, third.change_type] == ["first_seen", "unchanged", "changed"]
    assert len(snapshots) == 3
    assert third.detected_schema == "ocds_json"


def test_seeded_source_requiring_human_approval_is_never_fetched(session):
    """Finding E6-3: requires_human_approval was stored and displayed, never checked."""
    seed_framework_sources(session)
    tenders_direct = session.exec(
        select(FrameworkSource).where(FrameworkSource.name == "Tenders Direct aggregator backup")
    ).first()

    assert tenders_direct is not None
    assert tenders_direct.requires_human_approval is True

    def refusing_fetcher(url):  # pragma: no cover - must never run
        raise AssertionError(f"a source requiring human approval was fetched: {url}")

    snapshot = run_single_source_check(session, tenders_direct.id, fetcher=refusing_fetcher)

    assert snapshot.change_type == "failed"
    assert "requiring human approval" in (snapshot.notes or "")
    assert not snapshot.ok
    events = list(session.exec(select(AuditEvent).where(AuditEvent.entity_type == "SourceCheckSnapshot")))
    assert events, "the refusal must be recorded in audit history"


def test_agent_run_skips_sources_requiring_human_approval(session):
    customer = Customer(
        customer_name="Transport Authority",
        sector="Public sector transport",
        transport_domain="rail",
        customer_type="local authority",
        corporation_tax_status="no",
    )
    approved_source = FrameworkSource(
        name="Find a Tender approved source",
        source_type="ocds_api",
        base_url="https://www.find-tender.service.gov.uk",
        query_url="https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit=1",
        active=True,
        requires_human_approval=False,
    )
    gated_source = FrameworkSource(
        name="Licensed aggregator awaiting approval",
        source_type="ocds_api",
        base_url="https://www.contractsfinder.service.gov.uk",
        query_url="https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?limit=1",
        active=True,
        requires_human_approval=True,
        connector_status="licence_required",
    )
    session.add_all([customer, approved_source, gated_source])
    session.commit()
    session.refresh(customer)
    profile = CustomerWatchProfile(
        profile_name="Rail watch",
        customer_id=customer.id,
        keywords="rail, transport",
        active=True,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)

    fetched: list[str] = []

    def fetcher(url):
        fetched.append(url)
        assert "contractsfinder" not in url, "the gated source must never be fetched"
        return FetchResult(ok=True, status_code=200, url=url, text='{"releases":[]}', content_type="application/json")

    run = run_framework_agent_for_profile(session, profile.id, fetcher=fetcher)

    assert len(fetched) == 1
    assert "find-tender.service.gov.uk" in fetched[0]
    assert run.sources_checked == 2
    assert "blocked pending human approval" in run.error_summary
    assert run.status == "completed_with_warnings"
    snapshots = list(session.exec(select(SourceCheckSnapshot).where(SourceCheckSnapshot.source_id == gated_source.id)))
    assert len(snapshots) == 1
    assert "requiring human approval" in (snapshots[0].notes or "")


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
        source_id=source.id,
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
    session.refresh(source)
    session.refresh(opportunity)
    opportunity.source_id = source.id
    requirement = ExtractedRequirement(
        opportunity_id=opportunity.id,
        requirement_theme="real-time operation",
        requirement_text="Low-latency real-time transport operations with resilience requirements.",
        confidence="high",
        human_review_status="pending",
    )
    session.add(requirement)
    session.commit()
    session.refresh(requirement)
    session.add(
        RDECOpportunitySignal(
            opportunity_id=opportunity.id,
            requirement_id=requirement.id,
            signal_strength="strong indicators",
            signal_text="Real-time operation requirement may indicate an R&D candidate area where uncertainty is evidenced.",
            recommended_action="Ask engineering to capture tests, failed approaches, people time and competent professional notes.",
            human_review_status="pending",
        )
    )
    session.add(
        SourceCheckSnapshot(
            source_id=source.id,
            query_url=source.query_url,
            status_code=200,
            ok=True,
            content_hash="abc",
            change_type="first_seen",
            detected_schema="ocds",
            connector_status="checked",
        )
    )
    session.commit()

    markdown = generate_framework_intelligence_report_markdown(session, "Test framework summary")
    report = create_intelligence_report(session, "Stored framework summary")

    assert "Test framework summary" in markdown
    assert "Finance And Ayming Use" in markdown
    assert "Source Currency And Review Status" in markdown
    assert "Suggested Evidence Capture Prompts" in markdown
    assert "Review Queue - What To Do Next" in markdown
    assert "evidence prompt" in markdown
    assert "review pending" in markdown
    assert "strength strong indicators" in markdown
    assert "not bid, legal, tax, accounting, procurement, or HMRC submission advice" in markdown
    assert "Requires competent professional and tax review." in markdown
    assert report.id is not None
    assert session.exec(select(IntelligenceReport)).first() is not None


def test_framework_intelligence_hub_links_metrics_to_review_queues(session):
    opportunity = FrameworkOpportunity(
        title="TfL real-time signalling resilience platform",
        buyer_name="Transport for London",
        summary=(
            "Long source summary: requires low latency telemetry, cyber security, legacy integration, "
            "and resilience evidence that should remain available through an expandable summary."
        ),
        source_url="https://www.find-tender.service.gov.uk/Notice/456",
        relevance_score=88,
        status="new",
    )
    session.add(opportunity)
    session.commit()
    session.refresh(opportunity)
    requirement = ExtractedRequirement(
        opportunity_id=opportunity.id,
        requirement_theme="real-time operation",
        requirement_text="Low-latency transport operations with resilience and legacy integration constraints.",
        human_review_status="pending",
    )
    document = OpportunityDocument(
        opportunity_id=opportunity.id,
        title="Notice source",
        document_type="notice",
        url_or_path=opportunity.source_url,
    )
    session.add(requirement)
    session.add(document)
    session.commit()
    session.refresh(requirement)
    signal = RDECOpportunitySignal(
        opportunity_id=opportunity.id,
        requirement_id=requirement.id,
        signal_strength="strong indicators",
        signal_text="Real-time operation may indicate an R&D candidate area if technical uncertainty is evidenced.",
        recommended_action="Capture source documents, failed approaches, people time and review notes.",
        human_review_status="pending",
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)

    client = client_for(session)
    try:
        response = client.get("/framework-intelligence")
        filtered_response = client.get("/framework-intelligence/opportunities?status=new")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    html = response.text
    assert "RDEC Candidate Review Queue" in html
    assert "Actionable RDEC review items" in html
    assert "/framework-intelligence/requirements#rdec-signal-queue" in html
    assert f"/framework-intelligence/requirements#signal-{signal.id}" in html
    assert "strong indicators" in html
    assert "Read source summary" in html
    assert "requires low latency telemetry" in html
    assert "Requires competent professional and tax review." in html
    assert filtered_response.status_code == 200
    assert "Filtered by status" in filtered_response.text
    assert "Open workbench" in filtered_response.text


def test_framework_opportunity_workbench_and_return_to_reviews(session):
    source = FrameworkSource(
        name="Workbench Find a Tender source",
        source_type="ocds_api",
        base_url="https://www.find-tender.service.gov.uk",
        query_url="https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit=1",
        active=True,
        connector_status="live_mvp",
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    opportunity = FrameworkOpportunity(
        source_id=source.id,
        title="Rail SCADA telemetry resilience workbench opportunity",
        buyer_name="National Rail",
        summary="SCADA telemetry, resilience, cyber security and real-time operation requirements.",
        source_url="https://www.find-tender.service.gov.uk/Notice/789",
        relevance_score=91,
        status="new",
    )
    session.add(opportunity)
    session.commit()
    session.refresh(opportunity)
    requirement = ExtractedRequirement(
        opportunity_id=opportunity.id,
        requirement_theme="operational technology / SCADA",
        requirement_text="Telemetry and SCADA resilience requirement.",
        human_review_status="pending",
    )
    session.add(requirement)
    session.commit()
    session.refresh(requirement)
    signal = RDECOpportunitySignal(
        opportunity_id=opportunity.id,
        requirement_id=requirement.id,
        signal_strength="strong indicators",
        signal_text="SCADA resilience may indicate an R&D candidate area if uncertainty is evidenced.",
        recommended_action="Capture test evidence and competent professional review notes.",
        human_review_status="pending",
    )
    document = OpportunityDocument(
        opportunity_id=opportunity.id,
        title="Notice source",
        document_type="notice",
        url_or_path=opportunity.source_url,
    )
    session.add(signal)
    session.add(document)
    session.commit()
    session.refresh(signal)

    client = client_for(session)
    try:
        response = client.get(f"/framework-intelligence/opportunities/{opportunity.id}")
        signal_review = client.post(
            f"/framework-intelligence/signals/{signal.id}/review",
            data={
                "human_review_status": "accepted",
                "recommended_action": "Create an evidence capture task for engineering review.",
                "return_to": f"/framework-intelligence/opportunities/{opportunity.id}#signal-{signal.id}",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Opportunity Review Workbench" in response.text
    assert "What To Review Next" in response.text
    assert "Source Health" in response.text
    assert "Save signal review" in response.text
    assert "Requires competent professional and tax review." in response.text
    assert signal_review.status_code == 303
    assert signal_review.headers["location"] == f"/framework-intelligence/opportunities/{opportunity.id}#signal-{signal.id}"


def test_source_catalogue_and_watch_profiles_are_human_readable(session):
    seed_framework_sources(session)
    client = client_for(session)
    try:
        source_response = client.get("/framework-intelligence/source-catalogue")
        profile_response = client.get("/framework-intelligence/watch-profiles")
    finally:
        app.dependency_overrides.clear()

    assert source_response.status_code == 200
    assert "parked - validate first" in source_response.text
    assert "live source" in source_response.text
    assert "Not part of live guarded checks" in source_response.text
    assert profile_response.status_code == 200
    assert "Suggested Watch Terms" in profile_response.text
    assert "TfL / London transport" in profile_response.text


def test_opportunity_document_extraction_creates_quality_questions_and_retrieval_task(session):
    opportunity = FrameworkOpportunity(
        title="Real-time roadside technology platform",
        buyer_name="National Highways",
        summary="Operational technology and real-time resilience requirements.",
    )
    session.add(opportunity)
    session.commit()
    session.refresh(opportunity)
    document = OpportunityDocument(
        opportunity_id=opportunity.id,
        title="ITT quality questions",
        document_type="itt_document",
        content_summary="Quality response pack for real-time operational technology.",
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    req_count, signal_count, question_count = extract_document_intelligence(
        session,
        opportunity,
        document,
        "Quality question 1: Describe how you will deliver low latency real-time resilience. Weighting 20%",
    )
    session.commit()
    run = create_portal_retrieval_run(session, opportunity.id, notes="Human to retrieve ProContract ITT documents.")

    questions = list(session.exec(select(ExtractedQualityQuestion)))
    retrieval_runs = list(session.exec(select(PortalRetrievalRun)))
    assert req_count >= 1
    assert signal_count >= 1
    assert question_count == 1
    assert questions[0].weighting == "20%"
    assert retrieval_runs[0].id == run.id


REALISTIC_ITT_PACK = """Invitation to Tender - Roadside Technology Refresh
Section 4: Quality Questions and Weightings

Q1. Describe how you will deliver low latency real-time telemetry across the
    roadside estate, including how you will evidence performance. Weighting 25%

Q2. How will you integrate with the legacy SCADA control system without
    interrupting live operations? Weighting 20%

Q3. Set out your approach to cyber security accreditation for the connected
    network. Weighting 15 marks

Q4. Explain your resilience and failover design for the 24/7 service. 20%

Q5. How will you manage the asset management data migration? Weighting 20%
"""


def test_itt_pack_extracts_one_question_per_question_not_one_blob(session):
    """Finding E6-1: newline collapsing turned a whole pasted pack into one question."""
    opportunity = FrameworkOpportunity(
        title="Roadside technology refresh",
        buyer_name="National Highways",
        summary="Real-time roadside technology and legacy integration requirements.",
    )
    session.add(opportunity)
    session.commit()
    session.refresh(opportunity)
    document = OpportunityDocument(
        opportunity_id=opportunity.id,
        title="ITT quality questions",
        document_type="itt_document",
        content_summary="Quality response pack for real-time operational technology.",
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    _, _, question_count = extract_document_intelligence(session, opportunity, document, REALISTIC_ITT_PACK)
    session.commit()

    questions = list(session.exec(select(ExtractedQualityQuestion)))
    texts = [question.question_text for question in questions]

    assert question_count > 1
    assert all(len(text) <= QUESTION_TEXT_MAX_LENGTH for text in texts)
    assert not any("Q1." in text and "Q5." in text for text in texts), "questions were merged into one blob"
    # Each hard-wrapped question survives as one whole question, not as fragments.
    for marker, weighting in [("Q1.", "25%"), ("Q2.", "20%"), ("Q3.", "15 marks"), ("Q4.", "20%"), ("Q5.", "20%")]:
        matching = [question for question in questions if question.question_text.startswith(marker)]
        assert len(matching) == 1, f"{marker} was not extracted exactly once"
        assert matching[0].weighting == weighting
        assert matching[0].question_text.endswith(weighting)
    # Exactly one extra candidate: the section header. This extractor is a review
    # queue, so a low-precision candidate is triaged by a human; a merged blob is
    # not triageable at all. Pinned so precision cannot silently regress.
    assert question_count == 6
    assert sum(1 for text in texts if text.startswith("Section 4")) == 0
    assert sum(1 for text in texts if "Invitation to Tender" in text) == 1
    # Document metadata must not become a question: this document is titled
    # "ITT quality questions" and summarised as a "Quality response pack".
    assert not any(text == "ITT quality questions" for text in texts)
    assert not any("Quality response pack" in text for text in texts)


def test_quality_question_extraction_separates_adjacent_questions():
    pack = "Q1. First question? Weighting 10%\nQ2. Second question? Weighting 90%"

    extracted = extract_quality_questions_from_text(pack)

    assert [text for text, _ in extracted] == [
        "Q1. First question? Weighting 10%",
        "Q2. Second question? Weighting 90%",
    ]
    assert [weighting for _, weighting in extracted] == ["10%", "90%"]


def test_a_hard_wrapped_question_is_rejoined_not_fragmented():
    pack = "Q1. Describe how you will deliver\n    resilient real-time telemetry.\n    Weighting 30%"

    extracted = extract_quality_questions_from_text(pack)

    assert extracted == [
        ("Q1. Describe how you will deliver resilient real-time telemetry. Weighting 30%", "30%")
    ]


def test_normalise_document_lines_keeps_newlines_and_collapses_indentation():
    assert normalise_document_lines("a  b\r\n   c \t d\n\n e") == "a b\nc d\n\ne"


def test_a_single_long_paragraph_is_split_rather_than_truncated():
    sentence = "Describe how you will evidence real-time resilience at 20%. "
    pack = (sentence * 20).strip()

    extracted = extract_quality_questions_from_text(pack)

    assert len(extracted) > 1
    assert all(len(text) <= QUESTION_TEXT_MAX_LENGTH for text, _ in extracted)
    assert sum(len(text) for text, _ in extracted) > QUESTION_TEXT_MAX_LENGTH


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


def test_framework_source_portal_and_document_routes(session):
    seed_framework_sources(session)
    opportunity = FrameworkOpportunity(
        title="Portal document test opportunity",
        buyer_name="Transport Authority",
        summary="Real-time data and cyber security requirements.",
    )
    session.add(opportunity)
    session.commit()
    session.refresh(opportunity)

    client = client_for(session)
    try:
        responses = [
            client.get("/framework-intelligence/source-catalogue"),
            client.get("/framework-intelligence/source-changes"),
            client.get("/framework-intelligence/portal-platforms"),
            client.get(f"/framework-intelligence/opportunities/{opportunity.id}/documents"),
        ]
        doc_response = client.post(
            f"/framework-intelligence/opportunities/{opportunity.id}/documents",
            data={
                "title": "ITT extract",
                "document_type": "itt_document",
                "retrieval_status": "retrieved",
                "platform_name": "ProContract",
                "document_text": "Quality question: Explain real-time data resilience. Weighting 25%",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert all(response.status_code == 200 for response in responses)
    assert doc_response.status_code == 303
    assert session.exec(select(ExtractedQualityQuestion)).first() is not None
