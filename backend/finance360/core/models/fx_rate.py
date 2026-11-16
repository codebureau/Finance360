"""FxRate (ADR-0009 section 4). Services live in ``capabilities/reference``.

A dated FX rate: units of ``to_ccy`` per 1 unit of ``from_ccy``, exact at scale 10. Rows are
never updated or deleted (see ``SupersessionMixin``). One current row per pair, date and source,
enforced by a partial unique index.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.columns import ScaledDecimal
from finance360.core.db import Base
from finance360.core.fx import RATE_SCALE
from finance360.core.models.supersession import SupersessionMixin


class FxRate(SupersessionMixin, Base):
    __tablename__ = "fx_rate"
    __table_args__ = (
        CheckConstraint("length(from_ccy) = 3", name="from_ccy_len"),
        CheckConstraint("length(to_ccy) = 3", name="to_ccy_len"),
        CheckConstraint("from_ccy <> to_ccy", name="pair_distinct"),
        CheckConstraint("rate > 0", name="rate_positive"),
        CheckConstraint("length(trim(coalesce(provenance_note, ''))) > 0", name="note_required"),
        Index(
            "uq_fx_rate_current",
            "from_ccy",
            "to_ccy",
            "as_at",
            "source",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_ccy: Mapped[str] = mapped_column(String(3))
    to_ccy: Mapped[str] = mapped_column(String(3))
    as_at: Mapped[date] = mapped_column(Date)
    #: Units of ``to_ccy`` per 1 unit of ``from_ccy``.
    rate: Mapped[Decimal] = mapped_column(ScaledDecimal(RATE_SCALE))
    #: ``manual`` now; provider names later (ADR-0009).
    source: Mapped[str] = mapped_column(String(20))
    provenance_note: Mapped[str | None] = mapped_column(Text)
