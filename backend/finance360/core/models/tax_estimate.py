"""Tax what-if and saved Tax estimate (ADR-0019 sections 5 and 6).

Services: ``capabilities/tax/saved.py``.

A ``tax_whatif`` is editable and audited, like a Scenario: a name, a financial year and its
overrides as canonical JSON (absolute values, never deltas; it never inherits from another).

A ``tax_estimate`` is the immutable counterpart of a saved projection run (ADR-0016 section 4):
one row per explicit "Save estimate". Migration 0031 adds triggers that reject UPDATE and DELETE and
``json_valid`` CHECKs on the payload columns. The payloads are canonical JSON text (money as
``{minor, ccy}``, rates as percent strings, no floats), so no Money or Rate sits in a relational
column. ``whatif_id`` is RESTRICT, not SET NULL: nulling it would be an UPDATE, and an estimate is
never updated. ``whatif_id`` is null for the base estimate.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

NAME_MAX = 100
NOTES_MAX = 500
LABEL_MAX = 100
ORIGINS = ("user", "ai")


class TaxWhatIf(Base):
    __tablename__ = "tax_whatif"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("origin IN ('user', 'ai')", name="origin_known"),
        CheckConstraint("json_valid(overrides_json)", name="overrides_json_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX), unique=True)
    #: The year it ends in: 2027 is FY 2026-27.
    financial_year: Mapped[int]
    notes: Mapped[str | None] = mapped_column(String(NOTES_MAX))
    #: ``user``, or ``ai`` for a what-if an AI turned a question into (shown with its figures).
    origin: Mapped[str] = mapped_column(String(10))
    #: Canonical JSON: a list of structured overrides (``capabilities/tax/whatif.py``).
    overrides_json: Mapped[str] = mapped_column(Text)
    #: UTC, stored naive like every timestamp here.
    created_at: Mapped[datetime] = mapped_column(DateTime)


class TaxEstimate(Base):
    __tablename__ = "tax_estimate"
    __table_args__ = (
        CheckConstraint("json_valid(input_json)", name="input_json_valid"),
        CheckConstraint("json_valid(result_json)", name="result_json_valid"),
        CheckConstraint("json_valid(provenance_json)", name="provenance_json_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: UTC, stored naive like every timestamp here.
    created_at: Mapped[datetime] = mapped_column(DateTime)
    label: Mapped[str | None] = mapped_column(String(LABEL_MAX))
    financial_year: Mapped[int]
    #: RESTRICT, not SET NULL: nulling it would be an UPDATE, and an estimate is never updated.
    #: Null for the base estimate (the register's totals, no what-if).
    whatif_id: Mapped[int | None] = mapped_column(ForeignKey("tax_whatif.id", ondelete="RESTRICT"))
    #: The what-if's name when the estimate was saved.
    whatif_name: Mapped[str | None] = mapped_column(String(NAME_MAX))
    tax_engine_version: Mapped[int]
    tax_input_version: Mapped[int]
    #: The ADR-0018 label, such as ``au/2026-27/r1``.
    rule_pack_version: Mapped[str] = mapped_column(String(50))
    input_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    provenance_json: Mapped[str] = mapped_column(Text)
    incomplete: Mapped[bool]
    has_provisional: Mapped[bool]
