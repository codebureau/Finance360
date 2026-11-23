"""Fund-of-funds references on an Exposure profile (ADR-0030 section 5, #85).

Additive only (live-data rule): one new table, its indexes and triggers. Nothing existing is
touched and no data is moved, so a rollback needs no restore. A reference is a weighted pointer
from a profile to another Instrument; like ``exposure_weight`` it belongs to one profile version and
never changes (a correction writes a new profile with new references). Triggers enforce that.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exposure_reference",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("weight_ppm", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "weight_ppm > 0 AND weight_ppm <= 1000000",
            name=op.f("ck_exposure_reference_weight_range"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.id"],
            name=op.f("fk_exposure_reference_instrument_id_instrument"),
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["exposure_profile.id"],
            name=op.f("fk_exposure_reference_profile_id_exposure_profile"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exposure_reference")),
        sa.UniqueConstraint(
            "profile_id",
            "instrument_id",
            name=op.f("uq_exposure_reference_profile_instrument"),
        ),
    )
    op.create_index(
        op.f("ix_exposure_reference_profile_id"), "exposure_reference", ["profile_id"], unique=False
    )
    op.create_index(
        op.f("ix_exposure_reference_instrument_id"),
        "exposure_reference",
        ["instrument_id"],
        unique=False,
    )
    op.execute(
        "CREATE TRIGGER exposure_reference_no_delete BEFORE DELETE ON exposure_reference "
        "BEGIN SELECT RAISE(ABORT, 'exposure_reference is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER exposure_reference_no_update BEFORE UPDATE ON exposure_reference "
        "BEGIN SELECT RAISE(ABORT, 'exposure_reference is immutable'); END"
    )
    # SQLite would store a float or a numeric string in an INTEGER column: refuse it.
    op.execute(
        "CREATE TRIGGER exposure_reference_ppm_integer BEFORE INSERT ON exposure_reference "
        "WHEN typeof(NEW.weight_ppm) IS NOT 'integer' "
        "BEGIN SELECT RAISE(ABORT, 'exposure_reference weight_ppm must be an integer'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER exposure_reference_ppm_integer")
    op.execute("DROP TRIGGER exposure_reference_no_update")
    op.execute("DROP TRIGGER exposure_reference_no_delete")
    op.drop_index(op.f("ix_exposure_reference_instrument_id"), table_name="exposure_reference")
    op.drop_index(op.f("ix_exposure_reference_profile_id"), table_name="exposure_reference")
    op.drop_table("exposure_reference")
