"""Household facts (ADR-0029 section 1).

Additive only (live-data rule): three nullable columns on ``household``, added with SQLite's own
``ADD COLUMN`` so the table is not rebuilt. Null means "not entered": the service resolves a
labelled default, so every existing row keeps behaving as before.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE household ADD COLUMN couple_status VARCHAR(10) "
        "CONSTRAINT ck_household_couple_status_known "
        "CHECK (couple_status IN ('single', 'couple'))"
    )
    op.execute("ALTER TABLE household ADD COLUMN homeowner BOOLEAN")
    op.execute(
        "ALTER TABLE household ADD COLUMN principal_home_account_id INTEGER "
        "CONSTRAINT fk_household_principal_home_account_id_account REFERENCES account (id)"
    )


def downgrade() -> None:
    # Not a batch rebuild: other tables refer to ``household``. Each constraint belongs to its own
    # column, so SQLite lets it go with it.
    op.execute("ALTER TABLE household DROP COLUMN principal_home_account_id")
    op.execute("ALTER TABLE household DROP COLUMN homeowner")
    op.execute("ALTER TABLE household DROP COLUMN couple_status")
