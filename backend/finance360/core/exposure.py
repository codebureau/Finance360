"""Exposure vocabulary constants and the weight-sum tolerance rules (ADR-0030 sections 2 and 3).

Pure: no database, so the Reference write path and the look-through resolver (which may not reach
``core.models``) share one rule. Weights are stored exactly as entered; these rules judge a total
and say how a within-tolerance total resolves. Nothing is normalised.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
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

#: Most levels of fund-of-funds references one look-through follows (ADR-0030 section 5): a profile
#: may reference funds that reference funds, this many references deep. A deeper write is rejected.
MAX_DEPTH = 5


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
    #: Fund-of-funds references as they resolve, ``(instrument_id, ppm)`` (ADR-0030 section 5).
    references: tuple[tuple[int, int], ...] = ()
    #: ``(instrument_id, ppm)`` trimmed from the largest reference(s) when no direct weight could
    #: take the excess of a within-tolerance total over 100%.
    trimmed_references: tuple[tuple[int, int], ...] = ()


def effective_weights(
    weights: Sequence[tuple[str, int]],
    order: Mapping[str, int] | Sequence[str],
    references: Sequence[tuple[int, int]] = (),
) -> Effective:
    """Resolve one Dimension's ``(label_key, ppm)`` weights and the profile's references.

    A Dimension's total is its direct weights plus every reference weight (ADR-0030 sections 3
    and 5):

    - nothing at all: the Dimension is omitted, all of it is Not classified;
    - short within tolerance: the shortfall is Not classified;
    - over within tolerance: the excess is trimmed from the largest direct weight (ties go to the
      earliest label in ``order``, the seed order) and, with no direct weight to take it, from the
      largest reference (ties go to the lower Instrument id);
    - beyond tolerance either way: ``OutsideTolerance``. A profile that gives references but no
      weight in this Dimension leaves it out: only an excess is judged, and whatever the
      references do not cover is Not classified.
    """
    rank = order if isinstance(order, Mapping) else {k: i for i, k in enumerate(order)}
    if not weights and not references:
        return Effective((), PPM, ())
    total = sum(w for _, w in weights) + sum(w for _, w in references)
    difference = total - PPM
    if abs(difference) > WEIGHT_TOLERANCE_PPM and (weights or difference > 0):
        raise OutsideTolerance(total)
    if difference <= 0:
        return Effective(tuple(weights), -difference, (), tuple(references), ())
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
    trimmed_refs: list[tuple[int, int]] = []
    kept_refs = dict(references)
    for instrument_id, ppm in sorted(references, key=lambda r: (-r[1], r[0])):
        if remaining == 0:
            break
        cut = min(ppm, remaining)
        kept_refs[instrument_id] = ppm - cut
        trimmed_refs.append((instrument_id, cut))
        remaining -= cut
    return Effective(
        tuple((k, w) for k, w in kept.items() if w > 0),
        0,
        tuple(trimmed),
        tuple((i, w) for i, w in kept_refs.items() if w > 0),
        tuple(trimmed_refs),
    )


# --- fund-of-funds references: cycles and depth (ADR-0030 section 5) ---------------------------


class ReferenceCycle(ValueError):
    """A write would make Instruments reference each other in a loop. ``loop`` names it, first
    Instrument repeated at the end (``(1, 2, 1)``: 1 references 2, which references 1)."""

    def __init__(self, loop: tuple[int, ...]) -> None:
        super().__init__(f"References loop: {loop}")
        self.loop = loop


class ReferencesTooDeep(ValueError):
    """A write would nest references more than ``MAX_DEPTH`` levels. ``depth`` is the deepest chain
    it would make."""

    def __init__(self, depth: int) -> None:
        super().__init__(f"References nest {depth} levels")
        self.depth = depth


def check_references(
    edges: Mapping[int, Collection[int]], instrument_id: int, references: Collection[int]
) -> None:
    """Judge a write of ``references`` on ``instrument_id`` against the graph of current ones.

    ``edges`` maps an Instrument to the Instruments its current profiles reference, without the
    profile being replaced. Raises ``ReferenceCycle`` if any reference leads back to
    ``instrument_id`` (including a reference to itself), else ``ReferencesTooDeep`` if a chain
    through ``instrument_id`` would have more than ``MAX_DEPTH`` references. Pure.
    """
    graph: dict[int, set[int]] = {k: set(v) for k, v in edges.items()}
    graph[instrument_id] = graph.get(instrument_id, set()) | set(references)

    def route_back(start: int, target: int, seen: tuple[int, ...]) -> tuple[int, ...] | None:
        if start == target:
            return (*seen, start)
        if start in seen:
            return None
        for nxt in sorted(graph.get(start, ())):
            found = route_back(nxt, target, (*seen, start))
            if found is not None:
                return found
        return None

    for ref in sorted(references):
        found = route_back(ref, instrument_id, (instrument_id,))
        if found is not None:
            raise ReferenceCycle(found)

    below: dict[int, int] = {}

    def down(node: int) -> int:
        if node not in below:
            below[node] = 0  # a loop elsewhere in bad data ends here rather than recursing
            below[node] = max((1 + down(n) for n in graph.get(node, ())), default=0)
        return below[node]

    parents: dict[int, set[int]] = {}
    for parent, children in graph.items():
        for child in children:
            parents.setdefault(child, set()).add(parent)
    above: dict[int, int] = {}

    def up(node: int) -> int:
        if node not in above:
            above[node] = 0
            above[node] = max((1 + up(n) for n in parents.get(node, ())), default=0)
        return above[node]

    depth = up(instrument_id) + down(instrument_id)
    if depth > MAX_DEPTH:
        raise ReferencesTooDeep(depth)


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
    #: Fund-of-funds references, ``(instrument_id, weight_ppm)``: they apply to every Dimension.
    references: tuple[tuple[int, int], ...] = ()

    def for_dimension(self, dimension_key: str) -> tuple[tuple[str, int], ...]:
        return tuple((label, ppm) for dim, label, ppm in self.weights if dim == dimension_key)


def normalise_option_name(name: str) -> str:
    """The key an Investment option's mapping is found by (ADR-0030 section 4): case-folded, with
    runs of whitespace collapsed to one space and the ends trimmed, so a later statement that
    spells the option with other case or spacing still finds its mapping."""
    return " ".join(name.split()).casefold()
