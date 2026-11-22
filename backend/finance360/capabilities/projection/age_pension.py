"""The Age Pension means test: a pure calculation (B11, ADR-0018 section 1).

From the pension parameters, whether the household is a couple and owns its home, and its
assessable assets and income, to the Age Pension for a year as exact ``Money``. The lower of the
income test and the assets test applies.

Pure: no I/O, and it imports neither ``core.rulepack`` nor ``rulepacks/`` (the caller reads the
Rule pack and passes ``AgePensionParams`` in; see the import-linter contract). The parameters are
fortnightly figures *as published*; this module converts them to a year (26 fortnights).

The simplified treatment, stated:

* **Maximum rate** is the basic rate plus the pension supplement. The energy supplement, rent
  assistance, the work bonus, gifting, asset hardship and part-rate special cases are not modelled.
* **A couple** is both partners on the Age Pension, assessed on their combined assets and income;
  the result is the couple's total (twice the each-partner rate, less one combined reduction).
* **Deeming.** ``financial_assets`` (a subset of ``assets``) earns deemed income: the lower rate up
  to the threshold, the upper rate above it. ``other_income`` is every other assessable income a
  year and is added to it. Non-financial assets are assessed in the assets test only.
* **Income test:** the pension falls by ``income_taper`` of each dollar of annual income above the
  annual free area. **Assets test:** it falls by ``assets_taper`` per fortnight of each dollar of
  assets above the limit for the household's status (so $3 per $1,000 is 0.3%).

Everything is worked exactly in integers (cents times parts per million) and rounded **once**, at
the end, half to even, by ``core.money.round_div``. Rounding once is what makes the result
monotone: more assets or income can never raise the pension.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from finance360.core.money import CurrencyMismatchError, Money, MoneyError, opaque, round_div
from finance360.core.rate import PPM, Rate

FORTNIGHTS_PER_YEAR = 26


@dataclass(frozen=True, slots=True)
class AgePensionParams:
    """Every figure the means test reads, as published (money per fortnight, assets as totals)."""

    max_basic_single: Money
    max_basic_couple_each: Money
    pension_supplement_single: Money
    pension_supplement_couple_each: Money
    income_free_area_single: Money
    income_free_area_couple: Money
    income_taper: Rate
    assets_threshold_homeowner_single: Money
    assets_threshold_homeowner_couple: Money
    assets_threshold_non_homeowner_single: Money
    assets_threshold_non_homeowner_couple: Money
    assets_taper: Rate
    deeming_threshold_single: Money
    deeming_threshold_couple: Money
    deeming_rate_lower: Rate
    deeming_rate_upper: Rate

    def money_fields(self) -> tuple[Money, ...]:
        return (
            self.max_basic_single,
            self.max_basic_couple_each,
            self.pension_supplement_single,
            self.pension_supplement_couple_each,
            self.income_free_area_single,
            self.income_free_area_couple,
            self.assets_threshold_homeowner_single,
            self.assets_threshold_homeowner_couple,
            self.assets_threshold_non_homeowner_single,
            self.assets_threshold_non_homeowner_couple,
            self.deeming_threshold_single,
            self.deeming_threshold_couple,
        )


def _check(params: AgePensionParams, *amounts: Money) -> str:
    currency = params.max_basic_single.currency
    for money in (*params.money_fields(), *amounts):
        if money.currency != currency:
            raise CurrencyMismatchError(f"Age Pension mixes {currency} and {money.currency}")
        if money.amount_minor < 0:
            raise MoneyError("Age Pension figures and amounts are not negative")
    return currency


def maximum_annual(params: AgePensionParams, *, couple: bool) -> Money:
    """The full pension for a year: basic rate plus pension supplement (both if a couple)."""
    currency = _check(params)
    if couple:
        fortnight = params.max_basic_couple_each.amount_minor
        fortnight += params.pension_supplement_couple_each.amount_minor
        fortnight *= 2
    else:
        fortnight = params.max_basic_single.amount_minor
        fortnight += params.pension_supplement_single.amount_minor
    return Money(fortnight * FORTNIGHTS_PER_YEAR, currency)


#: Which test bound the pension: the income test or the assets test paid less, or ``none`` when
#: neither reduced the maximum.
BOUND_INCOME = "income"
BOUND_ASSETS = "assets"
BOUND_NONE = "none"


@dataclass(frozen=True, slots=True)
class AgePensionWorking:
    """The means test's working for one assessment (ADR-0029 section 4): what was tested, what each
    test gave and which one bound. ``pension`` is the household's total (a couple's both partners).

    ``income_test`` and ``assets_test`` are each test's pension, from 0 up to ``maximum``, rounded
    once. Rounding is monotone, so ``pension`` is exactly the lower of the two.
    """

    couple: bool
    homeowner: bool
    assets: Money
    financial_assets: Money
    other_income: Money
    #: Deemed income on ``financial_assets`` (a year), for display; ``other_income`` is separate.
    deemed_income: Money
    maximum: Money
    income_test: Money
    assets_test: Money
    bound: str
    pension: Money


def assess(
    params: AgePensionParams,
    *,
    couple: bool,
    homeowner: bool,
    assets: Money,
    financial_assets: Money,
    other_income: Money,
) -> AgePensionWorking:
    """The means test with its working. ``age_pension_annual`` is a thin wrapper over this.

    ``assets`` is the household's total assessable assets (the home excluded for a homeowner);
    ``financial_assets`` is the part of it that is deemed and must not exceed it; ``other_income``
    is annual assessable income apart from deeming. For a couple all three are combined.
    """
    currency = _check(params, assets, financial_assets, other_income)
    if financial_assets.amount_minor > assets.amount_minor:
        raise MoneyError("Financial assets are part of assessable assets, so cannot exceed them")
    maximum = maximum_annual(params, couple=couple).amount_minor

    # Income test, in cents times PPM (so the deeming rates stay exact integers).
    threshold = params.deeming_threshold_couple if couple else params.deeming_threshold_single
    below = min(financial_assets.amount_minor, threshold.amount_minor)
    above = financial_assets.amount_minor - below
    deemed = below * params.deeming_rate_lower.ppm + above * params.deeming_rate_upper.ppm
    income = other_income.amount_minor * PPM + deemed
    free = params.income_free_area_couple if couple else params.income_free_area_single
    excess_income = max(0, income - free.amount_minor * FORTNIGHTS_PER_YEAR * PPM)
    # cents * PPM * PPM
    by_income = maximum * PPM * PPM - params.income_taper.ppm * excess_income

    # Assets test, in cents times PPM.
    if homeowner:
        limit = (
            params.assets_threshold_homeowner_couple
            if couple
            else params.assets_threshold_homeowner_single
        )
    else:
        limit = (
            params.assets_threshold_non_homeowner_couple
            if couple
            else params.assets_threshold_non_homeowner_single
        )
    excess_assets = max(0, assets.amount_minor - limit.amount_minor)
    by_assets = (
        maximum * PPM - FORTNIGHTS_PER_YEAR * params.assets_taper.ppm * excess_assets
    ) * PPM

    cap = maximum * PPM * PPM
    income_test = round_div(max(0, min(by_income, cap)), PPM * PPM)
    assets_test = round_div(max(0, min(by_assets, cap)), PPM * PPM)
    if income_test == maximum and assets_test == maximum:
        bound = BOUND_NONE
    elif income_test <= assets_test:
        bound = BOUND_INCOME
    else:
        bound = BOUND_ASSETS
    return AgePensionWorking(
        couple,
        homeowner,
        assets,
        financial_assets,
        other_income,
        Money(round_div(deemed, PPM), currency),
        Money(maximum, currency),
        Money(income_test, currency),
        Money(assets_test, currency),
        bound,
        Money(min(income_test, assets_test), currency),
    )


def age_pension_annual(
    params: AgePensionParams,
    *,
    couple: bool,
    homeowner: bool,
    assets: Money,
    financial_assets: Money,
    other_income: Money,
) -> Money:
    """The Age Pension for a year: the lower of the income test and the assets test, never below
    zero and never above the maximum (see ``assess`` for the arguments and the working)."""
    return assess(
        params,
        couple=couple,
        homeowner=homeowner,
        assets=assets,
        financial_assets=financial_assets,
        other_income=other_income,
    ).pension


#: The pack keys the means test reads, and the ``AgePensionParams`` field each fills (ADR-0029
#: section 5). The caller reads the Rule pack; this module only maps keys to fields.
KEY_FIELDS: dict[str, str] = {
    "age_pension.max_basic.single": "max_basic_single",
    "age_pension.max_basic.couple_each": "max_basic_couple_each",
    "age_pension.pension_supplement.single": "pension_supplement_single",
    "age_pension.pension_supplement.couple_each": "pension_supplement_couple_each",
    "age_pension.income_free_area.single": "income_free_area_single",
    "age_pension.income_free_area.couple": "income_free_area_couple",
    "age_pension.income_taper": "income_taper",
    "age_pension.assets_threshold.homeowner_single": "assets_threshold_homeowner_single",
    "age_pension.assets_threshold.homeowner_couple": "assets_threshold_homeowner_couple",
    "age_pension.assets_threshold.non_homeowner_single": "assets_threshold_non_homeowner_single",
    "age_pension.assets_threshold.non_homeowner_couple": "assets_threshold_non_homeowner_couple",
    "age_pension.assets_taper": "assets_taper",
    "age_pension.deeming.threshold_single": "deeming_threshold_single",
    "age_pension.deeming.threshold_couple": "deeming_threshold_couple",
    "age_pension.deeming.rate_lower": "deeming_rate_lower",
    "age_pension.deeming.rate_upper": "deeming_rate_upper",
}


def params_from_values(values: Mapping[str, object]) -> AgePensionParams:
    """``AgePensionParams`` from pack key to value (``Money`` or ``Rate``). A key missing or of the
    wrong kind raises ``MoneyError`` naming it."""
    fields: dict[str, Any] = {}
    for key, name in KEY_FIELDS.items():
        if key not in values:
            raise MoneyError(f"The Age Pension figure {key!r} is missing")
        fields[name] = values[key]
    try:
        params = AgePensionParams(**fields)
    except TypeError as error:  # pragma: no cover - the keys above are the whole field set
        raise MoneyError(str(error)) from None
    for key, name in KEY_FIELDS.items():
        want = Rate if name in RATE_FIELDS else Money
        if not isinstance(opaque(getattr(params, name)), want):
            raise MoneyError(f"The Age Pension figure {key!r} must be a {want.__name__}")
    return params


RATE_FIELDS = frozenset(
    {"income_taper", "assets_taper", "deeming_rate_lower", "deeming_rate_upper"}
)
