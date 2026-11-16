"""FX rate endpoints. Thin: every rule lives in ``capabilities.reference.fx_rates``.

Rates and amounts cross the API as plain decimal strings (``"2.0143"``, ``"1234.56"``), never as
JSON floats.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.reference import fx_rates as service
from finance360.core.errors import FieldError, ValidationError
from finance360.core.fx import NoRate, format_rate
from finance360.core.models.fx_rate import FxRate
from finance360.core.money import CURRENCY_EXPONENTS, MoneyError, format_amount, parse_amount

router = APIRouter(prefix="/api/fx-rates", tags=["fx-rates"])


class FxRateOut(BaseModel):
    id: int
    from_ccy: str
    to_ccy: str
    as_at: date
    rate: str = Field(description="Units of to_ccy per 1 from_ccy, exact decimal string.")
    source: str
    provenance_note: str | None
    recorded_at: datetime = Field(description="UTC.")
    status: Literal["current", "corrected", "removed"]
    superseded_at: datetime | None
    superseded_by_id: int | None


class FxRateIn(BaseModel):
    from_ccy: str = ""
    as_at: date
    rate: str = Field(default="", description="Decimal string, at most 10 decimal places.")
    provenance_note: str | None = None
    to_ccy: str = Field(default="AUD", description="M1 stores foreign to AUD only.")


class ConversionOut(BaseModel):
    result: Literal["converted", "no_rate"]
    on: date
    amount: str
    currency: str
    to_ccy: str
    converted: str | None = Field(description="Null when there is no rate. Never a default.")
    rate: str | None
    rate_as_at: date | None
    rate_source: str | None = Field(default=None, description="Where the rate came from.")


def _out(fx_rate: FxRate) -> FxRateOut:
    if fx_rate.superseded_at is None:
        status: Literal["current", "corrected", "removed"] = "current"
    else:
        status = "corrected" if fx_rate.superseded_by_id is not None else "removed"
    return FxRateOut(
        id=fx_rate.id,
        from_ccy=fx_rate.from_ccy,
        to_ccy=fx_rate.to_ccy,
        as_at=fx_rate.as_at,
        rate=format_rate(fx_rate.rate),
        source=fx_rate.source,
        provenance_note=fx_rate.provenance_note,
        recorded_at=fx_rate.recorded_at,
        status=status,
        superseded_at=fx_rate.superseded_at,
        superseded_by_id=fx_rate.superseded_by_id,
    )


def _input(body: FxRateIn) -> service.FxRateInput:
    return service.FxRateInput(
        from_ccy=body.from_ccy,
        as_at=body.as_at,
        rate=body.rate,
        provenance_note=body.provenance_note,
        to_ccy=body.to_ccy,
    )


@router.get("", response_model=list[FxRateOut], operation_id="listFxRates")
def list_fx_rates(session: SessionDep) -> list[FxRateOut]:
    """The whole history by date, superseded rates included (status says which)."""
    return [_out(r) for r in service.list_fx_rates(session)]


@router.get(
    "/convert",
    response_model=ConversionOut,
    operation_id="convertOn",
    responses=ERRORS,  # type: ignore[arg-type]
)
def convert_on(
    session: SessionDep, amount: str, currency: str, on: date, to_ccy: str = "AUD"
) -> ConversionOut:
    """Convert at the latest rate on or before ``on``; ``no_rate`` is an answer, not an error."""
    currency = currency.strip().upper()
    to_ccy = to_ccy.strip().upper()
    try:
        money = parse_amount(amount, currency)
    except MoneyError:
        raise ValidationError(
            [FieldError("amount", f"Enter an amount in {currency}, e.g. 1234.56.")]
        ) from None
    if to_ccy not in CURRENCY_EXPONENTS:
        raise ValidationError([FieldError("to_ccy", "Choose a currency code we know.")])
    try:
        found = service.convert_on(session, money, to_ccy, on)
    except MoneyError:
        # The converted amount would not fit in Money's int64 range.
        raise ValidationError(
            [FieldError("amount", f"That amount is too large to convert to {to_ccy}.")]
        ) from None
    out = ConversionOut(
        result="no_rate",
        on=on,
        amount=format_amount(money),
        currency=currency,
        to_ccy=to_ccy,
        converted=None,
        rate=None,
        rate_as_at=None,
    )
    if not isinstance(found, NoRate):
        out.result = "converted"
        out.converted = format_amount(found.converted)
        out.rate = format_rate(found.rate)
        out.rate_as_at = found.rate_as_at
        out.rate_source = found.rate_source
    return out


@router.post(
    "",
    status_code=201,
    response_model=FxRateOut,
    operation_id="addFxRate",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_fx_rate(body: FxRateIn, session: SessionDep, ctx: ContextDep) -> FxRateOut:
    return _out(service.add_fx_rate(session, ctx, _input(body)))


@router.put(
    "/{fx_rate_id}",
    response_model=FxRateOut,
    operation_id="correctFxRate",
    responses=ERRORS,  # type: ignore[arg-type]
)
def correct_fx_rate(
    fx_rate_id: int, body: FxRateIn, session: SessionDep, ctx: ContextDep
) -> FxRateOut:
    """Correct a current rate: returns the new row; the old one stays, marked corrected."""
    return _out(service.correct_fx_rate(session, ctx, fx_rate_id, _input(body)))


@router.delete(
    "/{fx_rate_id}",
    status_code=204,
    operation_id="removeFxRate",
    responses=ERRORS,  # type: ignore[arg-type]
)
def remove_fx_rate(fx_rate_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    """Remove a current rate: it stays in the history, marked removed."""
    service.remove_fx_rate(session, ctx, fx_rate_id)
    return Response(status_code=204)
