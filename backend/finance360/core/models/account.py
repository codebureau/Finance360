"""Account, ownership entries and Tags (ADR-0009 section 2). Services live in
``capabilities/position``.

Accounts are closed, never deleted. ``closed_on`` is half-open: the first date the Account no
longer counts. Ownership shares are integer basis points (10000 = 100%); the cross-row sum rule is
enforced in the service because SQLite cannot check it.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance360.core.db import Base
from finance360.core.models.catalogue import AccountType, Category
from finance360.core.models.household import Member

OWNERSHIP_MODES = ("sole", "joint")
#: 100% in basis points.
FULL_SHARE_BP = 10000


class Tag(Base):
    __tablename__ = "tag"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class AccountTag(Base):
    __tablename__ = "account_tag"

    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id"), primary_key=True)


class AccountOwner(Base):
    __tablename__ = "account_owner"
    __table_args__ = (
        CheckConstraint(f"share_bp > 0 AND share_bp <= {FULL_SHARE_BP}", name="share_bp_range"),
    )

    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), primary_key=True)
    share_bp: Mapped[int]

    member: Mapped[Member] = relationship()


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("ownership IN ('sole', 'joint')", name="ownership_known"),
        CheckConstraint("length(currency) = 3", name="currency_len"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"))
    account_type_id: Mapped[int] = mapped_column(ForeignKey("account_type.id"))
    #: ISO 4217 code; AUD unless overridden.
    currency: Mapped[str] = mapped_column(String(3))
    ownership: Mapped[str] = mapped_column(String(10))
    notes: Mapped[str | None] = mapped_column(Text)
    #: Null means open from the beginning.
    opened_on: Mapped[date | None] = mapped_column(Date)
    #: Null means open. Half-open: the first date the Account no longer counts.
    closed_on: Mapped[date | None] = mapped_column(Date)

    category: Mapped[Category] = relationship()
    account_type: Mapped[AccountType] = relationship()
    owners: Mapped[list[AccountOwner]] = relationship(
        cascade="all, delete-orphan", order_by="AccountOwner.member_id"
    )
    tags: Mapped[list[Tag]] = relationship(secondary="account_tag", order_by="Tag.name")
