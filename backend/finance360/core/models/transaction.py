"""Transaction with supersession (ADR-0013 section 2). Services: capabilities/imports.

One dated record in an Account's trade or cash Stream. Rows are never updated or deleted (see
``SupersessionMixin``; triggers in the migration enforce it): a later Import that supersedes a row
sets ``superseded_at`` and the row stays retrievable. ``import_id`` is null for a manual Opening
position row, which sits outside any Coverage.

No uniqueness constraint: identical same-day rows are legitimate. Money is integer minor units with
a currency; quantities and unit prices are scaled integers (``ScaledDecimal``). No floats.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.columns import ScaledDecimal
from finance360.core.db import Base
from finance360.core.models.supersession import SupersessionMixin
from finance360.core.money import Money
from finance360.core.price import PRICE_SCALE

# Spelt out here because ``core.transaction`` imports this module; a test keeps them in step.
_TRADE_KINDS = ("buy", "sell", "opening_balance")
_CASH_KINDS = (
    "deposit",
    "withdrawal",
    "trade_settlement",
    "distribution",
    "interest",
    "fee",
    "opening_balance",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Transaction(SupersessionMixin, Base):
    __tablename__ = "transaction"
    __table_args__ = (
        CheckConstraint(
            f"(stream = 'trade' AND {_in('kind', _TRADE_KINDS)})"
            f" OR (stream = 'cash' AND {_in('kind', _CASH_KINDS)})",
            name="kind_fits_stream",
        ),
        CheckConstraint("stream <> 'trade' OR quantity IS NOT NULL", name="trade_has_quantity"),
        CheckConstraint("stream <> 'cash' OR amount_minor IS NOT NULL", name="cash_has_amount"),
        CheckConstraint(
            "(fee_minor IS NULL) = (fee_ccy IS NULL)"
            " AND (amount_minor IS NULL) = (amount_ccy IS NULL)",
            name="money_pairs",
        ),
        CheckConstraint("(unit_price IS NULL) = (unit_price_ccy IS NULL)", name="unit_price_pair"),
        Index("ix_transaction_account_stream", "account_id", "stream"),
        Index("ix_transaction_import_id", "import_id"),
        Index("ix_transaction_instrument_id", "instrument_id"),
        Index(
            "ix_transaction_current",
            "account_id",
            "stream",
            "occurred_on",
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    #: ``trade`` or ``cash``.
    stream: Mapped[str] = mapped_column(String(10))
    #: The Import that produced it; ``None`` for a manual Opening position row.
    import_id: Mapped[int | None] = mapped_column(ForeignKey("import.id"))
    #: The row number in that Import's file, for tracing back.
    row_no: Mapped[int | None]
    occurred_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instrument.id"))
    #: Signed: buy positive, sell negative (trade Stream).
    quantity: Mapped[Decimal | None] = mapped_column(ScaledDecimal(6))
    unit_price: Mapped[Decimal | None] = mapped_column(ScaledDecimal(PRICE_SCALE))
    unit_price_ccy: Mapped[str | None] = mapped_column(String(3))
    #: Informational brokerage on a trade row. Never moves cash; only a cash ``fee`` row does.
    fee_minor: Mapped[int | None] = mapped_column(BigInteger)
    fee_ccy: Mapped[str | None] = mapped_column(String(3))
    #: Signed cash effect (cash Stream).
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    amount_ccy: Mapped[str | None] = mapped_column(String(3))

    @property
    def fee(self) -> Money | None:
        if self.fee_minor is None or self.fee_ccy is None:
            return None
        return Money(self.fee_minor, self.fee_ccy)

    @property
    def amount(self) -> Money | None:
        if self.amount_minor is None or self.amount_ccy is None:
            return None
        return Money(self.amount_minor, self.amount_ccy)
