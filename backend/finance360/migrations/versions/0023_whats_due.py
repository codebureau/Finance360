"""What's due: the Household Effort level and stored Snoozes (ADR-0020).

Additive only (live-data rule): one new column on ``household`` (default ``light``, so every
existing row reads Light until the user chooses) and one new table. Nothing existing is
changed. The column is added with SQLite's own ``ADD COLUMN ... CHECK`` so the table is not rebuilt.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE household ADD COLUMN effort_level VARCHAR(10) NOT NULL DEFAULT 'light' "
        "CONSTRAINT ck_household_effort_level_known "
        "CHECK (effort_level IN ('light', 'steady', 'thorough'))"
    )
    op.create_table(
        "due_snooze",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_key", sa.String(length=100), nullable=False),
        sa.Column("subject_key", sa.String(length=200), nullable=False),
        sa.Column("until_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(rule_key)) > 0", name=op.f("ck_due_snooze_rule_key_not_blank")
        ),
        sa.CheckConstraint(
            "length(trim(subject_key)) > 0", name=op.f("ck_due_snooze_subject_key_not_blank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_due_snooze")),
        sa.UniqueConstraint("rule_key", "subject_key", name=op.f("uq_due_snooze_subject")),
    )


def downgrade() -> None:
    op.drop_table("due_snooze")
    # Not a batch rebuild: other tables refer to ``household``. The CHECK belongs to this column, so
    # SQLite lets it go with it.
    op.execute("ALTER TABLE household DROP COLUMN effort_level")
