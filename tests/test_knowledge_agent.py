from datetime import date

import httpx
import pytest
from sqlmodel import select

from app.knowledge_agent import (
    KnowledgeSource,
    check_source,
    extract_last_updated,
    knowledge_agent_summary,
    load_knowledge_sources,
    official_source_allowed,
)
from app.models import KnowledgeSourceCheck


OFFICIAL_URL = "https://www.gov.uk/guidance/rd-tax-relief"


def knowledge_source(url: str = OFFICIAL_URL) -> KnowledgeSource:
    return KnowledgeSource(
        id="test-source",
        title="Test official source",
        url=url,
        topic="rdec",
        applies_to_rules=["eligibility_weights.yml"],
        priority="high",
        last_reviewed=date(2026, 1, 1),
        review_interval_days=45,
    )


def client_for_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=5.0)


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


OFF_ALLOWLIST_BODY = "Last updated 1 January 2030 - attacker controlled content"


@pytest.mark.parametrize(
    "redirect_target",
    [
        "https://evil.example.com/copy",
        "https://gov.uk.evil.com/copy",
        "https://www.gov.uk.evil.com/copy",
        "http://www.gov.uk/downgraded",
        "https://203.0.113.10/copy",
    ],
)
def test_check_source_refuses_content_served_after_an_off_allowlist_redirect(redirect_target):
    """Finding E6-2: the final URL was never re-validated after following redirects."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) == OFFICIAL_URL:
            return httpx.Response(302, headers={"location": redirect_target})
        return httpx.Response(200, text=OFF_ALLOWLIST_BODY)

    with client_for_handler(handler) as client:
        check = check_source(knowledge_source(), client)

    assert len(requested) == 2, "the redirect must actually have been followed"
    assert not check.ok
    assert "redirected outside the approved official domain allow-list" in check.notes
    assert check.url == redirect_target
    # Nothing from the off-allow-list response may be ingested.
    assert check.content_hash == ""
    assert check.detected_last_updated == ""
    assert "1 January 2030" not in check.detected_last_updated


def test_check_source_accepts_a_redirect_that_stays_on_the_allowlist():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == OFFICIAL_URL:
            return httpx.Response(301, headers={"location": "https://www.gov.uk/guidance/rd-tax-relief-2026"})
        return httpx.Response(200, text="Last updated 8 January 2026")

    with client_for_handler(handler) as client:
        check = check_source(knowledge_source(), client)

    assert check.ok
    assert check.url == "https://www.gov.uk/guidance/rd-tax-relief-2026"
    assert check.detected_last_updated == "8 January 2026"
    assert check.content_hash


def test_check_source_still_blocks_an_off_allowlist_url_before_any_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError(f"no request may be made to {request.url}")

    with client_for_handler(handler) as client:
        check = check_source(knowledge_source("https://evil-gov.uk/guidance"), client)

    assert not check.ok
    assert "outside the approved official domain allow-list" in check.notes


@pytest.mark.parametrize(
    "url",
    [
        "https://evil-gov.uk/guidance",
        "https://gov.uk.evil.com/guidance",
        "https://www.gov.uk@evil.com/guidance",
        "https://203.0.113.10/guidance",
        "http://www.gov.uk/guidance",
    ],
)
def test_official_source_allowlist_is_unchanged_and_still_blocks_lookalikes(url):
    """Negative control: the existing allow-list must not be weakened by E6-2."""
    assert not official_source_allowed(url)


def test_official_source_allowlist_still_admits_genuine_official_urls():
    assert official_source_allowed("https://www.gov.uk/guidance/rd-tax-relief")
    assert official_source_allowed("https://assets.publishing.service.gov.uk/media/report.pdf")
