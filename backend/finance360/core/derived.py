"""The shapes of a derived value (ADR-0013 section 3): pure values, no I/O.

A Behaviour's derive hook (``behaviours/``) turns an Account's recorded facts (``DeriveInputs``)
into a ``DerivedValue`` or an explicit ``NoDerivedValue``. The shapes live in ``core`` so the
Position Capability can build the inputs and read the result without importing `behaviours/`.
The hooks are registered into the Position service at startup from the composition root, as
Importers are (ADR-0013 section 5).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from finance360.core.fx import FxPoint
from finance360.core.money import Money
from finance360.core.price import PricePoint
from finance360.core.transaction import STREAMS, Change

#: A Price older than this many days is flagged stale unless the instance says otherwise.
DEFAULT_STALE_DAYS = 7


@dataclass(frozen=True)
class DeriveInputs:
    """Everything a derive hook may use, as immutable values (no ORM, no I/O)."""

    account_currency: str
    #: Dated balance changes from the Account's current Transactions.
    changes: Sequence[Change]
    #: Instrument id -> the currency its Prices are in.
    instrument_currencies: Mapping[int, str]
    #: Instrument id -> name, for messages only.
    instrument_names: Mapping[int, str]
    prices: Sequence[PricePoint]
    fx: Sequence[FxPoint]
    #: Stream -> the latest ``coverage_to`` of a committed Import. An absent Stream has none.
    coverage: Mapping[str, date]
    #: A Price older than this many days is flagged stale (shown, not refused).
    stale_days: int = DEFAULT_STALE_DAYS


@dataclass(frozen=True)
class DerivedLine:
    """One term of a derived value, in the Account's currency. Lines sum exactly to the total."""

    kind: str  # "holding" | "cash"
    instrument_id: int | None
    #: Holding: its Price currency. Cash: the cash currency.
    currency: str
    #: Holdings only.
    units: Decimal | None
    price: PricePoint | None
    #: ``None`` when no conversion was needed.
    fx: FxPoint | None
    value: Money
    #: The Price is older than the staleness limit.
    stale: bool = False


@dataclass(frozen=True)
class DerivedValue:
    on: date
    total: Money
    lines: tuple[DerivedLine, ...]

    @property
    def stale(self) -> bool:
        return any(line.stale for line in self.lines)


@dataclass(frozen=True)
class StreamIssue:
    """One Stream that cannot support a value: ``missing`` (no committed Import) or ``stale`` (its
    Coverage ends before the date). ``message`` is the prose built from these fields."""

    stream: str
    kind: Literal["missing", "stale"]
    message: str


@dataclass(frozen=True)
class NoDerivedValue:
    """There is no derived value on ``on``, and why. Never a partial total."""

    on: date
    reason: str
    #: The Stream problems behind ``reason``, structured and ordered (``stream_issues``); empty
    #: when the reason is not about a Stream.
    issues: tuple[StreamIssue, ...] = ()


DeriveHook = Callable[[DeriveInputs, date], DerivedValue | NoDerivedValue]


def _issue(stream: str, coverage: Mapping[str, date], on: date) -> StreamIssue | None:
    reached = coverage.get(stream)
    if reached is None:
        return StreamIssue(stream, "missing", f"The {stream} Stream has no committed Import.")
    if reached < on:
        return StreamIssue(
            stream,
            "stale",
            f"The {stream} Stream's Coverage ends {reached.isoformat()}, "
            f"before {on.isoformat()}: it is stale.",
        )
    return None


def stream_issues(coverage: Mapping[str, date], on: date) -> tuple[StreamIssue, ...]:
    """Every Stream that cannot support a value on ``on``: ``missing`` before ``stale`` (nothing
    imported is the first thing to fix), each group in Stream order."""
    found = [i for s in STREAMS if (i := _issue(s, coverage, on))]
    return tuple(sorted(found, key=lambda i: i.kind != "missing"))


def stream_issue(stream: str, coverage: Mapping[str, date], on: date) -> str | None:
    """Why a Stream cannot support a value on ``on``, or ``None`` when its Coverage reaches it."""
    issue = _issue(stream, coverage, on)
    return issue.message if issue else None
