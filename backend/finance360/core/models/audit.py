"""The append-only audit trail (ADR-0009 section 5). Write it only through ``core.audit.record``.

Migration 0002 adds triggers that reject UPDATE and DELETE on this table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

ACTIONS = ("create", "update", "delete", "close", "supersede")
ACTOR_KINDS = ("owner", "import", "agent", "system")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint(_in("action", ACTIONS), name="action_known"),
        CheckConstraint(_in("actor_kind", ACTOR_KINDS), name="actor_kind_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: UTC. SQLite has no time zones, so it is stored (and read back) as naive UTC.
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int]
    action: Mapped[str] = mapped_column(String(20))
    actor_kind: Mapped[str] = mapped_column(String(20))
    actor_ref: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(String(500))
