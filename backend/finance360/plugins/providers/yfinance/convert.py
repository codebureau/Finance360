"""The float to Decimal boundary for Yahoo Finance closes (ADR-0013 section 6; AGENTS.md rule 3).

yfinance hands back ``float`` closes, and some carry binary noise from a 32-bit source
(``34.560001373291016`` for a quote of ``34.56``). Money and prices are never floats, so this is the
one place a float becomes a ``Decimal``, and it does so deliberately:

1. take the float's shortest round-trip text (``repr``), **not** its binary expansion, so
   ``34.5678`` becomes exactly ``Decimal("34.5678")``;
2. round **half-even to 4 decimal places**, the precision Yahoo quotes listed ETFs and shares at.
   That removes the 32-bit noise and is the only rounding step; the Price column keeps scale 6 for
   manual entries.

A value that is not finite, is not above zero, or rounds to zero is a gap (``None``), never a
repaired number: nothing is fabricated.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from math import isfinite

PRICE_PLACES = 4
_QUANTUM = Decimal(1).scaleb(-PRICE_PLACES)
_CONTEXT = Context(prec=60)  # wide enough that no realistic price can overflow quantize


def close_to_decimal(value: object) -> Decimal | None:
    """An exact ``Decimal`` at ``PRICE_PLACES`` decimals for a provider float, or ``None``."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]  # also numpy floats and ints
    except (TypeError, ValueError):
        return None
    if not isfinite(number) or number <= 0:
        return None
    try:
        rounded = Decimal(repr(number)).quantize(
            _QUANTUM, rounding=ROUND_HALF_EVEN, context=_CONTEXT
        )
    except InvalidOperation:
        return None
    return rounded if rounded > 0 else None
