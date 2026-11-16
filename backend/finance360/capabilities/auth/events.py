"""The append-only auth trail (ADR-0010). ``detail`` holds non-sensitive facts only."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from finance360.core.models.auth import EVENT_KINDS, AuthEvent


def record_event(
    session: Session,
    kind: str,
    *,
    now: datetime,
    login_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> AuthEvent:
    if kind not in EVENT_KINDS:
        raise ValueError(f"Unknown auth event kind: {kind!r}")
    event = AuthEvent(occurred_at=now, kind=kind, login_id=login_id, detail=detail or {})
    session.add(event)
    session.flush()
    return event
