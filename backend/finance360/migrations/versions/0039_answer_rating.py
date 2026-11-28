"""Answer rating (ADR-0032 section 6): the owner's Yes, Partly or No on a Question's answer.

Additive only (live-data rule): one new table; nothing existing is touched and no data is moved.
Downgrade drops the table, so a rollback needs no restore.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_rating",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("question_key", sa.String(length=100), nullable=False),
        sa.Column("answer", sa.String(length=10), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("readiness_at_rating", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "answer IN ('yes', 'partly', 'no')", name=op.f("ck_answer_rating_answer_known")
        ),
        sa.CheckConstraint(
            "note IS NULL OR length(note) <= 200", name=op.f("ck_answer_rating_note_length")
        ),
        sa.CheckConstraint(
            "length(trim(question_key)) > 0", name=op.f("ck_answer_rating_question_key_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["login_id"], ["login.id"], name=op.f("fk_answer_rating_login_id_login")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_rating")),
    )


def downgrade() -> None:
    op.drop_table("answer_rating")
