"""The Login: create, lock out, change password, manage TOTP (ADR-0010).

Mutating functions take the caller's ``Session`` and flush but never commit: the API request owns
the transaction. Failures are *returned*, not raised, wherever the caller must still commit the
failure counter (raising would roll it back).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from finance360.capabilities.auth import passwords, sessions, totp
from finance360.capabilities.auth.events import record_event
from finance360.capabilities.auth.policy import lock_seconds, password_problem
from finance360.capabilities.auth.types import LockedOut, Role
from finance360.core.models.auth import Login


class LoginExists(Exception):  # noqa: N818
    """Exactly one Login is allowed in M1."""


class PasswordPolicyError(Exception):
    """The new password breaks the policy; ``message`` says how."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TotpStateError(Exception):
    """A TOTP step was asked for in the wrong state (e.g. confirm with nothing pending)."""


def get_login(session: Session) -> Login | None:
    return session.scalars(select(Login).order_by(Login.id).limit(1)).first()


def get_by_id(session: Session, login_id: int) -> Login | None:
    return session.get(Login, login_id)


def setup_required(session: Session) -> bool:
    return get_login(session) is None


def create_login(
    session: Session,
    password: str,
    *,
    now: datetime,
    event: str | None = "setup",
    detail: dict[str, Any] | None = None,
) -> Login:
    """Create the one owner Login. Raises ``PasswordPolicyError`` or ``LoginExists``."""
    problem = password_problem(password)
    if problem:
        raise PasswordPolicyError(problem)
    if get_login(session) is not None:
        raise LoginExists
    login = Login(role=Role.OWNER.value, password_hash=passwords.hash_password(password))
    login.failed_count = 0
    session.add(login)
    session.flush()
    if event:
        record_event(session, event, now=now, login_id=login.id, detail=detail)
    return login


def seed_owner(session: Session, password: str, *, now: datetime) -> bool:
    """Create the owner from a seeded password only if no Login exists. Never overwrites."""
    if get_login(session) is not None:
        return False
    create_login(session, password, now=now, event="setup", detail={"via": "seed"})
    return True


# --- lockout -------------------------------------------------------------------------------


def check_lock(login: Login, now: datetime) -> None:
    """Raise ``LockedOut`` while the Login is locked, even for correct credentials."""
    if login.locked_until is not None and login.locked_until > now:
        raise LockedOut(math.ceil((login.locked_until - now).total_seconds()))


def charge_attempt(session: Session, login: Login, now: datetime) -> int:
    """Count one credential attempt *before* it is verified. Returns the lock seconds it engaged.

    The counter is a read-modify-write, so concurrent requests would all pass the lock check
    before any of them counted. This takes SQLite's write lock first (a no-op UPDATE), re-reads
    the row under it, checks the lock, then counts and locks in the same transaction. Requests
    are serialised only here: after the fifth attempt the rest are refused without verifying,
    so at most five credentials are ever checked before the lock engages, under any concurrency.
    The lock is held to the end of the request (the argon2 check), which also makes each TOTP
    step single-use across concurrent requests. Raises ``LockedOut`` while locked.
    """
    session.execute(
        update(Login)
        .where(Login.id == login.id)
        .values(failed_count=Login.failed_count)
        .execution_options(synchronize_session=False)
    )
    session.refresh(login)
    check_lock(login, now)
    login.failed_count += 1
    seconds = lock_seconds(login.failed_count)
    if seconds:
        login.locked_until = now + timedelta(seconds=seconds)
    session.flush()
    return seconds


def record_failure(
    session: Session,
    login: Login,
    now: datetime,
    locked_seconds: int,
    detail: dict[str, Any] | None = None,
) -> None:
    """Audit a failed attempt already counted by ``charge_attempt``."""
    record_event(session, "login_failed", now=now, login_id=login.id, detail=detail)
    if locked_seconds:
        record_event(
            session,
            "lockout",
            now=now,
            login_id=login.id,
            detail={**(detail or {}), "seconds": locked_seconds},
        )


