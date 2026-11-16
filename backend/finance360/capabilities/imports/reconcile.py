"""Cross-Stream reconciliation: does each trade have its cash? (ADR-0013 section 5).

A trade settles in the cash Stream. For each trade the *value* (``abs(quantity) * unit price``,
rounded once to minor units) should appear as a ``trade_settlement`` row on the same date, out for a
buy and in for a sell, and a non-zero brokerage ``fee`` should appear as a separate ``fee`` row on
that date. Matching is by date and amount, and **each cash row is matched at most once**, so two
trades with the same fee on one day need two fee rows, and a fee is never counted twice. The trade's
own ``fee`` is informational: it never moves cash and is never added to a settlement.

A trade with no match is a warning, not an error: the other Stream may not be imported yet.

Pure: no I/O, no ORM.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finance360.core.money import Money
from finance360.core.transaction import trade_value


@dataclass(frozen=True)
class TradeLeg:
    """A trade Stream row, as reconciliation needs it."""

    ref: int
    occurred_on: date
    kind: str
    quantity: Decimal
    unit_price: Decimal
    unit_price_ccy: str
    fee: Money | None
    #: False for a trade with no cash settlement (a distribution reinvestment): neither a
    #: settlement nor a fee is expected for it.
    settles: bool = True


@dataclass(frozen=True)
class CashLeg:
    """A cash Stream row, as reconciliation needs it."""

    occurred_on: date
    kind: str
    amount: Money


@dataclass(frozen=True)
class Unmatched:
    """A trade missing part of its cash: the settlement, the fee, or both."""

    ref: int
    #: The cash row expected but not found (signed, as it would appear in the cash Stream).
    settlement: Money | None
    fee: Money | None


def expected_settlement(trade: TradeLeg) -> Money | None:
    """The cash a trade should settle for: out for a buy, in for a sell. ``None`` if there is none
    to expect (an opening balance, or a zero value)."""
    if trade.kind not in ("buy", "sell") or not trade.settles:
        return None
    value = trade_value(trade.quantity, trade.unit_price, trade.unit_price_ccy)
    if value.amount_minor == 0:
        return None
    return -value if trade.kind == "buy" else value


def reconcile(trades: Sequence[TradeLeg], cash: Sequence[CashLeg]) -> list[Unmatched]:
    """Trades with no matching cash, in date then ref order. Settlements are matched before fees,
    and each cash row is used once."""
    pool: Counter[tuple[date, str, int, str]] = Counter(
        (c.occurred_on, c.kind, c.amount.amount_minor, c.amount.currency)
        for c in cash
        if c.kind in ("trade_settlement", "fee")
    )

    def take(on: date, kind: str, amount: Money) -> bool:
        key = (on, kind, amount.amount_minor, amount.currency)
        if pool[key] > 0:
            pool[key] -= 1
            return True
        return False

    ordered = sorted(trades, key=lambda t: (t.occurred_on, t.ref))
    missing_settlement: dict[int, Money] = {}
    for trade in ordered:
        expected = expected_settlement(trade)
        if expected is not None and not take(trade.occurred_on, "trade_settlement", expected):
            missing_settlement[trade.ref] = expected
    missing_fee: dict[int, Money] = {}
    for trade in ordered:
        if not trade.settles or trade.kind not in ("buy", "sell"):
            continue
        if trade.fee is None or trade.fee.amount_minor == 0:
            continue
        if not take(trade.occurred_on, "fee", -trade.fee):
            missing_fee[trade.ref] = -trade.fee
    return [
        Unmatched(t.ref, missing_settlement.get(t.ref), missing_fee.get(t.ref))
        for t in ordered
        if t.ref in missing_settlement or t.ref in missing_fee
    ]
