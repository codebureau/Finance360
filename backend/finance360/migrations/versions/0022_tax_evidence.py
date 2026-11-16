"""Tax evidence (ADR-0019 sections 5 and 7).

Additive only (live-data rule): two new tables. Nothing existing is touched. The files themselves
are not in the database: they live under ``documents/tax/`` in the data directory, which the backup
already carries (ADR-0011).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tax_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=150), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(sha256) = 64", name=op.f("ck_tax_evidence_sha256_length")),
        sa.CheckConstraint(
            "length(trim(file_name)) > 0", name=op.f("ck_tax_evidence_file_name_not_blank")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_tax_evidence_size_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_evidence")),
        sa.UniqueConstraint("sha256", name=op.f("uq_tax_evidence_sha256")),
    )
    op.create_table(
        "tax_record_evidence",
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["tax_evidence.id"],
            name=op.f("fk_tax_record_evidence_evidence_id_tax_evidence"),
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["tax_record.id"],
            name=op.f("fk_tax_record_evidence_record_id_tax_record"),
        ),
        sa.PrimaryKeyConstraint("record_id", "evidence_id", name=op.f("pk_tax_record_evidence")),
    )
    op.create_index("ix_tax_record_evidence_evidence_id", "tax_record_evidence", ["evidence_id"])


def downgrade() -> None:
    op.drop_index("ix_tax_record_evidence_evidence_id", table_name="tax_record_evidence")
    op.drop_table("tax_record_evidence")
    op.drop_table("tax_evidence")
