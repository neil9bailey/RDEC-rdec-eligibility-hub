from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from html import unescape
import json
import re
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from sqlmodel import Session, col, select

from app.audit import compact_snapshot, log_event
from app.models import (
    BusinessUnit,
    Customer,
    CustomerWatchProfile,
    ExtractedRequirement,
    FrameworkAgentRun,
    FrameworkOpportunity,
    FrameworkSource,
    IntelligenceReport,
    OpportunityDocument,
    RDECOpportunitySignal,
)
from app.services import CAVEAT, money


FRAMEWORK_AGENT_CAVEAT = (
    "This is procurement and R&D candidate intelligence only. It is not bid, legal, tax, accounting, "
    "procurement, or HMRC submission advice. Requires competent professional and tax review."
)

OFFICIAL_FRAMEWORK_DOMAINS = {
    "www.gov.uk",
    "gov.uk",
    "assets.publishing.service.gov.uk",
    "www.find-tender.service.gov.uk",
    "find-tender.service.gov.uk",
    "www.contractsfinder.service.gov.uk",
    "contractsfinder.service.gov.uk",
    "www.crowncommercial.gov.uk",
    "crowncommercial.gov.uk",
    "www.gca.gov.uk",
    "gca.gov.uk",
}

DEFAULT_FRAMEWORK_SOURCES = [
    {
        "name": "Find a Tender OCDS release package API",
        "source_type": "ocds_api",
        "base_url": "https://www.find-tender.service.gov.uk",
        "query_url": "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit=50",
        "notes": "Official UK high-value public and utilities procurement notice source. Uses public OCDS data where available.",
    },
    {
        "name": "Contracts Finder OCDS search API",
        "source_type": "ocds_api",
        "base_url": "https://www.contractsfinder.service.gov.uk",
        "query_url": "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?limit=50",
        "notes": "Official public-sector opportunity source for lower-value, future, live, award and early engagement notices.",
    },
    {
        "name": "GOV.UK Contracts Finder guidance",
        "source_type": "web_page",
        "base_url": "https://www.gov.uk/contracts-finder",
        "query_url": "https://www.gov.uk/contracts-finder",
        "active": False,
        "notes": "Official guidance reference. Inactive by default because it is not a live notice feed.",
    },
    {
        "name": "GOV.UK Find a Tender guidance",
        "source_type": "web_page",
        "base_url": "https://www.gov.uk/find-tender",
        "query_url": "https://www.gov.uk/find-tender",
        "active": False,
        "notes": "Official guidance reference. Inactive by default because it is not a live notice feed.",
    },
]

REQUIREMENT_PATTERNS: dict[str, list[str]] = {
    "asset management": ["asset management", "asset", "maintenance"],
    "cyber security": ["cyber", "security", "secure", "accreditation"],
    "data and analytics": ["data", "analytics", "reporting", "dashboard", "prediction", "forecast"],
    "high availability and resilience": ["resilience", "resilient", "availability", "24/7", "failover", "continuity"],
    "legacy integration": ["legacy", "integration", "interface", "migration", "interoperability"],
    "operational technology / SCADA": ["scada", "operational technology", " ot ", "telemetry", "remote monitoring"],
    "real-time operation": ["real-time", "real time", "low latency", "latency", "live data"],
    "service management": ["service management", "itil", "service desk", "incident", "sla"],
    "software development": ["software", "application", "platform", "development", "digital service"],
    "telecommunications and networks": ["network", "telecom", "fibre", "connectivity", "communications"],
    "transport operations": ["highways", "rail", "tfl", "transport", "traffic", "passenger", "ticketing"],
}

RDEC_SIGNAL_THEMES = {
    "cyber security",
    "data and analytics",
    "high availability and resilience",
    "legacy integration",
    "operational technology / SCADA",
    "real-time operation",
    "software development",
    "telecommunications and networks",
    "transport operations",
}


@dataclass
class FetchResult:
    ok: bool
    status_code: int
    url: str
    text: str
    content_type: str = ""
    error: str = ""


