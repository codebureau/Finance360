"""Income stream and its owners (ADR-0017 section 2). Services live in
``capabilities/projection/income_streams.py``.

Only the **stored** kind is a row (a salary now; business income or an annuity later). A calculated
entitlement such as the Age Pension is never a row. ``type_key`` is a code registry key
(``capabilities/projection/income.py``), like ``behaviour_key``: the tax-relevant kind and the
indexation belong to the type, so neither is a column. Money is integer minor units plus a currency
code. Rows are edited in place; the Audit trail is the history.

``start_on`` / ``start_event`` and ``end_on`` / ``end_event`` are nullable pairs with at most one of
each pair set: a date, or a Life event key. Both start columns null means "now".

Ownership has the same sole or joint shape as an Account, but only sole is allowed in M3, so there
is exactly one ``income_stream_owner`` row per stream and no migration is needed for joint later.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance360.core.columns import money_columns, money_composite
from finance360.core.db import Base
from finance360.core.models.account import FULL_SHARE_BP
from finance360.core.models.household import Member
from finance360.core.money import Money

OWNERSHIP_MODES = ("sole", "joint")


class IncomeStreamOwner(Base):
    __tablename__ = "income_stream_owner"
    __table_args__ = (
        CheckConstraint(f"share_bp > 0 AND share_bp <= {FULL_SHARE_BP}", name="share_bp_range"),
    )

    stream_id: Mapped[int] = mapped_column(ForeignKey("income_stream.id"), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), primary_key=True)
    share_bp: Mapped[int]

    member: Mapped[Member] = relationship()


class IncomeStream(Base):
    __tablename__ = "income_stream"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("ownership IN ('sole', 'joint')", name="ownership_known"),
        CheckConstraint("gross_annual_minor > 0", name="gross_positive"),
        CheckConstraint(
            "(take_home_annual_minor IS NULL) = (take_home_annual_ccy IS NULL)",
            name="take_home_pair",
        ),
        CheckConstraint(
            "take_home_annual_ccy IS NULL OR length(take_home_annual_ccy) = 3",
            name="take_home_ccy_len",
        ),
        CheckConstraint(
            "take_home_annual_minor IS NULL OR take_home_annual_minor >= 0",
            name="take_home_not_negative",
        ),
        CheckConstraint("start_on IS NULL OR start_event IS NULL", name="one_start"),
        CheckConstraint("end_on IS NULL OR end_event IS NULL", name="one_end"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    #: A key of the type registry (``income.TYPES``); M3 registers ``salary`` only.
    type_key: Mapped[str] = mapped_column(String(30))
    ownership: Mapped[str] = mapped_column(String(10))
    #: The annual cash amount in today's dollars of the projection's ``as_at``, before tax and
    #: excluding employer super.
    gross_annual_minor, gross_annual_ccy = money_columns("gross_annual")
    gross_annual: Mapped[Money] = money_composite(gross_annual_minor, gross_annual_ccy)
    #: After tax, what reaches the household. Required for ``salary`` in M3 (service-checked).
    take_home_annual_minor: Mapped[int | None] = mapped_column(BigInteger)
    take_home_annual_ccy: Mapped[str | None] = mapped_column(String(3))
    start_on: Mapped[date | None] = mapped_column(Date)
    start_event: Mapped[str | None] = mapped_column(String(30))
    end_on: Mapped[date | None] = mapped_column(Date)
    end_event: Mapped[str | None] = mapped_column(String(30))
    #: The Category whose line the stream credits.
    credit_category: Mapped[str] = mapped_column(String(30), default="cash", server_default="cash")

    owners: Mapped[list[IncomeStreamOwner]] = relationship(
        cascade="all, delete-orphan", order_by="IncomeStreamOwner.member_id"
    )

    @property
    def take_home_annual(self) -> Money | None:
        if self.take_home_annual_minor is None or self.take_home_annual_ccy is None:
            return None
        return Money(self.take_home_annual_minor, self.take_home_annual_ccy)
