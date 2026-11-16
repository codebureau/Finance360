"""Household, Member and the append-only audit trail (ADR-0009 sections 2 and 5).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "household",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_household_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_household")),
    )
    op.create_table(
        "member",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_member_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["household_id"], ["household.id"], name=op.f("fk_member_household_id_household")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member")),
    )
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("actor_kind", sa.String(length=20), nullable=False),
        sa.Column("actor_ref", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'delete', 'close', 'supersede')",
            name=op.f("ck_audit_event_action_known"),
        ),
        sa.CheckConstraint(
            "actor_kind IN ('owner', 'import', 'agent', 'system')",
            name=op.f("ck_audit_event_actor_kind_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
    )
    # Append-only: the database itself refuses to rewrite or remove the trail.
    op.execute(
        "CREATE TRIGGER audit_event_no_update BEFORE UPDATE ON audit_event "
        "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_event_no_delete BEFORE DELETE ON audit_event "
        "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_event_no_delete")
    op.execute("DROP TRIGGER audit_event_no_update")
    op.drop_table("audit_event")
    op.drop_table("member")
    op.drop_table("household")
