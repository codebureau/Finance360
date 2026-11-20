"""Comparing Tax estimates (ADR-0019 sections 6 and 8). Pure.

Two parts. ``compare(a, b)`` compares two saved estimates of one financial year (below).
``compare_to_lodged(result, lodged)`` compares one Member's estimate with a Lodged result, the
assessed outcome from a notice (the worksheet, and the local proof against ``private/``): each
result key's difference (the engine less the lodged amount), and every lodged item the engine has
no value to compare with, by name. Net outcome is matched within the notice's rounding (5 cents,
the 2026-11-17 amendment of ADR-0019 section 8, #61); every other item is compared to the cent.

``compare(a, b)`` reports, for each Member, every Label total that differs, and for each Member and
the Household every result line, each with its exact delta: ``b`` less ``a``. Money deltas are exact
``Money``; a days delta is a whole number; a flag has no delta (only the change). It reads the two
estimates' pinned input and stored result and nothing else, so a comparison never depends on the
register, the Rule pack or the engine of the day.
"""

from __future__ import annotations

from dataclasses import dataclass

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.engine import MemberResult, TaxResult
from finance360.capabilities.tax.input import TaxInput, Total, default_for
from finance360.core.money import Money

#: The result lines compared, in the order shown (they are the lines the Estimate screen shows).
RESULT_KEYS: tuple[str, ...] = (
    "taxable_income",
    "tax_on_taxable_income",
    "offsets",
    "medicare_levy",
    "medicare_levy_surcharge",
    "help_repayment",
    "total_tax",
    "credits",
    "net_outcome",
)


class CompareError(ValueError):
    """The two estimates cannot be compared (different year or different Members)."""


@dataclass(frozen=True)
class Side:
    """One saved estimate as the comparison reads it."""

    input: TaxInput
    result: TaxResult


@dataclass(frozen=True)
class LabelDelta:
    member_id: int
    label: str
    a: Total
    b: Total
    #: ``b`` less ``a`` for money and days; ``None`` for a flag (it changed, with no amount).
    delta: Money | int | None


@dataclass(frozen=True)
class ResultDelta:
    key: str
    #: ``None`` for the Household.
    member_id: int | None
    a: Money
    b: Money
    delta: Money


@dataclass(frozen=True)
class Comparison:
    financial_year: int
    member_ids: tuple[int, ...]
    #: Only the totals that differ, in label order then Member order.
    labels: tuple[LabelDelta, ...]
    #: Every result line for each Member then the Household, changed or not.
    results: tuple[ResultDelta, ...]
    dependants: tuple[int, int]
    engine_versions: tuple[int, int]
    rule_pack_differs: bool

    @property
    def same_engine(self) -> bool:
        return self.engine_versions[0] == self.engine_versions[1]


def _delta(a: Total, b: Total) -> Money | int | None:
    if isinstance(a, Money) and isinstance(b, Money):
        return b - a
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, Money) or isinstance(b, Money):
        return None
    return b - a


def _total(side: TaxInput, member_id: int, key: str) -> Total:
    for member in side.members:
        if member.member_id == member_id:
            return member.total(key)
    return default_for(labels.LABELS[key].kind)


def _member(result: TaxResult, member_id: int) -> MemberResult:
    return next(m for m in result.members if m.member_id == member_id)


def compare(a: Side, b: Side) -> Comparison:
    if a.input.financial_year != b.input.financial_year:
        raise CompareError("Only estimates of the same financial year can be compared.")
    ids_a = tuple(m.member_id for m in a.input.members)
    ids_b = tuple(m.member_id for m in b.input.members)
    if ids_a != ids_b:
        raise CompareError("Both estimates must cover the same Members.")
    order = {label.key: i for i, label in enumerate(labels.all_labels())}
    keys = sorted(
        {
            (key, m.member_id)
            for side in (a.input, b.input)
            for m in side.members
            for key, _ in m.totals
        },
        key=lambda item: (order[item[0]], item[1]),
    )
    changed: list[LabelDelta] = []
    for key, member_id in keys:
        before, after = _total(a.input, member_id, key), _total(b.input, member_id, key)
        if before != after:
            changed.append(LabelDelta(member_id, key, before, after, _delta(before, after)))
    results: list[ResultDelta] = []
    for member_id in ids_a:
        left, right = _member(a.result, member_id), _member(b.result, member_id)
        for name in RESULT_KEYS:
            x, y = getattr(left, name), getattr(right, name)
            results.append(ResultDelta(name, member_id, x, y, y - x))
    for name in RESULT_KEYS:
        x, y = getattr(a.result.household, name), getattr(b.result.household, name)
        results.append(ResultDelta(name, None, x, y, y - x))
    return Comparison(
        a.input.financial_year,
        ids_a,
        tuple(changed),
        tuple(results),
        (a.input.dependants, b.input.dependants),
        (a.result.engine_version, b.result.engine_version),
        a.input.rules != b.input.rules,
    )


