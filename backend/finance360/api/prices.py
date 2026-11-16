"""Price endpoints. Thin: every rule lives in ``capabilities.reference.prices``.

Prices cross the API as plain decimal strings (``"34.5678"``), never as JSON floats. The API only
ever enters ``manual`` Prices.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.reference import instruments
from finance360.capabilities.reference import prices as service
from finance360.core.models.price import Price
from finance360.core.price import NoPrice, format_price

router = APIRouter(prefix="/api/instruments/{instrument_id}", tags=["prices"])


class PriceOut(BaseModel):
    id: int
    instrument_id: int
    as_at: date
    value: str = Field(description="Price of one unit, exact decimal string.")
    currency: str
    source: str
    provenance_note: str | None
    recorded_at: datetime = Field(description="UTC.")
    status: Literal["current", "corrected", "removed"]
    superseded_at: datetime | None
    superseded_by_id: int | None


class PriceIn(BaseModel):
    as_at: date
    value: str = Field(default="", description="Decimal string, at most 6 decimal places.")
    provenance_note: str | None = None
    currency: str | None = Field(
        default=None, description="Optional; if given it must equal the Instrument's currency."
    )


class PriceLookupOut(BaseModel):
    result: Literal["price", "no_price"]
    on: date
    value: str | None = Field(description="Null when there is no Price. Never zero.")
    currency: str | None
    as_at: date | None = Field(description="The date of the Price used, which may be earlier.")
    source: str | None


def _out(price: Price) -> PriceOut:
    if price.superseded_at is None:
        status: Literal["current", "corrected", "removed"] = "current"
    else:
        status = "corrected" if price.superseded_by_id is not None else "removed"
    return PriceOut(
        id=price.id,
        instrument_id=price.instrument_id,
        as_at=price.as_at,
        value=format_price(price.value),
        currency=price.ccy,
        source=price.source,
        provenance_note=price.provenance_note,
        recorded_at=price.recorded_at,
        status=status,
        superseded_at=price.superseded_at,
        superseded_by_id=price.superseded_by_id,
    )


def _input(body: PriceIn) -> service.PriceInput:
    return service.PriceInput(
        as_at=body.as_at,
        value=body.value,
        provenance_note=body.provenance_note,
        currency=body.currency,
    )


@router.get(
    "/prices",
    response_model=list[PriceOut],
    operation_id="listPrices",
    responses=ERRORS,  # type: ignore[arg-type]
)
def list_prices(instrument_id: int, session: SessionDep) -> list[PriceOut]:
    """The whole history by date, superseded Prices included (status says which)."""
    return [_out(p) for p in service.list_prices(session, instrument_id)]


@router.get(
    "/price",
    response_model=PriceLookupOut,
    operation_id="priceOn",
    responses=ERRORS,  # type: ignore[arg-type]
)
def price_on(instrument_id: int, session: SessionDep, on: date) -> PriceLookupOut:
    """The Price on or before ``on``; ``no_price`` is an answer, not an error, and not zero."""
    found = service.price_for(session, instrument_id, on)
    if isinstance(found, NoPrice):
        return PriceLookupOut(
            result="no_price", on=on, value=None, currency=None, as_at=None, source=None
        )
    currency = instruments.get_instrument(session, instrument_id).currency
    return PriceLookupOut(
        result="price",
        on=on,
        value=format_price(found.value),
        currency=currency,
        as_at=found.as_at,
        source=found.source,
    )


@router.post(
    "/prices",
    status_code=201,
    response_model=PriceOut,
    operation_id="addPrice",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_price(instrument_id: int, body: PriceIn, session: SessionDep, ctx: ContextDep) -> PriceOut:
    return _out(service.add_price(session, ctx, instrument_id, _input(body)))


@router.put(
    "/prices/{price_id}",
    response_model=PriceOut,
    operation_id="correctPrice",
    responses=ERRORS,  # type: ignore[arg-type]
)
def correct_price(
    instrument_id: int, price_id: int, body: PriceIn, session: SessionDep, ctx: ContextDep
) -> PriceOut:
    """Correct a current manual Price: returns the new row; the old one stays, marked corrected."""
    return _out(service.correct_price(session, ctx, instrument_id, price_id, _input(body)))


@router.delete(
    "/prices/{price_id}",
    status_code=204,
    operation_id="removePrice",
    responses=ERRORS,  # type: ignore[arg-type]
)
def remove_price(
    instrument_id: int, price_id: int, session: SessionDep, ctx: ContextDep
) -> Response:
    """Remove a current manual Price: it stays in the history, marked removed."""
    service.remove_price(session, ctx, instrument_id, price_id)
    return Response(status_code=204)
