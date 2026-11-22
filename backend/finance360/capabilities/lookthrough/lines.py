"""The pure Account look-through composition (ADR-0030 section 6). No I/O.

An Account's parts (each Holding at its Price x quantity through FX, plus cash) are split across a
Dimension's labels, with explicit lines for what has no profile, what a profile does not classify,
and the gap between the parts and the Account's Position value (Unallocated). The lines sum
**exactly** to the Position value: parts are allocated across the converted parts total, and
Unallocated is the Position value minus that total, so nothing is absorbed or scaled. Percentages
are display only, rounded once from Money.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from finance360.capabilities.lookthrough.reconcile import is_material, reconcile_tolerance
from finance360.capabilities.lookthrough.resolve import resolve
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
    trimmed: tuple[tuple[str, int], ...]
    #: The part has a profile that gives this Dimension no weights: all Not classified.
    omitted: bool = False


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


@dataclass(frozen=True)
class Composition:
    total: Money
    lines: tuple[Line, ...]
    parts: tuple[PartResult, ...]
    unallocated: Money
    #: The Unallocated gap is beyond the reconcile tolerance (shown quietly otherwise).
    flagged: bool
    tolerance: Money


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
) -> Composition:
    """The Account's look-through in one Dimension.

    ``position_value`` is the Account's Position value and ``parts_value`` the parts' total, both
    in the Base currency (the service converts); ``labels`` is the Dimension's ``(key, name)`` in
    seed order. The returned lines sum exactly to ``position_value``.
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

    by_label: dict[str, Money] = {}
    no_profile: dict[int, Money] = {}
    not_classified = zero
    results: list[PartResult] = []
    for part, amount in zip(parts, in_base, strict=True):
        if part.instrument_id is None:
            cash_label = _cash_label(dimension, part.currency, names)
            if cash_label is not None:
                by_label[cash_label] = by_label.get(cash_label, zero) + amount
            else:
                not_classified += amount
            results.append(PartResult(None, part.name, amount, None, False, 0, ()))
            continue
        resolved = resolve(amount, part.instrument_id, dimension, on, profiles, order)
        for line in resolved.lines:
            if line.kind == "label" and line.label_key is not None:
                by_label[line.label_key] = by_label.get(line.label_key, zero) + line.amount
            elif line.kind == "no_profile":
                no_profile[part.instrument_id] = (
                    no_profile.get(part.instrument_id, zero) + line.amount
                )
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
                resolved.trimmed,
                resolved.omitted,
            )
        )

    unallocated = position_value - parts_value
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
                )
            )
    parts_by_id = {p.instrument_id: p.name for p in parts if p.instrument_id is not None}
    for instrument_id, amount in no_profile.items():
        lines.append(
            Line(
                "no_profile",
                None,
                f"No profile: {parts_by_id[instrument_id]}",
                amount,
                percent_text(amount, position_value),
                instrument_id,
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
    return Composition(
        position_value,
        tuple(lines),
        tuple(results),
        unallocated,
        is_material(unallocated, position_value),
        reconcile_tolerance(position_value),
    )
