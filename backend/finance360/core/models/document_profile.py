"""A saved Document profile (ADR-0024 section 3, ADR-0027 section 5).
Service: ``capabilities/documents/store``.

A profile is data (ADR-0024). **Shipped profiles are not rows** (they live in
``plugins/documents``); this table holds the ones saved in the instance: ``pasted`` by the owner,
``mcp`` proposed from a conversation or ``ai`` (ADR-0024 slice 2, later).

Append-only in the database itself (migration 0029 adds the triggers): ``key``, ``version``,
``profile_json`` and everything about the proposal never change, and the lifecycle columns are each
set once. The **status is derived**, never stored, so it can never disagree with them: no
``activated_at`` and no ``ended_at`` is *proposed*; ``activated_at`` set is *active*; ``ended_at``
set is *retired*. A revision of a profile is a new ``version`` row.

``test_snapshot`` holds the outcome, each field's found / not-found / ambiguous state and each
check's result, **never a value or any text from the document**, so a Reading is still not stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

SOURCES = ("pasted", "mcp", "ai")
PROPOSED, ACTIVE, RETIRED = "proposed", "active", "retired"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class DocumentProfile(Base):
    __tablename__ = "document_profile"
    __table_args__ = (
        UniqueConstraint("key", "version"),
        CheckConstraint(_in("source", SOURCES), name="source_known"),
        CheckConstraint("length(test_document_sha256) = 64", name="test_sha256_length"),
        CheckConstraint("json_valid(profile_json)", name="profile_json_valid"),
        CheckConstraint("json_valid(test_snapshot)", name="test_snapshot_valid"),
        CheckConstraint(
            "(activated_at IS NULL) = (activated_by IS NULL)", name="activated_together"
        ),
        CheckConstraint(
            "(ended_at IS NULL) = (ended_by IS NULL)"
            " AND (ended_at IS NULL) = (ended_reason IS NULL)",
            name="ended_together",
        ),
        # At most one active row per key: ending the old row comes before activating the new one.
        Index(
            "uq_document_profile_active",
            "key",
            unique=True,
            sqlite_where=text("activated_at IS NOT NULL AND ended_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    #: The Fill target the profile names (``profile_json['target']``), for listing without parsing.
    target: Mapped[str] = mapped_column(String(64))
    #: The profile exactly as validated and saved (``capabilities.documents.profile`` schema).
    profile_json: Mapped[Any] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(10))
    #: Who proposed it: ``login:<id>`` (the owner pasting it), or later the token's label.
    proposed_by: Mapped[str] = mapped_column(String(100))
    #: The access token it came in through; null for a person. No foreign key yet.
    proposed_token_id: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime)
    #: The Library document the profile was tested on. The id is for the link back; the hash is
    #: what is looked up if the id was reused, and what ADR-0027 names.
    test_document_id: Mapped[int]
    test_document_sha256: Mapped[str] = mapped_column(String(64))
    #: ``capabilities.documents.store.snapshot_of`` a Reading: no values, no text.
    test_snapshot: Mapped[Any] = mapped_column(JSON)
    #: The AI call a ``source='ai'`` proposal came from (ADR-0024 slice 2); null otherwise.
    ai_log_id: Mapped[int | None]
    #: Each of these is set once, in this order, never changed.
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)
    activated_by: Mapped[str | None] = mapped_column(String(100))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_by: Mapped[str | None] = mapped_column(String(100))
    #: Fixed wording: ``retired`` (the owner retired it) or ``replaced`` (a newer version of the
    #: same key was activated).
    ended_reason: Mapped[str | None] = mapped_column(String(20))
