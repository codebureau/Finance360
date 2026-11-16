"""Fetch run history (ADR-0013 sections 2 and 6).

Additive only (live-data rule): one new table and one index. Nothing existing is touched.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fetch_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("trigger", sa.String(length=10), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("added", sa.Integer(), nullable=False),
        sa.Column("revised", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.CheckConstraint("dataset IN ('prices', 'fx')", name=op.f("ck_fetch_run_dataset_known")),
        sa.CheckConstraint(
            "trigger IN ('scheduled', 'catch-up', 'manual')",
            name=op.f("ck_fetch_run_trigger_known"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'partial', 'failed')",
            name=op.f("ck_fetch_run_status_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fetch_run")),
    )
    op.create_index(
        "uq_fetch_run_running",
        "fetch_run",
        ["dataset"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_fetch_run_running", table_name="fetch_run")
    op.drop_table("fetch_run")
