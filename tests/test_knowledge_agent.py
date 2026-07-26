from datetime import date

import httpx
import pytest
from sqlmodel import select

from app.knowledge_agent import (
    MAX_REDIRECT_HOPS,
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
    """A client that would auto-follow redirects if check_source let it.

    ``follow_redirects=True`` here is deliberate and load-bearing. ``run_live_source_checks``
    builds its client with False, but the guard must not depend on that: ``check_source`` passes
    ``follow_redirects=False`` per request, which overrides the client default. If someone later
    reverts the per-request argument and relies on the client setting alone, these tests go red.
    """
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
def test_check_source_never_requests_an_off_allowlist_redirect_target(redirect_target):
    """G5b E6-SSRF: the off-allow-list hop must not be REQUESTED, not merely not ingested.

    This supersedes the earlier E6-2 assertion ``len(requested) == 2`` ("the redirect must
    actually have been followed"). That assertion pinned the vulnerable behaviour: following the
    redirect and then discarding the body still sends a request to whatever host the open
    redirect names, which is the blind SSRF G5b confirmed by instrumentation. The property under
    test is now the absence of the second request.
    """
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) == OFFICIAL_URL:
            return httpx.Response(302, headers={"location": redirect_target})
        return httpx.Response(200, text=OFF_ALLOWLIST_BODY)

    with client_for_handler(handler) as client:
        check = check_source(knowledge_source(), client)

    # The load-bearing assertion: exactly one request, to the allow-listed origin. The redirect
    # target received nothing at all.
    assert requested == [OFFICIAL_URL], f"a request escaped to an off-allow-list host: {requested}"
    assert redirect_target not in requested
    assert not check.ok
    assert "redirected outside the approved official domain allow-list" in check.notes
    assert "no request was made to that address" in check.notes
    # The refused address is still recorded, so the refusal is visible rather than silent.
    assert check.url == redirect_target
    # Nothing from the off-allow-list response may be ingested.
    assert check.content_hash == ""
    assert check.detected_last_updated == ""
    assert "1 January 2030" not in check.detected_last_updated


def test_check_source_never_requests_an_internal_host_on_a_bounce_chain():
    """official -> internal -> official. The bounce passes any post-hoc final-URL check.

    The final URL is back on the allow-list, so re-checking only the end of the chain sees
    nothing wrong -- while the internal address has already been contacted. Per-hop validation is
    the only thing that stops this one.
    """
    internal = "https://169.254.169.254/latest/meta-data/"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) == OFFICIAL_URL:
            return httpx.Response(302, headers={"location": internal})
        if str(request.url) == internal:
            return httpx.Response(302, headers={"location": "https://www.gov.uk/guidance/final"})
        return httpx.Response(200, text="Last updated 8 January 2026")

    with client_for_handler(handler) as client:
        check = check_source(knowledge_source(), client)

    assert requested == [OFFICIAL_URL], f"the internal host was contacted: {requested}"
    assert internal not in requested
    assert not check.ok
    assert check.url == internal
    assert check.content_hash == ""


