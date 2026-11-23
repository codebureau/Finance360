"""Exposure vocabulary constants and the weight-sum tolerance rules (ADR-0030 sections 2 and 3).

Pure: no database, so the Reference write path and the look-through resolver (which may not reach
``core.models``) share one rule. Weights are stored exactly as entered; these rules judge a total
and say how a within-tolerance total resolves. Nothing is normalised.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from finance360.core.rate import PPM

#: One percentage point, in ppm. A Dimension's weights may total 100% plus or minus this much:
#: published figures round. Make it a Household setting only if real use shows the need.
WEIGHT_TOLERANCE_PPM = 10_000

#: The seeded Dimensions, and every Dimension's residual label (the manager's own "other").
ASSET_CLASS = "asset_class"
REGION = "region"
SECTOR = "sector"
EXPOSURE_CURRENCY = "exposure_currency"
OTHER = "other"
CASH = "cash"


def percent_string(ppm: int) -> str:
    """Any ppm count as a canonical percent string (``"99.5"``, ``"-0.5"``, ``"0"``): integers
    only, so a total over 100% (which a ``Rate`` cannot hold) formats too."""
    whole, frac = divmod(abs(ppm), 10_000)
    text = f"{frac:04d}".rstrip("0")
    sign = "-" if ppm < 0 else ""
    return f"{sign}{whole}.{text}" if text else f"{sign}{whole}"


class OutsideTolerance(ValueError):
    """A Dimension's weights total too far from 100%. ``difference_ppm`` is total minus 100%."""

    def __init__(self, total_ppm: int) -> None:
        super().__init__(f"Weights total {total_ppm} ppm")
        self.total_ppm = total_ppm
        self.difference_ppm = total_ppm - PPM


@dataclass(frozen=True)
class Effective:
    """A Dimension's weights as they resolve: with ``not_classified_ppm``, exactly 100%."""

    weights: tuple[tuple[str, int], ...]
    #: A within-tolerance shortfall, or all of an omitted Dimension.
    not_classified_ppm: int
    #: Label and ppm trimmed from the largest weight(s) of a within-tolerance excess.
    trimmed: tuple[tuple[str, int], ...]


def effective_weights(
    weights: Sequence[tuple[str, int]], order: Mapping[str, int] | Sequence[str]
) -> Effective:
    """Resolve one Dimension's ``(label_key, ppm)`` weights.

    - no weights: the Dimension is omitted, all of it is Not classified;
    - short within tolerance: the shortfall is Not classified;
    - over within tolerance: the excess is trimmed from the largest weight (ties go to the
      earliest label in ``order``, the seed order);
    - beyond tolerance either way: ``OutsideTolerance``.
    """
    rank = order if isinstance(order, Mapping) else {k: i for i, k in enumerate(order)}
    if not weights:
        return Effective((), PPM, ())
    total = sum(w for _, w in weights)
    difference = total - PPM
    if abs(difference) > WEIGHT_TOLERANCE_PPM:
        raise OutsideTolerance(total)
    if difference <= 0:
        return Effective(tuple(weights), -difference, ())
    remaining = difference
    trimmed: list[tuple[str, int]] = []
    kept = dict(weights)
    for key, ppm in sorted(weights, key=lambda w: (-w[1], rank.get(w[0], len(rank)))):
        if remaining == 0:
            break
        cut = min(ppm, remaining)
        kept[key] = ppm - cut
        trimmed.append((key, cut))
        remaining -= cut
    return Effective(tuple((k, w) for k, w in kept.items() if w > 0), 0, tuple(trimmed))


@dataclass(frozen=True)
class ProfileFacts:
    """One current Exposure profile as the pure resolver needs it: immutable, no ORM."""

    profile_id: int
    instrument_id: int
    as_at: date
    #: A glide-path band (#6); ``None`` for an ordinary profile.
    age_from: int | None
    source: str
    #: ``(dimension_key, label_key, weight_ppm)``, in entry order.
    weights: tuple[tuple[str, str, int], ...]

    def for_dimension(self, dimension_key: str) -> tuple[tuple[str, int], ...]:
        return tuple((label, ppm) for dim, label, ppm in self.weights if dim == dimension_key)
