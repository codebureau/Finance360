"""The Position endpoint. Thin: the maths lives in ``capabilities.position.calculation``.

Amounts are plain AUD decimal strings (``"-1234.56"``), signed (Liabilities negative), never JSON
floats. ``original`` is in the Account's own currency.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, SessionDep
from finance360.capabilities.household import service as household
from finance360.capabilities.position import accounts, service
from finance360.capabilities.position import calculation as calc
from finance360.core.fx import format_rate
from finance360.core.money import Money, format_amount

router = APIRouter(prefix="/api/position", tags=["position"])


class MemberFigureOut(BaseModel):
    member_id: int
    sole: str = Field(description="Held solely by this Member.")
    joint_share: str = Field(description="This Member's share of joint Accounts.")
    total: str = Field(description="sole + joint_share.")


class FiguresOut(BaseModel):
    household: str
    joint: str = Field(description="Joint Accounts, whole.")
    members: list[MemberFigureOut]


class AccountLineOut(BaseModel):
    account_id: int
    name: str
    ownership: Literal["sole", "joint"]
    currency: str
    tags: list[str]
    status: Literal["ok", "no_valuation", "no_rate"]
    valuation_as_at: date | None
    source: str | None
    original: str | None = Field(description="The Valuation as recorded, in the Account currency.")
    rate: str | None
    rate_as_at: date | None
    rate_source: str | None = Field(description="Where the rate came from (manual, rba, ...).")
    value: str | None = Field(description="Signed AUD; null when flagged.")
    shares: dict[str, str] = Field(description="Signed AUD per owner, keyed by Member id.")


class PositionCategoryOut(BaseModel):
    key: str
    name: str
    figures: FiguresOut
    flagged: int
    accounts: list[AccountLineOut]


class PositionClassOut(BaseModel):
    class_: Literal["asset", "liability"] = Field(serialization_alias="class")
    figures: FiguresOut
    flagged: int
    categories: list[PositionCategoryOut]


class MemberRefOut(BaseModel):
    id: int
    name: str


class PositionOut(BaseModel):
    as_at: date
    tag: str | None
    members: list[MemberRefOut]
    tags: list[str] = Field(description="Every Tag in use, for the filter.")
    classes: list[PositionClassOut]
    net: FiguresOut
    flagged: int = Field(description="Accounts left out of every total (no Valuation or FX rate).")


def _amount(money: Money) -> str:
    return format_amount(money)


def _figures(f: calc.Figures) -> FiguresOut:
    return FiguresOut(
        household=_amount(f.household),
        joint=_amount(f.joint),
        members=[
            MemberFigureOut(
                member_id=m.member_id,
                sole=_amount(m.sole),
                joint_share=_amount(m.joint_share),
                total=_amount(m.total),
            )
            for m in f.members
        ],
    )


def _line(line: calc.AccountLine) -> AccountLineOut:
    return AccountLineOut(
        account_id=line.account_id,
        name=line.name,
        ownership=line.ownership,  # type: ignore[arg-type]
        currency=line.currency,
        tags=list(line.tags),
        status=line.status,
        valuation_as_at=line.valuation_as_at,
        source=line.source,
        original=_amount(line.original) if line.original is not None else None,
        rate=format_rate(line.rate) if line.rate is not None else None,
        rate_as_at=line.rate_as_at,
        rate_source=line.rate_source,
        value=_amount(line.value) if line.value is not None else None,
        shares={str(member_id): _amount(part) for member_id, part in line.shares},
    )


@router.get(
    "",
    response_model=PositionOut,
    operation_id="getPosition",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_position(
    session: SessionDep, on: date | None = None, tag: str | None = None
) -> PositionOut:
    """The Position as at ``on`` (default today in the Household time zone), optionally one Tag."""
    position = service.position_on(session, on, tag)
    names = {m.id: m.name for m in household.list_members(session)}
    return PositionOut(
        as_at=position.on,
        tag=position.tag,
        members=[MemberRefOut(id=i, name=n) for i, n in names.items()],
        tags=accounts.list_tags(session),
        classes=[
            PositionClassOut(
                class_=c.class_,  # type: ignore[arg-type]
                figures=_figures(c.figures),
                flagged=c.flagged,
                categories=[
                    PositionCategoryOut(
                        key=k.key,
                        name=k.name,
                        figures=_figures(k.figures),
                        flagged=k.flagged,
                        accounts=[_line(a) for a in k.accounts],
                    )
                    for k in c.categories
                ],
            )
            for c in position.classes
        ],
        net=_figures(position.net),
        flagged=position.flagged,
    )


#: A guard against a request for a decade of weeks; month and quarter steps stay far below it.
MAX_HISTORY_POINTS = 600


class HistoryPointOut(BaseModel):
    on: date
    assets: FiguresOut
    liabilities: FiguresOut = Field(description="Signed: negative.")
    net: FiguresOut
    flagged: int = Field(
        description="Accounts left out of this point's figures. Above 0 the point is incomplete."
    )


class PositionHistoryOut(BaseModel):
    tag: str | None
    members: list[MemberRefOut]
    tags: list[str]
    points: list[HistoryPointOut]


@router.get(
    "/history",
    response_model=PositionHistoryOut,
    operation_id="getPositionHistory",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_position_history(
    session: SessionDep,
    start: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    step: calc.HistoryStep = "month",
    tag: str | None = None,
) -> PositionHistoryOut:
    """The Position at month-ends (or quarter-ends) over a range, plus the end date itself.

    Defaults: ``to`` is today in the Household zone, ``from`` the earliest Valuation. Each point
    carries the same Figures as the single-date Position, so the client picks the view.
    """
    points = service.position_history(session, start, to, step, tag, MAX_HISTORY_POINTS)
    members = household.list_members(session)
    return PositionHistoryOut(
        tag=tag,
        members=[MemberRefOut(id=m.id, name=m.name) for m in members],
        tags=accounts.list_tags(session),
        points=[
            HistoryPointOut(
                on=p.on,
                assets=_figures(p.classes[0].figures),
                liabilities=_figures(p.classes[1].figures),
                net=_figures(p.net),
                flagged=p.flagged,
            )
            for p in points
        ],
    )
