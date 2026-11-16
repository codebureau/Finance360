"""Login, Session and auth-event tables (ADR-0010).

``login`` holds the credentials (user-changeable state, so it lives with the data). ``auth_session``
holds server-side Sessions, keyed by the SHA-256 of the cookie value, never the value itself.
``auth_event`` is append-only like ``audit_event``: migration 0006 adds the triggers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

ROLES = ("owner", "viewer")
EVENT_KINDS = (
    "setup",
    "login",
    "login_failed",
    "lockout",
    "logout",
    "password_changed",
    "password_reset",
    "totp_begin",
    "totp_confirmed",
    "totp_disabled",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Login(Base):
    """The one account that can sign in (a Member is not a Login: CONTEXT.md)."""

    __tablename__ = "login"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="role_known"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(300))  # argon2id PHC string
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_pending_secret: Mapped[str | None] = mapped_column(String(64))
    #: The last accepted 30-second time-step; codes at or before it are rejected (replay).
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    failed_count: Mapped[int] = mapped_column(default=0)
    #: UTC, stored naive like every timestamp here.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)


class AuthSession(Base):
    __tablename__ = "auth_session"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    login_id: Mapped[int] = mapped_column(ForeignKey("login.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    trusted: Mapped[bool] = mapped_column(Boolean)


class AuthEvent(Base):
    __tablename__ = "auth_event"
    __table_args__ = (CheckConstraint(_in("kind", EVENT_KINDS), name="kind_known"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(30))
    login_id: Mapped[int | None] = mapped_column(ForeignKey("login.id"))
    #: Non-sensitive facts only (client address, user agent). Never a secret or a code.
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
