"""Account, ownership entries and Tags (ADR-0009 section 2).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tag",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_tag_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tag")),
        sa.UniqueConstraint("name", name=op.f("uq_tag_name")),
    )
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("account_type_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("ownership", sa.String(length=10), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("opened_on", sa.Date(), nullable=True),
        sa.Column("closed_on", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "ownership IN ('sole', 'joint')", name=op.f("ck_account_ownership_known")
        ),
        sa.CheckConstraint("length(currency) = 3", name=op.f("ck_account_currency_len")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_account_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["account_type_id"],
            ["account_type.id"],
            name=op.f("fk_account_account_type_id_account_type"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["category.id"], name=op.f("fk_account_category_id_category")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
    )
    op.create_table(
        "account_owner",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("share_bp", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "share_bp > 0 AND share_bp <= 10000", name=op.f("ck_account_owner_share_bp_range")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name=op.f("fk_account_owner_account_id_account")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["member.id"], name=op.f("fk_account_owner_member_id_member")
        ),
        sa.PrimaryKeyConstraint("account_id", "member_id", name=op.f("pk_account_owner")),
    )
    op.create_table(
        "account_tag",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name=op.f("fk_account_tag_account_id_account")
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], name=op.f("fk_account_tag_tag_id_tag")),
        sa.PrimaryKeyConstraint("account_id", "tag_id", name=op.f("pk_account_tag")),
    )


def downgrade() -> None:
    op.drop_table("account_tag")
    op.drop_table("account_owner")
    op.drop_table("account")
    op.drop_table("tag")