@dataclass
class CandidateOpportunity:
    title: str
    buyer_name: str = ""
    notice_identifier: str = ""
    ocid: str = ""
    notice_type: str = ""
    procurement_stage: str = ""
    published_date: date | None = None
    deadline_date: date | None = None
    value_low: float = 0
    value_high: float = 0
    currency: str = "GBP"
    cpv_codes: str = ""
    location: str = ""
    source_url: str = ""
    summary: str = ""
    content_hash: str = ""


def official_framework_source_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc.lower() in OFFICIAL_FRAMEWORK_DOMAINS


def seed_framework_sources(session: Session) -> None:
    existing_names = {source.name for source in session.exec(select(FrameworkSource))}
    for item in DEFAULT_FRAMEWORK_SOURCES:
        if item["name"] in existing_names:
            continue
        session.add(
            FrameworkSource(
                name=item["name"],
                source_type=item["source_type"],
                base_url=item["base_url"],
                query_url=item["query_url"],
                active=bool(item.get("active", True)),
                notes=item["notes"],
            )
        )
    session.commit()


def framework_intelligence_metrics(session: Session) -> dict:
    profiles = list(session.exec(select(CustomerWatchProfile)))
    opportunities = list(session.exec(select(FrameworkOpportunity)))
    requirements = list(session.exec(select(ExtractedRequirement)))
    signals = list(session.exec(select(RDECOpportunitySignal)))
    runs = list(session.exec(select(FrameworkAgentRun).order_by(col(FrameworkAgentRun.started_at).desc()).limit(5)))
    return {
        "active_profiles": sum(1 for profile in profiles if profile.active),
        "active_sources": len(list(session.exec(select(FrameworkSource).where(FrameworkSource.active == True)))),  # noqa: E712
        "opportunity_count": len(opportunities),
        "new_opportunities": sum(1 for opportunity in opportunities if opportunity.status == "new"),
        "pending_requirements": sum(1 for requirement in requirements if requirement.human_review_status == "pending"),
        "rdec_signals": len(signals),
        "pending_signals": sum(1 for signal in signals if signal.human_review_status == "pending"),
        "latest_runs": runs,
    }


def split_terms(value: str) -> list[str]:
    raw_terms = re.split(r"[,;\n]+", value or "")
    terms = []
    for term in raw_terms:
        cleaned = re.sub(r"\s+", " ", term).strip().lower()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    return terms


def watch_profile_terms(session: Session, profile: CustomerWatchProfile) -> list[str]:
    terms: list[str] = []
    customer = session.get(Customer, profile.customer_id) if profile.customer_id else None
    business_unit = session.get(BusinessUnit, profile.business_unit_id) if profile.business_unit_id else None
    for value in [
        profile.profile_name,
        profile.buyer_aliases,
        profile.keywords,
        profile.domains,
        customer.customer_name if customer else "",
        customer.transport_domain if customer else "",
        business_unit.name if business_unit else "",
    ]:
        for term in split_terms(value):
            if term not in terms:
                terms.append(term)
    return terms or ["transport"]


def source_query_url(source: FrameworkSource, terms: list[str], today: date | None = None) -> str:
    today = today or date.today()
    query = " ".join(terms[:6])
    url = source.query_url
    replacements = {
        "{query}": quote_plus(query),
        "{published_from}": (today - timedelta(days=30)).isoformat(),
        "{published_to}": today.isoformat(),
    }
    for token, replacement in replacements.items():
        url = url.replace(token, replacement)
    return url


def fetch_source_url(url: str) -> FetchResult:
    if not official_framework_source_allowed(url):
        return FetchResult(
            ok=False,
            status_code=0,
            url=url,
            text="",
            error="Blocked by official-source allow-list.",
        )
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            response = client.get(url, headers={"User-Agent": "RDEC Eligibility Hub local intelligence agent"})
        final_url = str(response.url)
        if not official_framework_source_allowed(final_url):
            return FetchResult(
                ok=False,
                status_code=response.status_code,
                url=final_url,
                text="",
                content_type=response.headers.get("content-type", ""),
                error="Blocked because the source redirected outside the official-source allow-list.",
            )
        return FetchResult(
            ok=response.status_code < 400,
            status_code=response.status_code,
            url=final_url,
            text=response.text or "",
            content_type=response.headers.get("content-type", ""),
            error="" if response.status_code < 400 else f"Source returned HTTP {response.status_code}.",
        )
    except httpx.HTTPError as exc:
        return FetchResult(ok=False, status_code=0, url=url, text="", error=f"Network source check failed: {exc}")


