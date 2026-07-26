from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import re
from urllib.parse import urlparse

import httpx
from sqlmodel import Session, col, select

from app.models import KnowledgeSourceCheck
from app.rule_loader import load_rule_file


OFFICIAL_DOMAINS = {
    "www.gov.uk",
    "gov.uk",
    "www.hmrc.gov.uk",
    "hmrc.gov.uk",
    "assets.publishing.service.gov.uk",
}


@dataclass
class KnowledgeSource:
    id: str
    title: str
    url: str
    topic: str
    applies_to_rules: list[str]
    priority: str
    last_reviewed: date
    review_interval_days: int

    @property
    def review_due_date(self) -> date:
        return self.last_reviewed + timedelta(days=self.review_interval_days)


def load_knowledge_sources() -> list[KnowledgeSource]:
    data = load_rule_file("knowledge_sources.yml")
    sources = []
    for item in data.get("sources", []):
        sources.append(
            KnowledgeSource(
                id=item["id"],
                title=item["title"],
                url=item["url"],
                topic=item["topic"],
                applies_to_rules=list(item.get("applies_to_rules", [])),
                priority=item.get("priority", "medium"),
                last_reviewed=date.fromisoformat(item["last_reviewed"]),
                review_interval_days=int(item.get("review_interval_days", 45)),
            )
        )
    return sources


def official_source_allowed(url: str) -> bool:
    """Whether a URL may be requested at all.

    Deliberately compares the FULL netloc, not ``parsed.hostname``. The netloc carries any
    userinfo, so ``https://www.gov.uk@attacker.host/`` yields ``www.gov.uk@attacker.host``,
    which is not in the set and is refused. Using ``.hostname`` here would return
    ``attacker.host`` for that URL and ``www.gov.uk`` for ``https://www.gov.uk@x/`` variants
    in other parsers -- that asymmetry is the whole userinfo-confusion bypass class. Do not
    "tidy" this into ``.hostname``.
    """
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc.lower() in OFFICIAL_DOMAINS


#: Redirects followed after the first request. A legitimate gov.uk canonicalisation is one or
#: two hops; the cap stops a redirect loop between two allow-listed URLs from spinning.
MAX_REDIRECT_HOPS = 5


@dataclass(frozen=True)
class AllowlistedFetch:
    """Outcome of a GET in which every hop was allow-list checked BEFORE it was sent.

    ``response`` is None whenever ``refusal`` is set, so a caller cannot reach for the body of
    a request that was refused: there is no body, because the request was never made.
    """

    response: httpx.Response | None
    #: The URL that served the content, or -- when refused -- the hop that was not requested.
    url: str
    #: "" | "off_allowlist" | "hop_limit"
    refusal: str = ""
    #: Status of the last allow-listed response actually received (the redirect itself).
    refused_after_status: int = 0


def fetch_via_allowlisted_hops(
    client: httpx.Client,
    url: str,
    is_allowed,
    *,
    headers: dict[str, str] | None = None,
    max_hops: int = MAX_REDIRECT_HOPS,
) -> AllowlistedFetch:
    """GET ``url``, validating every redirect target against ``is_allowed`` before requesting it.

    G5b finding E6-SSRF. The previous form of both fetchers passed ``follow_redirects=True`` and
    re-applied the allow-list to the FINAL url afterwards. That check is post-hoc by construction:
    an open redirect on an allow-listed gov.uk or procurement host made the client send a request
    to the internal address, and only then was the result discarded. The content was never
    ingested, but the request left the host -- blind SSRF. Instrumentation at G5b confirmed the
    intermediate request was really being sent.

    Two properties do the work here:

    * ``follow_redirects=False`` is passed PER REQUEST, not merely on the client. A caller that
      hands in a client built with ``follow_redirects=True`` therefore still cannot make this
      function auto-follow -- the per-request value overrides the client default in httpx.
    * The URL that is validated is the one taken off the request object that is about to be
      sent (``response.next_request``, built by httpx itself from the ``Location`` header), and
      that same object is then sent unmodified. There is no re-parse between the check and the
      send, so relative, scheme-relative and userinfo-bearing Location values cannot resolve to
      one thing for the allow-list and another for the transport.

    This lives in ``knowledge_agent`` rather than in a module of its own only because a new
    application module was outside the remediation's file scope; it is shared verbatim with
    ``framework_intelligence.fetch_source_url`` so the two outbound guards cannot drift apart.
    Extracting it to ``app/safe_fetch.py`` is a sensible follow-up.
    """
    request = client.build_request("GET", url, headers=headers)
    last_status = 0
    for _ in range(max_hops + 1):
        current = str(request.url)
        if not is_allowed(current):
            return AllowlistedFetch(None, current, "off_allowlist", last_status)
        response = client.send(request, follow_redirects=False)
        last_status = response.status_code
        next_request = response.next_request
        if next_request is None:
            return AllowlistedFetch(response, current)
        request = next_request
    return AllowlistedFetch(None, str(request.url), "hop_limit", last_status)


def latest_checks_by_source(session: Session) -> dict[str, KnowledgeSourceCheck]:
    checks = list(session.exec(select(KnowledgeSourceCheck).order_by(col(KnowledgeSourceCheck.checked_at).desc())))
    latest: dict[str, KnowledgeSourceCheck] = {}
    for check in checks:
        latest.setdefault(check.source_id, check)
    return latest


