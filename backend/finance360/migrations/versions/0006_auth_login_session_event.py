"""Login, Session and the append-only auth-event trail (ADR-0010).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("password_hash", sa.String(length=300), nullable=False),
        sa.Column("totp_secret", sa.String(length=64), nullable=True),
        sa.Column("totp_pending_secret", sa.String(length=64), nullable=True),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'viewer')", name=op.f("ck_login_role_known")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login")),
    )
    op.create_table(
        "auth_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("login_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('setup', 'login', 'login_failed', 'lockout', 'logout', 'password_changed', "
            "'password_reset', 'totp_begin', 'totp_confirmed', 'totp_disabled')",
            name=op.f("ck_auth_event_kind_known"),
        ),
        sa.ForeignKeyConstraint(
            ["login_id"], ["login.id"], name=op.f("fk_auth_event_login_id_login")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_event")),
    )
    op.create_table(
        "auth_session",
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["login_id"], ["login.id"], name=op.f("fk_auth_session_login_id_login")
        ),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_auth_session")),
    )
    # Append-only, like audit_event: the database itself refuses to rewrite or remove the trail.
    op.execute(
        "CREATE TRIGGER auth_event_no_update BEFORE UPDATE ON auth_event "
        "BEGIN SELECT RAISE(ABORT, 'auth_event is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER auth_event_no_delete BEFORE DELETE ON auth_event "
        "BEGIN SELECT RAISE(ABORT, 'auth_event is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER auth_event_no_delete")
    op.execute("DROP TRIGGER auth_event_no_update")
    op.drop_table("auth_session")
    op.drop_table("auth_event")
    op.drop_table("login")
