"""Derived Valuation points and the Account's derived-value view (ADR-0013 sections 3 and 4).

An Account whose type's Behaviour has a derive hook (``holdings_based``) is valued on a date by
it, from its Transactions, Prices and FX rates. Nothing derived is stored: the Position service
adds a ``ValuationPoint(source='derived')`` for the date it is evaluating, and the unchanged
``best_on_or_before`` picks between it and the recorded Valuations (the latest ``as_at`` wins; at
the same date ``statement > manual > derived``). An Account with a simple balance Behaviour, or a
holdings-based one with nothing imported, receives no derived point and is valued exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.position import accounts, valuations
from finance360.capabilities.position.calculation import AccountFacts
from finance360.capabilities.reference import fx_rates, prices
from finance360.core import transaction as core_transaction
from finance360.core.derived import (
    DEFAULT_STALE_DAYS,
    DerivedLine,
    DerivedValue,
    DeriveHook,
    DeriveInputs,
    NoDerivedValue,
    stream_issue,
)
from finance360.core.models.account import Account
from finance360.core.models.instrument import Instrument
from finance360.core.money import Money
from finance360.core.transaction import STREAMS
from finance360.core.valuation import NoValuation, ValuationPoint, best_on_or_before

#: Account id -> (the Behaviour's derive hook, the facts it derives from).
Derivers = Mapping[int, tuple[DeriveHook, DeriveInputs]]

#: Behaviour key -> derive hook. Filled at startup by the composition root (``main.py``), which is
#: the one place that may import ``behaviours/``: capabilities never do (import-linter).
_HOOKS: dict[str, DeriveHook] = {}


def register_hook(behaviour_key: str, hook: DeriveHook) -> None:
    """Register a Behaviour's derive hook. Registering the same key again replaces it."""
    _HOOKS[behaviour_key] = hook


def registered_hooks() -> dict[str, DeriveHook]:
    return dict(_HOOKS)


def _inputs(session: Session, account: Account, stale_days: int) -> DeriveInputs:
    changes = core_transaction.changes_of(core_transaction.current(session, account.id))
    ids = sorted({c.instrument_id for c in changes if c.instrument_id is not None})
    instruments = (
        list(session.scalars(select(Instrument).where(Instrument.id.in_(ids)))) if ids else []
    )
    return DeriveInputs(
        account_currency=account.currency,
        changes=changes,
        instrument_currencies={i.id: i.currency for i in instruments},
        instrument_names={i.id: i.name for i in instruments},
        prices=[prices.point(p) for i in ids for p in prices.current_prices(session, i)],
        fx=[fx_rates.point(r) for r in fx_rates.current_fx_rates(session)],
        coverage=core_transaction.stream_coverage(session, account.id),
        stale_days=stale_days,
    )


def derivers(
    session: Session, stale_days: int = DEFAULT_STALE_DAYS, only: int | None = None
) -> Derivers:
    """The Accounts that are valued by a derive hook, each with its inputs loaded once."""
    found: dict[int, tuple[DeriveHook, DeriveInputs]] = {}
    for account in accounts.list_accounts(session, include_closed=True):
        if only is not None and account.id != only:
            continue
        hook = _HOOKS.get(account.account_type.behaviour_key)
        if hook is not None:
            found[account.id] = (hook, _inputs(session, account, stale_days))
    return found


def with_derived(
    facts: Sequence[AccountFacts], found: Derivers, on: date
) -> tuple[AccountFacts, ...]:
    """The Accounts with a ``derived`` point on ``on`` added where the Behaviour can derive one.
    No derived value (nothing imported, a missing Price...) adds nothing: never a partial total."""
    if not found:
        return tuple(facts)
    out: list[AccountFacts] = []
    for account in facts:
        entry = found.get(account.id)
        if entry is not None:
            hook, inputs = entry
            result = hook(inputs, on)
            if isinstance(result, DerivedValue):
                point = ValuationPoint(0, on, "derived", result.total)
                account = replace(account, valuations=(*account.valuations, point))
        out.append(account)
    return tuple(out)


# --- the Account's derived-value view ------------------------------------------------------


@dataclass(frozen=True)
class StreamStatus:
    stream: str
    coverage_to: date | None
    status: Literal["ok", "missing", "stale"]
    message: str | None


@dataclass(frozen=True)
class ViewLine:
    line: DerivedLine
    #: The Instrument's name for a holding line.
    name: str | None


@dataclass(frozen=True)
class DerivedView:
    #: False when the Account's Behaviour does not derive (the page then shows nothing).
    applies: bool
    as_at: date
    result: DerivedValue | NoDerivedValue | None
    lines: tuple[ViewLine, ...]
    streams: tuple[StreamStatus, ...]
    #: The best recorded (non-derived) Valuation on or before ``as_at``.
    observed: ValuationPoint | None
    #: Observed minus derived, in the Account currency; set only when both exist.
    difference: Money | None
    stale_days: int


def default_as_at(coverage: Mapping[str, date], today: date) -> date:
    """Today, or the latest date both Streams' Coverage reaches when that is earlier: the newest
    date a value can exist for. With a Stream missing it is today (and the page says why)."""
    if all(s in coverage for s in STREAMS):
        return min(today, *(coverage[s] for s in STREAMS))
    return today


def account_view(
    session: Session,
    account_id: int,
    today: date,
    as_at: date | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> DerivedView:
    accounts.get_account(session, account_id)  # NotFoundError for an unknown Account
    found = derivers(session, stale_days, only=account_id)
    if account_id not in found:
        return DerivedView(False, as_at or today, None, (), (), None, None, stale_days)
    hook, inputs = found[account_id]
    on = as_at or default_as_at(inputs.coverage, today)
    result = hook(inputs, on)
    streams: list[StreamStatus] = []
    for stream in STREAMS:
        issue = stream_issue(stream, inputs.coverage, on)
        reached = inputs.coverage.get(stream)
        status: Literal["ok", "missing", "stale"] = (
            "ok" if issue is None else "missing" if reached is None else "stale"
        )
        streams.append(StreamStatus(stream, reached, status, issue))
    recorded = [
        p for p in map(valuations.point, valuations.current_valuations(session, account_id))
    ]
    best = best_on_or_before(recorded, on)
    observed = None if isinstance(best, NoValuation) else best
    names = inputs.instrument_names
    lines: tuple[ViewLine, ...] = ()
    difference = None
    if isinstance(result, DerivedValue):
        lines = tuple(
            ViewLine(line, names.get(line.instrument_id) if line.instrument_id else None)
            for line in result.lines
        )
        if observed is not None and observed.amount.currency == result.total.currency:
            difference = observed.amount - result.total
    return DerivedView(True, on, result, lines, tuple(streams), observed, difference, stale_days)
