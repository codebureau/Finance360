"""The pure Account look-through composition (ADR-0030 section 6). No I/O.

An Account's parts (each Holding at its Price x quantity through FX, plus cash) are split across a
Dimension's labels, with explicit lines for what has no profile, what a profile does not classify,
and the gap between the parts and the Account's Position value (Unallocated). A Holding of a
fund-of-funds is resolved through its underlying funds (ADR-0030 section 5): a line that any
underlying fund contributed to carries those contributions, so the panel can show which fund gave
which part. The lines sum **exactly** to the Position value: parts are allocated across the
converted parts total, and Unallocated is the Position value minus that total, so nothing is
absorbed or scaled. Percentages are display only, rounded once from Money.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from finance360.capabilities.lookthrough.reconcile import (
    is_material,
    is_stale_material,
    reconcile_tolerance,
    stale_tolerance,
)
from finance360.capabilities.lookthrough.resolve import NoProfileReason, resolve
from finance360.core.exposure import ASSET_CLASS, CASH, EXPOSURE_CURRENCY, ProfileFacts
from finance360.core.money import Money, allocate, round_div

LineKind = Literal["label", "no_profile", "not_classified", "unallocated"]


@dataclass(frozen=True)
class Part:
    """One term of the Account's derived value, in the Account's currency (never negative)."""

    #: ``None`` for cash.
    instrument_id: int | None
    name: str
    amount: Money
    #: A cash part's currency: its Exposure currency (ADR-0030 section 6). ``None`` for a Holding.
    currency: str | None = None
    #: A super Investment option part: the option's name as the statement states it. With no
    #: ``instrument_id`` the option is not mapped to an Instrument yet (a No profile line).
    option: str | None = None


@dataclass(frozen=True)
class PartResult:
    instrument_id: int | None
    name: str
    #: The part in the Base currency, as allocated from the Account's Position value.
    amount: Money
    #: ``None`` for cash or when there is no profile on any date.
    profile_as_at: date | None
    dated_after: bool
    shortfall_ppm: int
    #: ``(label key or underlying fund name, ppm)`` trimmed from the largest weight (over 100%).
    trimmed: tuple[tuple[str, int], ...]
    #: The part has a profile that gives this Dimension no weights: all Not classified.
    omitted: bool = False
    #: The underlying funds this part resolved through, each with its own profile's notes.
    underlying: tuple[Underlying, ...] = ()
    #: An Investment option part: its name on the statement (``instrument_id`` is the mapped
    #: Instrument, ``None`` while unmapped).
    option: str | None = None
    #: The mapped Instrument's name, for an option part.
    instrument_name: str | None = None


@dataclass(frozen=True)
class Underlying:
    """One underlying fund a fund-of-funds Holding resolved through, and its profile's notes."""

    #: The Instruments' names from the held fund to this one.
    path: tuple[str, ...]
    #: What reached it, in the Base currency.
    amount: Money
    profile_as_at: date
    dated_after: bool
    shortfall_ppm: int
    trimmed: tuple[tuple[str, int], ...]
    omitted: bool


@dataclass(frozen=True)
class Contribution:
    """What one route through the funds gave to a line: the names from the held fund down."""

    path: tuple[str, ...]
    amount: Money


@dataclass(frozen=True)
class Line:
    kind: LineKind
    #: The label key of a ``label`` line.
    key: str | None
    name: str
    amount: Money
    #: Percent of the Position value, two decimal places, display only; ``None`` for a zero total.
    percent: str | None
    #: A ``no_profile`` line names the Instrument so the page can link to entering one.
    instrument_id: int | None = None
    #: Set only when an underlying fund gave to this line: every route that did (the held fund's
    #: own direct share too), summing to ``amount``. Empty for an ordinary line.
    contributions: tuple[Contribution, ...] = ()
    #: A ``no_profile`` line whose Instrument is not the held one: ``cycle`` or ``too_deep``.
    reason: NoProfileReason | None = None
    #: A ``no_profile`` line for an Investment option not mapped to an Instrument yet: its name,
    #: so the page can offer the mapping.
    option_name: str | None = None


