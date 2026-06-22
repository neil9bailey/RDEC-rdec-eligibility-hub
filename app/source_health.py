from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, col, select

from app.framework_intelligence import latest_snapshot_by_source, source_readiness_for_source
from app.knowledge_agent import knowledge_agent_summary, knowledge_review_actions
from app.models import FrameworkSource, SourceCheckSnapshot


SOURCE_HEALTH_CAVEAT = (
    "Decision support only. This pack summarises local source-health evidence and does not change rules, "
    "submit external actions, or make bid, legal, tax, procurement, HMRC, or RDEC conclusions."
)


def _checked_at_text(value) -> str:
    if not value:
        return "not checked"
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)


def _snapshot_needs_attention(snapshot: SourceCheckSnapshot | None) -> bool:
    if snapshot is None:
        return True
    status_text = f"{snapshot.change_type} {snapshot.connector_status} {snapshot.notes}".lower()
    return (not snapshot.ok) or "failed" in status_text or "warning" in status_text or "blocked" in status_text


def source_health_triage_context(session: Session) -> dict:
    knowledge = knowledge_agent_summary(session)
    knowledge_actions = knowledge_review_actions(session)

    framework_sources = list(session.exec(select(FrameworkSource).order_by(col(FrameworkSource.name))))
    snapshots = list(session.exec(select(SourceCheckSnapshot).order_by(col(SourceCheckSnapshot.checked_at).desc()).limit(500)))
    latest_snapshots = latest_snapshot_by_source(snapshots)

    knowledge_rows = []
    for source in knowledge["sources"]:
        check = knowledge["latest_checks"].get(source.id)
        knowledge_rows.append(
            {
                "title": source.title,
                "url": source.url,
                "topic": source.topic,
                "priority": source.priority,
                "review_due_date": source.review_due_date,
                "status_label": f"HTTP {check.status_code}" if check and check.ok else "failed" if check else "not checked",
                "ok": bool(check and check.ok),
                "checked_at": _checked_at_text(check.checked_at if check else None),
                "update_marker": (check.detected_last_updated or check.last_modified_header) if check else "",
                "notes": check.notes if check else "No live check has been recorded.",
            }
        )

    framework_rows = []
    for source in framework_sources:
        snapshot = latest_snapshots.get(source.id or 0)
        readiness = source_readiness_for_source(source, snapshot)
        needs_attention = readiness["key"] != "live" or _snapshot_needs_attention(snapshot)
        framework_rows.append(
            {
                "source": source,
                "snapshot": snapshot,
                "readiness": readiness,
                "needs_attention": needs_attention,
                "status_label": (
                    f"HTTP {snapshot.status_code}" if snapshot and snapshot.ok else "failed" if snapshot else "not checked"
                ),
                "checked_at": _checked_at_text(snapshot.checked_at if snapshot else source.last_checked_at),
                "change_type": snapshot.change_type if snapshot else "not captured",
                "detected_schema": snapshot.detected_schema if snapshot else "",
                "notes": snapshot.notes if snapshot else source.last_status or "No source snapshot has been captured.",
            }
        )

    failed_framework_rows = [row for row in framework_rows if row["needs_attention"]]
    never_checked_framework_rows = [row for row in framework_rows if row["snapshot"] is None]
    never_checked_live_rows = [
        row for row in never_checked_framework_rows if row["source"].active and row["readiness"]["key"] == "live"
    ]
    inactive_or_approval_rows = [
        row for row in framework_rows if row["readiness"]["key"] in {"inactive_validation", "licence_required"}
    ]

    actions = list(knowledge_actions)
    if failed_framework_rows:
        actions.append(
            f"Review {len(failed_framework_rows)} Framework Intelligence source(s) with missing, failed, blocked, "
            "or approval-gated source-health evidence."
        )
    if never_checked_live_rows:
        actions.append(
            f"Run explicit guarded source checks for {len(never_checked_live_rows)} active Framework Intelligence "
            "source(s) before relying on procurement-source currency."
        )
    if inactive_or_approval_rows:
        actions.append(
            f"Keep {len(inactive_or_approval_rows)} inactive or approval-gated procurement source(s) out of active "
            "runs until endpoint behaviour, licence, or human approval is confirmed."
        )
    actions.append(
        "Do not auto-update YAML rules, source configuration, bids, portal actions, customer communications, "
        "or RDEC conclusions from this pack."
    )

    return {
        "generated_at": datetime.now(UTC),
        "caveat": SOURCE_HEALTH_CAVEAT,
        "actions": actions,
        "knowledge": {
            "source_count": knowledge["source_count"],
            "stale_count": len(knowledge["stale_sources"]),
            "unchecked_count": len(knowledge["unchecked_sources"]),
            "failing_count": len(knowledge["failing_checks"]),
            "rows": knowledge_rows,
        },
        "framework": {
            "source_count": len(framework_sources),
            "active_count": sum(1 for source in framework_sources if source.active),
            "attention_count": len(failed_framework_rows),
            "never_checked_count": len(never_checked_framework_rows),
            "rows": framework_rows,
        },
    }


