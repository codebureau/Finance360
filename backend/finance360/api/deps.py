"""Shared request dependencies: the database session, the signed-in Principal, the AuditContext.

Every dependency here that touches the database is injected with ``scope="function"``. Mixing
scopes would give a request two database sessions (FastAPI caches per scope), and two SQLite
transactions in one request can block each other.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from finance360.capabilities.auth import sessions
from finance360.capabilities.auth.policy import utcnow
from finance360.capabilities.auth.types import Principal, Role
from finance360.core.audit import AuditContext

COOKIE_NAME = "f360_session"


def get_session(request: Request) -> Iterator[Session]:
    """One transaction per request: a change and its audit record commit or fail together.

    Always injected with ``scope="function"`` (see ``SessionDep``): the commit then runs as soon
    as the route function returns, before the response is sent. With the default request scope
    FastAPI commits after sending, so a client could read back before the write landed and a
    failed commit would follow a 2xx.
    """
    with request.app.state.session_factory.begin() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session, scope="function")]


def current_principal(request: Request, session: SessionDep) -> Principal:
    """The signed-in Principal, or 401. Every API router depends on this (default deny).

    Tests replace it with ``app.dependency_overrides`` (see ``tests/support.py``); there is no
    configuration switch that turns authentication off (ADR-0010).
    """
    token = request.cookies.get(COOKIE_NAME)
    principal = sessions.resolve(session, token, utcnow()) if token else None
    if principal is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal, scope="function")]


def require_owner(principal: PrincipalDep) -> Principal:
    if principal.role is not Role.OWNER:
        raise HTTPException(status_code=403, detail="Only the owner can change data.")
    return principal


OwnerDep = Annotated[Principal, Depends(require_owner, scope="function")]


def get_audit_context(principal: OwnerDep) -> AuditContext:
    """The Principal as the request's AuditContext (ADR-0010). Writes need an owner."""
    return AuditContext("owner", f"login:{principal.login_id}", "api")


ContextDep = Annotated[AuditContext, Depends(get_audit_context)]
