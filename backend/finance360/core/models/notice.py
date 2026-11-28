"""Notice (ADR-0033): a short stored record that one write moved the picture.

Words plus one exact figure (integer minor units and a currency). It carries no identifier, no
document content and no owner free text other than the Account's display name. Not audited (a
derived convenience record); never read back as state.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base


class Notice(Base):
    __tablename__ = "notice"
    __table_args__ = (
        CheckConstraint("length(trim(kind)) > 0", name="kind_not_blank"),
        CheckConstraint("length(trim(headline)) > 0", name="headline_not_blank"),
        CheckConstraint(
            "(delta_minor IS NULL) = (delta_currency IS NULL)", name="delta_both_or_neither"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: UTC, stored naive like every timestamp here.
    created_at: Mapped[datetime] = mapped_column(DateTime)
    #: What was written, a short code: ``valuation.added``, ``valuation.corrected``.
    kind: Mapped[str] = mapped_column(String(50))
    headline: Mapped[str] = mapped_column(String(300))
    effect: Mapped[str | None] = mapped_column(String(1000))
    #: The exact net-Position change in the Household's base currency; null for a Question only.
    delta_minor: Mapped[int | None] = mapped_column(Integer)
    delta_currency: Mapped[str | None] = mapped_column(String(3))
    #: An in-app route, never a full URL.
    link_path: Mapped[str] = mapped_column(String(200))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime)
