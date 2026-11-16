"""Tax record (ADR-0019 section 5).

Additive only (live-data rule): one new table. Nothing existing is touched; Tax records are entered
by the household, and the fixture loader writes the synthetic ones.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tax_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("record_type", sa.String(length=30), nullable=False),
        sa.Column("from_on", sa.Date(), nullable=False),
        sa.Column("to_on", sa.Date(), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(description)) > 0", name=op.f("ck_tax_record_description_not_blank")
        ),
        sa.CheckConstraint(
            "to_on IS NULL OR to_on >= from_on", name=op.f("ck_tax_record_span_ordered")
        ),
        sa.CheckConstraint(
            "json_valid(payload_json)", name=op.f("ck_tax_record_payload_json_valid")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["member.id"], name=op.f("fk_tax_record_member_id_member")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_record")),
    )
    op.create_index("ix_tax_record_member_id_from_on", "tax_record", ["member_id", "from_on"])
    op.create_index("ix_tax_record_record_type", "tax_record", ["record_type"])


def downgrade() -> None:
    op.drop_index("ix_tax_record_record_type", table_name="tax_record")
    op.drop_index("ix_tax_record_member_id_from_on", table_name="tax_record")
    op.drop_table("tax_record")
