"""Answer rating (ADR-0032 section 6): the owner's Yes, Partly or No on whether a Question's answer
helped, with an optional note.

Append-only: the newest row per Login and Question is current, earlier rows are the history. Held
apart from Readiness and never combined with it; ``readiness_at_rating`` is only the state word at
the time, so "rated No while Ready" can be read later. Not audited (it changes no household data),
never sent anywhere. The note carries no figure: the service refuses one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

NOTE_MAX = 200
ANSWERS = ("yes", "partly", "no")


class AnswerRating(Base):
    __tablename__ = "answer_rating"
    __table_args__ = (
        CheckConstraint("answer IN ('yes', 'partly', 'no')", name="answer_known"),
        CheckConstraint(f"note IS NULL OR length(note) <= {NOTE_MAX}", name="note_length"),
        CheckConstraint("length(trim(question_key)) > 0", name="question_key_not_blank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    login_id: Mapped[int] = mapped_column(ForeignKey("login.id"))
    question_key: Mapped[str] = mapped_column(String(100))
    answer: Mapped[str] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(String(NOTE_MAX))
    #: The Question's Readiness state at the time (ready, partly_ready, ...), never a score.
    readiness_at_rating: Mapped[str] = mapped_column(String(20))
    #: UTC, stored naive like every timestamp here.
    created_at: Mapped[datetime] = mapped_column(DateTime)