def _markdown_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- None recorded."


def generate_source_health_triage_markdown(session: Session, context: dict | None = None) -> str:
    context = context or source_health_triage_context(session)
    knowledge_lines = [
        (
            f"- {row['title']} ({row['priority']}, {row['topic']}): {row['status_label']}; "
            f"checked {row['checked_at']}; manual review due {row['review_due_date']}; "
            f"update marker {row['update_marker'] or 'not detected'}; notes {row['notes']}"
        )
        for row in context["knowledge"]["rows"]
    ]
    framework_lines = [
        (
            f"- {row['source'].name}: {row['status_label']}; readiness {row['readiness']['label']}; "
            f"checked {row['checked_at']}; change {row['change_type']}; schema {row['detected_schema'] or 'not detected'}; "
            f"action {row['readiness']['action']}; notes {row['notes']}"
        )
        for row in context["framework"]["rows"]
    ]
    generated = context["generated_at"].isoformat(timespec="seconds")
    return f"""# Source Health Triage Pack

**Generated at:** {generated}
**Decision-support caveat:** {context['caveat']}

## Guardrails
- Summarises existing local Knowledge Agent checks and Framework Intelligence source snapshots only.
- Does not run live checks, poll sources, update YAML rules, or change source configuration.
- Does not authorise portal login, customer communication, bid submission, infrastructure change, HMRC submission, or RDEC/tax/procurement conclusions.
- Human review remains required before relying on source currency or changing rules.

## Priority Actions
{_markdown_bullets(context['actions'])}

## Metrics
- Knowledge Agent sources tracked: {context['knowledge']['source_count']}
- Knowledge Agent stale sources: {context['knowledge']['stale_count']}
- Knowledge Agent unchecked sources: {context['knowledge']['unchecked_count']}
- Knowledge Agent failing latest checks: {context['knowledge']['failing_count']}
- Framework Intelligence sources tracked: {context['framework']['source_count']}
- Framework Intelligence active sources: {context['framework']['active_count']}
- Framework Intelligence sources needing attention: {context['framework']['attention_count']}
- Framework Intelligence sources without snapshots: {context['framework']['never_checked_count']}

## Knowledge Agent Source Status
{_markdown_bullets(knowledge_lines)}

## Framework Intelligence Source Status
{_markdown_bullets(framework_lines)}

## Human Follow-Up
- Use `/knowledge-agent` to run explicit official HMRC/GOV.UK source checks when internet access is available.
- Use `/framework-intelligence/source-catalogue` and `/framework-intelligence/source-changes` to inspect procurement-source readiness and source snapshots.
- Record any rule or source-configuration change through normal human review, version control, and competent professional/tax review.
"""
