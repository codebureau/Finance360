"""A captured Rule figure (ADR-0022 section 1). The store is ``core.rulepack_store``.

Append-only: a row is never edited or deleted, only ended. Correcting a figure captures a new row
for the same ``(jurisdiction, key, fy)``, which supersedes the old one; withdrawing ends a row with
no replacement. At most one row is active (``ended_at`` null) per figure, by a partial unique
index. Migration 0024 adds triggers that reject DELETE and any UPDATE other than ending a row.
Every write also writes an ``audit_event``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

METHODS = ("typed", "ai", "matched", "carried")
ENDED_KINDS = ("withdrawn", "superseded")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class RuleCapture(Base):
    __tablename__ = "rule_capture"
    __table_args__ = (
        CheckConstraint(_in("method", METHODS), name="method_known"),
        CheckConstraint(
            f"ended_kind IS NULL OR {_in('ended_kind', ENDED_KINDS)}", name="ended_kind_known"
        ),
        CheckConstraint("(ended_at IS NULL) = (ended_kind IS NULL)", name="ended_together"),
        CheckConstraint("length(trim(key)) > 0", name="key_not_blank"),
        CheckConstraint(
            "provisional_note IS NULL OR length(trim(provisional_note)) > 0",
            name="provisional_note_not_blank",
        ),
        Index(
            "uq_rule_capture_active",
            "jurisdiction",
            "key",
            "fy",
            unique=True,
            sqlite_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    jurisdiction: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(100))
    #: The year the FY ends in (FY 2026-27 is 2027).
    fy: Mapped[int]
    #: ``core.rulepack.value_json`` of the figure: integers, strings and lists only (no float).
    value_json: Mapped[Any] = mapped_column(JSON)
    #: Null for a firm figure; a note when announced but not yet law.
    provisional_note: Mapped[str | None] = mapped_column(Text)
    #: The sentence the figure was read from. Optional for a typed capture; the AI path
    #: requires it.
    quote: Mapped[str | None] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(String(500))
    retrieved_on: Mapped[date] = mapped_column(Date)
    #: ``typed`` by hand; ``ai`` read by a model; ``matched`` found by the word matcher
    #: on the pasted page, which is not an AI call (ADR-0023 section 6); ``carried`` an earlier
    #: year's figure the owner accepted from "Start from <FY>", provisional until confirmed.
    method: Mapped[str] = mapped_column(String(10))
    #: The call a ``ai`` capture came from; null for ``typed`` and ``matched``.
    ai_log_id: Mapped[int | None]
    #: The proposal that was confirmed (``ai`` and ``matched``), beside the confirmed value.
    ai_value_json: Mapped[Any | None] = mapped_column(JSON)
    #: UTC, stored naive like every timestamp here.
    captured_at: Mapped[datetime] = mapped_column(DateTime)
    #: The actor, as ``audit_event`` names it: ``actor_kind`` and ``actor_ref``.
    captured_kind: Mapped[str] = mapped_column(String(20))
    captured_by: Mapped[str] = mapped_column(String(100))
    #: Null while active.
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_by: Mapped[str | None] = mapped_column(String(100))
    ended_kind: Mapped[str | None] = mapped_column(String(10))
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("rule_capture.id"))
