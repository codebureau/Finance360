"""Ordered Drawdown policy per Scenario (ADR-0016 section 3).

Additive only (live-data rule): one nullable column on ``scenario``. NULL means the default order,
so every existing row is unchanged and behaves exactly as before.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scenario", sa.Column("drawdown_order", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("scenario", "drawdown_order")
