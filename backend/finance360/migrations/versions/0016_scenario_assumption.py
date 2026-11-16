"""Scenario and Assumption (ADR-0016 section 1).

Additive only (live-data rule): two new tables and two partial unique indexes. No data: the seeded
Household defaults and Best / Likely / Worst come from the startup seeder. Nothing existing is
touched.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=20), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_scenario_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario")),
        sa.UniqueConstraint("key", name=op.f("uq_scenario_key")),
        sa.UniqueConstraint("name", name=op.f("uq_scenario_name")),
    )
    op.create_table(
        "assumption",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_ref", sa.String(length=50), nullable=False),
        sa.Column("value_rate_ppm", sa.Integer(), nullable=True),
        sa.Column("value_int", sa.Integer(), nullable=True),
        sa.Column("value_minor", sa.BigInteger(), nullable=True),
        sa.Column("value_ccy", sa.String(length=3), nullable=True),
        sa.CheckConstraint(
            "scope_type IN ('household', 'category', 'member')",
            name=op.f("ck_assumption_scope_type_known"),
        ),
        sa.CheckConstraint(
            "(scope_type = 'household') = (scope_ref = '')",
            name=op.f("ck_assumption_scope_ref_matches_type"),
        ),
        sa.CheckConstraint(
            "(value_rate_ppm IS NOT NULL) + (value_int IS NOT NULL) "
            "+ (value_minor IS NOT NULL) = 1",
            name=op.f("ck_assumption_one_value"),
        ),
        sa.CheckConstraint(
            "(value_minor IS NULL) = (value_ccy IS NULL)", name=op.f("ck_assumption_money_pair")
        ),
        sa.CheckConstraint(
            "value_ccy IS NULL OR length(value_ccy) = 3", name=op.f("ck_assumption_value_ccy_len")
        ),
        sa.CheckConstraint(
            "value_rate_ppm IS NULL OR (value_rate_ppm > -1000000 AND value_rate_ppm <= 1000000)",
            name=op.f("ck_assumption_rate_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenario.id"], name=op.f("fk_assumption_scenario_id_scenario")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assumption")),
    )
    op.create_index(
        "uq_assumption_default",
        "assumption",
        ["key", "scope_type", "scope_ref"],
        unique=True,
        sqlite_where=sa.text("scenario_id IS NULL"),
    )
    op.create_index(
        "uq_assumption_override",
        "assumption",
        ["scenario_id", "key", "scope_type", "scope_ref"],
        unique=True,
        sqlite_where=sa.text("scenario_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_assumption_override", table_name="assumption")
    op.drop_index("uq_assumption_default", table_name="assumption")
    op.drop_table("assumption")
    op.drop_table("scenario")
