"""The pure Exposure resolver (ADR-0030 sections 3 and 5): one amount, one Dimension, a split.

No database, audit, service or other Capability (import-linter): ``profiles`` is a plain mapping,
preloaded by the service. Every split uses ``core.money.allocate`` (largest remainder), so the
lines sum exactly to the amount and none is negative. #83 resolves direct weights only;
references (#85) and glide-path ages (#6) extend this.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from finance360.core.exposure import (
    WEIGHT_TOLERANCE_PPM as WEIGHT_TOLERANCE_PPM,
)
from finance360.core.exposure import (
    OutsideTolerance,
    ProfileFacts,
    effective_weights,
)
from finance360.core.money import Money, allocate

LineKind = Literal["label", "not_classified", "no_profile"]


@dataclass(frozen=True)
class Selected:
    profile: ProfileFacts
    #: There was no profile on or before the date: this is the earliest later one.
    dated_after: bool


def select_profile(profiles: Sequence[ProfileFacts], on: date) -> Selected | None:
    """The latest profile whose ``as_at`` is on or before ``on``; with none, the earliest later
    one, marked ``dated_after`` (a fund's mix changes slowly), rather than No profile. A glide-path
    band (``age_from``) is not resolved here (#6)."""
    plain = [p for p in profiles if p.age_from is None]
    before = [p for p in plain if p.as_at <= on]
    if before:
        return Selected(max(before, key=lambda p: (p.as_at, p.profile_id)), False)
    after = [p for p in plain if p.as_at > on]
    if after:
        return Selected(min(after, key=lambda p: (p.as_at, p.profile_id)), True)
    return None


@dataclass(frozen=True)
class ExposureLine:
    kind: LineKind
    #: The label key for a ``label`` line.
    label_key: str | None
    amount: Money
    #: The Instruments this came through (one, until fund-of-funds, #85).
    path: tuple[int, ...]


@dataclass(frozen=True)
class Resolution:
    lines: tuple[ExposureLine, ...]
    #: The profile used, its date, and whether it is dated after the look-through date.
    profile_id: int | None
    as_at: date | None
    dated_after: bool
    #: A within-tolerance shortfall (ppm), already in a Not classified line.
    shortfall_ppm: int
    #: ``(label_key, ppm)`` trimmed from the largest weight when the profile totals over 100%.
    trimmed: tuple[tuple[str, int], ...]
    #: The profile exists but gives this Dimension no weights (a bond fund with no sectors): all
    #: of the amount is Not classified.
    omitted: bool = False


def resolve(
    amount: Money,
    instrument_id: int,
    dimension: str,
    on: date,
    profiles: Mapping[int, Sequence[ProfileFacts]],
    order: Sequence[str],
) -> Resolution:
    """Split ``amount`` of one Instrument across a Dimension's labels.

    No profile on the date: one No profile line. Otherwise the weights resolve through
    ``effective_weights`` (a within-tolerance shortfall and any weightless remainder go to Not
    classified; an excess is trimmed from the largest weight) and ``amount`` is allocated across
    them. A Dimension the profile omits is all Not classified.
    """
    path = (instrument_id,)
    selected = select_profile(profiles.get(instrument_id, ()), on)
    if selected is None:
        line = ExposureLine("no_profile", None, amount, path)
        return Resolution((line,), None, None, False, 0, ())
    try:
        effective = effective_weights(selected.profile.for_dimension(dimension), order)
    except OutsideTolerance:  # cannot happen for a profile the write path accepted
        line = ExposureLine("not_classified", None, amount, path)
        return Resolution(
            (line,), selected.profile.profile_id, selected.profile.as_at, False, 0, (), True
        )
    keys = [key for key, _ in effective.weights]
    weights = {i: ppm for i, (_, ppm) in enumerate(effective.weights)}
    if effective.not_classified_ppm:
        weights[len(keys)] = effective.not_classified_ppm
    split = allocate(amount, weights)
    lines = [ExposureLine("label", key, split[i], path) for i, key in enumerate(keys)]
    if effective.not_classified_ppm:
        lines.append(ExposureLine("not_classified", None, split[len(keys)], path))
    shortfall = effective.not_classified_ppm if effective.weights else 0
    return Resolution(
        tuple(line for line in lines if line.amount.amount_minor),
        selected.profile.profile_id,
        selected.profile.as_at,
        selected.dated_after,
        shortfall,
        effective.trimmed,
        not effective.weights,
    )
