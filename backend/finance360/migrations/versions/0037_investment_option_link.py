"""Investment option links (ADR-0030 section 4, #86).

Additive only (live-data rule): one new table and its indexes. Nothing existing is touched and no
data is moved, so a rollback needs no restore. A link maps one super Investment option name, per
Account, to the Instrument that holds its Exposure profile. It is edited in place (the audit trail
is its history), and unique on the Account and the normalised option name.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investment_option_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("option_name", sa.String(length=100), nullable=False),
        sa.Column("option_name_norm", sa.String(length=100), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(trim(option_name)) > 0",
            name=op.f("ck_investment_option_link_option_name_required"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_investment_option_link_account_id_account"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.id"],
            name=op.f("fk_investment_option_link_instrument_id_instrument"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investment_option_link")),
        sa.UniqueConstraint(
            "account_id",
            "option_name_norm",
            name=op.f("uq_investment_option_link_account_name"),
        ),
    )
    op.create_index(
        op.f("ix_investment_option_link_account_id"),
        "investment_option_link",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_investment_option_link_instrument_id"),
        "investment_option_link",
        ["instrument_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_investment_option_link_instrument_id"), table_name="investment_option_link"
    )
    op.drop_index(op.f("ix_investment_option_link_account_id"), table_name="investment_option_link")
    op.drop_table("investment_option_link")
