"""Import source, Import and staged rows (ADR-0013 sections 1, 2, 5 and 5b).

Additive only (live-data rule): three new tables and their indexes. Nothing existing is touched.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_source",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("importer_key", sa.String(length=50), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name=op.f("fk_import_source_account_id_account")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_source")),
        sa.UniqueConstraint("account_id", "importer_key", name=op.f("uq_import_source_account_id")),
    )
    op.create_index(op.f("ix_import_source_account_id"), "import_source", ["account_id"])
    op.create_table(
        "import",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_source_id", sa.Integer(), nullable=False),
        sa.Column("stream", sa.String(length=10), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("coverage_from", sa.Date(), nullable=False),
        sa.Column("coverage_to", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('staged', 'committed', 'discarded')", name=op.f("ck_import_status_known")
        ),
        sa.CheckConstraint("stream IN ('trade', 'cash')", name=op.f("ck_import_stream_known")),
        sa.CheckConstraint("coverage_from <= coverage_to", name=op.f("ck_import_coverage_ordered")),
        sa.ForeignKeyConstraint(
            ["import_source_id"],
            ["import_source.id"],
            name=op.f("fk_import_import_source_id_import_source"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import")),
    )
    op.create_index(op.f("ix_import_import_source_id"), "import", ["import_source_id"])
    op.create_index(
        "uq_import_current",
        "import",
        ["file_sha256", "import_source_id", "stream"],
        unique=True,
        sqlite_where=sa.text("status <> 'discarded'"),
    )
    op.create_table(
        "staged_row",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("row_no", sa.Integer(), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("instrument_ref", sa.String(length=200), nullable=True),
        sa.Column("instrument_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["import_id"], ["import.id"], name=op.f("fk_staged_row_import_id_import")
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.id"],
            name=op.f("fk_staged_row_instrument_id_instrument"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staged_row")),
        sa.UniqueConstraint("import_id", "row_no", name=op.f("uq_staged_row_import_id")),
    )
    op.create_index(op.f("ix_staged_row_import_id"), "staged_row", ["import_id"])
    op.create_index(op.f("ix_staged_row_instrument_id"), "staged_row", ["instrument_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_staged_row_instrument_id"), table_name="staged_row")
    op.drop_index(op.f("ix_staged_row_import_id"), table_name="staged_row")
    op.drop_table("staged_row")
    op.drop_index("uq_import_current", table_name="import")
    op.drop_index(op.f("ix_import_import_source_id"), table_name="import")
    op.drop_table("import")
    op.drop_index(op.f("ix_import_source_account_id"), table_name="import_source")
    op.drop_table("import_source")
