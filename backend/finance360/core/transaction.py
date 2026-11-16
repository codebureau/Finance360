"""Transaction vocabulary, exact trade value and the derived cash and unit queries (ADR-0013
sections 2 and 3).

A Transaction is one dated record in an Account's *trade* or *cash* Stream. Holdings and the cash
balance are **derived** from current Transactions, never stored: units of an Instrument on a date
are the sum of current trade ``quantity`` up to it, and cash is the sum of current cash ``amount``.

**Fees are recorded once, by construction.** A trade row's ``fee`` is informational (the input to
reconciliation and later cost base). Only a cash Stream row of kind ``fee`` moves cash, so nothing
here ever adds a trade ``fee`` to the cash balance.

The queries read only *current* rows (``superseded_at IS NULL``). Pure helpers take and return
``Decimal`` and ``Money``; no floats.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.core.models.imports import Import, ImportSource
from finance360.core.models.transaction import Transaction
from finance360.core.money import Money, MoneyError, decimal_parts, exponent, round_div
from finance360.core.price import PRICE_SCALE

STREAMS = ("trade", "cash")
TRADE_KINDS = ("buy", "sell", "opening_balance")
CASH_KINDS = (
    "deposit",
    "withdrawal",
    "trade_settlement",
    "distribution",
    "interest",
    "fee",
    "opening_balance",
)
KINDS_BY_STREAM = {"trade": TRADE_KINDS, "cash": CASH_KINDS}

#: Quantities are exact at scale 6, as Prices are.
QUANTITY_SCALE = 6


def _scaled(value: Decimal, scale: int) -> int:
    """``abs(value)`` as an integer count of ``10**-scale`` units; refuses over-precise values."""
    _, units, exp = decimal_parts(value)
    shift = exp + scale
    if shift >= 0:
        return units * 10**shift
    units, remainder = divmod(units, 10**-shift)
    if remainder:
        raise MoneyError(f"{value} has more than {scale} decimal places")
    return units


def trade_value(quantity: Decimal, unit_price: Decimal, currency: str) -> Money:
    """The value of a trade, ``abs(quantity) * unit_price`` rounded half-even once to minor units.
    Never negative: the sign of the cash it settles for is the caller's (buy out, sell in)."""
    product = _scaled(quantity, QUANTITY_SCALE) * _scaled(unit_price, PRICE_SCALE)
    minor = round_div(product * 10 ** exponent(currency), 10 ** (QUANTITY_SCALE + PRICE_SCALE))
    return Money(minor, currency)


def current(session: Session, account_id: int, stream: str | None = None) -> list[Transaction]:
    """The Account's current Transactions (optionally one Stream), oldest first."""
    query = select(Transaction).where(
        Transaction.account_id == account_id, Transaction.superseded_at.is_(None)
    )
    if stream is not None:
        query = query.where(Transaction.stream == stream)
    return list(session.scalars(query.order_by(Transaction.occurred_on, Transaction.id)))


def cash_balance(session: Session, account_id: int, currency: str, on: date | None = None) -> Money:
    """Cash on ``on`` (or ever): the sum of current cash Stream ``amount``. Trade fees are not in
    it."""
    query = select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
        Transaction.account_id == account_id,
        Transaction.stream == "cash",
        Transaction.superseded_at.is_(None),
        Transaction.amount_ccy == currency,
    )
    if on is not None:
        query = query.where(Transaction.occurred_on <= on)
    return Money(int(session.scalar(query) or 0), currency)


def units_held(session: Session, account_id: int, on: date | None = None) -> dict[int, Decimal]:
    """Units of each Instrument on ``on`` (or ever): the sum of current trade ``quantity``."""
    totals: dict[int, Decimal] = {}
    for row in current(session, account_id, "trade"):
        if row.instrument_id is None or row.quantity is None:
            continue
        if on is not None and row.occurred_on > on:
            continue
        totals[row.instrument_id] = totals.get(row.instrument_id, Decimal(0)) + row.quantity
    return totals


def earliest_trade_dates(session: Session) -> dict[int, date]:
    """Instrument id -> date of its earliest *current* trade Transaction, across every Account.

    Superseded rows are ignored (a later Import that replaced them decides), as are Instruments
    with no trade Transaction. Used to decide where a first Price fetch starts.
    """
    rows = session.execute(
        select(Transaction.instrument_id, func.min(Transaction.occurred_on))
        .where(
            Transaction.stream == "trade",
            Transaction.superseded_at.is_(None),
            Transaction.instrument_id.is_not(None),
        )
        .group_by(Transaction.instrument_id)
    )
    return {instrument_id: first for instrument_id, first in rows if instrument_id is not None}


def stream_coverage(session: Session, account_id: int) -> dict[str, date]:
    """Stream -> the latest ``coverage_to`` among the Account's *committed* Imports. A Stream with
    no committed Import is absent. The Coverage a derived value needs to reach its date."""
    rows = session.execute(
        select(Import.stream, func.max(Import.coverage_to))
        .join(ImportSource, Import.import_source_id == ImportSource.id)
        .where(ImportSource.account_id == account_id, Import.status == "committed")
        .group_by(Import.stream)
    )
    return {stream: reached for stream, reached in rows}


