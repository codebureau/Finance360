"""Instrument and identifier endpoints. Thin: every rule lives in
``capabilities.reference.instruments``."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.reference import instruments as service
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.instrument import Instrument

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


class IdentifierOut(BaseModel):
    id: int
    scheme: str
    value: str


class InstrumentOut(BaseModel):
    id: int
    name: str
    kind: str
    currency: str
    notes: str | None
    identifiers: list[IdentifierOut]


class IdentifierIn(BaseModel):
    scheme: str = Field(default="", description="ticker | yahoo | isin | vendor_id | name")
    value: str = ""


class InstrumentIn(BaseModel):
    name: str = ""
    kind: str = Field(default="", description="etf | share | fund")
    currency: str = Field(default="", description="ISO 4217, e.g. AUD.")
    notes: str | None = None


class InstrumentCreate(InstrumentIn):
    identifiers: list[IdentifierIn] = []


def _out(instrument: Instrument) -> InstrumentOut:
    return InstrumentOut(
        id=instrument.id,
        name=instrument.name,
        kind=instrument.kind,
        currency=instrument.currency,
        notes=instrument.notes,
        identifiers=[
            IdentifierOut(id=i.id, scheme=i.scheme, value=i.value) for i in instrument.identifiers
        ],
    )


def _input(
    body: InstrumentIn, identifiers: list[IdentifierIn] | None = None
) -> service.InstrumentInput:
    return service.InstrumentInput(
        name=body.name,
        kind=body.kind,
        currency=body.currency,
        notes=body.notes,
        identifiers=tuple(service.IdentifierInput(i.scheme, i.value) for i in identifiers or []),
    )


@router.get("", response_model=list[InstrumentOut], operation_id="listInstruments")
def list_instruments(session: SessionDep) -> list[InstrumentOut]:
    return [_out(i) for i in service.list_instruments(session)]


@router.get(
    "/lookup",
    response_model=InstrumentOut,
    operation_id="lookupInstrument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def lookup_instrument(session: SessionDep, value: str, scheme: str | None = None) -> InstrumentOut:
    """The one Instrument a ticker, ISIN or name alias names (case and spacing ignored)."""
    found = service.resolve(session, value, scheme.strip().lower() if scheme else None)
    if found is None:
        raise NotFoundError(f"No Instrument is identified by {value.strip()!r}.")
    if isinstance(found, service.Ambiguous):
        raise ValidationError(
            [
                FieldError(
                    "scheme",
                    f"{value.strip()!r} matches several Instruments "
                    f"({', '.join(found.schemes)}). Say which scheme.",
                )
            ]
        )
    return _out(found)


@router.get(
    "/{instrument_id}",
    response_model=InstrumentOut,
    operation_id="getInstrument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_instrument(instrument_id: int, session: SessionDep) -> InstrumentOut:
    return _out(service.get_instrument(session, instrument_id))


@router.post(
    "",
    status_code=201,
    response_model=InstrumentOut,
    operation_id="createInstrument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def create_instrument(
    body: InstrumentCreate, session: SessionDep, ctx: ContextDep
) -> InstrumentOut:
    return _out(service.create_instrument(session, ctx, _input(body, body.identifiers)))


@router.put(
    "/{instrument_id}",
    response_model=InstrumentOut,
    operation_id="updateInstrument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_instrument(
    instrument_id: int, body: InstrumentIn, session: SessionDep, ctx: ContextDep
) -> InstrumentOut:
    return _out(service.update_instrument(session, ctx, instrument_id, _input(body)))


@router.post(
    "/{instrument_id}/identifiers",
    status_code=201,
    response_model=InstrumentOut,
    operation_id="addInstrumentIdentifier",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_identifier(
    instrument_id: int, body: IdentifierIn, session: SessionDep, ctx: ContextDep
) -> InstrumentOut:
    """Attach an identifier. One already naming another Instrument is rejected (422)."""
    ident = service.IdentifierInput(body.scheme, body.value)
    return _out(service.add_identifier(session, ctx, instrument_id, ident))


@router.delete(
    "/{instrument_id}/identifiers/{identifier_id}",
    response_model=InstrumentOut,
    operation_id="removeInstrumentIdentifier",
    responses=ERRORS,  # type: ignore[arg-type]
)
def remove_identifier(
    instrument_id: int, identifier_id: int, session: SessionDep, ctx: ContextDep
) -> InstrumentOut:
    return _out(service.remove_identifier(session, ctx, instrument_id, identifier_id))
