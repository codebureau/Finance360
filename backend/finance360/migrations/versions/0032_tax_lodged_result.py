"""Lodged result (ADR-0019 sections 5 and 8).

Additive only (live-data rule): one new table, its indexes and its triggers. Nothing existing is
touched. ``tax_lodged_result`` is append-only in the database itself, as for ``valuation`` and
``super_statement``: a row is only ever superseded (by an amended notice), never changed or
removed. Money sits in canonical JSON (``json_valid`` checked, integer minor units).

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every column except the supersession pair: what a supersession must leave unchanged.
_COLUMNS = (
    "id",
    "member_id",
    "financial_year",
    "assessed_on",
    "items_json",
    "other_json",
    "recorded_at",
)


def upgrade() -> None:
    op.create_table(
        "tax_lodged_result",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("assessed_on", sa.Date(), nullable=False),
        sa.Column("items_json", sa.Text(), nullable=False),
        sa.Column("other_json", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "json_valid(items_json)", name=op.f("ck_tax_lodged_result_items_json_valid")
        ),
        sa.CheckConstraint(
            "json_valid(other_json)", name=op.f("ck_tax_lodged_result_other_json_valid")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["member.id"], name=op.f("fk_tax_lodged_result_member_id_member")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["tax_lodged_result.id"],
            name=op.f("fk_tax_lodged_result_superseded_by_id_tax_lodged_result"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_lodged_result")),
    )
    op.create_index(
        op.f("ix_tax_lodged_result_member_id"), "tax_lodged_result", ["member_id"], unique=False
    )
    op.create_index(
        "uq_tax_lodged_result_current",
        "tax_lodged_result",
        ["member_id", "financial_year"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
    )
    # Append-only: a row is superseded (set once, then linked once), never changed or removed.
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in _COLUMNS)
    op.execute(
        "CREATE TRIGGER tax_lodged_result_no_delete BEFORE DELETE ON tax_lodged_result "
        "BEGIN SELECT RAISE(ABORT, 'tax_lodged_result is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER tax_lodged_result_no_update BEFORE UPDATE ON tax_lodged_result "
        f"WHEN NOT ({unchanged} AND OLD.superseded_by_id IS NULL AND ("
        "(OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)"
        " OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS OLD.superseded_at"
        " AND NEW.superseded_by_id IS NOT NULL))) "
        "BEGIN SELECT RAISE(ABORT, 'tax_lodged_result is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER tax_lodged_result_no_update")
    op.execute("DROP TRIGGER tax_lodged_result_no_delete")
    op.drop_index("uq_tax_lodged_result_current", table_name="tax_lodged_result")
    op.drop_index(op.f("ix_tax_lodged_result_member_id"), table_name="tax_lodged_result")
    op.drop_table("tax_lodged_result")
