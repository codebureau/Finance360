"""Transaction with supersession, append-only in the database (ADR-0013 sections 1 and 2).

Additive only (live-data rule): one new table, its indexes and its triggers. Nothing existing is
touched.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "id",
    "account_id",
    "stream",
    "import_id",
    "row_no",
    "occurred_on",
    "kind",
    "instrument_id",
    "quantity",
    "unit_price",
    "unit_price_ccy",
    "fee_minor",
    "fee_ccy",
    "amount_minor",
    "amount_ccy",
    "recorded_at",
)
#: Columns that hold exact integers (scaled quantity and price, minor-unit money): never a float.
_INTEGER_COLUMNS = ("quantity", "unit_price", "fee_minor", "amount_minor")


def upgrade() -> None:
    op.create_table(
        "transaction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("stream", sa.String(length=10), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=True),
        sa.Column("row_no", sa.Integer(), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("unit_price", sa.Integer(), nullable=True),
        sa.Column("unit_price_ccy", sa.String(length=3), nullable=True),
        sa.Column("fee_minor", sa.BigInteger(), nullable=True),
        sa.Column("fee_ccy", sa.String(length=3), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("amount_ccy", sa.String(length=3), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(stream = 'trade' AND kind IN ('buy', 'sell', 'opening_balance'))"
            " OR (stream = 'cash' AND kind IN ('deposit', 'withdrawal', 'trade_settlement', "
            "'distribution', 'interest', 'fee', 'opening_balance'))",
            name=op.f("ck_transaction_kind_fits_stream"),
        ),
        sa.CheckConstraint(
            "stream <> 'trade' OR quantity IS NOT NULL",
            name=op.f("ck_transaction_trade_has_quantity"),
        ),
        sa.CheckConstraint(
            "stream <> 'cash' OR amount_minor IS NOT NULL",
            name=op.f("ck_transaction_cash_has_amount"),
        ),
        sa.CheckConstraint(
            "(fee_minor IS NULL) = (fee_ccy IS NULL)"
            " AND (amount_minor IS NULL) = (amount_ccy IS NULL)",
            name=op.f("ck_transaction_money_pairs"),
        ),
        sa.CheckConstraint(
            "(unit_price IS NULL) = (unit_price_ccy IS NULL)",
            name=op.f("ck_transaction_unit_price_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name=op.f("fk_transaction_account_id_account")
        ),
        sa.ForeignKeyConstraint(
            ["import_id"], ["import.id"], name=op.f("fk_transaction_import_id_import")
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.id"],
            name=op.f("fk_transaction_instrument_id_instrument"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["transaction.id"],
            name=op.f("fk_transaction_superseded_by_id_transaction"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transaction")),
    )
    op.create_index("ix_transaction_account_stream", "transaction", ["account_id", "stream"])
    op.create_index("ix_transaction_import_id", "transaction", ["import_id"])
    op.create_index("ix_transaction_instrument_id", "transaction", ["instrument_id"])
    op.create_index(
        "ix_transaction_current",
        "transaction",
        ["account_id", "stream", "occurred_on"],
        sqlite_where=sa.text("superseded_at IS NULL"),
    )
    # Append-only, as for valuation, fx_rate and price: a row is only ever superseded.
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in _COLUMNS)
    op.execute(
        'CREATE TRIGGER transaction_no_delete BEFORE DELETE ON "transaction" '
        "BEGIN SELECT RAISE(ABORT, 'transaction is append-only'); END"
    )
    op.execute(
        'CREATE TRIGGER transaction_no_update BEFORE UPDATE ON "transaction" '
        f"WHEN NOT ({unchanged} AND OLD.superseded_by_id IS NULL AND ("
        "(OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)"
        " OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS OLD.superseded_at"
        " AND NEW.superseded_by_id IS NOT NULL))) "
        "BEGIN SELECT RAISE(ABORT, 'transaction is append-only'); END"
    )
    for column in _INTEGER_COLUMNS:
        op.execute(
            f'CREATE TRIGGER transaction_{column}_integer BEFORE INSERT ON "transaction" '
            f"WHEN NEW.{column} IS NOT NULL AND typeof(NEW.{column}) IS NOT 'integer' "
            f"BEGIN SELECT RAISE(ABORT, 'transaction {column} must be an integer'); END"
        )


def downgrade() -> None:
    for column in _INTEGER_COLUMNS:
        op.execute(f"DROP TRIGGER transaction_{column}_integer")
    op.execute("DROP TRIGGER transaction_no_update")
    op.execute("DROP TRIGGER transaction_no_delete")
    op.drop_index("ix_transaction_current", table_name="transaction")
    op.drop_index("ix_transaction_instrument_id", table_name="transaction")
    op.drop_index("ix_transaction_import_id", table_name="transaction")
    op.drop_index("ix_transaction_account_stream", table_name="transaction")
    op.drop_table("transaction")