def register_success(login: Login) -> None:
    login.failed_count = 0
    login.locked_until = None


# --- credential checks that count toward lockout ----------------------------------------------


def confirm_password(
    session: Session,
    login: Login,
    password: str,
    now: datetime,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Check a password for a signed-in action. A wrong one counts toward lockout."""
    locked = charge_attempt(session, login, now)
    if passwords.verify_password(login.password_hash, password):
        register_success(login)
        return True
    record_failure(session, login, now, locked, detail)
    return False


def change_password(
    session: Session,
    login: Login,
    new_password: str,
    *,
    now: datetime,
    keep_token: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Set a new password and revoke every other Session."""
    problem = password_problem(new_password)
    if problem:
        raise PasswordPolicyError(problem)
    login.password_hash = passwords.hash_password(new_password)
    sessions.revoke_all(session, login.id, except_token=keep_token)
    record_event(session, "password_changed", now=now, login_id=login.id, detail=detail)


def reset_password(
    session: Session, login: Login, new_password: str, *, now: datetime, detail: dict[str, Any]
) -> None:
    """Host-side recovery: new password, TOTP cleared, lock cleared, every Session revoked."""
    problem = password_problem(new_password)
    if problem:
        raise PasswordPolicyError(problem)
    login.password_hash = passwords.hash_password(new_password)
    login.totp_secret = None
    login.totp_pending_secret = None
    login.totp_last_step = None
    register_success(login)
    sessions.revoke_all(session, login.id)
    record_event(session, "password_reset", now=now, login_id=login.id, detail=detail)


# --- TOTP --------------------------------------------------------------------------------------


def totp_enabled(login: Login) -> bool:
    return login.totp_secret is not None


def totp_begin(
    session: Session, login: Login, now: datetime, detail: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Start enrolment: a fresh pending secret and its otpauth URI. Login is unchanged until
    ``totp_confirm`` succeeds."""
    if login.totp_secret is not None:
        raise TotpStateError("An authenticator is already set up. Disable it first.")
    secret = totp.new_secret()
    login.totp_pending_secret = secret
    record_event(session, "totp_begin", now=now, login_id=login.id, detail=detail)
    return secret, totp.provisioning_uri(secret)


def totp_confirm(
    session: Session,
    login: Login,
    code: str,
    now: datetime,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Promote the pending secret if ``code`` is valid for it. A wrong code counts as a failure."""
    locked = charge_attempt(session, login, now)
    if login.totp_pending_secret is None:  # read under the lock; the error rolls the charge back
        raise TotpStateError("Start enrolment first.")
    step = totp.accepted_step(login.totp_pending_secret, code, None, now)
    if step is None:
        record_failure(session, login, now, locked, detail)
        return False
    register_success(login)
    login.totp_secret = login.totp_pending_secret
    login.totp_pending_secret = None
    login.totp_last_step = step
    record_event(session, "totp_confirmed", now=now, login_id=login.id, detail=detail)
    return True


def totp_disable(
    session: Session,
    login: Login,
    password: str,
    code: str,
    now: datetime,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Turn TOTP off. Needs the password and a current (unused) code."""
    locked = charge_attempt(session, login, now)
    if login.totp_secret is None:  # read under the lock; the error rolls the charge back
        raise TotpStateError("No authenticator is set up.")
    step = totp.accepted_step(login.totp_secret, code, login.totp_last_step, now)
    password_ok = passwords.verify_password(login.password_hash, password)
    if step is None or not password_ok:
        record_failure(session, login, now, locked, detail)
        return False
    register_success(login)
    login.totp_secret = None
    login.totp_pending_secret = None
    login.totp_last_step = None
    record_event(session, "totp_disabled", now=now, login_id=login.id, detail=detail)
    return True
