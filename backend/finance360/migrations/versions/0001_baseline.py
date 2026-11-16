"""Baseline: an empty schema, so the migration machinery has a version to report.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No domain tables yet; they arrive with their slices (ADR-0009 sections 2 to 6)."""


def downgrade() -> None:
    """Nothing to undo."""
