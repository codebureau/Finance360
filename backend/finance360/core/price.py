"""The pure Price lookup and price text helpers (ADR-0013 section 2). No I/O.

A Price is the end-of-day value of one unit of an Instrument, an exact ``Decimal`` held at scale 6
in the database, in the Instrument's currency. The lookup mirrors FX (``core.fx``): the current
Price with the latest ``as_at`` on or before the date; several on that date are ordered by source
precedence with ``manual`` first. With no such Price the answer is an explicit ``NoPrice``: never
zero, never a Price from after the date. It carries its ``as_at`` so staleness can be shown. Nothing
is filled forward in storage.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finance360.core.money import INT64_MAX, MoneyError

PRICE_SCALE = 6
#: The largest Price a ``ScaledDecimal(PRICE_SCALE)`` column can hold.
MAX_PRICE = Decimal(INT64_MAX).scaleb(-PRICE_SCALE)

#: Sources, best first. ``manual`` always wins a same-date tie (a deliberate owner entry beats a
#: provider's). A source not listed (a future provider) ranks after these, by name, but still above
#: ``IMPORT_SOURCE``: an Import's unit price is a dealing price that only fills gaps.
SOURCE_PRECEDENCE: tuple[str, ...] = ("manual", "yfinance")
#: Unit prices read from a committed Import. Always last in a same-date tie.
IMPORT_SOURCE = "import"

_PRICE = re.compile(r"[0-9]+(\.[0-9]+)?")  # ASCII digits only


@dataclass(frozen=True)
class PricePoint:
    """An immutable view of one current Price: what the lookup needs, and no ORM."""

    id: int
    instrument_id: int
    as_at: date
    value: Decimal
    source: str


@dataclass(frozen=True)
class NoPrice:
    """The Instrument has no Price on or before ``on``. Explicit, never zero."""

    instrument_id: int
    on: date


def _rank(source: str, precedence: Sequence[str]) -> tuple[int, str]:
    if source in precedence:
        return (precedence.index(source), "")
    if source == IMPORT_SOURCE:
        return (len(precedence) + 1, "")
    return (len(precedence), source)


def price_on_or_before(
    rows: Iterable[PricePoint],
    instrument_id: int,
    on: date,
    precedence: Sequence[str] = SOURCE_PRECEDENCE,
) -> PricePoint | NoPrice:
    """The latest ``as_at <= on`` for the Instrument. Callers pass only *current* rows.

    Among Prices sharing that date the best-ranked source wins (``manual`` first); if the same
    source somehow appears twice, the highest id (most recently entered) wins. The answer never
    depends on the order of ``rows``.
    """
    best: PricePoint | None = None
    for row in rows:
        if row.instrument_id != instrument_id or row.as_at > on:
            continue
        if best is None or _better(row, best, precedence):
            best = row
    return best if best is not None else NoPrice(instrument_id, on)


def _better(row: PricePoint, best: PricePoint, precedence: Sequence[str]) -> bool:
    if row.as_at != best.as_at:
        return row.as_at > best.as_at
    rank, best_rank = _rank(row.source, precedence), _rank(best.source, precedence)
    if rank != best_rank:
        return rank < best_rank
    return row.id > best.id


def parse_price(text: str) -> Decimal:
    """A plain positive decimal string such as ``"34.5678"``, exact, at most 6 decimal places.

    Extra precision is rejected, never rounded; so are zero, negatives, exponents and anything
    too large for the column.
    """
    clean = text.strip()
    if not _PRICE.fullmatch(clean):
        raise MoneyError(f"Not a price: {text!r}")
    price = Decimal(clean)
    if -price.as_tuple().exponent > PRICE_SCALE:  # type: ignore[operator]
        raise MoneyError(f"A price has at most {PRICE_SCALE} decimal places: {text!r}")
    if price <= 0:
        raise MoneyError(f"A price must be greater than zero: {text!r}")
    if price > MAX_PRICE:
        raise MoneyError(f"That price is too large: {text!r}")
    return price


def format_price(price: Decimal) -> str:
    """The price as a plain decimal string without trailing zeros but at least 2 places."""
    text = format(price.normalize(), "f")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"
