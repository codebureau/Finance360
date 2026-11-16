"""Sign-in orchestration (ADR-0010): providers establish identity, this issues the Session.

``login`` *returns* its outcome rather than raising: a failed attempt must still commit its
failure counter and event, and an exception would roll the request's transaction back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from finance360.capabilities.auth import sessions
from finance360.capabilities.auth.events import record_event
from finance360.capabilities.auth.types import AuthAttempt, AuthProvider, LockedOut, Principal


@dataclass(frozen=True)
class LoginOutcome:
    kind: Literal["ok", "invalid", "locked"]
    token: str | None = None
    trusted: bool = False
    retry_after: int = 0


def client_detail(attempt: AuthAttempt) -> dict[str, Any]:
    """Non-sensitive request facts for the auth trail."""
    return {"client": attempt.client, "user_agent": attempt.headers.get("user-agent")}


def login(
    session: Session,
    providers: Sequence[AuthProvider],
    attempt: AuthAttempt,
    *,
    trust_device: bool,
) -> LoginOutcome:
    for provider in providers:
        try:
            principal = provider.authenticate(attempt)
        except LockedOut as locked:
            return LoginOutcome("locked", retry_after=locked.retry_after)
        if principal is not None:
            return sign_in(session, principal, attempt, trust_device=trust_device)
    return LoginOutcome("invalid")


def sign_in(
    session: Session, principal: Principal, attempt: AuthAttempt, *, trust_device: bool
) -> LoginOutcome:
    """Issue a Session for an established Principal (also used right after setup)."""
    sessions.purge_expired(session, attempt.now)
    token = sessions.issue(session, principal.login_id, trusted=trust_device, now=attempt.now)
    record_event(
        session,
        "login",
        now=attempt.now,
        login_id=principal.login_id,
        detail={**client_detail(attempt), "trusted": trust_device},
    )
    return LoginOutcome("ok", token=token, trusted=trust_device)


def logout(session: Session, token: str, principal: Principal, now: datetime) -> None:
    sessions.revoke(session, token)
    record_event(session, "logout", now=now, login_id=principal.login_id)
