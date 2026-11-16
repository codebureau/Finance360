"""Due snooze (ADR-0020 section 4). The service lives in ``capabilities/due``.

Prompts are never stored, only the user's Snoozes. ``until_on`` null means "skip this one": the
item for this period only (the ``subject_key`` carries the period). Expiry is computed against
today, so no job and no clean-up exist; an inert row is harmless.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base


class DueSnooze(Base):
    __tablename__ = "due_snooze"
    __table_args__ = (
        UniqueConstraint("rule_key", "subject_key", name="uq_due_snooze_subject"),
        CheckConstraint("length(trim(rule_key)) > 0", name="rule_key_not_blank"),
        CheckConstraint("length(trim(subject_key)) > 0", name="subject_key_not_blank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(100))
    subject_key: Mapped[str] = mapped_column(String(200))
    until_on: Mapped[date | None] = mapped_column(Date)
    #: UTC, stored naive like every timestamp here.
    created_at: Mapped[datetime] = mapped_column(DateTime)
