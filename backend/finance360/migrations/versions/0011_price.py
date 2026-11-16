"""Price with supersession, append-only in the database (ADR-0013 sections 1 and 2).

Additive only (live-data rule): one new table, its indexes and its triggers. Nothing existing is
touched.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "id",
    "instrument_id",
    "as_at",
    "value",
    "ccy",
    "source",
    "provenance_note",
    "recorded_at",
)


def upgrade() -> None:
    op.create_table(
        "price",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("as_at", sa.Date(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("ccy", sa.String(length=3), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("provenance_note", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("length(ccy) = 3", name=op.f("ck_price_ccy_len")),
        sa.CheckConstraint("value > 0", name=op.f("ck_price_value_positive")),
        sa.CheckConstraint(
            "length(trim(coalesce(provenance_note, ''))) > 0", name=op.f("ck_price_note_required")
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instrument.id"], name=op.f("fk_price_instrument_id_instrument")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["price.id"], name=op.f("fk_price_superseded_by_id_price")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price")),
    )
    op.create_index(op.f("ix_price_instrument_id"), "price", ["instrument_id"], unique=False)
    op.create_index(
        "uq_price_current",
        "price",
        ["instrument_id", "as_at", "source"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
    )
    # Append-only, as for valuation and fx_rate (0008): a row is only ever superseded.
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in _COLUMNS)
    op.execute(
        "CREATE TRIGGER price_no_delete BEFORE DELETE ON price "
        "BEGIN SELECT RAISE(ABORT, 'price is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER price_no_update BEFORE UPDATE ON price "
        f"WHEN NOT ({unchanged} AND OLD.superseded_by_id IS NULL AND ("
        "(OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)"
        " OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS OLD.superseded_at"
        " AND NEW.superseded_by_id IS NOT NULL))) "
        "BEGIN SELECT RAISE(ABORT, 'price is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER price_value_integer BEFORE INSERT ON price "
        "WHEN typeof(NEW.value) IS NOT 'integer' "
        "BEGIN SELECT RAISE(ABORT, 'price value must be an integer'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER price_value_integer")
    op.execute("DROP TRIGGER price_no_update")
    op.execute("DROP TRIGGER price_no_delete")
    op.drop_index("uq_price_current", table_name="price")
    op.drop_index(op.f("ix_price_instrument_id"), table_name="price")
    op.drop_table("price")
