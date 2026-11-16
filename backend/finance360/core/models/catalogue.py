"""Category, Account type and the ``meta`` table (ADR-0009 section 6).

Both classification tables are ordinary, user-editable data with stable string keys; ``seeded``
marks the rows the catalogue seeder created. ``behaviour_key`` is a plain string resolved against
the Behaviour registry; ``core`` never imports ``behaviours/``, so the check lives at startup.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, false
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

CLASSES = ("asset", "liability")


class Category(Base):
    __tablename__ = "category"
    __table_args__ = (CheckConstraint("class IN ('asset', 'liability')", name="class_known"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    class_: Mapped[str] = mapped_column("class", String(20))
    seeded: Mapped[bool] = mapped_column(Boolean, server_default=false())


class AccountType(Base):
    __tablename__ = "account_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"))
    behaviour_key: Mapped[str] = mapped_column(String(50))
    seeded: Mapped[bool] = mapped_column(Boolean, server_default=false())


class Meta(Base):
    """Small key/value facts about the database itself, e.g. ``catalogue_version``."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
