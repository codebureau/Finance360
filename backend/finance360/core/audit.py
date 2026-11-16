"""The shared audit writer (ADR-0009 section 5).

Every mutating service calls ``record`` in the same session, and so the same transaction, as the
change it describes: the two commit or roll back together. The caller passes an explicit
``AuditContext`` (who and from where) down from the API layer. There is deliberately no automatic
flush listener: it could not carry the actor, the source or a readable summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from finance360.core.models.audit import ACTIONS, ACTOR_KINDS, AuditEvent


@dataclass(frozen=True)
class AuditContext:
    """Who made a change (``actor_kind``/``actor_ref``) and from where (``source``)."""

    actor_kind: str
    actor_ref: str
    source: str

    def __post_init__(self) -> None:
        if self.actor_kind not in ACTOR_KINDS:
            raise ValueError(f"Unknown actor kind: {self.actor_kind!r}")


def record(
    session: Session,
    context: AuditContext,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    summary: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit event to ``session``. It is committed with the caller's transaction.

    Snapshots are JSON-safe dicts (money as ``{"minor": ..., "ccy": ...}``, never floats).
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown audit action: {action!r}")
    event = AuditEvent(
        occurred_at=datetime.now(UTC).replace(tzinfo=None),
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_kind=context.actor_kind,
        actor_ref=context.actor_ref,
        source=context.source,
        before=before,
        after=after,
        summary=summary,
    )
    session.add(event)
    session.flush()
    return event
