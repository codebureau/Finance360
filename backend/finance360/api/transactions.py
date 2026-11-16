"""Transaction endpoints (ADR-0013 sections 2, 3 and 7). Thin. Transactions
are written by committing an Import (``api/imports.py``); the one direct write is an Opening
position."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.imports import opening, transactions
from finance360.core.money import Money, format_amount

router = APIRouter(prefix="/api", tags=["transactions"])


class MoneyOut(BaseModel):
    amount: str = Field(description="Decimal string, e.g. -1922.15.")
    amount_minor: int
    currency: str


class TransactionOut(BaseModel):
    id: int
    stream: str
    occurred_on: date
    kind: str
    instrument_id: int | None
    instrument_name: str | None
    quantity: str | None
    unit_price: str | None
    unit_price_ccy: str | None
    fee: MoneyOut | None
    amount: MoneyOut | None
    import_id: int | None
    file_name: str | None
    row_no: int | None
    #: Set when a later Import replaced this row; it stays retrievable.
    superseded_at: datetime | None


class TransactionsOut(BaseModel):
    total: int
    items: list[TransactionOut]


class AccountTransactionsOut(TransactionsOut):
    #: Current Transactions per Stream, so the page can say what each Stream holds.
    current_trade: int
    current_cash: int


def plain(value: Decimal | None, places: int = 0) -> str | None:
    """A stored scaled decimal as plain text without trailing zeros (``-40``, ``96.1075``), but
    with at least ``places`` decimal places (a price reads ``6.40``). Exact: no float."""
    if value is None:
        return None
    text = format(value.normalize(), "f")
    whole, _, fraction = text.partition(".")
    return f"{whole}.{fraction.ljust(places, '0')}" if places else text


def money_out(money: Money | None) -> MoneyOut | None:
    if money is None:
        return None
    return MoneyOut(
        amount=format_amount(money), amount_minor=money.amount_minor, currency=money.currency
    )


def transaction_out(row: transactions.TransactionRow) -> TransactionOut:
    t = row.transaction
    return TransactionOut(
        id=t.id,
        stream=t.stream,
        occurred_on=t.occurred_on,
        kind=t.kind,
        instrument_id=t.instrument_id,
        instrument_name=row.instrument_name,
        quantity=plain(t.quantity),
        unit_price=plain(t.unit_price, 2),
        unit_price_ccy=t.unit_price_ccy,
        fee=money_out(t.fee),
        amount=money_out(t.amount),
        import_id=t.import_id,
        file_name=row.file_name,
        row_no=t.row_no,
        superseded_at=t.superseded_at.replace(tzinfo=UTC) if t.superseded_at else None,
    )


@router.get(
    "/accounts/{account_id}/transactions",
    response_model=AccountTransactionsOut,
    operation_id="listTransactions",
    responses=ERRORS,  # type: ignore[arg-type]
)
def list_transactions(
    account_id: int,
    session: SessionDep,
    stream: Literal["trade", "cash"] = "trade",
    include_superseded: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 10,
) -> AccountTransactionsOut:
    """One Stream of an Account's Transactions, newest first. Superseded rows are included only on
    request; they stay retrievable."""
    page = transactions.list_for_account(
        session,
        account_id,
        stream,
        include_superseded=include_superseded,
        offset=offset,
        limit=limit,
    )
    counts = transactions.current_counts(session, account_id)
    return AccountTransactionsOut(
        total=page.total,
        items=[transaction_out(r) for r in page.items],
        current_trade=counts["trade"],
        current_cash=counts["cash"],
    )


class HoldingOut(BaseModel):
    instrument_id: int
    instrument_name: str
    units: str = Field(description="Exact decimal string, e.g. 12.5.")


class InvalidBalanceOut(BaseModel):
    """A balance that went negative: explained, never shown as a value."""

    instrument_id: int | None = Field(description="Null for cash.")
    instrument_name: str | None
    currency: str | None = Field(description="Set for cash.")
    on: date = Field(description="The first date the balance ended below zero.")
    message: str


class HoldingsOut(BaseModel):
    as_at: date | None = Field(description="Null means the latest: every current Transaction.")
    holdings: list[HoldingOut]
    cash: list[MoneyOut]
    invalid: list[InvalidBalanceOut]


@router.get(
    "/accounts/{account_id}/holdings",
    response_model=HoldingsOut,
    operation_id="getHoldings",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_holdings(account_id: int, session: SessionDep, as_at: date | None = None) -> HoldingsOut:
    """Units per Instrument and the cash balance on a date, derived from current Transactions.
    A balance that went negative is reported under ``invalid`` instead of as a value."""
    view = transactions.holdings(session, account_id, as_at)
    derived = view.holdings
    invalid: list[InvalidBalanceOut] = []
    for n in derived.invalid:
        name = view.names.get(n.instrument_id) if n.instrument_id is not None else None
        what = name or f"cash in {n.currency}"
        invalid.append(
            InvalidBalanceOut(
                instrument_id=n.instrument_id,
                instrument_name=name,
                currency=n.currency,
                on=n.on,
                message=f"{what} went negative on {n.on.isoformat()}: possible missing Opening "
                "position.",
            )
        )
    return HoldingsOut(
        as_at=as_at,
        holdings=[
            HoldingOut(
                instrument_id=i,
                instrument_name=view.names.get(i, "Unnamed Instrument"),
                units=plain(q) or "0",
            )
            for i, q in sorted(derived.units.items(), key=lambda kv: view.names.get(kv[0], ""))
        ],
        cash=[m for c in sorted(derived.cash) if (m := money_out(derived.cash[c])) is not None],
        invalid=invalid,
    )


class OpeningPositionIn(BaseModel):
    kind: str = Field(default="", description="holding (units of an Instrument) | cash")
    occurred_on: date | None = None
    instrument_id: int | None = None
    quantity: str = Field(default="", description="Units, exact decimal string (holding).")
    amount: str = Field(default="", description="In the Account's currency (cash).")


@router.post(
    "/accounts/{account_id}/opening-positions",
    status_code=201,
    response_model=TransactionOut,
    operation_id="addOpeningPosition",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_opening_position(
    account_id: int, body: OpeningPositionIn, session: SessionDep, ctx: ContextDep
) -> TransactionOut:
    """Record a manual, dated Opening position: units of an Instrument or cash."""
    txn = opening.record(
        session,
        ctx,
        account_id,
        opening.OpeningInput(
            kind=body.kind,
            occurred_on=body.occurred_on,
            instrument_id=body.instrument_id,
            quantity=body.quantity,
            amount=body.amount,
        ),
    )
    return transaction_out(transactions.TransactionRow(txn, None, None))
