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
    BuyerPortalInstance,
    BusinessUnit,
    Customer,
    CustomerWatchProfile,
    ExtractedQualityQuestion,
    ExtractedRequirement,
    FrameworkAgentRun,
    FrameworkOpportunity,
    FrameworkSource,
    IntelligenceReport,
    OpportunityDocument,
    PortalRetrievalRun,
    ProcurementPlatform,
    RDECOpportunitySignal,
    SourceCheckSnapshot,
)
from app.rule_loader import load_rule_file
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

EVIDENCE_CAPTURE_PROMPTS: dict[str, str] = {
    "asset management": "Capture lifecycle assumptions, degradation data, asset-condition evidence, and decision records for any non-routine technical uncertainty.",
    "cyber security": "Capture threat-model changes, security test results, failed mitigations, and competent professional rationale for uncertainty beyond routine hardening.",
    "data and analytics": "Capture model assumptions, data-quality constraints, validation results, and why known analytics patterns were insufficient.",
    "high availability and resilience": "Capture resilience targets, failure-mode experiments, recovery tests, and evidence of uncertainty beyond standard HA design.",
    "legacy integration": "Capture interface constraints, failed integration approaches, protocol evidence, and why known adapters or patterns were insufficient.",
    "operational technology / SCADA": "Capture OT constraints, telemetry behaviour, test-rig evidence, safety limitations, and competent professional review notes.",
    "real-time operation": "Capture latency targets, live-data constraints, load tests, timing failures, and boundary evidence for experimental work.",
    "software development": "Capture prototypes, design alternatives, test results, and evidence separating R&D candidate work from routine build activity.",
    "telecommunications and networks": "Capture network constraints, lab/test evidence, failed routing or capacity approaches, and field-trial boundaries.",
    "transport operations": "Capture operational constraints, scenario testing, disruption evidence, and human review of whether uncertainty is technological.",
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


def framework_source_rules() -> dict:
    return load_rule_file("framework_sources.yml")


def procurement_platform_rules() -> dict:
    return load_rule_file("procurement_platforms.yml")


def source_change_rules() -> dict:
    return load_rule_file("source_change_tracking.yml")


def evidence_capture_prompt(theme: str) -> str:
    normalized = (theme or "").strip().lower()
    return EVIDENCE_CAPTURE_PROMPTS.get(
        normalized,
        "Capture source documents, technical assumptions, failed approaches, review notes, and cost/evidence links before treating this as an R&D candidate.",
    )


def latest_snapshot_by_source(snapshots: list[SourceCheckSnapshot]) -> dict[int, SourceCheckSnapshot]:
    latest: dict[int, SourceCheckSnapshot] = {}
    for snapshot in snapshots:
        if snapshot.source_id and snapshot.source_id not in latest:
            latest[snapshot.source_id] = snapshot
    return latest


def framework_source_catalogue() -> list[dict]:
    return list(framework_source_rules().get("sources") or [])


def procurement_platform_catalogue() -> list[dict]:
    return list(procurement_platform_rules().get("platforms") or [])


def configured_framework_source_types() -> list[str]:
    return list(framework_source_rules().get("source_types") or ["ocds_api", "web_page"])


def approved_framework_domains() -> set[str]:
    guardrails = framework_source_rules().get("guardrails") or {}
    configured = {str(domain).lower() for domain in guardrails.get("approved_domains") or []}
    return OFFICIAL_FRAMEWORK_DOMAINS | configured


def official_framework_source_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc.lower() in approved_framework_domains()


def seed_framework_sources(session: Session) -> None:
    existing_names = {source.name for source in session.exec(select(FrameworkSource))}
    for item in framework_source_catalogue():
        if item["name"] in existing_names:
            continue
        source_metadata = {
            "config_key": item.get("key", ""),
            "config_version": framework_source_rules().get("version", "unknown"),
            "source_metadata": framework_source_rules().get("source_metadata", {}),
        }
        session.add(
            FrameworkSource(
                name=item["name"],
                source_type=item.get("source_type", "ocds_api"),
                source_family=item.get("source_family", "official_notice"),
                base_url=item["base_url"],
                query_url=item["query_url"],
                official=bool(item.get("official", True)),
                active=bool(item.get("active", True)),
                review_frequency=item.get("review_frequency", "manual"),
                coverage=item.get("coverage", ""),
                auth_model=item.get("auth_model", "none"),
                data_format=item.get("data_format", ""),
                dedupe_strategy=item.get("dedupe_strategy", "ocid_or_reference"),
                change_tracking_enabled=bool(item.get("change_tracking_enabled", True)),
                requires_human_approval=bool(item.get("requires_human_approval", False)),
                connector_status=item.get("connector_status", "configured"),
                source_metadata=json.dumps(source_metadata, separators=(",", ":")),
                notes=item["notes"],
            )
        )
    existing_platform_names = {platform.name for platform in session.exec(select(ProcurementPlatform))}
    for item in procurement_platform_catalogue():
        if item["name"] in existing_platform_names:
            continue
        session.add(
            ProcurementPlatform(
                name=item["name"],
                platform_type=item.get("platform_type", "buyer_portal"),
                login_model=item.get("login_model", "supplier_account"),
                supported_actions="; ".join(item.get("supported_actions") or []),
                requires_credentials=bool(item.get("requires_credentials", True)),
                human_approval_required=bool(item.get("human_approval_required", True)),
                active=bool(item.get("active", True)),
                connector_status=item.get("connector_status", "manual_assisted"),
                platform_domains=item.get("platform_domains", ""),
                notes=item.get("notes", ""),
            )
        )
    session.commit()


def framework_intelligence_metrics(session: Session) -> dict:
    profiles = list(session.exec(select(CustomerWatchProfile)))
    opportunities = list(session.exec(select(FrameworkOpportunity)))
    requirements = list(session.exec(select(ExtractedRequirement)))
    signals = list(session.exec(select(RDECOpportunitySignal)))
    platforms = list(session.exec(select(ProcurementPlatform)))
    snapshots = list(session.exec(select(SourceCheckSnapshot).order_by(col(SourceCheckSnapshot.checked_at).desc()).limit(100)))
    documents = list(session.exec(select(OpportunityDocument)))
    quality_questions = list(session.exec(select(ExtractedQualityQuestion)))
    runs = list(session.exec(select(FrameworkAgentRun).order_by(col(FrameworkAgentRun.started_at).desc()).limit(5)))
    return {
        "active_profiles": sum(1 for profile in profiles if profile.active),
        "active_sources": len(list(session.exec(select(FrameworkSource).where(FrameworkSource.active == True)))),  # noqa: E712
        "platform_count": len(platforms),
        "active_platforms": sum(1 for platform in platforms if platform.active),
        "opportunity_count": len(opportunities),
        "new_opportunities": sum(1 for opportunity in opportunities if opportunity.status == "new"),
        "pending_requirements": sum(1 for requirement in requirements if requirement.human_review_status == "pending"),
        "rdec_signals": len(signals),
        "pending_signals": sum(1 for signal in signals if signal.human_review_status == "pending"),
        "document_count": len(documents),
        "quality_questions": len(quality_questions),
        "source_changes": sum(1 for snapshot in snapshots if snapshot.change_type == "changed"),
        "source_warnings": sum(1 for snapshot in snapshots if snapshot.change_type == "failed"),
        "latest_runs": runs,
    }


def opportunity_rdec_review_summary(
    opportunity: FrameworkOpportunity,
    signals: list[RDECOpportunitySignal],
    requirements: list[ExtractedRequirement],
    documents: list[OpportunityDocument],
) -> dict:
    pending_signals = [signal for signal in signals if signal.human_review_status == "pending"]
    accepted_signals = [signal for signal in signals if signal.human_review_status == "accepted"]
    strong_signals = [
        signal for signal in signals if "strong" in (signal.signal_strength or "").lower()
    ]
    rdec_theme_requirements = [
        requirement for requirement in requirements if requirement.requirement_theme in RDEC_SIGNAL_THEMES
    ]
    score = float(opportunity.relevance_score or 0)

    if accepted_signals or strong_signals or (score >= 70 and pending_signals):
        classification = "strong indicators"
        badge_class = "green"
        priority = 3
        review_action = (
            "Prioritise competent professional triage, source-document capture, entitlement facts, "
            "and Finance/Ayming evidence planning."
        )
    elif pending_signals or rdec_theme_requirements or score >= 45:
        classification = "review required"
        badge_class = "amber"
        priority = 2
        review_action = (
            "Review the extracted requirement themes, confirm whether technical uncertainty exists, "
            "and capture evidence ownership before treating this as an R&D candidate."
        )
    else:
        classification = "triage only"
        badge_class = "weak"
        priority = 1
        review_action = (
            "Keep in the opportunity catalogue. No current R&D candidate signal has been extracted; "
            "reassess if source documents introduce non-routine technical uncertainty."
        )

    if not documents:
        evidence_gap = "No opportunity documents linked yet."
    elif pending_signals:
        evidence_gap = "RDEC candidate signal review is pending."
    elif any(document.human_review_status == "pending" for document in documents):
        evidence_gap = "Document review is pending."
    else:
        evidence_gap = "Initial document links captured."

    first_signal = pending_signals[0] if pending_signals else signals[0] if signals else None
    themes = sorted({requirement.requirement_theme for requirement in requirements if requirement.requirement_theme})
    return {
        "opportunity": opportunity,
        "classification": classification,
        "badge_class": badge_class,
        "priority": priority,
        "pending_signal_count": len(pending_signals),
        "accepted_signal_count": len(accepted_signals),
        "signal_count": len(signals),
        "requirement_count": len(requirements),
        "document_count": len(documents),
        "themes": themes,
        "review_action": review_action,
        "evidence_gap": evidence_gap,
        "signal_review_url": (
            f"/framework-intelligence/requirements#signal-{first_signal.id}" if first_signal and first_signal.id else
            "/framework-intelligence/requirements#rdec-signal-queue"
        ),
        "document_url": f"/framework-intelligence/opportunities/{opportunity.id}/documents" if opportunity.id else "",
        "requires_review": "Requires competent professional and tax review.",
    }


def framework_opportunity_review_context(session: Session, limit: int = 8) -> dict:
    opportunities = list(
        session.exec(select(FrameworkOpportunity).order_by(col(FrameworkOpportunity.updated_at).desc()).limit(200))
    )
    signals_by_opportunity: dict[int, list[RDECOpportunitySignal]] = {}
    for signal in session.exec(select(RDECOpportunitySignal)):
        signals_by_opportunity.setdefault(signal.opportunity_id, []).append(signal)
    requirements_by_opportunity: dict[int, list[ExtractedRequirement]] = {}
    for requirement in session.exec(select(ExtractedRequirement)):
        requirements_by_opportunity.setdefault(requirement.opportunity_id, []).append(requirement)
    documents_by_opportunity: dict[int, list[OpportunityDocument]] = {}
    for document in session.exec(select(OpportunityDocument)):
        documents_by_opportunity.setdefault(document.opportunity_id, []).append(document)

    summaries = []
    for opportunity in opportunities:
        if not opportunity.id:
            continue
        summary = opportunity_rdec_review_summary(
            opportunity,
            signals_by_opportunity.get(opportunity.id, []),
            requirements_by_opportunity.get(opportunity.id, []),
            documents_by_opportunity.get(opportunity.id, []),
        )
        summaries.append(summary)

    summaries.sort(
        key=lambda item: (
            item["priority"],
            item["pending_signal_count"],
            float(item["opportunity"].relevance_score or 0),
            item["opportunity"].updated_at,
        ),
        reverse=True,
    )
    action_queue = [item for item in summaries if item["priority"] >= 2]
    return {
        "items": action_queue[:limit],
        "total": len(action_queue),
        "triaged_out": sum(1 for item in summaries if item["priority"] == 1),
        "by_opportunity_id": {item["opportunity"].id: item for item in summaries if item["opportunity"].id},
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


def content_hash_for_text(text: str) -> str:
    return sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def detect_source_schema(text: str, content_type: str = "") -> str:
    sample = (text or "")[:5000]
    lowered = sample.lower()
    rules = source_change_rules().get("schema_detection") or {}
    if "json" in content_type.lower() or any(str(marker).lower() in lowered for marker in rules.get("ocds_markers") or []):
        return "ocds_json"
    if "xml" in content_type.lower() or any(str(marker).lower() in lowered for marker in rules.get("eforms_markers") or []):
        return "eforms_or_xml"
    if "<html" in lowered or "<a " in lowered:
        return "html"
    return "unknown"


def record_source_check_snapshot(
    session: Session,
    source: FrameworkSource,
    fetch: FetchResult,
    query_url: str,
) -> SourceCheckSnapshot:
    latest = session.exec(
        select(SourceCheckSnapshot)
        .where(SourceCheckSnapshot.source_id == source.id)
        .order_by(col(SourceCheckSnapshot.checked_at).desc())
    ).first()
    current_hash = content_hash_for_text(fetch.text) if fetch.ok else ""
    previous_hash = latest.content_hash if latest else ""
    if not fetch.ok:
        change_type = "failed"
    elif not latest:
        change_type = "first_seen"
    elif previous_hash != current_hash:
        change_type = "changed"
    else:
        change_type = "unchanged"
    snapshot = SourceCheckSnapshot(
        source_id=source.id,
        query_url=query_url,
        status_code=fetch.status_code,
        ok=fetch.ok,
        content_hash=current_hash,
        previous_hash=previous_hash,
        change_type=change_type,
        detected_schema=detect_source_schema(fetch.text, fetch.content_type),
        connector_status=source.connector_status,
        notes=fetch.error or source.last_status,
    )
    session.add(snapshot)
    session.flush()
    log_event(
        session,
        entity_type="SourceCheckSnapshot",
        entity_id=snapshot.id,
        action="create",
        summary=f"Recorded {change_type} source snapshot for {source.name}",
        after=snapshot,
    )
    return snapshot


def run_single_source_check(
    session: Session,
    source_id: int,
    fetcher=fetch_source_url,
    today: date | None = None,
) -> SourceCheckSnapshot:
    source = session.get(FrameworkSource, source_id)
    if not source:
        raise ValueError(f"Framework source {source_id} not found")
    url = source_query_url(source, ["transport", "technology", "framework"], today=today)
    if not official_framework_source_allowed(url):
        fetch = FetchResult(ok=False, status_code=0, url=url, text="", error="Blocked by approved-source allow-list.")
    else:
        fetch = fetcher(url)
    before_snapshot = compact_snapshot(source)
    source.last_checked_at = datetime.now(UTC)
    source.last_status = f"HTTP {fetch.status_code}" if fetch.ok else (fetch.error or "failed")
    source.connector_status = "checked" if fetch.ok else "warning"
    session.add(source)
    snapshot = record_source_check_snapshot(session, source, fetch, url)
    log_event(
        session,
        entity_type="FrameworkSource",
        entity_id=source.id,
        action="update",
        summary=f"Checked framework source {source.name}",
        before=before_snapshot,
        after=source,
    )
    session.commit()
    session.refresh(snapshot)
    return snapshot


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


def extract_quality_questions_from_text(text: str) -> list[tuple[str, str]]:
    questions: list[tuple[str, str]] = []
    for raw_line in re.split(r"[\n\r]+", text or ""):
        line = textish(raw_line)
        if len(line) < 12:
            continue
        lower = line.lower()
        looks_like_question = "?" in line or "quality question" in lower or lower.startswith(("q.", "q1", "q2", "q3"))
        has_weighting = "weight" in lower or "%" in line or "marks" in lower or "score" in lower
        if not (looks_like_question or has_weighting):
            continue
        weighting = ""
        weight_match = re.search(r"(\d{1,3}\s?%|\d{1,3}\s?marks?)", line, flags=re.I)
        if weight_match:
            weighting = weight_match.group(1)
        questions.append((line[:1200], weighting))
    return questions[:20]


def create_portal_retrieval_run(
    session: Session,
    opportunity_id: int,
    portal_instance_id: int | None = None,
    notes: str = "",
) -> PortalRetrievalRun:
    run = PortalRetrievalRun(
        opportunity_id=opportunity_id,
        portal_instance_id=portal_instance_id,
        run_type="manual",
        status="requested",
        guardrail_summary=(
            "Manual/on-demand retrieval task only. No credentials are stored; no portal action, expression of "
            "interest, submission, or client communication is automated by the MVP."
        ),
        notes=notes,
    )
    session.add(run)
    session.flush()
    log_event(
        session,
        entity_type="PortalRetrievalRun",
        entity_id=run.id,
        action="create",
        summary=f"Created portal retrieval task for opportunity {opportunity_id}",
        after=run,
    )
    session.commit()
    session.refresh(run)
    return run


def extract_document_intelligence(
    session: Session,
    opportunity: FrameworkOpportunity,
    document: OpportunityDocument,
    text: str,
) -> tuple[int, int, int]:
    source_text = textish(f"{document.title}. {document.content_summary}. {document.notes}. {text}")
    requirement_count = 0
    signal_count = 0
    for theme in requirement_themes_for_text(source_text):
        requirement_created, signal_created = create_requirement_and_signal(session, opportunity, theme, source_text)
        if requirement_created:
            requirement_count += 1
        if signal_created:
            signal_count += 1
    question_count = 0
    for question_text, weighting in extract_quality_questions_from_text(source_text):
        existing = session.exec(
            select(ExtractedQualityQuestion).where(
                ExtractedQualityQuestion.opportunity_id == opportunity.id,
                ExtractedQualityQuestion.question_text == question_text,
            )
        ).first()
        if existing:
            continue
        themes = requirement_themes_for_text(question_text)
        question = ExtractedQualityQuestion(
            opportunity_id=opportunity.id or 0,
            document_id=document.id,
            section_reference=document.document_type,
            question_text=question_text,
            weighting=weighting,
            requirement_theme=themes[0] if themes else "bid quality response",
            confidence="medium" if weighting else "low",
            rdec_relevance_note=(
                "Use this bid requirement as an early signal only. RDEC relevance depends on later technical "
                "advance, uncertainty, evidence and entitlement review."
            ),
        )
        session.add(question)
        session.flush()
        question_count += 1
        log_event(
            session,
            entity_type="ExtractedQualityQuestion",
            entity_id=question.id,
            action="create",
            summary=f"Extracted quality question for opportunity {opportunity.id}",
            after=question,
        )
    if question_count or requirement_count or signal_count:
        before_snapshot = compact_snapshot(document)
        document.retrieval_status = "review_required"
        document.human_review_status = "pending"
        session.add(document)
        log_event(
            session,
            entity_type="OpportunityDocument",
            entity_id=document.id,
            action="update",
            summary=f"Extracted document intelligence for opportunity {opportunity.id}",
            before=before_snapshot,
            after=document,
        )
    return requirement_count, signal_count, question_count


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
            record_source_check_snapshot(
                session,
                source,
                FetchResult(ok=False, status_code=0, url=url, text="", error=source.last_status),
                url,
            )
            errors.append(f"{source.name}: blocked by official-source allow-list")
            continue
        fetch = fetcher(url)
        source.last_checked_at = datetime.now(UTC)
        source.last_status = f"HTTP {fetch.status_code}" if fetch.ok else (fetch.error or "failed")
        session.add(source)
        record_source_check_snapshot(session, source, fetch, url)
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
    platforms = list(session.exec(select(ProcurementPlatform).order_by(col(ProcurementPlatform.name))))
    snapshots = list(session.exec(select(SourceCheckSnapshot).order_by(col(SourceCheckSnapshot.checked_at).desc()).limit(10)))
    latest_source_snapshots = latest_snapshot_by_source(snapshots)
    opportunity_ids = [opportunity.id for opportunity in opportunities if opportunity.id]
    requirements: list[ExtractedRequirement] = []
    signals: list[RDECOpportunitySignal] = []
    documents: list[OpportunityDocument] = []
    quality_questions: list[ExtractedQualityQuestion] = []
    if opportunity_ids:
        requirements = list(
            session.exec(select(ExtractedRequirement).where(ExtractedRequirement.opportunity_id.in_(opportunity_ids)))
        )
        signals = list(session.exec(select(RDECOpportunitySignal).where(RDECOpportunitySignal.opportunity_id.in_(opportunity_ids))))
        documents = list(session.exec(select(OpportunityDocument).where(OpportunityDocument.opportunity_id.in_(opportunity_ids))))
        quality_questions = list(
            session.exec(select(ExtractedQualityQuestion).where(ExtractedQualityQuestion.opportunity_id.in_(opportunity_ids)))
        )
    opportunity_by_id = {opportunity.id: opportunity for opportunity in opportunities if opportunity.id}
    requirement_by_id = {requirement.id: requirement for requirement in requirements if requirement.id}

    source_lines = [
        (
            f"- {source.name}: {'active' if source.active else 'inactive'}; family {source.source_family}; "
            f"format {source.data_format or source.source_type}; coverage {source.coverage or 'not recorded'}; "
            f"last status {source.last_status or 'not checked'}"
        )
        for source in sources
    ] or ["- No sources configured."]
    source_currency_lines = []
    for source in sources:
        snapshot = latest_source_snapshots.get(source.id or 0)
        if snapshot:
            source_currency_lines.append(
                f"- {source.name}: last checked {snapshot.checked_at.date().isoformat()}; "
                f"change {snapshot.change_type}; schema {snapshot.detected_schema or 'not detected'}; "
                f"connector {snapshot.connector_status or source.connector_status or 'not recorded'}."
            )
        else:
            source_currency_lines.append(
                f"- {source.name}: no source snapshot captured yet; manual review required before relying on currency."
            )
    if not source_currency_lines:
        source_currency_lines = ["- No source currency evidence captured yet."]
    platform_lines = [
        (
            f"- {platform.name}: {platform.connector_status}; actions {platform.supported_actions or 'not recorded'}; "
            f"human approval {'required' if platform.human_approval_required else 'not marked'}"
        )
        for platform in platforms
    ] or ["- No portal platforms configured."]
    source_change_lines = [
        (
            f"- {snapshot.checked_at.date()} source {snapshot.source_id}: {snapshot.change_type}; "
            f"schema {snapshot.detected_schema}; status {snapshot.status_code or snapshot.notes or 'not recorded'}"
        )
        for snapshot in snapshots
    ] or ["- No source-change snapshots captured yet."]
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
        (
            f"- {requirement.requirement_theme}: {requirement.requirement_text[:220]} "
            f"(confidence {requirement.confidence}; review {requirement.human_review_status}; "
            f"evidence prompt: {evidence_capture_prompt(requirement.requirement_theme)})"
        )
        for requirement in requirements
    ] or ["- No requirements extracted yet."]
    document_lines = [
        (
            f"- {document.title}: {document.document_type}; {document.retrieval_status}; "
            f"{document.url_or_path or 'no link'}"
        )
        for document in documents
    ] or ["- No opportunity documents captured yet."]
    quality_question_lines = [
        (
            f"- {question.requirement_theme}: {question.question_text[:220]} "
            f"weighting {question.weighting or 'not detected'} "
            f"(confidence {question.confidence}; review {question.human_review_status}; "
            f"evidence prompt: {evidence_capture_prompt(question.requirement_theme)})"
        )
        for question in quality_questions
    ] or ["- No ITT quality questions extracted yet."]
    signal_lines = []
    for signal in signals:
        opportunity = opportunity_by_id.get(signal.opportunity_id)
        requirement = requirement_by_id.get(signal.requirement_id) if signal.requirement_id else None
        theme = requirement.requirement_theme if requirement else signal.signal_type
        signal_lines.append(
            f"- {signal.signal_type}: opportunity '{opportunity.title if opportunity else 'not linked'}'; "
            f"theme '{theme}'; strength {signal.signal_strength}; review {signal.human_review_status}; "
            f"rationale: {signal.signal_text}; evidence prompt: {evidence_capture_prompt(theme)} "
            f"Action: {signal.recommended_action} {signal.caveat}"
        )
    if not signal_lines:
        signal_lines = ["- No RDEC candidate indicators extracted yet."]

    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    source_config_version = framework_source_rules().get("version", "unknown")
    platform_config_version = procurement_platform_rules().get("version", "unknown")
    return f"""# {report_name}

**Generated at:** {generated_at}
**Decision-support caveat:** {FRAMEWORK_AGENT_CAVEAT}
**Source config version:** {source_config_version}
**Portal platform config version:** {platform_config_version}

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

## Source Change Tracking
{chr(10).join(source_change_lines)}

## Source Currency And Review Status
{chr(10).join(source_currency_lines)}

## Buyer Portal Platforms
{chr(10).join(platform_lines)}

## Captured Opportunities
{chr(10).join(opportunity_lines)}

## Opportunity Documents
{chr(10).join(document_lines)}

## Consolidated Requirement Themes
{chr(10).join(requirement_lines)}

## ITT Quality Questions And Weightings
{chr(10).join(quality_question_lines)}

## RDEC Candidate Intelligence
{chr(10).join(signal_lines)}

## Suggested Evidence Capture Prompts
- Capture source documents, SOW/contract facts, quality-question extracts, technical assumptions, failed approaches, people-time records, cost evidence, and competent professional review notes for each R&D candidate indicator.
- Treat every signal as pending competent professional and tax review until project-specific evidence, entitlement facts, and cost apportionment are reviewed.
- Use accepted human-review signals to seed R&D candidate projects only after delivery teams identify a specific scientific or technological uncertainty.

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
