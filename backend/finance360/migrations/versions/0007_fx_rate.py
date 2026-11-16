"""FxRate with supersession (ADR-0009 section 4).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fx_rate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_ccy", sa.String(length=3), nullable=False),
        sa.Column("to_ccy", sa.String(length=3), nullable=False),
        sa.Column("as_at", sa.Date(), nullable=False),
        sa.Column("rate", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("provenance_note", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("length(from_ccy) = 3", name=op.f("ck_fx_rate_from_ccy_len")),
        sa.CheckConstraint("length(to_ccy) = 3", name=op.f("ck_fx_rate_to_ccy_len")),
        sa.CheckConstraint("from_ccy <> to_ccy", name=op.f("ck_fx_rate_pair_distinct")),
        sa.CheckConstraint("rate > 0", name=op.f("ck_fx_rate_rate_positive")),
        sa.CheckConstraint(
            "length(trim(coalesce(provenance_note, ''))) > 0",
            name=op.f("ck_fx_rate_note_required"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["fx_rate.id"], name=op.f("fk_fx_rate_superseded_by_id_fx_rate")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_rate")),
    )
    op.create_index(
        "uq_fx_rate_current",
        "fx_rate",
        ["from_ccy", "to_ccy", "as_at", "source"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_fx_rate_current", table_name="fx_rate")
    op.drop_table("fx_rate")
