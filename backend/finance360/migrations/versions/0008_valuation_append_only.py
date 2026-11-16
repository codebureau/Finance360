"""Valuation and FxRate are append-only in the database itself.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALUATION_COLUMNS = (
    "id",
    "account_id",
    "as_at",
    "amount_minor",
    "amount_ccy",
    "source",
    "provenance_note",
    "import_id",
    "recorded_at",
)
_FX_RATE_COLUMNS = (
    "id",
    "from_ccy",
    "to_ccy",
    "as_at",
    "rate",
    "source",
    "provenance_note",
    "recorded_at",
)
#: table -> (columns other than the supersession pair, the scaled-integer column to type-check)
_TABLES = {
    "valuation": (_VALUATION_COLUMNS, "amount_minor"),
    "fx_rate": (_FX_RATE_COLUMNS, "rate"),
}


def upgrade() -> None:
    for table, (columns, integer_column) in _TABLES.items():
        unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in columns)
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )
        # Only supersession may touch a row: superseded_at is set once on a current row, and
        # superseded_by_id is linked once afterwards (the service inserts the replacement between).
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            f"WHEN NOT ({unchanged} AND OLD.superseded_by_id IS NULL AND ("
            "(OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)"
            " OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS OLD.superseded_at"
            " AND NEW.superseded_by_id IS NOT NULL))) "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_{integer_column}_integer BEFORE INSERT ON {table} "
            f"WHEN typeof(NEW.{integer_column}) IS NOT 'integer' "
            f"BEGIN SELECT RAISE(ABORT, '{table} {integer_column} must be an integer'); END"
        )


def downgrade() -> None:
    for table, (_, integer_column) in _TABLES.items():
        op.execute(f"DROP TRIGGER {table}_{integer_column}_integer")
        op.execute(f"DROP TRIGGER {table}_no_update")
        op.execute(f"DROP TRIGGER {table}_no_delete")