# --- the engine's estimate against a Lodged result (ADR-0019 section 8) -----------------------

#: The notice rounds the final result by a few cents, and the Return labels cannot reproduce that
#: step, so net outcome is matched within this. Everything else is compared to the cent.
NET_OUTCOME_TOLERANCE = Money(5, "AUD")
NET_OUTCOME = "net_outcome"

#: ``LodgedLine.status``.
MATCHED = "matched"  # engine and notice agree to the cent
ROUNDING = "rounding"  # net outcome, off by no more than the notice's rounding; difference shown
DIFFERS = "differs"
UNMATCHED = "unmatched"  # a lodged item the engine has no value to compare with


@dataclass(frozen=True)
class OtherLine:
    """A named line on the notice that the engine has no result key for. ``amount`` is ``None``
    when only the name is known (the local proof lists names without figures)."""

    name: str
    amount: Money | None = None


@dataclass(frozen=True)
class LodgedResult:
    """The assessed outcome of a notice: result key to amount, plus the other named lines."""

    items: tuple[tuple[str, Money], ...]
    other: tuple[OtherLine, ...] = ()


@dataclass(frozen=True)
class LodgedLine:
    """One lodged item against the engine's value."""

    #: The result key, or the name of an ``other`` line.
    key: str
    status: str
    lodged: Money | None
    #: ``None`` when the engine has no value to compare.
    engine: Money | None
    #: The engine less the lodged amount; ``None`` when unmatched. Non-zero for a ``rounding`` line.
    difference: Money | None
    #: ``True`` for an ``other`` line (the notice names it, the engine has no key for it).
    is_other: bool = False


@dataclass(frozen=True)
class LodgedComparison:
    #: Result keys in the estimate's order, then unknown keys, then ``other`` lines.
    lines: tuple[LodgedLine, ...]

    @property
    def matched(self) -> tuple[LodgedLine, ...]:
        """Agreed to the cent, or net outcome within the notice's rounding."""
        return tuple(x for x in self.lines if x.status in (MATCHED, ROUNDING))

    @property
    def differences(self) -> tuple[LodgedLine, ...]:
        return tuple(x for x in self.lines if x.status == DIFFERS)

    @property
    def unmatched(self) -> tuple[LodgedLine, ...]:
        return tuple(x for x in self.lines if x.status == UNMATCHED)


def compare_to_lodged(result: MemberResult, lodged: LodgedResult) -> LodgedComparison:
    """Compare one Member's estimate with their Lodged result. Reports every difference (the engine
    less the lodged amount), and every lodged item with no engine value, by name. It never passes
    over an item."""
    order = {name: i for i, name in enumerate(RESULT_KEYS)}
    known = sorted((i for i in lodged.items if i[0] in order), key=lambda i: order[i[0]])
    unknown = [i for i in lodged.items if i[0] not in order]
    lines: list[LodgedLine] = []
    for key, amount in known:
        engine: Money = getattr(result, key)
        difference = engine - amount
        if difference.amount_minor == 0:
            status = MATCHED
        elif key == NET_OUTCOME and abs(difference.amount_minor) <= (
            NET_OUTCOME_TOLERANCE.amount_minor
        ):
            status = ROUNDING
        else:
            status = DIFFERS
        lines.append(LodgedLine(key, status, amount, engine, difference))
    lines.extend(LodgedLine(key, UNMATCHED, amount, None, None) for key, amount in unknown)
    lines.extend(
        LodgedLine(line.name, UNMATCHED, line.amount, None, None, is_other=True)
        for line in lodged.other
    )
    return LodgedComparison(tuple(lines))
