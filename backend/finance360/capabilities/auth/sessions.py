"""Server-side Sessions (ADR-0010): opaque 256-bit ids, stored only as their SHA-256."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from finance360.capabilities.auth.policy import (
    NORMAL_SESSION,
    TOUCH_INTERVAL,
    TRUSTED_CAP,
    TRUSTED_IDLE,
)
from finance360.capabilities.auth.types import Principal, Role
from finance360.core.models.auth import AuthSession, Login


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(session: Session, login_id: int, *, trusted: bool, now: datetime) -> str:
    """Create a Session and return the cookie value (the only time it exists in the clear)."""
    token = secrets.token_urlsafe(32)  # 256 random bits
    lifetime = TRUSTED_IDLE if trusted else NORMAL_SESSION
    session.add(
        AuthSession(
            id_hash=hash_token(token),
            login_id=login_id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + lifetime,
            trusted=trusted,
        )
    )
    session.flush()
    return token


def resolve(session: Session, token: str, now: datetime) -> Principal | None:
    """The Principal behind a cookie value, or None if unknown or expired.

    ``last_seen_at`` is touched at most once a minute; a trusted Session then slides its expiry
    (90 days from now), never beyond one year after it was created.
    """
    row = session.get(AuthSession, hash_token(token))
    if row is None or row.expires_at <= now:
        return None
    login = session.get(Login, row.login_id)
    if login is None:
        return None
    if now - row.last_seen_at >= TOUCH_INTERVAL:
        row.last_seen_at = now
        if row.trusted:
            row.expires_at = min(now + TRUSTED_IDLE, row.created_at + TRUSTED_CAP)
        session.flush()
    return Principal(login.id, Role(login.role))


def revoke(session: Session, token: str) -> None:
    session.execute(delete(AuthSession).where(AuthSession.id_hash == hash_token(token)))


def revoke_all(session: Session, login_id: int, *, except_token: str | None = None) -> int:
    """Delete every Session of a Login (optionally keeping the caller's own). Returns the count."""
    stmt = delete(AuthSession).where(AuthSession.login_id == login_id)
    if except_token is not None:
        stmt = stmt.where(AuthSession.id_hash != hash_token(except_token))
    return session.execute(stmt).rowcount  # type: ignore[attr-defined]


def purge_expired(session: Session, now: datetime) -> None:
    session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))


def count_for(session: Session, login_id: int) -> int:
    return len(session.scalars(select(AuthSession).where(AuthSession.login_id == login_id)).all())
