"""Captured Rule figures: the ``rule_capture`` table (ADR-0022).

Additive only (live-data rule): one new table, nothing existing is touched. The table is
append-only in the database itself: no DELETE, and an UPDATE may only end a row (``ended_at``,
``ended_by``, ``ended_kind``, then link ``superseded_by_id`` once). At most one row is active per
``(jurisdiction, key, fy)``.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNCHANGED = (
    "id",
    "jurisdiction",
    "key",
    "fy",
    "value_json",
    "provisional_note",
    "quote",
    "page_url",
    "retrieved_on",
    "method",
    "ai_log_id",
    "ai_value_json",
    "captured_at",
    "captured_kind",
    "captured_by",
)


def upgrade() -> None:
    op.create_table(
        "rule_capture",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=20), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("fy", sa.Integer(), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("provisional_note", sa.Text(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("page_url", sa.String(length=500), nullable=True),
        sa.Column("retrieved_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("ai_log_id", sa.Integer(), nullable=True),
        sa.Column("ai_value_json", sa.JSON(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("captured_kind", sa.String(length=20), nullable=False),
        sa.Column("captured_by", sa.String(length=100), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("ended_by", sa.String(length=100), nullable=True),
        sa.Column("ended_kind", sa.String(length=10), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("method IN ('typed', 'ai')", name=op.f("ck_rule_capture_method_known")),
        sa.CheckConstraint(
            "ended_kind IS NULL OR ended_kind IN ('withdrawn', 'superseded')",
            name=op.f("ck_rule_capture_ended_kind_known"),
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (ended_kind IS NULL)", name=op.f("ck_rule_capture_ended_together")
        ),
        sa.CheckConstraint("length(trim(key)) > 0", name=op.f("ck_rule_capture_key_not_blank")),
        sa.CheckConstraint(
            "provisional_note IS NULL OR length(trim(provisional_note)) > 0",
            name=op.f("ck_rule_capture_provisional_note_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["rule_capture.id"],
            name=op.f("fk_rule_capture_superseded_by_id_rule_capture"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_capture")),
    )
    op.create_index(
        "uq_rule_capture_active",
        "rule_capture",
        ["jurisdiction", "key", "fy"],
        unique=True,
        sqlite_where=sa.text("ended_at IS NULL"),
    )
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in _UNCHANGED)
    op.execute(
        "CREATE TRIGGER rule_capture_no_delete BEFORE DELETE ON rule_capture "
        "BEGIN SELECT RAISE(ABORT, 'rule_capture is append-only'); END"
    )
    # Only ending a row may touch it: ``ended_*`` is set once on an active row, and
    # ``superseded_by_id`` is linked once afterwards (the service inserts the replacement between).
    op.execute(
        "CREATE TRIGGER rule_capture_no_update BEFORE UPDATE ON rule_capture "
        f"WHEN NOT ({unchanged} AND OLD.superseded_by_id IS NULL AND ("
        "(OLD.ended_at IS NULL AND NEW.ended_at IS NOT NULL AND NEW.superseded_by_id IS NULL)"
        " OR (OLD.ended_at IS NOT NULL AND NEW.ended_at IS OLD.ended_at"
        " AND NEW.ended_by IS OLD.ended_by AND NEW.ended_kind IS OLD.ended_kind"
        " AND OLD.ended_kind = 'superseded' AND NEW.superseded_by_id IS NOT NULL))) "
        "BEGIN SELECT RAISE(ABORT, 'rule_capture is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER rule_capture_no_update")
    op.execute("DROP TRIGGER rule_capture_no_delete")
    op.drop_index("uq_rule_capture_active", table_name="rule_capture")
    op.drop_table("rule_capture")
