from sqlmodel import select

from app.knowledge_agent import (
    extract_last_updated,
    knowledge_agent_summary,
    load_knowledge_sources,
    official_source_allowed,
)
from app.models import KnowledgeSourceCheck


def test_knowledge_sources_are_official_and_cover_core_rules(session):
    sources = load_knowledge_sources()
    covered_rules = {rule for source in sources for rule in source.applies_to_rules}

    assert len(sources) >= 10
    assert all(official_source_allowed(source.url) for source in sources)
    assert "eligibility_weights.yml" in covered_rules
    assert "aif_rules.yml" in covered_rules
    assert "entitlement_rules.yml" in covered_rules
    assert "cost_categories.yml" in covered_rules


def test_knowledge_agent_summary_uses_latest_checks(session):
    check = KnowledgeSourceCheck(
        source_id="dsit-guidelines-2023",
        title="CIRD81910 - DSIT Guidelines (2023)",
        url="https://www.gov.uk/hmrc-internal-manuals/corporate-intangibles-research-and-development-manual/cird81910",
        ok=True,
        status_code=200,
        detected_last_updated="8 April 2026",
    )
    session.add(check)
    session.commit()

    summary = knowledge_agent_summary(session)

    assert summary["source_count"] >= 10
    assert summary["latest_checks"]["dsit-guidelines-2023"].status_code == 200
    assert not list(session.exec(select(KnowledgeSourceCheck).where(KnowledgeSourceCheck.ok == False)))  # noqa: E712


def test_extract_last_updated_from_govuk_text():
    text = "From: HM Revenue & Customs Published 18 March 2024 Last updated 8 January 2026 - See all updates"
    assert extract_last_updated(text) == "8 January 2026"
