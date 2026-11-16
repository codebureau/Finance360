"""Shared supersession columns (ADR-0009 section 3), reused by Valuation now and FxRate later.

Rows with these columns are never updated or deleted: a correction inserts a new row and sets
``superseded_at`` / ``superseded_by_id`` on the old one; a removal sets ``superseded_at`` with no
replacement. Queries must filter ``superseded_at IS NULL`` to see current rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class SupersessionMixin:
    __tablename__: str

    #: UTC, stored as naive UTC (SQLite has no time zones).
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime)

    @declared_attr
    def superseded_by_id(cls) -> Mapped[int | None]:  # noqa: N805 - declarative convention
        return mapped_column(ForeignKey(f"{cls.__tablename__}.id"))