@dataclass(frozen=True)
class Composition:
    total: Money
    lines: tuple[Line, ...]
    parts: tuple[PartResult, ...]
    unallocated: Money
    #: The Unallocated gap is beyond the reconcile tolerance (shown quietly otherwise).
    flagged: bool
    tolerance: Money
    #: The Super statement's date when the Account's Valuation is dated after it: the Unallocated
    #: line is then "change since" that date and is judged against the stale tolerance.
    change_since: date | None = None


def percent_text(amount: Money, total: Money) -> str | None:
    """``amount`` as a percent of ``total`` to two decimal places, by integer arithmetic."""
    if total.amount_minor <= 0:
        return None
    hundredths = round_div(abs(amount.amount_minor) * 10_000, total.amount_minor)
    sign = "-" if amount.amount_minor < 0 and hundredths else ""
    return f"{sign}{hundredths // 100}.{hundredths % 100:02d}"


def _cash_label(dimension: str, currency: str | None, names: Mapping[str, str]) -> str | None:
    """The label the Account's cash takes in a Dimension (ADR-0030 section 6): asset class
    ``cash`` and Exposure currency its own currency; Not classified (``None``) for region and
    sector, and for a currency the vocabulary does not list."""
    if dimension == ASSET_CLASS and CASH in names:
        return CASH
    if dimension == EXPOSURE_CURRENCY and currency is not None and currency.lower() in names:
        return currency.lower()
    return None


