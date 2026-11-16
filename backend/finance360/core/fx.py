"""The pure FX rate lookup and rate text helpers (ADR-0009 section 4).

A rate is units of ``to_ccy`` per 1 unit of ``from_ccy``, an exact ``Decimal`` held at scale 10 in
the database. Lookup is by the **exact pair** only: the current rate with the latest ``as_at`` on or
before the date. Several on that date are ordered by source precedence with ``manual`` first
(ADR-0013 section 6: a provider never beats a deliberate owner entry on the same date). Inverse
pairs and triangulation are not derived. With no such rate the answer is an explicit ``NoRate``:
never 1.0 and never zero. There is no maximum age; the rate carries its ``as_at`` so a caller can
show how stale it is. No I/O. Conversion itself is ``money.convert``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finance360.core.money import INT64_MAX, MoneyError

RATE_SCALE = 10
#: The largest rate a ``ScaledDecimal(RATE_SCALE)`` column can hold.
MAX_RATE = Decimal(INT64_MAX).scaleb(-RATE_SCALE)

#: Sources, best first. ``manual`` always wins a same-date tie. A source not listed ranks after
#: these, by name.
SOURCE_PRECEDENCE: tuple[str, ...] = ("manual", "rba")

_RATE = re.compile(r"[0-9]+(\.[0-9]+)?")  # ASCII digits only


@dataclass(frozen=True)
class FxPoint:
    """An immutable view of one current FxRate: what the lookup needs, and no ORM."""

    id: int
    from_ccy: str
    to_ccy: str
    as_at: date
    rate: Decimal
    #: Where the rate came from (``manual`` or a provider key), shown beside a converted figure.
    source: str = "manual"


@dataclass(frozen=True)
class NoRate:
    """There is no rate for the exact pair on or before ``on``. Explicit, never 1.0 or zero."""

    from_ccy: str
    to_ccy: str
    on: date


def _rank(source: str, precedence: Sequence[str]) -> tuple[int, str]:
    return (precedence.index(source), "") if source in precedence else (len(precedence), source)


def rate_on_or_before(
    rows: Iterable[FxPoint],
    from_ccy: str,
    to_ccy: str,
    on: date,
    precedence: Sequence[str] = SOURCE_PRECEDENCE,
) -> FxPoint | NoRate:
    """The latest ``as_at <= on`` for the exact pair. Callers pass only *current* rows.

    Among rates sharing that date the best-ranked source wins (``manual`` first); if the same
    source somehow appears twice, the highest id (the most recently entered) wins. The answer
    never depends on the order of ``rows``.
    """
    best: FxPoint | None = None
    for row in rows:
        if row.from_ccy != from_ccy or row.to_ccy != to_ccy or row.as_at > on:
            continue
        if best is None or _better(row, best, precedence):
            best = row
    return best if best is not None else NoRate(from_ccy, to_ccy, on)


def _better(row: FxPoint, best: FxPoint, precedence: Sequence[str]) -> bool:
    if row.as_at != best.as_at:
        return row.as_at > best.as_at
    rank, best_rank = _rank(row.source, precedence), _rank(best.source, precedence)
    if rank != best_rank:
        return rank < best_rank
    return row.id > best.id


def parse_rate(text: str) -> Decimal:
    """A plain positive decimal string such as ``"2.0143"``, exact, at most 10 decimal places.

    Extra precision is rejected, never rounded; so are zero, negatives, exponents and anything
    too large for the column.
    """
    clean = text.strip()
    if not _RATE.fullmatch(clean):
        raise MoneyError(f"Not a rate: {text!r}")
    rate = Decimal(clean)
    if -rate.as_tuple().exponent > RATE_SCALE:  # type: ignore[operator]
        raise MoneyError(f"A rate has at most {RATE_SCALE} decimal places: {text!r}")
    if rate <= 0:
        raise MoneyError(f"A rate must be greater than zero: {text!r}")
    if rate > MAX_RATE:
        raise MoneyError(f"That rate is too large: {text!r}")
    return rate


def format_rate(rate: Decimal) -> str:
    """The rate as a plain decimal string without trailing zeros but at least 2 places."""
    text = format(rate.normalize(), "f")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"
