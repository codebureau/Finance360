"""The staged diff: a multiset comparison on canonical fields (ADR-0013 section 5).

No row has an identity across exports, so a staged row and a current Transaction are *the same*
when every canonical field is equal (date, kind, Instrument, quantity, unit price and currency, fee,
amount). Identical same-day rows are legitimate, so the comparison counts: two equal rows in the
file pair with two equal current rows, and a third is added or removed. "Changed" is not a state:
it is one removed row plus one added row.

Pure: no I/O, no ORM. Callers pass ids and keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import NamedTuple


class RowKey(NamedTuple):
    """Every canonical field of a record. ``Decimal`` equality ignores trailing zeros, so the
    ``20`` in a file and the ``20.000000`` read back from the database are the same quantity."""

    occurred_on: date
    kind: str
    instrument_id: int | None
    quantity: Decimal | None
    unit_price: Decimal | None
    unit_price_ccy: str | None
    fee: tuple[int, str] | None
    amount: tuple[int, str] | None


@dataclass(frozen=True)
class Diff:
    #: Ids of staged rows with no counterpart among the current rows, in the order given.
    added: tuple[int, ...]
    #: Ids of current rows the staged file no longer has, in the order given.
    removed: tuple[int, ...]
    #: (staged id, current id) pairs.
    unchanged: tuple[tuple[int, int], ...]


def multiset_diff(
    staged: Sequence[tuple[int, RowKey]], current: Sequence[tuple[int, RowKey]]
) -> Diff:
    """Pair equal keys one for one; whatever is left over on either side is added or removed."""
    waiting: dict[RowKey, list[int]] = {}
    for current_id, key in current:
        waiting.setdefault(key, []).append(current_id)
    added: list[int] = []
    unchanged: list[tuple[int, int]] = []
    for staged_id, key in staged:
        candidates = waiting.get(key)
        if candidates:
            unchanged.append((staged_id, candidates.pop(0)))
        else:
            added.append(staged_id)
    paired = {current_id for _, current_id in unchanged}
    removed = [current_id for current_id, _ in current if current_id not in paired]
    return Diff(tuple(added), tuple(removed), tuple(unchanged))