def compose(
    *,
    position_value: Money,
    parts_value: Money,
    parts: Sequence[Part],
    dimension: str,
    labels: Sequence[tuple[str, str]],
    on: date,
    profiles: Mapping[int, Sequence[ProfileFacts]],
    names_of: Mapping[int, str] | None = None,
    change_since: date | None = None,
) -> Composition:
    """The Account's look-through in one Dimension.

    ``position_value`` is the Account's Position value and ``parts_value`` the parts' total, both
    in the Base currency (the service converts); ``labels`` is the Dimension's ``(key, name)`` in
    seed order and ``names_of`` the Instruments' names (including underlying funds). The returned
    lines sum exactly to ``position_value``. ``change_since`` is set when a super Account's
    Valuation is dated after its Super statement: Unallocated is then judged against the stale
    tolerance (ADR-0030 section 6).
    """
    names = dict(labels)
    order = [key for key, _ in labels]
    base = position_value.currency
    zero = Money.zero(base)
    weights = {i: p.amount.amount_minor for i, p in enumerate(parts)}
    if parts and parts_value.amount_minor and sum(weights.values()) > 0:
        shares = allocate(parts_value, weights)
        in_base = [shares[i] for i in range(len(parts))]
    else:  # nothing to analyse (or nothing worth analysing): everything is Unallocated
        in_base = [zero for _ in parts]
        parts_value = zero

    instrument_names = dict(names_of or {})
    for part in parts:
        if part.instrument_id is not None:
            instrument_names.setdefault(part.instrument_id, part.name)

    def called(instrument_id: int) -> str:
        return instrument_names.get(instrument_id, f"Instrument {instrument_id}")

    by_label: dict[str, Money] = {}
    #: Routes (Instrument names, held fund first) and what each gave, per label and No profile.
    label_routes: dict[str, dict[tuple[str, ...], Money]] = {}
    no_profile: dict[tuple[int, NoProfileReason | None], Money] = {}
    #: Investment options not mapped to an Instrument yet, by name.
    unmapped: dict[str, Money] = {}
    no_profile_routes: dict[tuple[int, NoProfileReason | None], dict[tuple[str, ...], Money]] = {}
    not_classified = zero
    results: list[PartResult] = []
    for part, amount in zip(parts, in_base, strict=True):
        if part.instrument_id is None and part.option is not None:
            unmapped[part.option] = unmapped.get(part.option, zero) + amount
            results.append(
                PartResult(None, part.name, amount, None, False, 0, (), option=part.option)
            )
            continue
        if part.instrument_id is None:
            cash_label = _cash_label(dimension, part.currency, names)
            if cash_label is not None:
                by_label[cash_label] = by_label.get(cash_label, zero) + amount
                routes = label_routes.setdefault(cash_label, {})
                routes[(part.name,)] = routes.get((part.name,), zero) + amount
            else:
                not_classified += amount
            results.append(PartResult(None, part.name, amount, None, False, 0, ()))
            continue
        resolved = resolve(amount, part.instrument_id, dimension, on, profiles, order)
        for line in resolved.lines:
            if line.kind == "label" and line.label_key is not None:
                by_label[line.label_key] = by_label.get(line.label_key, zero) + line.amount
                routes = label_routes.setdefault(line.label_key, {})
                route = tuple(called(i) for i in line.path)
                routes[route] = routes.get(route, zero) + line.amount
            elif line.kind == "no_profile":
                key = (line.path[-1], line.reason)
                no_profile[key] = no_profile.get(key, zero) + line.amount
                routes = no_profile_routes.setdefault(key, {})
                route = tuple(called(i) for i in line.path)
                routes[route] = routes.get(route, zero) + line.amount
            else:
                not_classified += line.amount
        results.append(
            PartResult(
                part.instrument_id,
                part.name,
                amount,
                resolved.as_at,
                resolved.dated_after,
                resolved.shortfall_ppm,
                (
                    *resolved.trimmed,
                    *((called(i), ppm) for i, ppm in resolved.trimmed_references),
                ),
                resolved.omitted,
                tuple(
                    Underlying(
                        tuple(called(i) for i in visit.path),
                        visit.amount,
                        visit.as_at,
                        visit.dated_after,
                        visit.shortfall_ppm,
                        (
                            *visit.trimmed,
                            *((called(i), ppm) for i, ppm in visit.trimmed_references),
                        ),
                        visit.omitted,
                    )
                    for visit in resolved.visits
                ),
                part.option,
                called(part.instrument_id) if part.option is not None else None,
            )
        )

    unallocated = position_value - parts_value

    def contributions(routes: Mapping[tuple[str, ...], Money]) -> tuple[Contribution, ...]:
        """Only a line some underlying fund gave to has them; a Holding's own is no news."""
        if all(len(path) == 1 for path in routes):
            return ()
        return tuple(
            Contribution(path, amount)
            for path, amount in sorted(
                routes.items(), key=lambda r: (len(r[0]), -r[1].amount_minor)
            )
            if amount.amount_minor
        )

    lines: list[Line] = []
    for key in order:
        if key in by_label and by_label[key].amount_minor:
            lines.append(
                Line(
                    "label",
                    key,
                    names[key],
                    by_label[key],
                    percent_text(by_label[key], position_value),
                    contributions=contributions(label_routes[key]),
                )
            )
    for (instrument_id, reason), amount in no_profile.items():
        note = {"cycle": " (a loop of funds)", "too_deep": " (nested too deep)"}.get(
            reason or "", ""
        )
        lines.append(
            Line(
                "no_profile",
                None,
                f"No profile: {called(instrument_id)}{note}",
                amount,
                percent_text(amount, position_value),
                instrument_id,
                contributions(no_profile_routes[(instrument_id, reason)]),
                reason,
            )
        )
    for option, amount in unmapped.items():
        lines.append(
            Line(
                "no_profile",
                None,
                f"No profile: {option} (not mapped to a fund)",
                amount,
                percent_text(amount, position_value),
                option_name=option,
            )
        )
    if not_classified.amount_minor:
        lines.append(
            Line(
                "not_classified",
                None,
                "Not classified",
                not_classified,
                percent_text(not_classified, position_value),
            )
        )
    lines.append(
        Line(
            "unallocated",
            None,
            "Unallocated",
            unallocated,
            percent_text(unallocated, position_value),
        )
    )
    stale = change_since is not None
    return Composition(
        position_value,
        tuple(lines),
        tuple(results),
        unallocated,
        (is_stale_material if stale else is_material)(unallocated, position_value),
        (stale_tolerance if stale else reconcile_tolerance)(position_value),
        change_since,
    )
