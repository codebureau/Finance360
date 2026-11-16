"""Scenario and Assumption (ADR-0016 section 1). Services: ``capabilities/projection/scenarios.py``.

An ``assumption`` row is one override. ``scenario_id`` NULL is the Household default layer. The
value sits in exactly one typed column, chosen by the key's registered kind: a Rate as integer parts
per million, a whole number (ages), or Money (minor units plus currency). No float, no ``Numeric``.

A ``spending_phase`` row is one step of the spending plan (ADR-0017 section 6), layered like an
``assumption`` (``scenario_id`` NULL is the Household default) but read and written as a whole list.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

SCOPE_TYPES = ("household", "category", "member")


class Scenario(Base):
    __tablename__ = "scenario"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    #: ``best`` / ``likely`` / ``worst`` for the seeded rows, otherwise NULL.
    key: Mapped[str | None] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    #: ADR-0016 s3: a JSON text list of Category keys, or NULL for the default order. Always read
    #: and written whole, validated by ``scenarios.set_drawdown_order``.
    drawdown_order: Mapped[str | None] = mapped_column(Text)
    #: UTC, stored naive like every timestamp here.
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Assumption(Base):
    __tablename__ = "assumption"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('household', 'category', 'member')", name="scope_type_known"
        ),
        CheckConstraint(
            "(scope_type = 'household') = (scope_ref = '')", name="scope_ref_matches_type"
        ),
        CheckConstraint(
            "(value_rate_ppm IS NOT NULL) + (value_int IS NOT NULL) "
            "+ (value_minor IS NOT NULL) = 1",
            name="one_value",
        ),
        CheckConstraint("(value_minor IS NULL) = (value_ccy IS NULL)", name="money_pair"),
        CheckConstraint("value_ccy IS NULL OR length(value_ccy) = 3", name="value_ccy_len"),
        CheckConstraint(
            "value_rate_ppm IS NULL OR (value_rate_ppm > -1000000 AND value_rate_ppm <= 1000000)",
            name="rate_in_range",
        ),
        # SQLite treats NULLs as distinct, so one partial unique index per layer.
        Index(
            "uq_assumption_default",
            "key",
            "scope_type",
            "scope_ref",
            unique=True,
            sqlite_where=text("scenario_id IS NULL"),
        ),
        Index(
            "uq_assumption_override",
            "scenario_id",
            "key",
            "scope_type",
            "scope_ref",
            unique=True,
            sqlite_where=text("scenario_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: NULL is the Household default layer.
    scenario_id: Mapped[int | None] = mapped_column(ForeignKey("scenario.id"))
    key: Mapped[str] = mapped_column(String(50))
    scope_type: Mapped[str] = mapped_column(String(20))
    #: ``""`` for household scope, otherwise a Category key or a Member id as text.
    scope_ref: Mapped[str] = mapped_column(String(50), default="")
    value_rate_ppm: Mapped[int | None] = mapped_column(Integer)
    value_int: Mapped[int | None] = mapped_column(Integer)
    value_minor: Mapped[int | None] = mapped_column(BigInteger)
    value_ccy: Mapped[str | None] = mapped_column(String(3))


class SpendingPhase(Base):
    """One spending phase: ``amount`` a year (today's dollars) from ``offset_years`` on.

    ``anchor`` is ``start`` (the projection start) or ``last_retirement``. The amount may be 0
    in the database only because the conversion of a pre-existing 0 ``annual_spending`` keeps it
    exactly; the service only ever writes amounts above 0.
    """

    __tablename__ = "spending_phase"
    __table_args__ = (
        CheckConstraint("anchor IN ('start', 'last_retirement')", name="anchor_known"),
        CheckConstraint("offset_years >= 0", name="offset_not_negative"),
        CheckConstraint("amount_minor >= 0", name="amount_not_negative"),
        CheckConstraint("length(amount_ccy) = 3", name="amount_ccy_len"),
        # SQLite treats NULLs as distinct, so one partial unique index per layer.
        Index(
            "uq_spending_phase_default",
            "anchor",
            "offset_years",
            unique=True,
            sqlite_where=text("scenario_id IS NULL"),
        ),
        Index(
            "uq_spending_phase_override",
            "scenario_id",
            "anchor",
            "offset_years",
            unique=True,
            sqlite_where=text("scenario_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: NULL is the Household default layer.
    scenario_id: Mapped[int | None] = mapped_column(ForeignKey("scenario.id"))
    anchor: Mapped[str] = mapped_column(String(20))
    offset_years: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    amount_ccy: Mapped[str] = mapped_column(String(3))
