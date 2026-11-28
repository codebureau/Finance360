"""Notices endpoints (ADR-0033). Thin: the logic lives in ``core.notices`` and
``capabilities.notices``. A Notice is words plus one exact figure, never read back as state."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from finance360.api.deps import SessionDep
from finance360.api.household import ERRORS
from finance360.capabilities.notices import service
from finance360.core.models.notice import Notice
from finance360.core.money import format_amount
from finance360.core.notices import HOME_LIMIT, direction

router = APIRouter(prefix="/api/notices", tags=["notices"])


class NoticeDeltaOut(BaseModel):
    minor: int = Field(description="Exact net Position change in minor units (signed).")
    currency: str
    amount: str = Field(description="The same as a plain decimal string, e.g. -1200.00.")


class NoticeOut(BaseModel):
    id: int
    created_at: datetime = Field(description="UTC.")
    kind: str = Field(description="What was written, e.g. valuation.added.")
    headline: str
    effect: str | None = Field(description="What it unlocked, in words.")
    delta: NoticeDeltaOut | None = Field(description="Null when only a Question changed.")
    direction: Literal["up", "down"] | None = Field(description="From the sign of the delta.")
    link_path: str = Field(description="An in-app route.")
    dismissed: bool


class NoticesOut(BaseModel):
    items: list[NoticeOut]


def notice_out(notice: Notice) -> NoticeOut:
    money = service.delta_of(notice)
    return NoticeOut(
        id=notice.id,
        created_at=notice.created_at,
        kind=notice.kind,
        headline=notice.headline,
        effect=notice.effect,
        delta=(
            NoticeDeltaOut(
                minor=money.amount_minor, currency=money.currency, amount=format_amount(money)
            )
            if money
            else None
        ),
        direction=direction(money),
        link_path=notice.link_path,
        dismissed=notice.dismissed_at is not None,
    )


@router.get("", response_model=NoticesOut, operation_id="listNotices")
def list_notices(
    session: SessionDep,
    include_dismissed: bool = False,
    limit: Annotated[int, Query(ge=1, le=service.LIST_LIMIT_MAX)] = HOME_LIMIT,
) -> NoticesOut:
    """Recent changes, newest first, never expired. Home asks for the last ten undismissed."""
    items = service.list_notices(session, include_dismissed=include_dismissed, limit=limit)
    return NoticesOut(items=[notice_out(n) for n in items])


@router.post(
    "/{notice_id}/dismiss",
    status_code=204,
    operation_id="dismissNotice",
    responses=ERRORS,  # type: ignore[arg-type]
)
def dismiss_notice(notice_id: int, session: SessionDep) -> Response:
    """Dismiss a Notice (idempotent). It leaves Home and stays, muted, on Recent changes."""
    service.dismiss(session, notice_id)
    return Response(status_code=204)
