"""Valuations with supersession, and the Household time zone (ADR-0009 section 3).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "household",
        sa.Column(
            "time_zone",
            sa.String(length=64),
            nullable=False,
            server_default="Australia/Melbourne",
        ),
    )
    op.create_table(
        "valuation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("as_at", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("amount_ccy", sa.String(length=3), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("provenance_note", sa.Text(), nullable=True),
        sa.Column("import_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("length(amount_ccy) = 3", name=op.f("ck_valuation_amount_ccy_len")),
        sa.CheckConstraint(
            "source IN ('manual', 'statement', 'derived', 'estimate')",
            name=op.f("ck_valuation_source_known"),
        ),
        sa.CheckConstraint(
            "NOT (source IN ('manual', 'statement', 'estimate'))"
            " OR length(trim(coalesce(provenance_note, ''))) > 0",
            name=op.f("ck_valuation_note_required"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name=op.f("fk_valuation_account_id_account")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["valuation.id"],
            name=op.f("fk_valuation_superseded_by_id_valuation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_valuation")),
    )
    op.create_index(op.f("ix_valuation_account_id"), "valuation", ["account_id"], unique=False)
    op.create_index(
        "uq_valuation_current",
        "valuation",
        ["account_id", "as_at", "source"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_valuation_current", table_name="valuation")
    op.drop_index(op.f("ix_valuation_account_id"), table_name="valuation")
    op.drop_table("valuation")
    with op.batch_alter_table("household") as batch:
        batch.drop_column("time_zone")
