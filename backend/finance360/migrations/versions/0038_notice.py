"""Notice (ADR-0033): the small stored "what you added moved this" record behind Recent changes.

Additive only (live-data rule): one new table; nothing existing is touched and no data is moved.
Downgrade drops the table, so a rollback needs no restore.

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notice",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("headline", sa.String(length=300), nullable=False),
        sa.Column("effect", sa.String(length=1000), nullable=True),
        sa.Column("delta_minor", sa.Integer(), nullable=True),
        sa.Column("delta_currency", sa.String(length=3), nullable=True),
        sa.Column("link_path", sa.String(length=200), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("length(trim(kind)) > 0", name=op.f("ck_notice_kind_not_blank")),
        sa.CheckConstraint("length(trim(headline)) > 0", name=op.f("ck_notice_headline_not_blank")),
        sa.CheckConstraint(
            "(delta_minor IS NULL) = (delta_currency IS NULL)",
            name=op.f("ck_notice_delta_both_or_neither"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notice")),
    )


def downgrade() -> None:
    op.drop_table("notice")
