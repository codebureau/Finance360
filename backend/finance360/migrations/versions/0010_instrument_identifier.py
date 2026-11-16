"""Instrument and instrument_identifier (ADR-0013 sections 1 and 2).

Additive only (live-data rule): two new tables, an index and a trigger on the new table. Nothing
existing is touched.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instrument",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('etf', 'share', 'fund')", name=op.f("ck_instrument_kind_known")
        ),
        sa.CheckConstraint("length(currency) = 3", name=op.f("ck_instrument_currency_len")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_instrument_name_required")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instrument")),
    )
    op.create_table(
        "instrument_identifier",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("scheme", sa.String(length=10), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("value_norm", sa.String(length=200), nullable=False),
        sa.CheckConstraint(
            "scheme IN ('ticker', 'yahoo', 'isin', 'vendor_id', 'name')",
            name=op.f("ck_instrument_identifier_scheme_known"),
        ),
        sa.CheckConstraint(
            "length(trim(value)) > 0", name=op.f("ck_instrument_identifier_value_required")
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.id"],
            name=op.f("fk_instrument_identifier_instrument_id_instrument"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instrument_identifier")),
    )
    op.create_index(
        op.f("ix_instrument_identifier_instrument_id"),
        "instrument_identifier",
        ["instrument_id"],
        unique=False,
    )
    op.create_index(
        "uq_instrument_identifier_value",
        "instrument_identifier",
        ["scheme", "value_norm"],
        unique=True,
    )
    # Instruments are never deleted (ADR-0013 section 2).
    op.execute(
        "CREATE TRIGGER instrument_no_delete BEFORE DELETE ON instrument "
        "BEGIN SELECT RAISE(ABORT, 'instrument is never deleted'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER instrument_no_delete")
    op.drop_index("uq_instrument_identifier_value", table_name="instrument_identifier")
    op.drop_index(
        op.f("ix_instrument_identifier_instrument_id"), table_name="instrument_identifier"
    )
    op.drop_table("instrument_identifier")
    op.drop_table("instrument")
