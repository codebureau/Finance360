"""Offline sample transport: invented closes, no network (review instance and demos only).

Selected with ``FINANCE360_PROVIDER_TRANSPORT=sample``. It stands in for ``yfinance_history`` and
returns the same shape, floats included (with deliberate 32-bit noise), so the real boundary
conversion and the real fetch service run end to end. Everything here is invented.

* a symbol starting ``FAIL`` simulates the provider being down (rate limited), so the failure path
  can be shown;
* the quote currency follows the symbol's suffix: ``.AX`` AUD, ``.L`` GBP, otherwise USD;
* weekdays only: weekends are non-trading days and are simply absent.
"""

from __future__ import annotations

import struct
from datetime import date, timedelta
from math import sin

from finance360.capabilities.reference.providers import ProviderThrottled
from finance360.plugins.providers.yfinance.transport import RawHistory

_SUFFIX_CURRENCY = {".AX": "AUD", ".L": "GBP"}


def _currency(symbol: str) -> str:
    for suffix, currency in _SUFFIX_CURRENCY.items():
        if symbol.upper().endswith(suffix):
            return currency
    return "USD"


def _float32(value: float) -> float:
    """Round-trip through 32 bits: the noise some real providers send (34.56 -> 34.560001373...)."""
    return struct.unpack("f", struct.pack("f", value))[0]


def sample_history(symbol: str, start: date, end: date) -> RawHistory:
    if symbol.upper().startswith("FAIL"):
        raise ProviderThrottled(
            "The sample provider simulates a rate-limit failure for this symbol."
        )
    base = 20 + sum(map(ord, symbol)) % 80
    rows: list[tuple[date, float]] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            wobble = 1 + 0.03 * sin(day.toordinal() / 9)
            rows.append((day, _float32(base * wobble)))
        day += timedelta(days=1)
    return RawHistory(_currency(symbol), tuple(rows))
