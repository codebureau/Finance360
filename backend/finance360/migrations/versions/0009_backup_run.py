"""Backup run history (ADR-0011).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("shipped", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.CheckConstraint(
            "reason IN ('nightly', 'manual', 'pre-deploy', 'maintenance')",
            name=op.f("ck_backup_run_reason_known"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name=op.f("ck_backup_run_status_known")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backup_run")),
    )
    op.create_index(
        "uq_backup_run_running",
        "backup_run",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_backup_run_running", table_name="backup_run")
    op.drop_table("backup_run")
