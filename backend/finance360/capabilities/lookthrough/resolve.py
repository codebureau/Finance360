"""The pure Exposure resolver (ADR-0030 sections 3 and 5): one amount, one Dimension, a split.

No database, audit, service or other Capability (import-linter): ``profiles`` is a plain mapping,
preloaded by the service. Every split uses ``core.money.allocate`` (largest remainder), so the
lines sum exactly to the amount and none is negative. A profile's fund-of-funds references are
followed recursively: at each level the amount is split across the references and the direct
share, then the direct share across its labels, so every level sums exactly. A cycle met at read,
a chain deeper than ``MAX_DEPTH`` and an underlying Instrument without a profile each become a No
profile line, so the total is never broken. Glide-path ages (#6) extend this.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from finance360.core.exposure import (
    MAX_DEPTH as MAX_DEPTH,
)
from finance360.core.exposure import (
    WEIGHT_TOLERANCE_PPM as WEIGHT_TOLERANCE_PPM,
)
from finance360.core.exposure import (
    OutsideTolerance,
    ProfileFacts,
    effective_weights,
)
from finance360.core.money import Money, allocate
from finance360.core.rate import PPM

LineKind = Literal["label", "not_classified", "no_profile"]
#: Why a No profile line is not simply "the Instrument has no profile".
NoProfileReason = Literal["cycle", "too_deep"]


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
    #: The Instruments this came through: the held Instrument first, then each fund-of-funds
    #: reference followed. For a No profile line the last one is the Instrument without a profile.
    path: tuple[int, ...]
    #: A No profile line: ``cycle`` (the Instrument was already on the path) or ``too_deep``
    #: (beyond ``MAX_DEPTH`` references); ``None`` when there is simply no profile.
    reason: NoProfileReason | None = None


@dataclass(frozen=True)
class Visit:
    """One underlying profile a fund-of-funds resolved through (not the held Instrument's own)."""

    path: tuple[int, ...]
    #: What reached this Instrument, in the amount's currency.
    amount: Money
    as_at: date
    dated_after: bool
    shortfall_ppm: int
    trimmed: tuple[tuple[str, int], ...]
    #: ``(instrument_id, ppm)`` trimmed from the largest reference.
    trimmed_references: tuple[tuple[int, int], ...]
    omitted: bool


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
    #: The underlying profiles reached through references, in the order visited.
    visits: tuple[Visit, ...] = ()
    #: ``(instrument_id, ppm)`` trimmed from the largest reference of this profile.
    trimmed_references: tuple[tuple[int, int], ...] = ()


def resolve(
    amount: Money,
    instrument_id: int,
    dimension: str,
    on: date,
    profiles: Mapping[int, Sequence[ProfileFacts]],
    order: Sequence[str],
) -> Resolution:
    """Split ``amount`` of one Instrument across a Dimension's labels, through its references.

    No profile on the date: one No profile line. Otherwise the weights resolve through
    ``effective_weights`` (a within-tolerance shortfall and any weightless remainder go to Not
    classified; an excess is trimmed from the largest weight, else the largest reference) and
    ``amount`` is allocated across the references and the direct share, then the direct share
    across its labels. Each reference resolves the same way on its own Instrument's profile.
    A Dimension the profile omits is all Not classified.
    """
    visits: list[Visit] = []
    root = _resolve(amount, (instrument_id,), dimension, on, profiles, order, visits)
    return Resolution(
        tuple(line for line in root.lines if line.amount.amount_minor),
        root.profile_id,
        root.as_at,
        root.dated_after,
        root.shortfall_ppm,
        root.trimmed,
        root.omitted,
        tuple(visits),
        root.trimmed_references,
    )


def _resolve(
    amount: Money,
    path: tuple[int, ...],
    dimension: str,
    on: date,
    profiles: Mapping[int, Sequence[ProfileFacts]],
    order: Sequence[str],
    visits: list[Visit],
) -> Resolution:
    instrument_id = path[-1]
    selected = select_profile(profiles.get(instrument_id, ()), on)
    if selected is None:
        line = ExposureLine("no_profile", None, amount, path)
        return Resolution((line,), None, None, False, 0, ())
    profile = selected.profile
    try:
        effective = effective_weights(profile.for_dimension(dimension), order, profile.references)
    except OutsideTolerance:  # cannot happen for a profile the write path accepted
        line = ExposureLine("not_classified", None, amount, path)
        return Resolution((line,), profile.profile_id, profile.as_at, False, 0, (), True)

    references = effective.references
    keys = [key for key, _ in effective.weights]
    # Stage one: the references and the direct share (the labels and any Not classified).
    direct_ppm = PPM - sum(ppm for _, ppm in references)
    buckets = {i: ppm for i, (_, ppm) in enumerate(references)}
    buckets[len(references)] = direct_ppm
    stage = allocate(amount, buckets)
    lines: list[ExposureLine] = []
    for i, (ref_id, _) in enumerate(references):
        ref_amount = stage[i]
        ref_path = (*path, ref_id)
        if ref_id in path:
            lines.append(ExposureLine("no_profile", None, ref_amount, ref_path, "cycle"))
        elif len(path) > MAX_DEPTH:
            lines.append(ExposureLine("no_profile", None, ref_amount, ref_path, "too_deep"))
        else:
            under = _resolve(ref_amount, ref_path, dimension, on, profiles, order, visits)
            lines.extend(under.lines)
            if under.as_at is not None:
                visits.append(
                    Visit(
                        ref_path,
                        ref_amount,
                        under.as_at,
                        under.dated_after,
                        under.shortfall_ppm,
                        under.trimmed,
                        under.trimmed_references,
                        under.omitted,
                    )
                )
    # Stage two: the direct share across its labels.
    direct_amount = stage[len(references)]
    if direct_amount.amount_minor:
        weights = {i: ppm for i, (_, ppm) in enumerate(effective.weights)}
        if effective.not_classified_ppm:
            weights[len(keys)] = effective.not_classified_ppm
        split = allocate(direct_amount, weights)
        lines.extend(ExposureLine("label", key, split[i], path) for i, key in enumerate(keys))
        if effective.not_classified_ppm:
            lines.append(ExposureLine("not_classified", None, split[len(keys)], path))
    shortfall = effective.not_classified_ppm if effective.weights or references else 0
    return Resolution(
        tuple(line for line in lines if line.amount.amount_minor),
        profile.profile_id,
        profile.as_at,
        selected.dated_after,
        shortfall,
        effective.trimmed,
        not effective.weights and not references,
        (),
        effective.trimmed_references,
    )
