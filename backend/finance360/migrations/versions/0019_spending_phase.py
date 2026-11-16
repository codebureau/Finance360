"""Spending phases replace the ``annual_spending`` Assumption (ADR-0017 section 6).

A new table, then a conversion of live data. Each ``annual_spending`` row of any layer (the
Household default, or a Scenario's override) becomes a ``(last_retirement, 0)`` phase in the
same layer with the same amount and currency, and the key is retired: its rows are deleted in the
same transaction. That is exactly what the key meant (spending from the year every Member has
retired, to the horizon), so every projection is unchanged. A Scenario that never stated the key
still has no phase rows and inherits the Household default list. A stored amount of 0 is kept
as a 0 phase (the table allows it for this reason), which projects as no spending, as 0 did.

Downgrade converts the ``(last_retirement, 0)`` phases back to ``annual_spending`` rows and
drops the table; any other phase has no old equivalent and is lost.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spending_phase",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("anchor", sa.String(length=20), nullable=False),
        sa.Column("offset_years", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("amount_ccy", sa.String(length=3), nullable=False),
        sa.CheckConstraint(
            "anchor IN ('start', 'last_retirement')",
            name=op.f("ck_spending_phase_anchor_known"),
        ),
        sa.CheckConstraint("offset_years >= 0", name=op.f("ck_spending_phase_offset_not_negative")),
        sa.CheckConstraint("amount_minor >= 0", name=op.f("ck_spending_phase_amount_not_negative")),
        sa.CheckConstraint("length(amount_ccy) = 3", name=op.f("ck_spending_phase_amount_ccy_len")),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenario.id"], name=op.f("fk_spending_phase_scenario_id_scenario")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spending_phase")),
    )
    op.create_index(
        "uq_spending_phase_default",
        "spending_phase",
        ["anchor", "offset_years"],
        unique=True,
        sqlite_where=sa.text("scenario_id IS NULL"),
    )
    op.create_index(
        "uq_spending_phase_override",
        "spending_phase",
        ["scenario_id", "anchor", "offset_years"],
        unique=True,
        sqlite_where=sa.text("scenario_id IS NOT NULL"),
    )
    op.execute(
        "INSERT INTO spending_phase (scenario_id, anchor, offset_years, amount_minor, amount_ccy) "
        "SELECT scenario_id, 'last_retirement', 0, value_minor, value_ccy "
        "FROM assumption WHERE key = 'annual_spending' ORDER BY id"
    )
    op.execute("DELETE FROM assumption WHERE key = 'annual_spending'")


def downgrade() -> None:
    op.execute(
        "INSERT INTO assumption (scenario_id, key, scope_type, scope_ref, value_minor, value_ccy) "
        "SELECT scenario_id, 'annual_spending', 'household', '', amount_minor, amount_ccy "
        "FROM spending_phase WHERE anchor = 'last_retirement' AND offset_years = 0 ORDER BY id"
    )
    op.drop_index("uq_spending_phase_override", table_name="spending_phase")
    op.drop_index("uq_spending_phase_default", table_name="spending_phase")
    op.drop_table("spending_phase")
