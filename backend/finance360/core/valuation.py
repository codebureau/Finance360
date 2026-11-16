"""The pure "best Valuation on or before a date" selector (ADR-0009 section 3).

Among the rows passed in (callers pass only *current*, i.e. non-superseded, rows), take those dated
on or before ``on``, then the latest ``as_at``. Source rank only breaks a same-date tie:
statement > manual > derived > estimate. With no such row the answer is an explicit
``NoValuation``, never zero. No I/O, so Position can reuse it over immutable inputs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from finance360.core.money import Money

SOURCES = ("manual", "statement", "derived", "estimate")
#: Sources a person enters. ``derived`` Valuations are calculated, never typed in.
ENTERED_SOURCES = ("manual", "statement", "estimate")
#: A provenance note is required for these (ADR-0009).
NOTE_REQUIRED_SOURCES = ENTERED_SOURCES
#: Higher wins a same-date tie.
SOURCE_RANK = {"statement": 3, "manual": 2, "derived": 1, "estimate": 0}


@dataclass(frozen=True)
class ValuationPoint:
    """An immutable view of one current Valuation: what the selector needs, and no ORM."""

    id: int
    as_at: date
    source: str
    amount: Money


@dataclass(frozen=True)
class NoValuation:
    """There is no Valuation on or before ``on``. Explicit, and deliberately not a zero amount."""

    on: date


def best_on_or_before(rows: Iterable[ValuationPoint], on: date) -> ValuationPoint | NoValuation:
    best: ValuationPoint | None = None
    for row in rows:
        if row.as_at > on:
            continue
        if best is None or (row.as_at, SOURCE_RANK[row.source]) > (
            best.as_at,
            SOURCE_RANK[best.source],
        ):
            best = row
    return best if best is not None else NoValuation(on)
