"""Offline sample transport: an invented table in the RBA's F11.1 layout (review instance and demos
only; no network).

Selected with ``FINANCE360_PROVIDER_TRANSPORT=sample``. It stands in for ``fetch_f11`` and returns
CSV bytes, so the real parser, inversion and fetch service run end to end. Every figure is invented.
Weekdays only. Includes a non-currency ``Index`` column and a JPY column to show they are handled.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from math import sin

_FIRST_DAY = date(2025, 1, 1)
#: currency -> (base foreign units per 1 AUD, decimal places published)
_CURRENCIES = {"USD": (0.65, 4), "GBP": (0.49, 4), "EUR": (0.58, 4), "JPY": (97.0, 2)}


def sample_f11(today: date | None = None) -> bytes:
    last = today or datetime.now(UTC).date()
    codes = list(_CURRENCIES)
    lines = [
        "F11.1  EXCHANGE RATES (SAMPLE - INVENTED DATA)",
        "Title," + ",".join([*(f"A$1={c}" for c in codes), "Trade-weighted Index"]),
        "Units," + ",".join([*codes, "Index"]),
        "",
        "Source," + ",".join(["Sample"] * (len(codes) + 1)),
        "Series ID," + ",".join([*(f"SAMPLE{c}" for c in codes), "SAMPLETWI"]),
    ]
    day = _FIRST_DAY
    while day <= last:
        if day.weekday() < 5:
            cells: list[str] = []
            for base, places in _CURRENCIES.values():
                cells.append(f"{base * (1 + 0.02 * sin(day.toordinal() / 11)):.{places}f}")
            cells.append("61.40")
            lines.append(f"{day.day:02d}-{day.strftime('%b')}-{day.year}," + ",".join(cells))
        day += timedelta(days=1)
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
