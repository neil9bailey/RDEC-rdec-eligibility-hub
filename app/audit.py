from __future__ import annotations

import json
from typing import Any

from sqlmodel import SQLModel, Session

from app.models import AuditEvent


def compact_snapshot(item: SQLModel | None) -> str:
    if item is None:
        return ""
    data: dict[str, Any] = item.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def log_event(
    session: Session,
    *,
    entity_type: str,
    entity_id: int | None,
    action: str,
    summary: str,
    before: SQLModel | str | None = None,
    after: SQLModel | str | None = None,
    actor: str = "local-user",
) -> AuditEvent:
    event = AuditEvent(
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        summary=summary,
        before_json=before if isinstance(before, str) else compact_snapshot(before),
        after_json=after if isinstance(after, str) else compact_snapshot(after),
    )
    session.add(event)
    return event

