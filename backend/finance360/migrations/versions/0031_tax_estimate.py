"""Tax what-if and saved Tax estimate (ADR-0019 sections 5 and 6).

Additive only (live-data rule): two new tables, no data, nothing existing is touched. ``tax_whatif``
is editable and audited. ``tax_estimate`` is immutable: triggers reject UPDATE and DELETE, as for
``projection_run`` and ``audit_event``, and the what-if FK is RESTRICT (SET NULL would be an
UPDATE).

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tax_whatif",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("overrides_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_tax_whatif_name_not_blank")),
        sa.CheckConstraint("origin IN ('user', 'ai')", name=op.f("ck_tax_whatif_origin_known")),
        sa.CheckConstraint(
            "json_valid(overrides_json)", name=op.f("ck_tax_whatif_overrides_json_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_whatif")),
        sa.UniqueConstraint("name", name=op.f("uq_tax_whatif_name")),
    )
    op.create_table(
        "tax_estimate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("whatif_id", sa.Integer(), nullable=True),
        sa.Column("whatif_name", sa.String(length=100), nullable=True),
        sa.Column("tax_engine_version", sa.Integer(), nullable=False),
        sa.Column("tax_input_version", sa.Integer(), nullable=False),
        sa.Column("rule_pack_version", sa.String(length=50), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("incomplete", sa.Boolean(), nullable=False),
        sa.Column("has_provisional", sa.Boolean(), nullable=False),
        sa.CheckConstraint("json_valid(input_json)", name=op.f("ck_tax_estimate_input_json_valid")),
        sa.CheckConstraint(
            "json_valid(result_json)", name=op.f("ck_tax_estimate_result_json_valid")
        ),
        sa.CheckConstraint(
            "json_valid(provenance_json)", name=op.f("ck_tax_estimate_provenance_json_valid")
        ),
        sa.ForeignKeyConstraint(
            ["whatif_id"],
            ["tax_whatif.id"],
            name=op.f("fk_tax_estimate_whatif_id_tax_whatif"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_estimate")),
    )
    # Immutable: the database itself refuses to rewrite or remove a saved estimate.
    op.execute(
        "CREATE TRIGGER tax_estimate_no_update BEFORE UPDATE ON tax_estimate "
        "BEGIN SELECT RAISE(ABORT, 'tax_estimate is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER tax_estimate_no_delete BEFORE DELETE ON tax_estimate "
        "BEGIN SELECT RAISE(ABORT, 'tax_estimate is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER tax_estimate_no_delete")
    op.execute("DROP TRIGGER tax_estimate_no_update")
    op.drop_table("tax_estimate")
    op.drop_table("tax_whatif")
