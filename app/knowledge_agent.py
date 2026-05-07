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
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc.lower() in OFFICIAL_DOMAINS


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
        response = client.get(source.url)
        text = response.text or ""
        return KnowledgeSourceCheck(
            source_id=source.id,
            title=source.title,
            url=source.url,
            ok=response.status_code < 400,
            status_code=response.status_code,
            last_modified_header=response.headers.get("last-modified", ""),
            detected_last_updated=extract_last_updated(text),
            content_hash=sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
            checked_at=datetime.now(UTC),
            notes="Official source reachable." if response.status_code < 400 else "Official source returned an error status.",
        )
    except httpx.HTTPError as exc:
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
    with httpx.Client(follow_redirects=True, timeout=12.0) as client:
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
