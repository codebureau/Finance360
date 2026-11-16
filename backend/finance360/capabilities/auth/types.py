"""The auth seam (ADR-0010): what a provider supplies and what the rest of the app sees."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.orm import Session


class Role(StrEnum):
    OWNER = "owner"
    VIEWER = "viewer"


@dataclass(frozen=True)
class Principal:
    """Who is making a request. Only a Login can be one (a Member is not a Login)."""

    login_id: int
    role: Role


@dataclass(frozen=True)
class AuthAttempt:
    """Everything a provider may look at. ``fields`` is the plain request body (a mapping).

    ``session`` is the request's database session: the password provider reads and updates the
    Login (failure counter, TOTP step) in the same transaction as the response.
    """

    headers: Mapping[str, str]
    client: str | None
    fields: Mapping[str, Any]
    session: Session
    now: datetime = field(compare=False)


class AuthProvider(Protocol):
    """Providers only establish identity. They never touch Sessions."""

    key: str

    def authenticate(self, req: AuthAttempt) -> Principal | None: ...


class LockedOut(Exception):  # noqa: N818 - reads better as a state than as an error
    """The Login is temporarily locked; even correct credentials are refused."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Locked for {retry_after} seconds.")
        self.retry_after = retry_after
