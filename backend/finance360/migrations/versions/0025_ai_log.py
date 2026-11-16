"""The AI log (ADR-0021 section 7).

Additive only (live-data rule): one new table, nothing existing is changed.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("task", sa.String(length=50), nullable=False),
        sa.Column("data_class", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("sent", sa.Text(), nullable=False),
        sa.Column("public_parts", sa.Text(), nullable=False),
        sa.Column("received", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd_micros", sa.Integer(), nullable=False),
        sa.Column("cost_estimated", sa.Boolean(), nullable=False),
        sa.Column("why", sa.String(length=200), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('ok', 'unsupported', 'error', 'refused_cap')",
            name=op.f("ck_ai_log_outcome_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_log")),
    )
    op.create_index("ix_ai_log_occurred_at", "ai_log", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_log_occurred_at", table_name="ai_log")
    op.drop_table("ai_log")
