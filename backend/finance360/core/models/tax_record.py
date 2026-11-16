"""Tax record (ADR-0019 sections 2 and 5). Services live in ``capabilities/tax``.

One dated entry of a Member's (a line amount against a Return label, or a fact). ``record_type``
is a code registry key (``capabilities/tax/record_types``) and ``payload_json`` holds what that
type parses: money is ``{"minor", "ccy"}``, a rate is parts per million, never a float. Rows are
edited in place and deleted by a service call; the Audit trail is the history.

The financial year is **derived** from ``from_on`` / ``to_on``, never stored, so it cannot disagree
with them. A Label total is never stored either: it is computed from the records on demand.
``member_id`` is nullable for a household-level fact (ADR-0019 section 2).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

DESCRIPTION_MAX = 200
NOTES_MAX = 500


class TaxRecord(Base):
    __tablename__ = "tax_record"
    __table_args__ = (
        CheckConstraint("length(trim(description)) > 0", name="description_not_blank"),
        CheckConstraint("to_on IS NULL OR to_on >= from_on", name="span_ordered"),
        CheckConstraint("json_valid(payload_json)", name="payload_json_valid"),
        Index("ix_tax_record_member_id_from_on", "member_id", "from_on"),
        Index("ix_tax_record_record_type", "record_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("member.id"))
    #: A key of the Record type registry (``record_types.TYPES``).
    record_type: Mapped[str] = mapped_column(String(30))
    from_on: Mapped[date] = mapped_column(Date)
    to_on: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(DESCRIPTION_MAX))
    notes: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text)
    #: UTC, stored naive like every timestamp here.
    created_at: Mapped[datetime] = mapped_column(DateTime)
