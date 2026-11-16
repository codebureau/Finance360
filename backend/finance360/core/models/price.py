"""Price with supersession (ADR-0013 section 2). Services: ``capabilities/reference``.

The end-of-day value of one unit of an Instrument, in the Instrument's currency (service-validated).
Rows are never updated or deleted (see ``SupersessionMixin``; triggers in the migration enforce it).
One current row per Instrument, date and source, enforced by a partial unique index.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.columns import ScaledDecimal
from finance360.core.db import Base
from finance360.core.models.supersession import SupersessionMixin
from finance360.core.price import PRICE_SCALE


class Price(SupersessionMixin, Base):
    __tablename__ = "price"
    __table_args__ = (
        CheckConstraint("length(ccy) = 3", name="ccy_len"),
        CheckConstraint("value > 0", name="value_positive"),
        CheckConstraint("length(trim(coalesce(provenance_note, ''))) > 0", name="note_required"),
        Index(
            "uq_price_current",
            "instrument_id",
            "as_at",
            "source",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), index=True)
    as_at: Mapped[date] = mapped_column(Date)
    #: Value of one unit, exact at scale 6. Not minor-unit Money: ETF prices have 4 d.p.
    value: Mapped[Decimal] = mapped_column(ScaledDecimal(PRICE_SCALE))
    #: Always the Instrument's currency (service-validated).
    ccy: Mapped[str] = mapped_column(String(3))
    #: ``manual`` or a provider name such as ``yfinance``.
    source: Mapped[str] = mapped_column(String(20))
    #: Required: for a manual Price, where it came from; for a provider, the symbol used.
    provenance_note: Mapped[str | None] = mapped_column(Text)
