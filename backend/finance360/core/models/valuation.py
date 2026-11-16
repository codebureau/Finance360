"""Valuation (ADR-0009 section 3). Services live in ``capabilities/position``.

A dated value of an Account. Rows are never updated or deleted (see ``SupersessionMixin``). One
current row per Account, date and source, enforced by a partial unique index.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.columns import money_columns, money_composite
from finance360.core.db import Base
from finance360.core.models.supersession import SupersessionMixin
from finance360.core.money import Money
from finance360.core.valuation import NOTE_REQUIRED_SOURCES, SOURCES


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Valuation(SupersessionMixin, Base):
    __tablename__ = "valuation"
    __table_args__ = (
        CheckConstraint(_in("source", SOURCES), name="source_known"),
        CheckConstraint(
            f"NOT ({_in('source', NOTE_REQUIRED_SOURCES)})"
            " OR length(trim(coalesce(provenance_note, ''))) > 0",
            name="note_required",
        ),
        Index(
            "uq_valuation_current",
            "account_id",
            "as_at",
            "source",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    as_at: Mapped[date] = mapped_column(Date)
    #: Always in the Account's currency (service-validated).
    amount_minor, amount_ccy = money_columns("amount")
    amount: Mapped[Money] = money_composite(amount_minor, amount_ccy)
    source: Mapped[str] = mapped_column(String(20))
    provenance_note: Mapped[str | None] = mapped_column(Text)
    #: The Import that produced it. No FK until Imports exist (ADR-0009).
    import_id: Mapped[int | None]
