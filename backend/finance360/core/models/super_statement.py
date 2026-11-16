"""Super statement and its Investment option lines (ADR-0025).

A super fund's annual statement for one Account and one financial year. Services live in
``capabilities/position/super_statements``. Rows are never updated or deleted (see
``SupersessionMixin``; triggers in the migration enforce it): a correction supersedes the statement
and its option lines stay with the old row.

Money is integer minor units. The whole statement is in one currency (the Account's, as for a
Valuation), held once in ``currency``; every amount column is ``<name>_minor``. ``NULL`` in an
optional amount means "not stated", which is not the same as zero. Units and unit prices are
``ScaledDecimal``; nothing here is a float.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance360.core.columns import ScaledDecimal
from finance360.core.db import Base
from finance360.core.models.supersession import SupersessionMixin
from finance360.core.money import Money
from finance360.core.price import PRICE_SCALE

#: The balance roll: always stated.
REQUIRED_AMOUNTS = ("opening", "contributions_in", "investment_returns", "withdrawals", "closing")
#: Stated only when the fund's statement gives them.
OPTIONAL_AMOUNTS = (
    "contributions_tax",
    "fees_before_rebate",
    "rebate",
    "fees_inside_investment",
    "division_293_released",
    "other_withdrawals",
    "insurance_premiums",
    "concessional_received",
    "non_concessional_received",
)
AMOUNTS = REQUIRED_AMOUNTS + OPTIONAL_AMOUNTS
NAME_MAX = 100
UNITS_SCALE = 6


class SuperStatement(SupersessionMixin, Base):
    __tablename__ = "super_statement"
    __table_args__ = (
        CheckConstraint("length(currency) = 3", name="currency_len"),
        CheckConstraint("json_valid(checks_ticked_json)", name="checks_ticked_json_valid"),
        CheckConstraint("period_end > period_start", name="period_ordered"),
        Index(
            "uq_super_statement_current",
            "account_id",
            "period_end",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    #: 1 July and 30 June; the financial year is derived from them, never stored.
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))

    opening_minor: Mapped[int] = mapped_column(BigInteger)
    contributions_in_minor: Mapped[int] = mapped_column(BigInteger)
    investment_returns_minor: Mapped[int] = mapped_column(BigInteger)
    withdrawals_minor: Mapped[int] = mapped_column(BigInteger)
    closing_minor: Mapped[int] = mapped_column(BigInteger)

    contributions_tax_minor: Mapped[int | None] = mapped_column(BigInteger)
    fees_before_rebate_minor: Mapped[int | None] = mapped_column(BigInteger)
    rebate_minor: Mapped[int | None] = mapped_column(BigInteger)
    fees_inside_investment_minor: Mapped[int | None] = mapped_column(BigInteger)
    division_293_released_minor: Mapped[int | None] = mapped_column(BigInteger)
    other_withdrawals_minor: Mapped[int | None] = mapped_column(BigInteger)
    insurance_premiums_minor: Mapped[int | None] = mapped_column(BigInteger)
    concessional_received_minor: Mapped[int | None] = mapped_column(BigInteger)
    non_concessional_received_minor: Mapped[int | None] = mapped_column(BigInteger)

    #: The Valuation this statement recorded at ``period_end`` (it may since have been superseded).
    valuation_id: Mapped[int] = mapped_column(ForeignKey("valuation.id"), index=True)
    #: JSON list of the check names the owner ticked ("roll", "outflows", "options").
    checks_ticked_json: Mapped[str] = mapped_column(Text, default="[]")

    #: The Investment option lines, in statement order.
    options: Mapped[list[SuperStatementOption]] = relationship(
        order_by="SuperStatementOption.position", lazy="selectin"
    )

    def amount(self, name: str) -> Money | None:
        """The named amount as Money in the statement's currency, or ``None`` when not stated."""
        minor: int | None = getattr(self, f"{name}_minor")
        return None if minor is None else Money(minor, self.currency)


class SuperStatementOption(Base):
    """One Investment option line of a Super statement. Immutable: a correction writes new lines."""

    __tablename__ = "super_statement_option"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("units IS NULL OR units >= 0", name="units_not_negative"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="unit_price_not_negative"),
        Index("ix_super_statement_option_statement_id", "statement_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("super_statement.id"))
    #: Order on the statement, from 0.
    position: Mapped[int]
    name: Mapped[str] = mapped_column(String(NAME_MAX))
    units: Mapped[Decimal | None] = mapped_column(ScaledDecimal(UNITS_SCALE))
    unit_price: Mapped[Decimal | None] = mapped_column(ScaledDecimal(PRICE_SCALE))
    #: Stated value in the statement's currency; never recomputed from units x price.
    value_minor: Mapped[int] = mapped_column(BigInteger)