# --- derived Holdings and cash, and the replay that finds a negative one (ADR-0013 s3, s7) ---


def parse_quantity(text: str) -> Decimal:
    """A quantity typed by a person, exact at scale 6. Refuses anything else, never rounds."""
    try:
        value = Decimal(text.strip().replace(",", ""))
    except ArithmeticError:
        raise MoneyError(f"Not a quantity: {text!r}") from None
    if not value.is_finite():
        raise MoneyError(f"Not a quantity: {text!r}")
    _scaled(value, QUANTITY_SCALE)  # raises if more than six decimal places
    return value


@dataclass(frozen=True)
class Change:
    """One dated change to a balance. A Holding when ``instrument_id`` is set (``delta`` is units);
    otherwise cash in ``currency`` (``delta`` is minor units)."""

    occurred_on: date
    instrument_id: int | None
    currency: str | None
    delta: Decimal


@dataclass(frozen=True)
class Negative:
    """A balance that went below zero: the *first* date it did, and what it was at the end of that
    day. An invalid state to be explained, never a value to be shown."""

    instrument_id: int | None
    currency: str | None
    on: date
    balance: Decimal


@dataclass(frozen=True)
class HoldingsAsAt:
    """What an Account held on a date. A balance that ever went negative by then is in ``invalid``
    and in neither ``units`` nor ``cash``."""

    on: date | None
    #: Instrument id -> units, only those held (above zero) and valid.
    units: dict[int, Decimal]
    #: Currency -> cash balance, only the valid ones.
    cash: dict[str, Money]
    invalid: list[Negative]


def _key(change: Change) -> tuple[int | None, str | None]:
    return (change.instrument_id, None if change.instrument_id is not None else change.currency)


def first_negatives(changes: Iterable[Change]) -> list[Negative]:
    """Replay changes by date and report, per balance, the first date it ends negative.

    Balances are taken at the *end of each day* (the order within a day is not known), so a buy and
    a sell on one date net out. Pure; a balance that goes negative and recovers is still reported.
    """
    ordered = sorted(changes, key=lambda c: c.occurred_on)
    running: dict[tuple[int | None, str | None], Decimal] = {}
    found: dict[tuple[int | None, str | None], Negative] = {}
    index = 0
    while index < len(ordered):
        day = ordered[index].occurred_on
        touched: set[tuple[int | None, str | None]] = set()
        while index < len(ordered) and ordered[index].occurred_on == day:
            change = ordered[index]
            key = _key(change)
            running[key] = running.get(key, Decimal(0)) + change.delta
            touched.add(key)
            index += 1
        for key in touched:
            if running[key] < 0 and key not in found:
                found[key] = Negative(key[0], key[1], day, running[key])
    return sorted(found.values(), key=lambda n: (n.on, n.instrument_id or 0, n.currency or ""))


def derive(changes: Iterable[Change], on: date | None = None) -> HoldingsAsAt:
    """Units per Instrument and cash per currency on ``on`` (or ever) from dated changes. A balance
    that went negative by then is reported as invalid, never returned as a value. Pure."""
    kept = [c for c in changes if on is None or c.occurred_on <= on]
    invalid = first_negatives(kept)
    bad = {(n.instrument_id, n.currency) for n in invalid}
    units: dict[int, Decimal] = {}
    minor: dict[str, int] = {}
    for change in kept:
        if change.instrument_id is not None:
            if (change.instrument_id, None) in bad:
                continue
            units[change.instrument_id] = units.get(change.instrument_id, Decimal(0)) + change.delta
        elif change.currency is not None and (None, change.currency) not in bad:
            minor[change.currency] = minor.get(change.currency, 0) + int(change.delta)
    return HoldingsAsAt(
        on=on,
        units={i: q for i, q in units.items() if q > 0},
        cash={c: Money(m, c) for c, m in minor.items()},
        invalid=invalid,
    )


def changes_of(rows: Iterable[Transaction]) -> list[Change]:
    """The balance changes of Transactions: trade ``quantity`` and cash ``amount``. A trade ``fee``
    is informational and never a change."""
    out: list[Change] = []
    for row in rows:
        if row.stream == "trade" and row.instrument_id is not None and row.quantity is not None:
            out.append(Change(row.occurred_on, row.instrument_id, None, row.quantity))
        elif row.stream == "cash" and row.amount_minor is not None and row.amount_ccy:
            out.append(Change(row.occurred_on, None, row.amount_ccy, Decimal(row.amount_minor)))
    return out


def holdings_as_at(session: Session, account_id: int, on: date | None = None) -> HoldingsAsAt:
    """The Account's Holdings and cash on ``on`` (or ever), derived from current Transactions."""
    return derive(changes_of(current(session, account_id)), on)
