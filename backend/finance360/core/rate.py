"""Rates as exact integers (ADR-0015 section 4): parts per million of 1, never a float.

A ``Rate`` of ``65_000`` is 6.5%. It is built from a percent decimal string (or a ``Decimal``) with
at most 4 decimal places of percent: finer precision is rejected, never rounded, as in
``money.parse_amount``. A float raises. Arithmetic with a rate is integer-only: the growth factor
``(1_000_000 + ppm) / 1_000_000`` is applied through ``money.scale``, the one place that rounds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from finance360.core.money import decimal_parts, opaque

#: One whole (100%) in parts per million.
PPM = 1_000_000
#: Percent decimal places a rate may carry: 4 places of percent is exactly 1 ppm.
PERCENT_PLACES = 4

_PERCENT = re.compile(r"-?[0-9]+(\.[0-9]+)?")  # ASCII digits only


class RateError(ValueError):
    """An invalid rate value."""


@dataclass(frozen=True, slots=True)
class Rate:
    """An immutable rate: an integer count of parts per million, above -100% and at most +100%."""

    ppm: int

    def __post_init__(self) -> None:
        value = opaque(self.ppm)
        if isinstance(value, float):
            raise TypeError(f"A rate must be an integer count of ppm, not a float: {value!r}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"A rate must be an integer count of ppm, got {type(value).__name__}")
        if not -PPM < self.ppm <= PPM:
            raise RateError("A rate must be above -100% and at most 100%.")

    @classmethod
    def zero(cls) -> Rate:
        return cls(0)

    @classmethod
    def from_percent(cls, value: str | Decimal) -> Rate:
        """``"6.5"`` or ``Decimal("6.5")`` (percent) to ``Rate(65_000)``. Floats raise."""
        raw = opaque(value)
        if isinstance(raw, float):
            raise TypeError("A rate must be a percent string or Decimal, not a float")
        if isinstance(raw, str):
            clean = raw.strip().replace(",", "")
            if not _PERCENT.fullmatch(clean):
                raise RateError(f"Not a percentage: {value!r}")
            parsed = Decimal(clean)
        elif isinstance(raw, Decimal):
            parsed = raw
        else:
            raise TypeError(f"A rate must be a percent string or Decimal, got {type(raw).__name__}")
        if not parsed.is_finite():
            raise RateError(f"Not a percentage: {value!r}")
        negative, coefficient, exp = decimal_parts(parsed)
        shift = exp + PERCENT_PLACES
        if shift >= 0:
            ppm = coefficient * 10**shift
        else:
            ppm, remainder = divmod(coefficient, 10**-shift)
            if remainder:
                raise RateError(
                    f"Rates have at most {PERCENT_PLACES} decimal places of a percent: {value!r}"
                )
        return cls(-ppm if negative else ppm)

    def to_percent(self) -> str:
        """The canonical percent string: no trailing zeros (``"6.5"``, ``"-2.25"``, ``"0"``)."""
        sign = "-" if self.ppm < 0 else ""
        whole, frac = divmod(abs(self.ppm), 10**PERCENT_PLACES)
        text = f"{frac:0{PERCENT_PLACES}d}".rstrip("0")
        return f"{sign}{whole}.{text}" if text else f"{sign}{whole}"
