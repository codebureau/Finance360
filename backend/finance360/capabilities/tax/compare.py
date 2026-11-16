"""Comparing two saved Tax estimates of the same financial year (ADR-0019 section 6). Pure.

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
