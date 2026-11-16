"""Income reconciliation: where the total package goes (ADR-0019 section 9). Pure.

From the Label totals of a Member's income statements (or the Household's: the sum of its
Members'), for one financial year:

* **Total package** = gross pay + employer super. It is 100%.
* **Take-home** is the net pay the statements state. A statement that states none has it worked
  out as its remainder (its own package minus its other slices), then summed with the stated ones:
  a mixed set is exact per statement, and only a stated figure can leave Unreconciled.
* **Slices**: tax withheld (which includes the Medicare levy; a factual split is not on an income
  statement), super, novated lease costs, other deductions and take-home.
* **Unreconciled** = package - the slices. Always returned, signed, never netted away.

Shares come from ``core.money.allocate`` over 10**6, so the drawn shares sum to exactly 100%. The
weights are the slices, plus Unreconciled when it is positive (a gap to show on the pie). A
negative Unreconciled means the slices already exceed the package: it is **not** a slice, it is
reported as a note, and the shares then divide the slices alone. Reportable fringe benefits (a
grossed-up, notional figure) and insurance premiums paid from the super balance (not paid from pay)
are carried beside the result and take no part in it.

Shares and the Unreconciled amount are facts at gross level, not an estimate. Nothing is stored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from finance360.core.money import BASE_CURRENCY, Money, allocate
from finance360.core.rate import PPM, Rate

#: The slices, in the order drawn: key, name, and the label whose total is the amount. Tax withheld
#: and super are first: they are the biggest and the most stable.
SLICES: tuple[tuple[str, str, str], ...] = (
    ("tax", "Tax withheld (includes Medicare levy)", "tax_withheld"),
    ("super", "Super", "employer_super"),
    ("lease", "Novated lease costs", "novated_lease_post_tax"),
    ("other", "Other deductions", "payroll_other_deductions"),
    ("take_home", "Take-home", "net_pay_received"),
)
UNRECONCILED = "unreconciled"


@dataclass(frozen=True)
class Slice:
    key: str
    name: str
    amount: Money
    #: Parts per million of the drawn whole, or ``None`` when it is not drawn.
    share_ppm: int | None


@dataclass(frozen=True)
class IncomeReconciliation:
    member_id: int | None
    gross_pay: Money
    employer_super: Money
    #: Gross pay plus employer super.
    package: Money
    #: The five slices, then Unreconciled when it is positive. In drawing order.
    slices: tuple[Slice, ...]
    #: Package minus the five slices. Signed. Always present.
    unreconciled: Money
    #: Beside the pie, not in it.
    reportable_fringe_benefits: Money
    #: A note under the pie, not in it.
    super_insurance_premiums: Money
    #: The part of Take-home that is the remainder of statements that state no net pay (worked out
    #: per statement, then summed). Zero when every statement states it.
    take_home_worked_out: Money = Money.zero(BASE_CURRENCY)

    @property
    def unreconciled_share_ppm(self) -> int | None:
        for item in self.slices:
            if item.key == UNRECONCILED:
                return item.share_ppm
        return None

    @property
    def has_figures(self) -> bool:
        return bool(self.package.amount_minor) or any(s.amount.amount_minor for s in self.slices)


def _get(totals: Mapping[str, Money], label: str) -> Money:
    return totals.get(label, Money.zero(BASE_CURRENCY))


def shares_ppm(amounts: Sequence[Money]) -> list[int]:
    """The amounts as parts per million of their sum, summing to exactly ``PPM`` (or all zero when
    there is nothing to share). Largest remainder, via ``allocate``."""
    weights = {i: a.amount_minor for i, a in enumerate(amounts)}
    if sum(weights.values()) <= 0:
        return [0] * len(amounts)
    parts = allocate(Money(PPM, BASE_CURRENCY), weights)
    return [parts[i].amount_minor for i in range(len(amounts))]


def reconcile(
    totals: Mapping[str, Money],
    member_id: int | None = None,
    worked_out: Money | None = None,
) -> IncomeReconciliation:
    """The reconciliation of one set of Label totals (a Member's, or summed for the Household).

    ``worked_out`` is the take-home of the statements that state no net pay, each worked out as its
    own package minus its other slices. It is added to the stated net pay, so a statement that
    states nothing never shows as Unreconciled, and a stated one still does.
    """
    zero = Money.zero(BASE_CURRENCY)
    worked_out = worked_out or zero
    gross = _get(totals, "salary")
    employer_super = _get(totals, "employer_super")
    package = gross + employer_super
    amounts = [
        (key, name, _get(totals, label) + (worked_out if key == "take_home" else zero))
        for key, name, label in SLICES
    ]
    accounted = Money.zero(BASE_CURRENCY)
    for _, _, amount in amounts:
        accounted = accounted + amount
    unreconciled = package - accounted

    drawn = list(amounts)
    if unreconciled.amount_minor > 0:
        drawn.append((UNRECONCILED, "Unreconciled", unreconciled))
    parts = shares_ppm([amount for _, _, amount in drawn])
    slices = tuple(
        Slice(key, name, amount, share)
        for (key, name, amount), share in zip(drawn, parts, strict=True)
    )
    return IncomeReconciliation(
        member_id,
        gross,
        employer_super,
        package,
        slices,
        unreconciled,
        _get(totals, "reportable_fringe_benefits"),
        _get(totals, "super_insurance_premiums"),
        worked_out,
    )


def reconcile_household(
    members: Sequence[Mapping[str, Money]], worked_out: Sequence[Money] = ()
) -> IncomeReconciliation:
    """The Household: its Members' Label totals and worked-out take-home summed, then reconciled
    once."""
    summed: dict[str, Money] = {}
    for totals in members:
        for label, amount in totals.items():
            summed[label] = summed.get(label, Money.zero(BASE_CURRENCY)) + amount
    worked_out_total = Money.zero(BASE_CURRENCY)
    for amount in worked_out:
        worked_out_total = worked_out_total + amount
    return reconcile(summed, None, worked_out_total)


def percent_text(share_ppm: int) -> str:
    """A share as a percent with up to four decimals, no trailing zeros (``12.5``)."""
    return Rate(share_ppm).to_percent()
