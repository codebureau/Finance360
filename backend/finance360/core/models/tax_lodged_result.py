"""Lodged result (ADR-0019 sections 5 and 8). Services live in ``capabilities/tax/lodged``.

A Member's assessed outcome for a financial year, entered from a notice of assessment. Rows are
never updated or deleted (see ``SupersessionMixin``; triggers in migration 0032 enforce it): an
amended notice supersedes the earlier row, which stays retrievable. One current row per Member and
financial year, enforced by a partial unique index.

Money sits in canonical JSON, ``{"ccy": "AUD", "minor": <integer>}``, never a float:
``items_json`` maps a result key (``compare.RESULT_KEYS``) to its amount, and ``other_json`` lists
the named lines the engine has no key for (``[{"name", "amount"}]``). Both are ``json_valid``
checked. A notice's documents are Document links of the ``tax_lodged_result`` kind (ADR-0026).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base
from finance360.core.models.supersession import SupersessionMixin

OTHER_NAME_MAX = 100


class TaxLodgedResult(SupersessionMixin, Base):
    __tablename__ = "tax_lodged_result"
    __table_args__ = (
        CheckConstraint("json_valid(items_json)", name="items_json_valid"),
        CheckConstraint("json_valid(other_json)", name="other_json_valid"),
        Index(
            "uq_tax_lodged_result_current",
            "member_id",
            "financial_year",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), index=True)
    #: The year it ends in: 2026 is FY 2025-26.
    financial_year: Mapped[int]
    #: The date on the notice of assessment.
    assessed_on: Mapped[date] = mapped_column(Date)
    items_json: Mapped[str] = mapped_column(Text)
    other_json: Mapped[str] = mapped_column(Text)
