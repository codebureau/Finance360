"""The local AI log (ADR-0021 section 7): one row per call that reached, or was stopped before,
the AI gateway. Separate from ``audit_event``: a call changes no household data.

``sent`` is the redacted text exactly as it left the instance and ``received`` is the model's reply
as received, still tokenised, so the log never holds a name, number or amount that was not already
sent. Cost is integer micro-dollars (no float). Services live in ``capabilities/ai``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

OUTCOMES = ("ok", "unsupported", "error", "refused_cap")


class AiLog(Base):
    __tablename__ = "ai_log"
    __table_args__ = (
        CheckConstraint(
            f"outcome IN ({', '.join(repr(v) for v in OUTCOMES)})", name="outcome_known"
        ),
        Index("ix_ai_log_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: UTC, stored naive like every timestamp here.
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    task: Mapped[str] = mapped_column(String(50))
    data_class: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(20))
    #: The redacted non-public parts as sent, tokens and all. Empty when nothing was sent.
    sent: Mapped[str] = mapped_column(Text)
    #: JSON: the name, sha256 and note of each public part (never its text).
    public_parts: Mapped[str] = mapped_column(Text)
    #: The model's reply as received, still tokenised. Null when there was none.
    received: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    #: Millionths of a US dollar.
    cost_usd_micros: Mapped[int] = mapped_column(Integer)
    #: True when the model's price was not known and the dearest known price was used.
    cost_estimated: Mapped[bool] = mapped_column(Boolean)
    why: Mapped[str] = mapped_column(String(200))
