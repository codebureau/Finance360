"""Category, Account type and the meta table (ADR-0009 section 6). Schema only.

The rows come from the idempotent startup seeder, not from this migration.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("class", sa.String(length=20), nullable=False),
        sa.Column("seeded", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("class IN ('asset', 'liability')", name=op.f("ck_category_class_known")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category")),
        sa.UniqueConstraint("key", name=op.f("uq_category_key")),
    )
    op.create_table(
        "account_type",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("behaviour_key", sa.String(length=50), nullable=False),
        sa.Column("seeded", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"], ["category.id"], name=op.f("fk_account_type_category_id_category")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_type")),
        sa.UniqueConstraint("key", name=op.f("uq_account_type_key")),
    )
    op.create_table(
        "meta",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_meta")),
    )


def downgrade() -> None:
    op.drop_table("meta")
    op.drop_table("account_type")
    op.drop_table("category")
