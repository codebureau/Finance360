"""Exposure vocabulary and Exposure profiles (ADR-0030 sections 2 and 3).

Additive only (live-data rule): four new tables, their indexes and triggers. Nothing existing is
touched. ``exposure_profile`` is append-only like ``price`` (a row is only ever superseded) and
``exposure_weight`` is immutable (a correction writes new rows); triggers enforce both.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROFILE_COLUMNS = (
    "id",
    "instrument_id",
    "as_at",
    "age_from",
    "source",
    "retrieved_on",
    "provenance_note",
    "recorded_at",
)


def upgrade() -> None:
    op.create_table(
        "exposure_dimension",
        sa.Column("key", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_exposure_dimension")),
    )
    op.create_table(
        "exposure_label",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dimension_key", sa.String(length=30), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dimension_key"],
            ["exposure_dimension.key"],
            name=op.f("fk_exposure_label_dimension_key_exposure_dimension"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exposure_label")),
        sa.UniqueConstraint(
            "dimension_key", "key", name=op.f("uq_exposure_label_dimension_key_label")
        ),
    )
    op.create_table(
        "exposure_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("as_at", sa.Date(), nullable=False),
        sa.Column("age_from", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("retrieved_on", sa.Date(), nullable=False),
        sa.Column("provenance_note", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "length(trim(coalesce(provenance_note, ''))) > 0",
            name=op.f("ck_exposure_profile_note_required"),
        ),
        sa.CheckConstraint(
            "age_from IS NULL OR age_from >= 0",
            name=op.f("ck_exposure_profile_age_from_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.id"],
            name=op.f("fk_exposure_profile_instrument_id_instrument"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["exposure_profile.id"],
            name=op.f("fk_exposure_profile_superseded_by_id_exposure_profile"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exposure_profile")),
    )
    op.create_index(
        op.f("ix_exposure_profile_instrument_id"),
        "exposure_profile",
        ["instrument_id"],
        unique=False,
    )
    op.create_index(
        "uq_exposure_profile_current",
        "exposure_profile",
        ["instrument_id", "as_at"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL AND age_from IS NULL"),
    )
    op.create_index(
        "uq_exposure_profile_current_banded",
        "exposure_profile",
        ["instrument_id", "as_at", "age_from"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL AND age_from IS NOT NULL"),
    )
    op.create_table(
        "exposure_weight",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("dimension_key", sa.String(length=30), nullable=False),
        sa.Column("label_key", sa.String(length=50), nullable=False),
        sa.Column("weight_ppm", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "weight_ppm > 0 AND weight_ppm <= 1000000",
            name=op.f("ck_exposure_weight_weight_range"),
        ),
        sa.ForeignKeyConstraint(
            ["dimension_key", "label_key"],
            ["exposure_label.dimension_key", "exposure_label.key"],
            name=op.f("fk_exposure_weight_label"),
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["exposure_profile.id"],
            name=op.f("fk_exposure_weight_profile_id_exposure_profile"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exposure_weight")),
        sa.UniqueConstraint(
            "profile_id",
            "dimension_key",
            "label_key",
            name=op.f("uq_exposure_weight_profile_label"),
        ),
    )
    op.create_index(
        op.f("ix_exposure_weight_profile_id"), "exposure_weight", ["profile_id"], unique=False
    )
    # Append-only, as for price (0011): a profile is only ever superseded.
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in _PROFILE_COLUMNS)
    op.execute(
        "CREATE TRIGGER exposure_profile_no_delete BEFORE DELETE ON exposure_profile "
        "BEGIN SELECT RAISE(ABORT, 'exposure_profile is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER exposure_profile_no_update BEFORE UPDATE ON exposure_profile "
        f"WHEN NOT ({unchanged} AND OLD.superseded_by_id IS NULL AND ("
        "(OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)"
        " OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS OLD.superseded_at"
        " AND NEW.superseded_by_id IS NOT NULL))) "
        "BEGIN SELECT RAISE(ABORT, 'exposure_profile is append-only'); END"
    )
    # Weights belong to one profile version and never change: a correction writes new ones.
    op.execute(
        "CREATE TRIGGER exposure_weight_no_delete BEFORE DELETE ON exposure_weight "
        "BEGIN SELECT RAISE(ABORT, 'exposure_weight is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER exposure_weight_no_update BEFORE UPDATE ON exposure_weight "
        "BEGIN SELECT RAISE(ABORT, 'exposure_weight is immutable'); END"
    )
    # SQLite would store a float or a numeric string in an INTEGER column: refuse it.
    op.execute(
        "CREATE TRIGGER exposure_weight_ppm_integer BEFORE INSERT ON exposure_weight "
        "WHEN typeof(NEW.weight_ppm) IS NOT 'integer' "
        "BEGIN SELECT RAISE(ABORT, 'exposure_weight weight_ppm must be an integer'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER exposure_weight_ppm_integer")
    op.execute("DROP TRIGGER exposure_weight_no_update")
    op.execute("DROP TRIGGER exposure_weight_no_delete")
    op.execute("DROP TRIGGER exposure_profile_no_update")
    op.execute("DROP TRIGGER exposure_profile_no_delete")
    op.drop_index(op.f("ix_exposure_weight_profile_id"), table_name="exposure_weight")
    op.drop_table("exposure_weight")
    op.drop_index("uq_exposure_profile_current_banded", table_name="exposure_profile")
    op.drop_index("uq_exposure_profile_current", table_name="exposure_profile")
    op.drop_index(op.f("ix_exposure_profile_instrument_id"), table_name="exposure_profile")
    op.drop_table("exposure_profile")
    op.drop_table("exposure_label")
    op.drop_table("exposure_dimension")
