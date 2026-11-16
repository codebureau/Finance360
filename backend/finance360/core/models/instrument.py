"""Instrument and its identifier crosswalk (ADR-0013 section 2).

Services: ``capabilities/reference``.

Instruments are never deleted (a trigger in the migration enforces it) and are edited in place; the
audit trail is their history. An identifier maps to exactly one Instrument: the unique index is on
the scheme and the *normalised* value.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance360.core.db import Base
from finance360.core.instrument import KINDS, SCHEMES


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Instrument(Base):
    __tablename__ = "instrument"
    __table_args__ = (
        CheckConstraint(_in("kind", KINDS), name="kind_known"),
        CheckConstraint("length(currency) = 3", name="currency_len"),
        CheckConstraint("length(trim(name)) > 0", name="name_required"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))
    #: The holding currency; Prices must be in it.
    currency: Mapped[str] = mapped_column(String(3))
    notes: Mapped[str | None] = mapped_column(Text)

    identifiers: Mapped[list[InstrumentIdentifier]] = relationship(
        order_by="InstrumentIdentifier.id", lazy="selectin"
    )


class InstrumentIdentifier(Base):
    __tablename__ = "instrument_identifier"
    __table_args__ = (
        CheckConstraint(_in("scheme", SCHEMES), name="scheme_known"),
        CheckConstraint("length(trim(value)) > 0", name="value_required"),
        Index("uq_instrument_identifier_value", "scheme", "value_norm", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), index=True)
    scheme: Mapped[str] = mapped_column(String(10))
    #: As entered (whitespace tidied), for display.
    value: Mapped[str] = mapped_column(String(200))
    #: ``normalise_identifier(value)``: what uniqueness and lookup compare.
    value_norm: Mapped[str] = mapped_column(String(200))
