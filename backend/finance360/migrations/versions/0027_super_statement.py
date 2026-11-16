"""Super statement and its Investment option lines (ADR-0025).

Additive only (live-data rule): two new tables, their indexes and their triggers. Nothing existing
is touched. Both are append-only in the database itself, as for ``valuation``: a statement is only
ever superseded, and an option line is never changed once written (a correction writes new lines
under the new statement). Money columns and the scaled units and price are type-checked as
integers on insert, so a float can never be stored.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED = ("opening", "contributions_in", "investment_returns", "withdrawals", "closing")
_OPTIONAL = (
    "contributions_tax",
    "fees_before_rebate",
    "rebate",
    "fees_inside_investment",
    "division_293_released",
    "other_withdrawals",
    "insurance_premiums",
    "concessional_received",
    "non_concessional_received",
)
_MINOR = tuple(f"{name}_minor" for name in _REQUIRED + _OPTIONAL)
#: Every column except the supersession pair: what a supersession must leave unchanged.
_STATEMENT_COLUMNS = (
    "id",
    "account_id",
    "period_start",
    "period_end",
    "currency",
    *_MINOR,
    "valuation_id",
    "checks_ticked_json",
    "recorded_at",
)
_OPTION_INTEGERS = ("units", "unit_price", "value_minor")


def upgrade() -> None:
    op.create_table(
        "super_statement",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        *(sa.Column(f"{name}_minor", sa.BigInteger(), nullable=False) for name in _REQUIRED),
        *(sa.Column(f"{name}_minor", sa.BigInteger(), nullable=True) for name in _OPTIONAL),
        sa.Column("valuation_id", sa.Integer(), nullable=False),
        sa.Column("checks_ticked_json", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("length(currency) = 3", name=op.f("ck_super_statement_currency_len")),
        sa.CheckConstraint(
            "json_valid(checks_ticked_json)",
            name=op.f("ck_super_statement_checks_ticked_json_valid"),
        ),
        sa.CheckConstraint(
            "period_end > period_start", name=op.f("ck_super_statement_period_ordered")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name=op.f("fk_super_statement_account_id_account")
        ),
        sa.ForeignKeyConstraint(
            ["valuation_id"],
            ["valuation.id"],
            name=op.f("fk_super_statement_valuation_id_valuation"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["super_statement.id"],
            name=op.f("fk_super_statement_superseded_by_id_super_statement"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_super_statement")),
    )
    op.create_index(
        op.f("ix_super_statement_account_id"), "super_statement", ["account_id"], unique=False
    )
    op.create_index(
        op.f("ix_super_statement_valuation_id"), "super_statement", ["valuation_id"], unique=False
    )
    op.create_index(
        "uq_super_statement_current",
        "super_statement",
        ["account_id", "period_end"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
    )
    op.create_table(
        "super_statement_option",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("units", sa.Integer(), nullable=True),
        sa.Column("unit_price", sa.Integer(), nullable=True),
        sa.Column("value_minor", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name=op.f("ck_super_statement_option_name_not_blank")
        ),
        sa.CheckConstraint(
            "units IS NULL OR units >= 0", name=op.f("ck_super_statement_option_units_not_negative")
        ),
        sa.CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name=op.f("ck_super_statement_option_unit_price_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["super_statement.id"],
            name=op.f("fk_super_statement_option_statement_id_super_statement"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_super_statement_option")),
    )
    op.create_index(
        "ix_super_statement_option_statement_id",
        "super_statement_option",
        ["statement_id", "position"],
    )

    # Append-only, as for valuation: a statement is only ever superseded (set once, then linked
    # once); an option line is never changed or removed.
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in _STATEMENT_COLUMNS)
    op.execute(
        "CREATE TRIGGER super_statement_no_delete BEFORE DELETE ON super_statement "
        "BEGIN SELECT RAISE(ABORT, 'super_statement is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER super_statement_no_update BEFORE UPDATE ON super_statement "
        f"WHEN NOT ({unchanged} AND OLD.superseded_by_id IS NULL AND ("
        "(OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)"
        " OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS OLD.superseded_at"
        " AND NEW.superseded_by_id IS NOT NULL))) "
        "BEGIN SELECT RAISE(ABORT, 'super_statement is append-only'); END"
    )
    for column in _MINOR:
        op.execute(
            f"CREATE TRIGGER super_statement_{column}_integer BEFORE INSERT ON super_statement "
            f"WHEN NEW.{column} IS NOT NULL AND typeof(NEW.{column}) IS NOT 'integer' "
            f"BEGIN SELECT RAISE(ABORT, 'super_statement {column} must be an integer'); END"
        )
    op.execute(
        "CREATE TRIGGER super_statement_option_no_delete BEFORE DELETE ON super_statement_option "
        "BEGIN SELECT RAISE(ABORT, 'super_statement_option is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER super_statement_option_no_update BEFORE UPDATE ON super_statement_option "
        "BEGIN SELECT RAISE(ABORT, 'super_statement_option is append-only'); END"
    )
    for column in _OPTION_INTEGERS:
        op.execute(
            f"CREATE TRIGGER super_statement_option_{column}_integer "
            "BEFORE INSERT ON super_statement_option "
            f"WHEN NEW.{column} IS NOT NULL AND typeof(NEW.{column}) IS NOT 'integer' "
            f"BEGIN SELECT RAISE(ABORT, 'super_statement_option {column} must be an integer'); END"
        )


def downgrade() -> None:
    for column in _OPTION_INTEGERS:
        op.execute(f"DROP TRIGGER super_statement_option_{column}_integer")
    op.execute("DROP TRIGGER super_statement_option_no_update")
    op.execute("DROP TRIGGER super_statement_option_no_delete")
    for column in _MINOR:
        op.execute(f"DROP TRIGGER super_statement_{column}_integer")
    op.execute("DROP TRIGGER super_statement_no_update")
    op.execute("DROP TRIGGER super_statement_no_delete")
    op.drop_index("ix_super_statement_option_statement_id", table_name="super_statement_option")
    op.drop_table("super_statement_option")
    op.drop_index("uq_super_statement_current", table_name="super_statement")
    op.drop_index(op.f("ix_super_statement_valuation_id"), table_name="super_statement")
    op.drop_index(op.f("ix_super_statement_account_id"), table_name="super_statement")
    op.drop_table("super_statement")
