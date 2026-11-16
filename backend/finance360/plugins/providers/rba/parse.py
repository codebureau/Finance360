"""Parsing the RBA's "F11.1 Exchange Rates, Daily" CSV, and the one inversion (ADR-0013 section 6).

The table is laid out as a few header rows (``Title``, ``Units`` and so on) followed by one row per
day: a ``dd-Mmm-yyyy`` date and one published figure per column. The ``Units`` row names each
column's currency. Each figure is **foreign units per 1 AUD** (``A$1 = 0.4902 GBP``).

ADR-0009 forbids implicit inverses, so the stored direction is foreign to AUD and the plug-in
**inverts once, here, at ingestion**: ``1 / published``, rounded **half-even to 10 decimal places**
(the FX rate scale). The published figure is kept in the note, so the stored rate can always be
traced to what the RBA printed. Pure: bytes in, exact decimals out, no network.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation

from finance360.capabilities.reference.providers import ProviderError
from finance360.core.money import CURRENCY_EXPONENTS

RATE_PLACES = 10
_QUANTUM = Decimal(1).scaleb(-RATE_PLACES)
_CONTEXT = Context(prec=60)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
LAYOUT_ERROR = "The RBA exchange rate table was not in the expected layout."


@dataclass(frozen=True)
class RbaTable:
    """Published figures: currency -> date -> foreign units per 1 AUD."""

    published: dict[str, dict[date, Decimal]]


def invert(published: Decimal) -> Decimal | None:
    """Foreign-per-AUD to AUD-per-foreign: ``1 / published``, half-even at 10 decimal places.

    ``None`` when the published figure is not above zero, or the result rounds to zero (so the
    caller drops it rather than store a rate of nothing).
    """
    if published <= 0:
        return None
    try:
        rate = _CONTEXT.divide(Decimal(1), published).quantize(
            _QUANTUM, rounding=ROUND_HALF_EVEN, context=_CONTEXT
        )
    except InvalidOperation:
        return None
    return rate if rate > 0 else None


def _parse_date(text: str) -> date | None:
    parts = text.strip().split("-")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit():
        return None
    month = _MONTHS.get(parts[1].strip().lower()[:3])
    if month is None:
        return None
    try:
        return date(int(parts[2]), month, int(parts[0]))
    except ValueError:
        return None


def parse_f11(data: bytes) -> RbaTable:
    """Read the CSV. Raises ``ProviderError`` if there is no ``Units`` row or no dated row."""
    text = data.decode("utf-8-sig", errors="replace")
    columns: dict[int, str] = {}
    published: dict[str, dict[date, Decimal]] = {}
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        head = row[0].strip()
        if head.lower() == "units" and not columns:
            for index, cell in enumerate(row[1:], 1):
                code = cell.strip().upper()
                # Only known ISO codes: the "Index" and "SDR" columns are not currencies we hold.
                if code in CURRENCY_EXPONENTS and code not in columns.values():
                    columns[index] = code
                    published[code] = {}
            continue
        as_at = _parse_date(head)
        if as_at is None or not columns:
            continue
        for index, code in columns.items():
            cell = row[index].strip() if index < len(row) else ""
            if not cell:
                continue  # no figure that day (a holiday): a gap, not a zero
            try:
                value = Decimal(cell)
            except InvalidOperation:
                continue
            if value.is_finite() and value > 0:
                published[code][as_at] = value
    if not columns or not any(published.values()):
        raise ProviderError(LAYOUT_ERROR)
    return RbaTable(published)
