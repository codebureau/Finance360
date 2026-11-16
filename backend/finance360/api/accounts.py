"""Account and Tag endpoints. Thin: every rule lives in ``capabilities.position``."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, MessageBody, SessionDep
from finance360.api.transactions import MoneyOut, money_out, plain
from finance360.capabilities.household.service import household_today
from finance360.capabilities.position import accounts as service
from finance360.capabilities.position import derived
from finance360.core.derived import DerivedValue, NoDerivedValue
from finance360.core.fx import format_rate
from finance360.core.models.account import Account
from finance360.core.price import format_price

router = APIRouter(prefix="/api", tags=["accounts"])

_CLOSE_ERRORS = {**ERRORS, 409: {"model": MessageBody}}


class OwnerOut(BaseModel):
    member_id: int
    member_name: str
    share_bp: int = Field(description="Basis points; 10000 is 100%.")


class AccountOut(BaseModel):
    id: int
    name: str
    category_key: str
    category_name: str
    account_type_key: str
    account_type_name: str
    currency: str
    notes: str | None
    opened_on: date | None
    closed_on: date | None
    ownership: Literal["sole", "joint"]
    owners: list[OwnerOut]
    tags: list[str]


class OwnerIn(BaseModel):
    member_id: int
    share_bp: int | None = Field(
        default=None,
        description="Basis points. Omit for every Member of a joint Account to share evenly.",
    )


class AccountIn(BaseModel):
    name: str = ""
    category_key: str = ""
    account_type_key: str = ""
    currency: str = "AUD"
    notes: str | None = None
    opened_on: date | None = None
    ownership: str = "sole"
    owners: list[OwnerIn] = []
    tags: list[str] = []


class CloseIn(BaseModel):
    closed_on: date | None = Field(
        default=None,
        description="First date it no longer counts. Default: today in the Household time zone.",
    )
    confirm_non_zero: bool = False


def _out(account: Account) -> AccountOut:
    return AccountOut(
        id=account.id,
        name=account.name,
        category_key=account.category.key,
        category_name=account.category.name,
        account_type_key=account.account_type.key,
        account_type_name=account.account_type.name,
        currency=account.currency,
        notes=account.notes,
        opened_on=account.opened_on,
        closed_on=account.closed_on,
        ownership="joint" if account.ownership == "joint" else "sole",
        owners=[
            OwnerOut(member_id=o.member_id, member_name=o.member.name, share_bp=o.share_bp)
            for o in account.owners
        ],
        tags=sorted(t.name for t in account.tags),
    )


def _input(body: AccountIn) -> service.AccountInput:
    return service.AccountInput(
        name=body.name,
        category_key=body.category_key,
        account_type_key=body.account_type_key,
        ownership=body.ownership,
        owners=[service.OwnerInput(o.member_id, o.share_bp) for o in body.owners],
        currency=body.currency,
        notes=body.notes,
        opened_on=body.opened_on,
        tags=body.tags,
    )


@router.get("/accounts", response_model=list[AccountOut], operation_id="listAccounts")
def list_accounts(session: SessionDep, include_closed: bool = False) -> list[AccountOut]:
    return [_out(a) for a in service.list_accounts(session, include_closed=include_closed)]


@router.get(
    "/accounts/{account_id}",
    response_model=AccountOut,
    operation_id="getAccount",
    responses={404: {"model": MessageBody}},  # type: ignore[arg-type]
)
def get_account(account_id: int, session: SessionDep) -> AccountOut:
    return _out(service.get_account(session, account_id))


@router.post(
    "/accounts",
    status_code=201,
    response_model=AccountOut,
    operation_id="createAccount",
    responses=ERRORS,  # type: ignore[arg-type]
)
def create_account(body: AccountIn, session: SessionDep, ctx: ContextDep) -> AccountOut:
    return _out(service.create_account(session, ctx, _input(body)))


@router.put(
    "/accounts/{account_id}",
    response_model=AccountOut,
    operation_id="updateAccount",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_account(
    account_id: int, body: AccountIn, session: SessionDep, ctx: ContextDep
) -> AccountOut:
    return _out(service.update_account(session, ctx, account_id, _input(body)))


@router.post(
    "/accounts/{account_id}/close",
    response_model=AccountOut,
    operation_id="closeAccount",
    responses=_CLOSE_ERRORS,  # type: ignore[arg-type]
)
def close_account(
    account_id: int, body: CloseIn, session: SessionDep, ctx: ContextDep
) -> AccountOut:
    return _out(
        service.close_account(
            session,
            ctx,
            account_id,
            body.closed_on or household_today(session),
            confirm_non_zero=body.confirm_non_zero,
        )
    )


class DerivedLineOut(BaseModel):
    kind: Literal["holding", "cash"]
    instrument_id: int | None
    instrument_name: str | None
    currency: str = Field(description="A holding's Price currency, or the cash currency.")
    units: str | None
    price: str | None
    price_as_at: date | None
    price_source: str | None
    stale: bool = Field(description="The Price is older than the staleness limit.")
    rate: str | None = Field(description="Null when no conversion was needed.")
    rate_as_at: date | None
    rate_source: str | None
    value: MoneyOut = Field(description="In the Account currency; the lines sum to the total.")


class StreamStatusOut(BaseModel):
    stream: Literal["trade", "cash"]
    coverage_to: date | None
    status: Literal["ok", "missing", "stale"]
    message: str | None


class ObservedOut(BaseModel):
    as_at: date
    source: str
    amount: MoneyOut


class DerivedValueOut(BaseModel):
    applies: bool = Field(description="False when the Account's Behaviour does not derive.")
    as_at: date
    total: MoneyOut | None = Field(description="Null when there is no derived value.")
    reason: str | None = Field(description="Why there is no derived value.")
    stale: bool
    stale_after_days: int
    lines: list[DerivedLineOut]
    streams: list[StreamStatusOut]
    observed: ObservedOut | None = Field(description="Best recorded Valuation on or before as_at.")
    difference: MoneyOut | None = Field(description="Observed minus derived (reconciliation).")


def _derived_line(view: derived.ViewLine) -> DerivedLineOut:
    line = view.line
    return DerivedLineOut(
        kind=line.kind,  # type: ignore[arg-type]
        instrument_id=line.instrument_id,
        instrument_name=view.name,
        currency=line.currency,
        units=plain(line.units),
        price=format_price(line.price.value) if line.price else None,
        price_as_at=line.price.as_at if line.price else None,
        price_source=line.price.source if line.price else None,
        stale=line.stale,
        rate=format_rate(line.fx.rate) if line.fx else None,
        rate_as_at=line.fx.as_at if line.fx else None,
        rate_source=line.fx.source if line.fx else None,
        value=money_out(line.value),  # type: ignore[arg-type]
    )


@router.get(
    "/accounts/{account_id}/derived-value",
    response_model=DerivedValueOut,
    operation_id="getDerivedValue",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_derived_value(
    account_id: int, request: Request, session: SessionDep, as_at: date | None = None
) -> DerivedValueOut:
    """A holdings-based Account's value on a date, derived from its Holdings, Prices and FX, with
    its lines, per-Stream status and reconciliation. ``as_at`` defaults to the latest date both
    Streams' Coverage reaches (at most today). ``applies`` is false for other Behaviours."""
    settings = request.app.state.settings
    view = derived.account_view(
        session, account_id, household_today(session), as_at, settings.price_stale_days
    )
    result = view.result
    return DerivedValueOut(
        applies=view.applies,
        as_at=view.as_at,
        total=money_out(result.total) if isinstance(result, DerivedValue) else None,
        reason=result.reason if isinstance(result, NoDerivedValue) else None,
        stale=isinstance(result, DerivedValue) and result.stale,
        stale_after_days=view.stale_days,
        lines=[_derived_line(v) for v in view.lines],
        streams=[
            StreamStatusOut(
                stream=s.stream,  # type: ignore[arg-type]
                coverage_to=s.coverage_to,
                status=s.status,
                message=s.message,
            )
            for s in view.streams
        ],
        observed=ObservedOut(
            as_at=view.observed.as_at,
            source=view.observed.source,
            amount=money_out(view.observed.amount),  # type: ignore[arg-type]
        )
        if view.observed
        else None,
        difference=money_out(view.difference),
    )


@router.get("/tags", response_model=list[str], operation_id="listTags")
def list_tags(session: SessionDep) -> list[str]:
    return service.list_tags(session)


def register_error_handlers(app: FastAPI) -> None:
    """Position's own domain errors; validation and not-found reuse the household handlers."""

    @app.exception_handler(service.CloseNeedsConfirmationError)
    def _needs_confirmation(_: Request, exc: service.CloseNeedsConfirmationError) -> JSONResponse:
        return JSONResponse({"message": str(exc)}, status_code=409)
