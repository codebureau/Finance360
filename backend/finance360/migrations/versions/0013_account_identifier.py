"""Account identifier with supersession, append-only in the database (ADR-0013 sections 1 and 5a).

Additive only (live-data rule): one new table, its indexes and its triggers. Nothing existing is
touched.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "id",
    "account_id",
    "kind",
    "value",
    "value_norm",
    "institution",
    "institution_norm",
    "recorded_at",
)


def upgrade() -> None:
    op.create_table(
        "account_identifier",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=100), nullable=False),
        sa.Column("value_norm", sa.String(length=100), nullable=False),
        sa.Column("institution", sa.String(length=100), nullable=True),
        sa.Column("institution_norm", sa.String(length=100), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('bsb_account', 'last4', 'member_number', 'policy_number', "
            "'vendor_account_id')",
            name=op.f("ck_account_identifier_kind_known"),
        ),
        sa.CheckConstraint(
            "length(trim(value)) > 0", name=op.f("ck_account_identifier_value_required")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name=op.f("fk_account_identifier_account_id_account")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["account_identifier.id"],
            name=op.f("fk_account_identifier_superseded_by_id_account_identifier"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_identifier")),
    )
    op.create_index(op.f("ix_account_identifier_account_id"), "account_identifier", ["account_id"])
    op.create_index(
        "uq_account_identifier_current",
        "account_identifier",
        ["kind", "value_norm", "institution_norm"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
    )
    # Append-only, as for valuation, fx_rate and price: a row is only ever superseded.
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in _COLUMNS)
    op.execute(
        "CREATE TRIGGER account_identifier_no_delete BEFORE DELETE ON account_identifier "
        "BEGIN SELECT RAISE(ABORT, 'account_identifier is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER account_identifier_no_update BEFORE UPDATE ON account_identifier "
        f"WHEN NOT ({unchanged} AND OLD.superseded_by_id IS NULL AND ("
        "(OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)"
        " OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS OLD.superseded_at"
        " AND NEW.superseded_by_id IS NOT NULL))) "
        "BEGIN SELECT RAISE(ABORT, 'account_identifier is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER account_identifier_no_update")
    op.execute("DROP TRIGGER account_identifier_no_delete")
    op.drop_index("uq_account_identifier_current", table_name="account_identifier")
    op.drop_index(op.f("ix_account_identifier_account_id"), table_name="account_identifier")
    op.drop_table("account_identifier")