def parse_dateish(value: object) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def numberish(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def textish(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def strip_html(value: str) -> str:
    cleaned = re.sub(r"<script.*?</script>", " ", value or "", flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return textish(unescape(cleaned))


def iter_ocds_releases(payload: object):
    if isinstance(payload, list):
        for item in payload:
            yield from iter_ocds_releases(item)
        return
    if not isinstance(payload, dict):
        return
    releases = payload.get("releases")
    if isinstance(releases, list):
        for release in releases:
            if isinstance(release, dict):
                yield release
    for key in ["packages", "records", "results", "data"]:
        values = payload.get(key)
        if isinstance(values, list):
            for item in values:
                yield from iter_ocds_releases(item)
    compiled = payload.get("compiledRelease")
    if isinstance(compiled, dict):
        yield compiled


def candidate_hash(source: FrameworkSource, title: str, notice_identifier: str, summary: str) -> str:
    material = f"{source.id}|{notice_identifier}|{title}|{summary}"
    return sha256(material.encode("utf-8", errors="ignore")).hexdigest()


def candidate_from_release(release: dict, source: FrameworkSource) -> CandidateOpportunity | None:
    tender = release.get("tender") if isinstance(release.get("tender"), dict) else {}
    planning = release.get("planning") if isinstance(release.get("planning"), dict) else {}
    buyer = release.get("buyer") if isinstance(release.get("buyer"), dict) else {}
    value = tender.get("value") if isinstance(tender.get("value"), dict) else {}
    tender_period = tender.get("tenderPeriod") if isinstance(tender.get("tenderPeriod"), dict) else {}
    title = textish(tender.get("title") or planning.get("rationale") or release.get("id") or release.get("ocid"))
    if not title:
        return None
    description = textish(tender.get("description") or planning.get("rationale") or "")
    items = tender.get("items") if isinstance(tender.get("items"), list) else []
    cpv_codes: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        classification = item.get("classification") if isinstance(item.get("classification"), dict) else {}
        code = textish(classification.get("id"))
        label = textish(classification.get("description"))
        if code or label:
            cpv_codes.append(" - ".join(part for part in [code, label] if part))
    tags = release.get("tag") if isinstance(release.get("tag"), list) else []
    links = release.get("links") if isinstance(release.get("links"), dict) else {}
    source_url = textish(links.get("self") or release.get("url") or source.base_url)
    identifier = textish(release.get("id") or release.get("ocid") or title)
    return CandidateOpportunity(
        title=title[:250],
        buyer_name=textish(buyer.get("name")),
        notice_identifier=identifier[:250],
        ocid=textish(release.get("ocid")),
        notice_type=", ".join(textish(tag) for tag in tags if textish(tag))[:250],
        procurement_stage=textish(tender.get("status") or (tags[0] if tags else ""))[:120],
        published_date=parse_dateish(release.get("date")),
        deadline_date=parse_dateish(tender_period.get("endDate")),
        value_high=numberish(value.get("amount")),
        currency=textish(value.get("currency") or "GBP")[:12],
        cpv_codes="; ".join(cpv_codes)[:500],
        source_url=source_url,
        summary=description[:2000],
        content_hash=candidate_hash(source, title, identifier, description),
    )


def parse_ocds_candidates(text: str, source: FrameworkSource) -> list[CandidateOpportunity]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    candidates = []
    seen: set[str] = set()
    for release in iter_ocds_releases(payload):
        candidate = candidate_from_release(release, source)
        if not candidate:
            continue
        key = candidate.notice_identifier or candidate.content_hash
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def parse_web_candidates(text: str, source: FrameworkSource, terms: list[str]) -> list[CandidateOpportunity]:
    candidates: list[CandidateOpportunity] = []
    for match in re.finditer(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", text or "", re.I | re.S):
        href, label_html = match.groups()
        title = strip_html(label_html)
        if not title or len(title) < 6:
            continue
        absolute_url = urljoin(source.base_url, unescape(href))
        if not official_framework_source_allowed(absolute_url):
            continue
        combined = f"{title} {absolute_url}".lower()
        if not any(term in combined for term in terms) and "/notice" not in absolute_url.lower():
            continue
        identifier = absolute_url.rstrip("/").rsplit("/", 1)[-1]
        summary = title
        candidates.append(
            CandidateOpportunity(
                title=title[:250],
                notice_identifier=identifier[:250],
                source_url=absolute_url,
                summary=summary,
                content_hash=candidate_hash(source, title, identifier, summary),
            )
        )
    return candidates


def relevance_for_candidate(candidate: CandidateOpportunity, profile: CustomerWatchProfile, terms: list[str]) -> tuple[float, str]:
    combined = " ".join(
        [
            candidate.title,
            candidate.buyer_name,
            candidate.summary,
            candidate.cpv_codes,
            candidate.notice_type,
            profile.cpv_codes,
        ]
    ).lower()
    matched = [term for term in terms if term and term in combined]
    if not matched:
        return 0, "No watch-profile terms matched the notice text."
    score = min(100, 20 + (len(matched) * 12))
    if profile.customer_id and candidate.buyer_name and any(term in candidate.buyer_name.lower() for term in matched):
        score = min(100, score + 15)
    if candidate.value_high and profile.minimum_value and candidate.value_high < profile.minimum_value:
        score = max(0, score - 30)
    rationale = f"Matched watch terms: {', '.join(matched[:8])}."
    return float(score), rationale


def upsert_opportunity(
    session: Session,
    source: FrameworkSource,
    profile: CustomerWatchProfile,
    candidate: CandidateOpportunity,
    relevance_score: float,
    rationale: str,
) -> tuple[FrameworkOpportunity, bool]:
    existing = None
    if candidate.notice_identifier:
        existing = session.exec(
            select(FrameworkOpportunity).where(
                FrameworkOpportunity.source_id == source.id,
                FrameworkOpportunity.notice_identifier == candidate.notice_identifier,
            )
        ).first()
    if not existing and candidate.content_hash:
        existing = session.exec(
            select(FrameworkOpportunity).where(FrameworkOpportunity.content_hash == candidate.content_hash)
        ).first()

    created = existing is None
    opportunity = existing or FrameworkOpportunity(
        source_id=source.id,
        customer_id=profile.customer_id,
        business_unit_id=profile.business_unit_id,
        title=candidate.title,
    )
    before_snapshot = compact_snapshot(opportunity) if existing else ""
    opportunity.source_id = source.id
    opportunity.customer_id = profile.customer_id
    opportunity.business_unit_id = profile.business_unit_id
    opportunity.title = candidate.title
    opportunity.buyer_name = candidate.buyer_name
    opportunity.notice_identifier = candidate.notice_identifier
    opportunity.ocid = candidate.ocid
    opportunity.notice_type = candidate.notice_type
    opportunity.procurement_stage = candidate.procurement_stage
    opportunity.published_date = candidate.published_date
    opportunity.deadline_date = candidate.deadline_date
    opportunity.value_low = candidate.value_low
    opportunity.value_high = candidate.value_high
    opportunity.currency = candidate.currency or "GBP"
    opportunity.cpv_codes = candidate.cpv_codes
    opportunity.location = candidate.location
    opportunity.source_url = candidate.source_url
    opportunity.summary = candidate.summary
    opportunity.relevance_score = relevance_score
    opportunity.relevance_rationale = rationale
    opportunity.content_hash = candidate.content_hash
    opportunity.updated_at = datetime.now(UTC)
    session.add(opportunity)
    session.flush()
    log_event(
        session,
        entity_type="FrameworkOpportunity",
        entity_id=opportunity.id,
        action="create" if created else "update",
        summary=f"{'Created' if created else 'Updated'} framework opportunity {opportunity.title}",
        before=before_snapshot,
        after=opportunity,
    )
    if created and opportunity.source_url:
        document = OpportunityDocument(
            opportunity_id=opportunity.id or 0,
            title=f"Notice source for {opportunity.title[:120]}",
            document_type="notice",
            url_or_path=opportunity.source_url,
            source_hash=opportunity.content_hash,
        )
        session.add(document)
        session.flush()
        log_event(
            session,
            entity_type="OpportunityDocument",
            entity_id=document.id,
            action="create",
            summary=f"Created source document link for opportunity {opportunity.id}",
            after=document,
        )
    return opportunity, created


def requirement_themes_for_text(text: str) -> list[str]:
    lower = f" {text.lower()} "
    themes = []
    for theme, patterns in REQUIREMENT_PATTERNS.items():
        if any(pattern in lower for pattern in patterns):
            themes.append(theme)
    return themes


def create_requirement_and_signal(
    session: Session,
    opportunity: FrameworkOpportunity,
    theme: str,
    source_text: str,
) -> tuple[bool, bool]:
    existing = session.exec(
        select(ExtractedRequirement).where(
            ExtractedRequirement.opportunity_id == opportunity.id,
            ExtractedRequirement.requirement_theme == theme,
        )
    ).first()
    requirement_created = False
    if existing:
        requirement = existing
    else:
        rdec_note = (
            "Potential R&D candidate context only. Use this to prompt evidence capture if delivery later involves "
            "a scientific or technological advance and uncertainty."
        )
        requirement = ExtractedRequirement(
            opportunity_id=opportunity.id or 0,
            requirement_theme=theme,
            requirement_text=source_text[:1200],
            requirement_source=opportunity.source_url,
            confidence="medium",
            rdec_relevance_note=rdec_note,
        )
        session.add(requirement)
        session.flush()
        requirement_created = True
        log_event(
            session,
            entity_type="ExtractedRequirement",
            entity_id=requirement.id,
            action="create",
            summary=f"Created extracted requirement '{theme}' for opportunity {opportunity.id}",
            after=requirement,
        )

    signal_created = False
    if theme in RDEC_SIGNAL_THEMES:
        existing_signal = session.exec(
            select(RDECOpportunitySignal).where(
                RDECOpportunitySignal.opportunity_id == opportunity.id,
                RDECOpportunitySignal.requirement_id == requirement.id,
            )
        ).first()
        if not existing_signal:
            signal = RDECOpportunitySignal(
                opportunity_id=opportunity.id or 0,
                requirement_id=requirement.id,
                signal_strength="review",
                signal_text=(
                    f"{theme} requirement may create R&D candidate indicators if Telent / M Group must resolve "
                    "scientific or technological uncertainty beyond routine implementation."
                ),
                recommended_action=(
                    "During bid/no-bid and delivery mobilisation, capture baseline capability, anticipated uncertainties, "
                    "contract intent, evidence sources, and cost-capture ownership for Finance and Ayming review."
                ),
                caveat=CAVEAT,
            )
            session.add(signal)
            session.flush()
            signal_created = True
            log_event(
                session,
                entity_type="RDECOpportunitySignal",
                entity_id=signal.id,
                action="create",
                summary=f"Created RDEC opportunity signal for opportunity {opportunity.id}",
                after=signal,
            )
    return requirement_created, signal_created


def extract_requirements_for_opportunity(session: Session, opportunity: FrameworkOpportunity) -> tuple[int, int]:
    source_text = textish(f"{opportunity.title}. {opportunity.summary}. {opportunity.cpv_codes}")
    themes = requirement_themes_for_text(source_text)
    if not themes:
        themes = ["general framework fit"]
    requirement_count = 0
    signal_count = 0
    for theme in themes:
        requirement_created, signal_created = create_requirement_and_signal(session, opportunity, theme, source_text)
        if requirement_created:
            requirement_count += 1
        if signal_created:
            signal_count += 1
    return requirement_count, signal_count


def candidates_from_fetch(fetch: FetchResult, source: FrameworkSource, terms: list[str]) -> list[CandidateOpportunity]:
    if not fetch.ok:
        return []
    if source.source_type == "ocds_api" or "json" in fetch.content_type.lower() or fetch.text.strip().startswith("{"):
        candidates = parse_ocds_candidates(fetch.text, source)
        if candidates:
            return candidates
    return parse_web_candidates(fetch.text, source, terms)


def run_framework_agent_for_profile(
    session: Session,
    profile_id: int,
    fetcher=fetch_source_url,
    today: date | None = None,
) -> FrameworkAgentRun:
    profile = session.get(CustomerWatchProfile, profile_id)
    if not profile:
        raise ValueError(f"Watch profile {profile_id} not found")
    terms = watch_profile_terms(session, profile)
    run = FrameworkAgentRun(
        watch_profile_id=profile.id,
        run_type="manual",
        status="started",
        guardrail_summary=(
            "Manual local run; official/public source allow-list enforced; no automatic bid, claim, tax, or rule decisions."
        ),
    )
    session.add(run)
    session.flush()
    log_event(
        session,
        entity_type="FrameworkAgentRun",
        entity_id=run.id,
        action="create",
        summary=f"Started framework intelligence run for {profile.profile_name}",
        after=run,
    )
    sources = list(
        session.exec(
            select(FrameworkSource)
            .where(FrameworkSource.active == True)  # noqa: E712
            .order_by(col(FrameworkSource.name))
        )
    )
    errors: list[str] = []
    opportunities_found = 0
    requirements_extracted = 0
    signals_created = 0
    for source in sources:
        run.sources_checked += 1
        url = source_query_url(source, terms, today=today)
        if not official_framework_source_allowed(url):
            source.last_status = "blocked by official-source allow-list"
            errors.append(f"{source.name}: blocked by official-source allow-list")
            continue
        fetch = fetcher(url)
        source.last_checked_at = datetime.now(UTC)
        source.last_status = f"HTTP {fetch.status_code}" if fetch.ok else (fetch.error or "failed")
        session.add(source)
        if not fetch.ok:
            errors.append(f"{source.name}: {source.last_status}")
            continue
        for candidate in candidates_from_fetch(fetch, source, terms):
            relevance_score, rationale = relevance_for_candidate(candidate, profile, terms)
            if relevance_score <= 0:
                continue
            opportunity, created = upsert_opportunity(session, source, profile, candidate, relevance_score, rationale)
            if created:
                opportunities_found += 1
            req_count, signal_count = extract_requirements_for_opportunity(session, opportunity)
            requirements_extracted += req_count
            signals_created += signal_count

    before_snapshot = compact_snapshot(run)
    run.opportunities_found = opportunities_found
    run.requirements_extracted = requirements_extracted
    run.signals_created = signals_created
    run.error_summary = "; ".join(errors)
    run.status = "completed_with_warnings" if errors else "completed"
    run.finished_at = datetime.now(UTC)
    session.add(run)
    session.flush()
    log_event(
        session,
        entity_type="FrameworkAgentRun",
        entity_id=run.id,
        action="update",
        summary=f"Completed framework intelligence run for {profile.profile_name}",
        before=before_snapshot,
        after=run,
    )
    session.commit()
    session.refresh(run)
    return run


def generate_framework_intelligence_report_markdown(
    session: Session,
    report_name: str = "Framework intelligence summary",
    customer_id: int | None = None,
    business_unit_id: int | None = None,
) -> str:
    opportunity_query = select(FrameworkOpportunity).order_by(col(FrameworkOpportunity.updated_at).desc())
    profile_query = select(CustomerWatchProfile).order_by(col(CustomerWatchProfile.profile_name))
    if customer_id:
        opportunity_query = opportunity_query.where(FrameworkOpportunity.customer_id == customer_id)
        profile_query = profile_query.where(CustomerWatchProfile.customer_id == customer_id)
    if business_unit_id:
        opportunity_query = opportunity_query.where(FrameworkOpportunity.business_unit_id == business_unit_id)
        profile_query = profile_query.where(CustomerWatchProfile.business_unit_id == business_unit_id)

    opportunities = list(session.exec(opportunity_query.limit(50)))
    profiles = list(session.exec(profile_query))
    sources = list(session.exec(select(FrameworkSource).order_by(col(FrameworkSource.name))))
    opportunity_ids = [opportunity.id for opportunity in opportunities if opportunity.id]
    requirements: list[ExtractedRequirement] = []
    signals: list[RDECOpportunitySignal] = []
    if opportunity_ids:
        requirements = list(
            session.exec(select(ExtractedRequirement).where(ExtractedRequirement.opportunity_id.in_(opportunity_ids)))
        )
        signals = list(session.exec(select(RDECOpportunitySignal).where(RDECOpportunitySignal.opportunity_id.in_(opportunity_ids))))

    source_lines = [
        f"- {source.name}: {'active' if source.active else 'inactive'}; last status {source.last_status or 'not checked'}"
        for source in sources
    ] or ["- No sources configured."]
    profile_lines = [
        f"- {profile.profile_name}: keywords '{profile.keywords or 'not recorded'}', aliases '{profile.buyer_aliases or 'not recorded'}'"
        for profile in profiles
    ] or ["- No watch profiles configured."]
    opportunity_lines = [
        (
            f"- {opportunity.title} | buyer {opportunity.buyer_name or 'not detected'} | "
            f"stage {opportunity.procurement_stage or 'not detected'} | deadline {opportunity.deadline_date or 'not detected'} | "
            f"value {money(opportunity.value_high)} | status {opportunity.status} | relevance {opportunity.relevance_score:g}"
        )
        for opportunity in opportunities
    ] or ["- No matching opportunities captured yet."]
    requirement_lines = [
        f"- {requirement.requirement_theme}: {requirement.requirement_text[:220]} ({requirement.human_review_status})"
        for requirement in requirements
    ] or ["- No requirements extracted yet."]
    signal_lines = [
        f"- {signal.signal_strength}: {signal.signal_text} Action: {signal.recommended_action}"
        for signal in signals
    ] or ["- No RDEC candidate indicators extracted yet."]

    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    return f"""# {report_name}

**Generated at:** {generated_at}
**Decision-support caveat:** {FRAMEWORK_AGENT_CAVEAT}

## Purpose
This report consolidates public-sector framework and bid-opportunity intelligence for Telent / M Group. It is intended to help sales, engineering, Finance, and Ayming discuss likely customer requirements, early evidence-capture needs, and possible R&D candidate signals before and during delivery.

## Guardrails
- Uses configured official or public procurement sources only.
- Does not make autonomous bid/no-bid decisions.
- Does not make RDEC, legal, tax, accounting, procurement, or HMRC submission conclusions.
- Human review is required before relying on extracted requirements or R&D candidate indicators.
- {CAVEAT}

## Watch Profiles
{chr(10).join(profile_lines)}

## Sources
{chr(10).join(source_lines)}

## Captured Opportunities
{chr(10).join(opportunity_lines)}

## Consolidated Requirement Themes
{chr(10).join(requirement_lines)}

## RDEC Candidate Intelligence
{chr(10).join(signal_lines)}

## Finance And Ayming Use
- Use the captured opportunity and requirement themes to plan project codes, people-time capture, evidence ownership, and contract/SOW fact capture early.
- Use the RDEC candidate indicators only as prompts for technical assessment, competent professional opinion, entitlement review, and cost evidence planning.
- Qualifying expenditure and relief value are not calculated by this framework intelligence agent.
"""


def create_intelligence_report(
    session: Session,
    report_name: str,
    report_type: str = "framework_summary",
    customer_id: int | None = None,
    business_unit_id: int | None = None,
) -> IntelligenceReport:
    markdown = generate_framework_intelligence_report_markdown(
        session,
        report_name=report_name,
        customer_id=customer_id,
        business_unit_id=business_unit_id,
    )
    report = IntelligenceReport(
        report_name=report_name,
        report_type=report_type,
        customer_id=customer_id,
        business_unit_id=business_unit_id,
        markdown=markdown,
        caveat=CAVEAT,
    )
    session.add(report)
    session.flush()
    log_event(
        session,
        entity_type="IntelligenceReport",
        entity_id=report.id,
        action="create",
        summary=f"Created framework intelligence report {report.report_name}",
        after=report,
    )
    session.commit()
    session.refresh(report)
    return report
