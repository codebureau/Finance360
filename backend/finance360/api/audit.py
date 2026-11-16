"""Audit trail endpoints (read only). Thin: the rules live in ``core.audit_query``."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from finance360.api.deps import OwnerDep, SessionDep
from finance360.core import audit_query as service
from finance360.core.audit_query import AuditQuery
from finance360.core.models.auth import EVENT_KINDS


class ChangeOut(BaseModel):
    field: str
    before: str | None
    after: str | None


class AuditEntryOut(BaseModel):
    id: int
    occurred_at: datetime = Field(description="UTC.")
    action: str
    entity_type: str
    entity_id: int
    actor: str = Field(description="A readable label, e.g. Owner.")
    actor_ref: str
    source: str
    summary: str
    changes: list[ChangeOut] = Field(description="Only the fields that changed.")


class AuditPage(BaseModel):
    items: list[AuditEntryOut]
    total: int


class AuthEntryOut(BaseModel):
    id: int
    occurred_at: datetime = Field(description="UTC.")
    kind: str
    label: str
    detail: dict[str, Any] = Field(description="Non-sensitive facts only (allow-listed).")


class AuthPage(BaseModel):
    items: list[AuthEntryOut]
    total: int


Limit = Annotated[int, Query(ge=1, le=service.MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=AuditPage, operation_id="listAudit")
def list_audit(
    session: SessionDep,
    entity_type: str | None = None,
    entity_id: int | None = None,
    account_id: Annotated[
        int | None, Query(description="An Account's own records plus its Valuations'.")
    ] = None,
    date_from: Annotated[date | None, Query(description="Household-local, inclusive.")] = None,
    date_to: Annotated[date | None, Query(description="Household-local, inclusive.")] = None,
    limit: Limit = service.DEFAULT_LIMIT,
    offset: Offset = 0,
) -> AuditPage:
    """Changes to household data, newest first."""
    page = service.list_audit(
        session, AuditQuery(entity_type, entity_id, account_id, date_from, date_to, limit, offset)
    )
    return AuditPage(
        items=[
            AuditEntryOut(
                id=e.id,
                occurred_at=e.occurred_at,
                action=e.action,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                actor=e.actor,
                actor_ref=e.actor_ref,
                source=e.source,
                summary=e.summary,
                changes=[
                    ChangeOut(field=c.field, before=c.before, after=c.after) for c in e.changes
                ],
            )
            for e in page.items
        ],
        total=page.total,
    )


@router.get("/auth-events", response_model=AuthPage, operation_id="listAuthEvents")
def list_auth_events(
    session: SessionDep,
    _owner: OwnerDep,
    kind: Annotated[str | None, Query(description=f"One of: {', '.join(EVENT_KINDS)}.")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = service.DEFAULT_LIMIT,
    offset: Offset = 0,
) -> AuthPage:
    """Sign-ins, failures, lockouts and password and authenticator changes. Owner only."""
    page = service.list_auth_events(
        session, kind=kind, date_from=date_from, date_to=date_to, limit=limit, offset=offset
    )
    return AuthPage(
        items=[
            AuthEntryOut(
                id=e.id, occurred_at=e.occurred_at, kind=e.kind, label=e.label, detail=e.detail
            )
            for e in page.items
        ],
        total=page.total,
    )
