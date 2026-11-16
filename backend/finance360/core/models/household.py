"""Household (exactly one row) and Member (ADR-0009 section 2). Services live in capabilities."""

from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base
from finance360.core.due import DEFAULT_LEVEL, LEVELS
from finance360.core.localtime import DEFAULT_TIME_ZONE

#: The one Household row always has this id; a CHECK constraint keeps it that way.
HOUSEHOLD_ID = 1


class Household(Base):
    __tablename__ = "household"
    __table_args__ = (
        CheckConstraint(f"id = {HOUSEHOLD_ID}", name="single_row"),
        CheckConstraint(
            f"effort_level IN ({', '.join(repr(v) for v in LEVELS)})", name="effort_level_known"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    #: IANA name. "Today" for the Household is the local date in this zone.
    time_zone: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_TIME_ZONE, server_default=DEFAULT_TIME_ZONE
    )
    #: How much What's due asks (ADR-0020 section 3): light, steady or thorough.
    effort_level: Mapped[str] = mapped_column(String(10), server_default=DEFAULT_LEVEL)


class Member(Base):
    __tablename__ = "member"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("household.id"))
    name: Mapped[str] = mapped_column(String(200))
    date_of_birth: Mapped[date] = mapped_column(Date)
