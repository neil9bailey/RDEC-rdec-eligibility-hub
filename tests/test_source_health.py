from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from app.models import FrameworkSource, KnowledgeSourceCheck, SourceCheckSnapshot
from app.source_health import generate_source_health_triage_markdown, source_health_triage_context


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_source_health_pack_combines_knowledge_and_framework_evidence(session):
    knowledge_check = KnowledgeSourceCheck(
        source_id="dsit-guidelines-2023",
        title="CIRD81910 - DSIT Guidelines (2023)",
        url="https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird81910",
        ok=False,
        status_code=503,
        notes="Official source returned an error status.",
    )
    source = FrameworkSource(
        name="Find a Tender test source",
        source_type="ocds_api",
        base_url="https://www.find-tender.service.gov.uk",
        query_url="https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit=1",
        active=True,
        connector_status="warning",
    )
    session.add(knowledge_check)
    session.add(source)
    session.commit()
    session.refresh(source)
    snapshot = SourceCheckSnapshot(
        source_id=source.id,
        checked_at=datetime(2026, 6, 22, tzinfo=UTC),
        query_url=source.query_url,
        status_code=503,
        ok=False,
        change_type="failed",
        detected_schema="ocds",
        connector_status="warning",
        notes="Test timeout.",
    )
    session.add(snapshot)
    session.commit()

    context = source_health_triage_context(session)
    markdown = generate_source_health_triage_markdown(session, context)

    assert context["knowledge"]["failing_count"] == 1
    assert context["framework"]["attention_count"] == 1
    assert "Source Health Triage Pack" in markdown
    assert "Knowledge Agent Source Status" in markdown
    assert "Framework Intelligence Source Status" in markdown
    assert "Does not run live checks" in markdown
    assert "Do not auto-update YAML rules" in markdown


def test_source_health_route_and_markdown_download(seeded_session):
    client = client_for(seeded_session)
    try:
        page_response = client.get("/source-health")
        markdown_response = client.get("/source-health?format=md")
    finally:
        app.dependency_overrides.clear()

    assert page_response.status_code == 200
    assert "Source Health Triage Pack" in page_response.text
    assert "Priority Actions" in page_response.text
    assert markdown_response.status_code == 200
    assert "text/markdown" in markdown_response.headers["content-type"]
    assert "Source Health Triage Pack" in markdown_response.text
