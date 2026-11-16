"""Income stream and its owners (ADR-0017 section 2).

Additive only (live-data rule): two new tables. No data and nothing existing is touched: income
streams are stated by the household, and the fixture loader writes the synthetic ones.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "income_stream",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("type_key", sa.String(length=30), nullable=False),
        sa.Column("ownership", sa.String(length=10), nullable=False),
        sa.Column("gross_annual_minor", sa.BigInteger(), nullable=False),
        sa.Column("gross_annual_ccy", sa.String(length=3), nullable=False),
        sa.Column("take_home_annual_minor", sa.BigInteger(), nullable=True),
        sa.Column("take_home_annual_ccy", sa.String(length=3), nullable=True),
        sa.Column("start_on", sa.Date(), nullable=True),
        sa.Column("start_event", sa.String(length=30), nullable=True),
        sa.Column("end_on", sa.Date(), nullable=True),
        sa.Column("end_event", sa.String(length=30), nullable=True),
        sa.Column("credit_category", sa.String(length=30), server_default="cash", nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_income_stream_name_not_blank")),
        sa.CheckConstraint(
            "ownership IN ('sole', 'joint')", name=op.f("ck_income_stream_ownership_known")
        ),
        sa.CheckConstraint("gross_annual_minor > 0", name=op.f("ck_income_stream_gross_positive")),
        sa.CheckConstraint(
            "length(gross_annual_ccy) = 3", name=op.f("ck_income_stream_gross_annual_ccy_len")
        ),
        sa.CheckConstraint(
            "(take_home_annual_minor IS NULL) = (take_home_annual_ccy IS NULL)",
            name=op.f("ck_income_stream_take_home_pair"),
        ),
        sa.CheckConstraint(
            "take_home_annual_ccy IS NULL OR length(take_home_annual_ccy) = 3",
            name=op.f("ck_income_stream_take_home_ccy_len"),
        ),
        sa.CheckConstraint(
            "take_home_annual_minor IS NULL OR take_home_annual_minor >= 0",
            name=op.f("ck_income_stream_take_home_not_negative"),
        ),
        sa.CheckConstraint(
            "start_on IS NULL OR start_event IS NULL", name=op.f("ck_income_stream_one_start")
        ),
        sa.CheckConstraint(
            "end_on IS NULL OR end_event IS NULL", name=op.f("ck_income_stream_one_end")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_income_stream")),
    )
    op.create_table(
        "income_stream_owner",
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("share_bp", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "share_bp > 0 AND share_bp <= 10000", name=op.f("ck_income_stream_owner_share_bp_range")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["member.id"], name=op.f("fk_income_stream_owner_member_id_member")
        ),
        sa.ForeignKeyConstraint(
            ["stream_id"],
            ["income_stream.id"],
            name=op.f("fk_income_stream_owner_stream_id_income_stream"),
        ),
        sa.PrimaryKeyConstraint("stream_id", "member_id", name=op.f("pk_income_stream_owner")),
    )


def downgrade() -> None:
    op.drop_table("income_stream_owner")
    op.drop_table("income_stream")