def test_check_source_follows_a_relative_redirect_that_stays_on_the_allowlist():
    """A relative Location must resolve against the current hop, not be treated as a host."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) == OFFICIAL_URL:
            return httpx.Response(301, headers={"location": "/guidance/rd-tax-relief-2026"})
        return httpx.Response(200, text="Last updated 8 January 2026")

    with client_for_handler(handler) as client:
        check = check_source(knowledge_source(), client)

    assert requested == [OFFICIAL_URL, "https://www.gov.uk/guidance/rd-tax-relief-2026"]
    assert check.ok
    assert check.url == "https://www.gov.uk/guidance/rd-tax-relief-2026"


def test_check_source_abandons_an_allowlisted_redirect_loop_at_the_hop_cap():
    """Two allow-listed URLs pointing at each other would otherwise spin forever."""
    first = OFFICIAL_URL
    second = "https://www.gov.uk/guidance/rd-tax-relief-b"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        target = second if str(request.url) == first else first
        return httpx.Response(302, headers={"location": target})

    with client_for_handler(handler) as client:
        check = check_source(knowledge_source(), client)

    assert len(requested) == MAX_REDIRECT_HOPS + 1
    assert not check.ok
    assert "redirected more than" in check.notes
    assert check.content_hash == ""


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


#: The G5b runtime battery, as URL classes. Every one of these was thrown at the live container
#: with a capturing listener reachable from inside it and every one was refused with zero listener
#: hits. Pinned here so the per-hop redirect change cannot weaken the entry check that produced
#: that result: the netloc comparison must stay a FULL-netloc match, https-only.
ATTACK_URLS = [
    "https://evil-gov.uk/guidance",
    "https://gov.uk.evil.com/guidance",
    "https://www.gov.uk.evil.com/guidance",
    "https://www.gov.uk@evil.com/guidance",  # userinfo at the start
    "https://evil.com@www.gov.uk.evil.com/guidance",  # userinfo at the end
    "https://user:pass@www.gov.uk/guidance",  # credentials on a genuine host
    "https://169.254.169.254/latest/meta-data/",  # cloud metadata
    "https://127.0.0.1:8080/customers",  # loopback: the live container itself
    "https://203.0.113.10/guidance",
    "http://www.gov.uk/guidance",  # scheme downgrade
    "https:///www.gov.uk/guidance",  # triple slash: empty netloc
    " https://evil.example.com/guidance",  # leading space before an off-allow-list scheme
    "https://www.gov.uk:8080/guidance",  # port confusion
    "https://www.gov.uk\t.evil.com/guidance",  # tab inside the host
    "https://www.gov.uk\n@evil.com/guidance",  # newline inside the host
    "https://www.gov.uk\\@evil.com/guidance",  # backslash: httpx resolves the host as evil.com
]


@pytest.mark.parametrize("url", ATTACK_URLS)
def test_official_source_allowlist_is_unchanged_and_still_blocks_lookalikes(url):
    """Negative control: the allow-list must not be weakened by E6-2 or by E6-SSRF."""
    assert not official_source_allowed(url)


def test_the_allowlist_agrees_with_httpx_about_the_host_it_is_judging():
    r"""The parser differential that the full-netloc comparison is what closes.

    ``urlparse`` and httpx do not agree about every string. ``https://www.gov.uk\@evil.com/``
    is the sharp case: httpx resolves its host to ``evil.com`` (backslash is not a delimiter for
    ``urlparse``, so the whole ``www.gov.uk\@evil.com`` lands in the netloc). Comparing the FULL
    netloc means the allow-list refuses it. Had the check compared ``parsed.hostname``, the two
    parsers' disagreement would decide the outcome instead of the allow-list.

    ``urlparse`` also strips leading whitespace and removes tab/CR/LF anywhere in the URL, so a
    space-prefixed value is judged as the URL it strips to. That is safe in both directions here:
    a space before an off-allow-list host is still refused (above), and a space before a genuine
    gov.uk URL is admitted by the allow-list but then fails closed at the transport, because
    httpx parses the same string as a relative URL with no host at all.
    """
    backslash = "https://www.gov.uk\\@evil.com/guidance"
    assert httpx.URL(backslash).host == "evil.com"
    assert not official_source_allowed(backslash)

    spaced = " https://www.gov.uk/guidance"
    assert official_source_allowed(spaced)
    assert httpx.URL(spaced).host == ""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError(f"no request may be made to {request.url}")

    with client_for_handler(handler) as client:
        check = check_source(knowledge_source(spaced), client)

    assert not check.ok
    assert check.content_hash == ""


#: The battery again, restricted to values that can legally occupy a Location header. Tab and
#: newline are excluded because httpx refuses to build a header containing them at all, so the
#: HTTP layer rejects those before the allow-list is ever consulted.
REDIRECT_ATTACK_URLS = [url for url in ATTACK_URLS if not any(ch in url for ch in "\t\r\n")]


@pytest.mark.parametrize("url", REDIRECT_ATTACK_URLS)
def test_no_request_escapes_the_allowlist_when_an_attack_url_is_the_redirect_target(url):
    """The same battery, as redirect targets rather than as configured source URLs.

    Entry validation already refuses these when they are typed in. This asserts the second door:
    an open redirect on a genuine gov.uk page naming any of them must not put a request on the
    wire to a host outside the allow-list.

    The assertion is "every URL requested was allow-listed" rather than "only one request was
    made", because httpx repairs two of these Location forms back onto the CURRENT host before
    the redirect request is built: ``https:///x`` (scheme, empty host) has the current host
    copied in, and a leading-space value parses as relative and is joined onto the current URL.
    Both therefore resolve to www.gov.uk, and a request to www.gov.uk is exactly what the
    allow-list exists to permit. Asserting a hop count would fail on a normalisation that is not
    a bypass; asserting the allow-list property holds for every hop is the real invariant, and it
    is the one that fails loudly if the per-hop check is ever removed.
    """
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) == OFFICIAL_URL:
            return httpx.Response(302, headers={"location": url})
        return httpx.Response(200, text=OFF_ALLOWLIST_BODY)

    with client_for_handler(handler) as client:
        check_source(knowledge_source(), client)

    assert requested, "the positive control failed: not even the official URL was requested"
    offending = [seen for seen in requested if not official_source_allowed(seen)]
    assert offending == [], f"redirect target {url!r} put an off-allow-list request on the wire: {offending}"


def test_official_source_allowlist_still_admits_genuine_official_urls():
    assert official_source_allowed("https://www.gov.uk/guidance/rd-tax-relief")
    assert official_source_allowed("https://assets.publishing.service.gov.uk/media/report.pdf")
