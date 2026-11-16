"""Rule capture method ``matched`` (ADR-0023 section 6).

``rule_capture.method`` gains a third value: ``matched``, a figure the page reader found by matching
words on the pasted page (not an AI call). ``method_known`` is a CHECK constraint, and SQLite cannot
widen a CHECK in place, so the table is rebuilt: the old one is renamed aside, the new one created
with the wider CHECK, every row copied across in ``id`` order with every column unchanged, and the
old one dropped. The partial unique index and the two append-only triggers are recreated from their
own stored SQL, byte for byte, so they cannot drift. No other table refers to ``rule_capture`` (only
its own ``superseded_by_id``).

Live-data rule: every row is kept exactly (the upgrade test compares them), and nothing is deleted
before the copy has been made inside the same transaction.

Downgrading rebuilds with the old CHECK. A ``matched`` row has no value there, so it becomes
``typed``; its quote, value and proposal (``ai_value_json``) are kept.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
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
    "ended_at",
    "ended_by",
    "ended_kind",
    "superseded_by_id",
)


def _rebuild(methods: str, method_expression: str) -> None:
    attached = op.get_bind().execute(
        sa.text(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE tbl_name = 'rule_capture' AND type IN ('index', 'trigger') AND sql IS NOT NULL "
            "ORDER BY type, name"
        )
    )
    saved = [(row[0], row[1], row[2]) for row in attached]
    for kind, name, _ in saved:
        op.execute(f"DROP {kind.upper()} {name}")
    op.execute("ALTER TABLE rule_capture RENAME TO rule_capture_old")
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
        sa.CheckConstraint(f"method IN ({methods})", name=op.f("ck_rule_capture_method_known")),
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
    names = ", ".join(_COLUMNS)
    values = ", ".join(method_expression if c == "method" else c for c in _COLUMNS)
    op.execute(
        f"INSERT INTO rule_capture ({names}) SELECT {values} FROM rule_capture_old ORDER BY id"  # noqa: S608
    )
    op.execute("DROP TABLE rule_capture_old")
    for _, _, sql in saved:  # the index first, then the triggers: as 0024 made them
        op.execute(sql)


def upgrade() -> None:
    _rebuild("'typed', 'ai', 'matched'", "method")


def downgrade() -> None:
    _rebuild("'typed', 'ai'", "CASE WHEN method = 'matched' THEN 'typed' ELSE method END")
