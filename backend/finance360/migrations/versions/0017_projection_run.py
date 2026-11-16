"""Saved projection run (ADR-0016 section 4).

Additive only (live-data rule): one new table, no data, nothing existing is touched. The table is
immutable: triggers reject UPDATE and DELETE, as for ``audit_event``, and the Scenario FK is
RESTRICT.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projection_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("scenario_id", sa.Integer(), nullable=False),
        sa.Column("scenario_name", sa.String(length=100), nullable=False),
        sa.Column("engine_version", sa.Integer(), nullable=False),
        sa.Column("input_version", sa.Integer(), nullable=False),
        sa.Column("rule_pack_version", sa.String(length=50), nullable=True),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("sustainable_json", sa.Text(), nullable=True),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("funds_last_to_age", sa.Integer(), nullable=True),
        sa.Column("lasts_beyond_horizon", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "json_valid(input_json)", name=op.f("ck_projection_run_input_json_valid")
        ),
        sa.CheckConstraint(
            "json_valid(result_json)", name=op.f("ck_projection_run_result_json_valid")
        ),
        sa.CheckConstraint(
            "sustainable_json IS NULL OR json_valid(sustainable_json)",
            name=op.f("ck_projection_run_sustainable_json_valid"),
        ),
        sa.CheckConstraint(
            "json_valid(provenance_json)", name=op.f("ck_projection_run_provenance_json_valid")
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenario.id"],
            name=op.f("fk_projection_run_scenario_id_scenario"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projection_run")),
    )
    # Immutable: the database itself refuses to rewrite or remove a saved run.
    op.execute(
        "CREATE TRIGGER projection_run_no_update BEFORE UPDATE ON projection_run "
        "BEGIN SELECT RAISE(ABORT, 'projection_run is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER projection_run_no_delete BEFORE DELETE ON projection_run "
        "BEGIN SELECT RAISE(ABORT, 'projection_run is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER projection_run_no_delete")
    op.execute("DROP TRIGGER projection_run_no_update")
    op.drop_table("projection_run")
