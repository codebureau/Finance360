"""Valuation endpoints. Thin: every rule lives in ``capabilities.position.valuations``.

Amounts cross the API as plain decimal strings in the Account's currency (``"1234.56"``) plus the
exact integer ``amount_minor``; never as a JSON float.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, MessageBody, SessionDep
from finance360.api.notices import NoticeOut, notice_out
from finance360.capabilities.notices import service as notices
from finance360.capabilities.position import valuations as service
from finance360.core.models.notice import Notice
from finance360.core.models.valuation import Valuation
from finance360.core.money import format_amount

router = APIRouter(prefix="/api", tags=["valuations"])


class ValuationOut(BaseModel):
    id: int
    account_id: int
    as_at: date
    amount: str = Field(description="Decimal string in the Account's currency, e.g. 1234.56.")
    amount_minor: int
    currency: str
    source: Literal["manual", "statement", "derived", "estimate"]
    provenance_note: str | None
    recorded_at: datetime = Field(description="UTC.")
    status: Literal["current", "corrected", "removed"]
    superseded_at: datetime | None
    superseded_by_id: int | None
    notice: NoticeOut | None = Field(
        default=None,
        description="Set on add and correct when the write moved the picture (ADR-0033).",
    )


class ValuationIn(BaseModel):
    as_at: date | None = Field(default=None, description="Defaults to today (Household time zone).")
    amount: str = Field(default="", description="Decimal string in the Account's currency.")
    source: str = "manual"
    provenance_note: str | None = None
    currency: str | None = Field(
        default=None, description="Optional. If given, must equal the Account's currency."
    )


def _out(valuation: Valuation, notice: Notice | None = None) -> ValuationOut:
    if valuation.superseded_at is None:
        status: Literal["current", "corrected", "removed"] = "current"
    else:
        status = "corrected" if valuation.superseded_by_id is not None else "removed"
    return ValuationOut(
        id=valuation.id,
        account_id=valuation.account_id,
        as_at=valuation.as_at,
        amount=format_amount(valuation.amount),
        amount_minor=valuation.amount.amount_minor,
        currency=valuation.amount.currency,
        source=valuation.source,  # type: ignore[arg-type]
        provenance_note=valuation.provenance_note,
        recorded_at=valuation.recorded_at,
        status=status,
        superseded_at=valuation.superseded_at,
        superseded_by_id=valuation.superseded_by_id,
        notice=notice_out(notice) if notice else None,
    )


def _input(body: ValuationIn) -> service.ValuationInput:
    return service.ValuationInput(
        as_at=body.as_at,
        amount=body.amount,
        source=body.source,
        provenance_note=body.provenance_note,
        currency=body.currency,
    )


@router.get(
    "/accounts/{account_id}/valuations",
    response_model=list[ValuationOut],
    operation_id="listValuations",
    responses={404: {"model": MessageBody}},  # type: ignore[arg-type]
)
def list_valuations(account_id: int, session: SessionDep) -> list[ValuationOut]:
    """The Account's whole history by date, superseded Valuations included (status says which)."""
    return [_out(v) for v in service.list_valuations(session, account_id)]


@router.post(
    "/accounts/{account_id}/valuations",
    status_code=201,
    response_model=ValuationOut,
    operation_id="addValuation",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_valuation(
    account_id: int, body: ValuationIn, session: SessionDep, ctx: ContextDep
) -> ValuationOut:
    return _out(*notices.add_valuation(session, ctx, account_id, _input(body)))


@router.put(
    "/valuations/{valuation_id}",
    response_model=ValuationOut,
    operation_id="correctValuation",
    responses=ERRORS,  # type: ignore[arg-type]
)
def correct_valuation(
    valuation_id: int, body: ValuationIn, session: SessionDep, ctx: ContextDep
) -> ValuationOut:
    """Correct a current Valuation: returns the new row; the old one stays, marked corrected."""
    return _out(*notices.correct_valuation(session, ctx, valuation_id, _input(body)))


@router.delete(
    "/valuations/{valuation_id}",
    status_code=204,
    operation_id="removeValuation",
    responses=ERRORS,  # type: ignore[arg-type]
)
def remove_valuation(valuation_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    """Remove a current Valuation: it stays in the history, marked removed."""
    service.remove_valuation(session, ctx, valuation_id)
    return Response(status_code=204)
