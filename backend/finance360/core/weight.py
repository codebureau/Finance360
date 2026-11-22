"""Exposure weights as exact integers (ADR-0030 section 3): parts per million of 1, never a float.

A ``Weight`` of ``600_000`` is 60%. It is built from a percent decimal string (or a ``Decimal``)
with at most 4 decimal places of percent, as a ``Rate`` is: finer precision is rejected, never
rounded, and a float raises. A weight is greater than 0 and at most 100%: a geared or short
position is entered net, so there are no negative weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from finance360.core.money import opaque
from finance360.core.rate import PPM, Rate, RateError


class WeightError(ValueError):
    """An invalid weight value."""


@dataclass(frozen=True, slots=True)
class Weight:
    """An immutable weight: an integer count of parts per million, above 0 and at most 100%."""

    ppm: int

    def __post_init__(self) -> None:
        value = opaque(self.ppm)
        if isinstance(value, float):
            raise TypeError(f"A weight must be an integer count of ppm, not a float: {value!r}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"A weight must be an integer count of ppm, got {type(value).__name__}")
        if not 0 < self.ppm <= PPM:
            raise WeightError("A weight must be above 0% and at most 100%.")

    @classmethod
    def from_percent(cls, value: str | Decimal) -> Weight:
        """``"60"`` or ``Decimal("60")`` (percent) to ``Weight(600_000)``. Floats raise."""
        try:
            return cls(Rate.from_percent(value).ppm)
        except RateError as problem:
            raise WeightError(
                str(problem).replace("rate", "weight").replace("Rate", "Weight")
            ) from None

    def to_percent(self) -> str:
        """The canonical percent string: no trailing zeros (``"60"``, ``"0.0001"``, ``"100"``)."""
        return Rate(self.ppm).to_percent()