def knowledge_agent_summary(session: Session, today: date | None = None) -> dict:
    today = today or date.today()
    sources = load_knowledge_sources()
    latest_checks = latest_checks_by_source(session)
    stale_sources = [source for source in sources if source.review_due_date < today]
    unchecked_sources = [source for source in sources if source.id not in latest_checks]
    failing_checks = [check for check in latest_checks.values() if not check.ok]
    rule_coverage: dict[str, list[KnowledgeSource]] = {}
    for source in sources:
        for rule_file in source.applies_to_rules:
            rule_coverage.setdefault(rule_file, []).append(source)

    return {
        "sources": sources,
        "latest_checks": latest_checks,
        "source_count": len(sources),
        "stale_sources": stale_sources,
        "unchecked_sources": unchecked_sources,
        "failing_checks": failing_checks,
        "rule_coverage": rule_coverage,
        "official_domains": sorted(OFFICIAL_DOMAINS),
        "policy": load_rule_file("knowledge_sources.yml").get("agent_policy", {}),
    }


def extract_last_updated(text: str) -> str:
    compact = re.sub(r"\s+", " ", text)
    patterns = [
        r"Last updated\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})",
        r"Updated:\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})",
        r"Updated\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def check_source(source: KnowledgeSource, client: httpx.Client) -> KnowledgeSourceCheck:
    if not official_source_allowed(source.url):
        return KnowledgeSourceCheck(
            source_id=source.id,
            title=source.title,
            url=source.url,
            ok=False,
            status_code=0,
            notes="Blocked: source is outside the approved official domain allow-list.",
        )

    try:
        # Every hop is allow-list checked before it is requested. The earlier form followed
        # redirects and re-checked the final URL afterwards, which discarded off-allow-list
        # CONTENT but had already SENT the request to the off-allow-list host (G5b E6-SSRF).
        fetched = fetch_via_allowlisted_hops(client, source.url, official_source_allowed)
        if fetched.refusal == "off_allowlist":
            return KnowledgeSourceCheck(
                source_id=source.id,
                title=source.title,
                # The refused address is recorded, not hidden: an operator reading the check
                # needs to see where the official source tried to send the Hub.
                url=fetched.url,
                ok=False,
                status_code=fetched.refused_after_status,
                checked_at=datetime.now(UTC),
                notes=(
                    "Blocked: the source redirected outside the approved official domain allow-list. "
                    "The redirect was not followed, so no request was made to that address."
                ),
            )
        if fetched.refusal == "hop_limit":
            return KnowledgeSourceCheck(
                source_id=source.id,
                title=source.title,
                url=fetched.url,
                ok=False,
                status_code=fetched.refused_after_status,
                checked_at=datetime.now(UTC),
                notes=(
                    f"Blocked: the source redirected more than {MAX_REDIRECT_HOPS} times. "
                    "The chain was abandoned and no further request was made."
                ),
            )
        response = fetched.response
        if response is None:  # pragma: no cover - unreachable: refusal is "" only with a response
            return KnowledgeSourceCheck(
                source_id=source.id,
                title=source.title,
                url=fetched.url,
                ok=False,
                status_code=0,
                checked_at=datetime.now(UTC),
                notes="Blocked: the official-source check returned no response.",
            )
        final_url = fetched.url
        text = response.text or ""
        return KnowledgeSourceCheck(
            source_id=source.id,
            title=source.title,
            url=final_url,
            ok=response.status_code < 400,
            status_code=response.status_code,
            last_modified_header=response.headers.get("last-modified", ""),
            detected_last_updated=extract_last_updated(text),
            content_hash=sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
            checked_at=datetime.now(UTC),
            notes="Official source reachable." if response.status_code < 400 else "Official source returned an error status.",
        )
    # httpx.InvalidURL is not an HTTPError: it is raised when a Location header cannot be
    # resolved into a request at all. Without it here a malformed redirect would escape as an
    # unhandled exception from a routine freshness check.
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return KnowledgeSourceCheck(
            source_id=source.id,
            title=source.title,
            url=source.url,
            ok=False,
            status_code=0,
            checked_at=datetime.now(UTC),
            notes=f"Network check failed: {exc}",
        )


def run_live_source_checks(session: Session) -> list[KnowledgeSourceCheck]:
    sources = load_knowledge_sources()
    checks: list[KnowledgeSourceCheck] = []
    # follow_redirects=False on the client as well as per request: check_source walks the chain
    # itself, validating each hop before it is sent (G5b E6-SSRF).
    with httpx.Client(follow_redirects=False, timeout=12.0) as client:
        for source in sources:
            check = check_source(source, client)
            session.add(check)
            checks.append(check)
    session.commit()
    for check in checks:
        session.refresh(check)
    return checks


def knowledge_review_actions(session: Session) -> list[str]:
    summary = knowledge_agent_summary(session)
    actions: list[str] = []
    if summary["stale_sources"]:
        actions.append("Review stale official sources and update affected YAML rule versions where needed.")
    if summary["unchecked_sources"]:
        actions.append("Run a live official-source check when internet access is available.")
    if summary["failing_checks"]:
        actions.append("Investigate failed official-source checks before relying on current-source freshness.")
    if not actions:
        actions.append("No Knowledge Agent freshness issues currently recorded.")
    actions.append("Do not auto-apply guidance changes; record competent professional and tax review before changing rule weights or blockers.")
    return actions
