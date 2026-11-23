"""Display preferences (ADR-0031 section 3): one presentation choice per Login and key.

Additive only (live-data rule): one new table; nothing existing is touched and no data is moved.
Downgrade drops the table, so a rollback needs no restore.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "display_preference",
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=24), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(key) BETWEEN 1 AND 64", name=op.f("ck_display_preference_key_length")
        ),
        sa.CheckConstraint(
            "length(value) BETWEEN 1 AND 24", name=op.f("ck_display_preference_value_length")
        ),
        sa.ForeignKeyConstraint(
            ["login_id"], ["login.id"], name=op.f("fk_display_preference_login_id_login")
        ),
        sa.PrimaryKeyConstraint("login_id", "key", name=op.f("pk_display_preference")),
    )


def downgrade() -> None:
    op.drop_table("display_preference")
