"""Saved Document profiles: the ``document_profile`` table (ADR-0027 section 5).

Additive only (live-data rule): one new table, its indexes and its triggers. Nothing existing is
touched. The table is append-only in the database itself: no DELETE, and an UPDATE may only set a
lifecycle column once. Activating a row sets ``activated_at`` and ``activated_by`` on a row that has
neither and has not ended; ending a row sets ``ended_at``, ``ended_by`` and ``ended_reason`` on a
row that has not ended. Every other column, including the profile itself, never changes. At most
one row is active per ``key``, by a partial unique index.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Everything but the lifecycle columns: never changes once written.
_UNCHANGED = (
    "id",
    "key",
    "version",
    "target",
    "profile_json",
    "source",
    "proposed_by",
    "proposed_token_id",
    "created_at",
    "test_document_id",
    "test_document_sha256",
    "test_snapshot",
    "ai_log_id",
)


def upgrade() -> None:
    op.create_table(
        "document_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("proposed_by", sa.String(length=100), nullable=False),
        sa.Column("proposed_token_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("test_document_id", sa.Integer(), nullable=False),
        sa.Column("test_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("test_snapshot", sa.JSON(), nullable=False),
        sa.Column("ai_log_id", sa.Integer(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_by", sa.String(length=100), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("ended_by", sa.String(length=100), nullable=True),
        sa.Column("ended_reason", sa.String(length=20), nullable=True),
        sa.CheckConstraint(
            "source IN ('pasted', 'mcp', 'ai')", name=op.f("ck_document_profile_source_known")
        ),
        sa.CheckConstraint(
            "length(test_document_sha256) = 64", name=op.f("ck_document_profile_test_sha256_length")
        ),
        sa.CheckConstraint(
            "json_valid(profile_json)", name=op.f("ck_document_profile_profile_json_valid")
        ),
        sa.CheckConstraint(
            "json_valid(test_snapshot)", name=op.f("ck_document_profile_test_snapshot_valid")
        ),
        sa.CheckConstraint(
            "(activated_at IS NULL) = (activated_by IS NULL)",
            name=op.f("ck_document_profile_activated_together"),
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (ended_by IS NULL)"
            " AND (ended_at IS NULL) = (ended_reason IS NULL)",
            name=op.f("ck_document_profile_ended_together"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_profile")),
        sa.UniqueConstraint("key", "version", name=op.f("uq_document_profile_key")),
    )
    op.create_index(
        "uq_document_profile_active",
        "document_profile",
        ["key"],
        unique=True,
        sqlite_where=sa.text("activated_at IS NOT NULL AND ended_at IS NULL"),
    )
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in _UNCHANGED)
    op.execute(
        "CREATE TRIGGER document_profile_no_delete BEFORE DELETE ON document_profile "
        "BEGIN SELECT RAISE(ABORT, 'document_profile is append-only'); END"
    )
    # Two moves only, each setting columns that were empty: activate a proposed row, or end a row
    # that has not ended (proposed or active).
    op.execute(
        "CREATE TRIGGER document_profile_no_update BEFORE UPDATE ON document_profile "
        f"WHEN NOT ({unchanged} AND ("
        "(OLD.activated_at IS NULL AND OLD.ended_at IS NULL AND NEW.activated_at IS NOT NULL"
        " AND NEW.ended_at IS NULL AND NEW.ended_by IS NULL AND NEW.ended_reason IS NULL)"
        " OR (OLD.ended_at IS NULL AND NEW.ended_at IS NOT NULL"
        " AND NEW.activated_at IS OLD.activated_at AND NEW.activated_by IS OLD.activated_by))) "
        "BEGIN SELECT RAISE(ABORT, 'document_profile is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER document_profile_no_update")
    op.execute("DROP TRIGGER document_profile_no_delete")
    op.drop_index("uq_document_profile_active", table_name="document_profile")
    op.drop_table("document_profile")
