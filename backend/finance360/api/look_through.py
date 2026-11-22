"""The Account look-through endpoint. Thin: every rule lives in ``capabilities.lookthrough``.

Amounts are Money (exact decimal strings with their currency); percentages are display only,
rounded once from Money.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, SessionDep
from finance360.api.transactions import MoneyOut, money_out
from finance360.capabilities.lookthrough import service
from finance360.capabilities.lookthrough.lines import Composition
from finance360.core.exposure import ASSET_CLASS, percent_string

router = APIRouter(prefix="/api", tags=["look-through"])


class LookThroughLineOut(BaseModel):
    kind: Literal["label", "no_profile", "not_classified", "unallocated"]
    key: str | None = Field(description="The Exposure label key of a label line.")
    name: str
    amount: MoneyOut
    percent: str | None = Field(
        description="Of the Account value, two decimal places; display only."
    )
    instrument_id: int | None = Field(description="The Instrument a No profile line asks for.")


class TrimmedOut(BaseModel):
    label_key: str
    percent: str


class LookThroughPartOut(BaseModel):
    instrument_id: int | None
    name: str
    amount: MoneyOut
    profile_as_at: date | None
    dated_after: bool = Field(description="The profile used is dated after the as-at date.")
    shortfall_percent: str = Field(description="A within-tolerance shortfall, in Not classified.")
    trimmed: list[TrimmedOut] = Field(description="Trimmed from the largest weight (over 100).")


class LookThroughOut(BaseModel):
    applies: bool = Field(description="False when the Account's value is not built from Holdings.")
    as_at: date
    status: Literal["ok", "not_analysed"]
    reason: str | None
    dimension_key: str
    dimension_name: str
    total: MoneyOut | None = Field(description="The Account's value in the Position.")
    lines: list[LookThroughLineOut]
    parts: list[LookThroughPartOut]
    flagged: bool = Field(description="Unallocated is beyond the reconcile tolerance.")
    tolerance: MoneyOut | None


def _lines(c: Composition) -> list[LookThroughLineOut]:
    return [
        LookThroughLineOut(
            kind=line.kind,
            key=line.key,
            name=line.name,
            amount=money_out(line.amount),  # type: ignore[arg-type]
            percent=line.percent,
            instrument_id=line.instrument_id,
        )
        for line in c.lines
    ]


def _parts(c: Composition) -> list[LookThroughPartOut]:
    return [
        LookThroughPartOut(
            instrument_id=p.instrument_id,
            name=p.name,
            amount=money_out(p.amount),  # type: ignore[arg-type]
            profile_as_at=p.profile_as_at,
            dated_after=p.dated_after,
            shortfall_percent=percent_string(p.shortfall_ppm),
            trimmed=[TrimmedOut(label_key=k, percent=percent_string(w)) for k, w in p.trimmed],
        )
        for p in c.parts
    ]


@router.get(
    "/accounts/{account_id}/look-through",
    response_model=LookThroughOut,
    operation_id="getLookThrough",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_look_through(
    account_id: int,
    request: Request,
    session: SessionDep,
    on: date | None = None,
    dimension: str = ASSET_CLASS,
) -> LookThroughOut:
    """An investment Account's value on a date split by an Exposure Dimension (asset class), from
    its Holdings' manually entered Exposure profiles. The lines, including No profile, Not
    classified and Unallocated, sum exactly to the Account's value in the Position. ``on``
    defaults to the latest date the Account's Streams' Coverage reaches (at most today), as the
    Derived value does; ``applies`` is false when the value is not built from Holdings."""
    settings = request.app.state.settings
    found = service.account_look_through(
        session, account_id, on, dimension, settings.price_stale_days
    )
    c = found.composition
    return LookThroughOut(
        applies=found.applies,
        as_at=found.on,
        status=found.status,
        reason=found.reason,
        dimension_key=found.dimension_key,
        dimension_name=found.dimension_name,
        total=money_out(c.total) if c else None,
        lines=_lines(c) if c else [],
        parts=_parts(c) if c else [],
        flagged=c.flagged if c else False,
        tolerance=money_out(c.tolerance) if c else None,
    )
