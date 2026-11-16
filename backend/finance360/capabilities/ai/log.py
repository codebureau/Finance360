"""The AI log: write a row per call, prune by age, read newest first (ADR-0021 section 7).

A row is written in **its own short transaction**, on the same database as the caller's session. A
question can take seconds inside the provider; the log row must not be lost if the caller's
transaction rolls back (a refusal or failure is exactly what the log is for), and it must not fail
because another writer committed meanwhile (a read snapshot cannot be upgraded to a write).
So the caller's session must not hold an uncommitted write when it calls the gateway.

Retention is applied when a row is written (no job): rows older than ``log_days`` go.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from finance360.core.ai_settings import AiSettings
from finance360.core.models.ai_log import AiLog


def utcnow() -> datetime:
    """UTC, naive: how every timestamp here is stored."""
    return datetime.now(UTC).replace(tzinfo=None)


def write(session: Session, settings: AiSettings, row: AiLog) -> AiLog:
    """Insert ``row`` (committed at once) and prune old rows. Returns it with its ``id``."""
    with Session(session.get_bind(), expire_on_commit=False) as own, own.begin():
        own.add(row)
        own.execute(
            delete(AiLog).where(
                AiLog.occurred_at < row.occurred_at - timedelta(days=settings.log_days)
            )
        )
        own.flush()
    return row


def set_outcome(session: Session, log_id: int, outcome: str) -> None:
    with Session(session.get_bind()) as own, own.begin():
        row = own.get(AiLog, log_id)
        if row is not None:
            row.outcome = outcome


def recent(session: Session, limit: int = 50) -> list[AiLog]:
    """Newest first."""
    return list(session.scalars(select(AiLog).order_by(AiLog.id.desc()).limit(limit)))
