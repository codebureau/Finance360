"""The tax estimate engine: golden answers, family tests, the unmodelled report and properties.

The rules are INVENTED (``tax_support``), so every expected figure below was worked out by hand from
those invented rules. Nothing here asserts an official rate, threshold or cap.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tax_support import FY, LEAP_FY, aud, invented_rules, member, pct, tax_input

from finance360.capabilities.tax import engine, labels
from finance360.capabilities.tax.engine import (
    TAX_ENGINE_VERSION,
    bracket_tax,
    estimate,
    help_repayment,
    low_income_offset,
    medicare_levy,
    net_capital_gain,
)
from finance360.capabilities.tax.input import TAX_INPUT_VERSION, MemberInput, days_in_fy
from finance360.capabilities.tax.rules import Band
from finance360.core.money import Money

#: Bump together with ``TAX_ENGINE_VERSION`` whenever the engine returns something different for the
#: same input (ADR-0015 s3). The goldens below were worked out with this version.
GOLDEN_ENGINE_VERSION = 5


def test_golden_answers_were_computed_with_this_engine_version() -> None:
    assert GOLDEN_ENGINE_VERSION == TAX_ENGINE_VERSION
    result = estimate(tax_input(member(1, salary="50000")))
    assert (result.engine_version, result.input_version) == (TAX_ENGINE_VERSION, TAX_INPUT_VERSION)


# --- a couple: bracket tax, LITO, Medicare shade-in, refund and amount payable ----------------


def test_a_couple_golden() -> None:
    result = estimate(
        tax_input(
            member(1, salary="80000", tax_withheld="14000"),
            member(2, salary="28000", tax_withheld="1000"),
        )
    )
    a, b = result.members
    # A: 29,000 at 7% = 2,030 and 39,000 at 22% = 8,580. The offset has tapered to nothing.
    assert (a.taxable_income, a.tax_on_taxable_income) == (aud("80000"), aud("10610"))
    assert a.low_income_offset == aud("0") and a.offsets == aud("0")
    assert a.medicare_levy == aud("2400")  # 3% of 80,000, below the shade-in
    assert a.total_tax == aud("13010") and a.net_outcome == aud("990")
    # B: 16,000 at 7% = 1,120; offset 450 (income below its first taper); levy shaded in:
    # 9% of 8,000 over the lower threshold = 720, below the full 840.
    assert b.tax_on_taxable_income == aud("1120")
    assert (b.low_income_offset, b.offsets) == (aud("450"), aud("450"))
    assert b.medicare_levy == aud("720")
    assert b.total_tax == aud("1390") and b.net_outcome == aud("-390")
    house = result.household
    assert house.taxable_income == aud("108000")
    assert (house.total_tax, house.credits, house.net_outcome) == (
        aud("14400"),
        aud("15000"),
        aud("600"),
    )
    assert (house.tax_on_taxable_income, house.offsets) == (aud("11730"), aud("450"))
    assert (house.medicare_levy, house.medicare_levy_surcharge) == (aud("3120"), aud("0"))
    assert house.help_repayment == aud("0")
    assert house.surcharge_thresholds == engine.FAMILY and house.surcharge_tier == 0
    assert not result.incomplete and result.unmodelled == ()


# --- a single Member: surcharge tier, HELP, exempt days ----------------------------------------


def single_high_income(**more: str | int | bool) -> engine.MemberResult:
    totals: dict[str, str | int | bool] = {
        "salary": "100000",
        "reportable_fringe_benefits": "10000",
        "help_debt": True,
        "tax_withheld": "25000",
    }
    totals.update(more)
    return estimate(tax_input(member(1, **totals))).members[0]


def test_surcharge_help_and_levy_golden() -> None:
    m = single_high_income()
    # 29,000 at 7% + 52,000 at 22% + 7,000 at 37% = 2,030 + 11,440 + 2,590
    assert m.tax_on_taxable_income == aud("16060")
    assert m.medicare_levy == aud("3000")
    # Surcharge income 110,000 is in the second single tier (1.2%), charged on taxable income plus
    # the fringe benefits amount: 1.2% of 110,000.
    assert (m.medicare_levy_surcharge, m.surcharge_rate) == (aud("1320"), pct("1.2"))
    assert m.help_repayment == aud("7700")  # 7% of 110,000, above the third threshold
    assert m.total_tax == aud("28080") and m.net_outcome == aud("-3080")


def test_exempt_days_apportion_the_surcharge_by_the_days_liable() -> None:
    # 132,000.00 in cents x 265 / 365, rounded to the nearest cent
    m = single_high_income(surcharge_exempt_days=100)
    assert m.medicare_levy_surcharge == aud("958.36")
    assert single_high_income(surcharge_exempt_days=365).medicare_levy_surcharge == aud("0")
    assert single_high_income(surcharge_exempt_days=365).surcharge_rate == pct("0")


def test_a_leap_year_has_366_days_to_apportion() -> None:
    assert (days_in_fy(FY), days_in_fy(LEAP_FY)) == (365, 366)
    rules = invented_rules(LEAP_FY)
    one = estimate(
        tax_input(
            member(
                1, salary="100000", reportable_fringe_benefits="10000", surcharge_exempt_days=183
            ),
            fy=LEAP_FY,
            rules=rules,
        )
    )
    # 183 of 366 days exempt is exactly half of 1,320.
    assert one.members[0].medicare_levy_surcharge == aud("660")


def test_help_repayment_steps() -> None:
    rules = invented_rules()
    assert help_repayment(aud("50000"), rules) == aud("0")
    assert help_repayment(aud("55000"), rules) == aud("100")  # 2% of 5,000
    assert help_repayment(aud("70000"), rules) == aud("600")  # 2% of 10,000 + 4% of 10,000
    assert help_repayment(aud("80000"), rules) == aud("1000")  # 2% of 10,000 + 4% of 20,000
    assert help_repayment(aud("80000.01"), rules) == aud("5600.00")  # 7% of everything, a cliff


# --- capital gains, business, instant deduction and the notes ---------------------------------


def test_gains_business_instant_deduction_and_offset_golden() -> None:
    m = estimate(
        tax_input(
            member(
                1,
                salary="50000.99",
                gains_discountable="10000",
                gains_other="2000",
                capital_losses="3000",
                business_income="20000",
                business_expenses="8000",
                sbito_eligible=True,
                work_car="300",
                use_instant_deduction=True,
            )
        )
    ).members[0]
    # Losses use the 2,000 of other gains first, then 1,000 of the discountable gains; the 9,000
    # left is taxed at 60% (a 40% discount): 5,400.
    # Income 50,000 (the 0.99 is dropped, as on the return) + 5,400 + 12,000, less the instant
    # deduction of 800 in place of 300 of car expenses: 66,600.
    assert m.total_income == aud("67400") and m.total_deductions == aud("800")
    assert m.taxable_income == aud("66600")
    assert m.tax_on_taxable_income == aud("7662")  # 2,030 + 25,600 at 22%
    # The offset: the share of tax on business income (12,000 of 66,600) at 12%.
    assert m.small_business_offset == aud("165.66")
    assert m.low_income_offset == aud("0") and m.offsets == aud("165.66")
    assert m.medicare_levy == aud("1998")
    assert m.total_tax == aud("9494.34")
    assert "Instant work-related expenses deduction used in place of D1 to D6." in m.notes


def test_a_capital_loss_beyond_the_gains_is_carried_forward_and_noted() -> None:
    gain = net_capital_gain(aud("1000"), aud("500"), aud("2000"), invented_rules())
    assert gain.net == aud("0") and gain.loss_carried_forward == aud("500")
    m = estimate(
        tax_input(member(1, salary="20000", gains_other="500", capital_losses="2000"))
    ).members[0]
    assert "Capital loss of 1500.00 carried forward." in m.notes


def test_a_business_loss_is_deferred_unless_the_loss_rules_are_met() -> None:
    deferred = estimate(tax_input(member(1, salary="30000", business_expenses="4000"))).members[0]
    assert deferred.taxable_income == aud("30000")
    assert any("deferred" in n for n in deferred.notes)
    allowed = estimate(
        tax_input(member(1, salary="30000", business_expenses="4000", business_loss_rules_met=True))
    ).members[0]
    assert allowed.taxable_income == aud("26000")


def test_the_instant_deduction_needs_a_year_that_has_one_and_work_expenses_below_it() -> None:
    base = {"salary": "30000", "work_car": "300", "use_instant_deduction": True}
    no_instant = estimate(tax_input(member(1, **base), rules=invented_rules(instant=None))).members[
        0
    ]
    assert no_instant.total_deductions == aud("300")
    above = estimate(tax_input(member(1, **{**base, "work_car": "900"}))).members[0]
    assert above.total_deductions == aud("900")


def test_a_deduction_beyond_income_is_a_tax_loss_and_taxable_income_is_zero() -> None:
    m = estimate(tax_input(member(1, salary="1000", other_deductions="1500.50"))).members[0]
    assert m.taxable_income == aud("0")
    assert "Tax loss of 500.00 carried forward." in m.notes


# --- a managed fund statement: the franking credit is already inside the franked amount (#53) ---


def test_a_managed_fund_statement_entered_as_printed_counts_the_franking_credit_once() -> None:
    # Statement items 13U 40.00 and 13C 120.00 (the gross-up is inside it), 13Q 36.00.
    m = estimate(tax_input(member(1, trust_income="160", trust_franking_credits="36"))).members[0]
    assert m.total_income == aud("160")  # not 196
    assert m.taxable_income == aud("160")
    assert m.franking_credits == aud("36") and m.credits == aud("36")


def test_direct_dividends_still_add_the_franking_credit_to_income() -> None:
    # Item 11: the franked amount excludes the credit, so the gross-up is added.
    m = estimate(
        tax_input(
            member(1, dividends_unfranked="40", dividends_franked="70", franking_credits="30")
        )
    ).members[0]
    assert m.total_income == aud("140")
    assert m.franking_credits == aud("30") and m.credits == aud("30")


# --- the return drops the cents of each income and deduction label first (#55) ----------------


def test_cents_are_dropped_from_each_label_before_adding_up() -> None:
    # Labels 100.60 + 200.70 less a deduction of 50.90: the return gives 100 + 200 - 50.
    first = estimate(
        tax_input(member(1, salary="100.60", other_income="200.70", other_deductions="50.90"))
    ).members[0]
    assert first.taxable_income == aud("250")
    # The engine used to add in cents (250.40) and round once, so a smaller deduction showed it:
    second = estimate(
        tax_input(member(1, salary="100.60", other_income="200.70", other_deductions="50.20"))
    ).members[0]
    assert second.taxable_income == aud("250")  # 100 + 200 - 50; adding in cents gave 251
    assert (second.total_income, second.total_deductions) == (aud("300"), aud("50"))


def test_cents_are_dropped_per_label_in_rent_and_business_too() -> None:
    m = estimate(
        tax_input(
            member(
                1,
                rent_gross="1000.90",
                rent_interest="300.90",
                rent_other="100.90",
                business_income="500.50",
                business_expenses="200.50",
                work_other="10.99",
                interest_deductions="20.99",
            )
        )
    ).members[0]
    # rent 1000 - 300 - 100 = 600; business 500 - 200 = 300; deductions 10 + 20
    assert m.total_income == aud("900") and m.total_deductions == aud("30")
    assert m.taxable_income == aud("870")


def test_the_net_capital_gain_is_the_label_that_drops_its_cents() -> None:
    # 1,000.99 discountable, discount 40%: 600.594 -> 600.59 net; the return shows 600.
    m = estimate(tax_input(member(1, gains_discountable="1000.99"))).members[0]
    assert m.total_income == aud("600")


def test_credits_keep_their_cents() -> None:
    m = estimate(
        tax_input(
            member(
                1,
                salary="50000.99",
                tax_withheld="9000.45",
                tfn_withheld="1.11",
                payg_instalments="200.25",
                franking_credits="30.30",
                trust_franking_credits="6.06",
            )
        )
    ).members[0]
    assert m.tax_withheld == aud("9001.56")
    assert m.payg_instalments == aud("200.25")
    assert m.franking_credits == aud("36.36")
    assert m.credits == aud("9238.17")


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.integers(0, 50_000_000), min_size=2, max_size=2),
    st.lists(st.integers(0, 5_000_000), min_size=2, max_size=2),
)
def test_taxable_income_is_the_whole_dollar_labels_added_and_subtracted(
    incomes: list[int], deductions: list[int]
) -> None:
    def text(cents: int) -> str:
        return f"{cents // 100}.{cents % 100:02d}"

    m = estimate(
        tax_input(
            member(
                1,
                salary=text(incomes[0]),
                other_income=text(incomes[1]),
                work_other=text(deductions[0]),
                other_deductions=text(deductions[1]),
            )
        )
    ).members[0]
    whole = sum(c // 100 for c in incomes) - sum(c // 100 for c in deductions)
    assert m.taxable_income == aud(str(max(0, whole)))


# --- family tests -------------------------------------------------------------------------


def test_dependants_raise_the_family_surcharge_thresholds_after_the_first_child() -> None:
    # 90,000 + 90,000 surcharge income is under the family first tier (180,000) only by 0; push over
    couple = (
        member(1, salary="100000", reportable_fringe_benefits="0"),
        member(2, salary="85000"),
    )
    over = estimate(tax_input(*couple))
    assert over.household.surcharge_tier == 1  # 185,000 is above 180,000
    with_children = estimate(tax_input(*couple, dependants=5))
    # four children after the first lift each tier by 4 x 1,500 = 6,000: 186,000 is not exceeded
    assert with_children.household.surcharge_tier == 0
    one_child = estimate(tax_input(*couple, dependants=1))
    assert one_child.household.surcharge_tier == 1  # the first child adds nothing


def test_a_lone_parent_uses_the_family_tests() -> None:
    result = estimate(tax_input(member(1, salary="30000"), dependants=2))
    assert result.household.surcharge_thresholds == engine.FAMILY


def test_family_levy_reduction_applies_when_family_income_is_low() -> None:
    rules = invented_rules()
    # Family income 40,000 against a threshold of 33,000 plus 3,000 a child (two children: 39,000):
    # reduction = 3% of 39,000 - 6% of 1,000 = 1,110; levy = full - reduction x (own / family).
    levy = medicare_levy(aud("25000"), aud("40000"), 2, rules)
    full = aud("750")
    # 1,110.00 is 111,000 cents; scaled by 25,000 / 40,000
    assert levy == full - Money(round_half_away_check(111000 * 25000, 40000), "AUD")
    assert levy == aud("750") - aud("693.75")
    # Family income at or below the threshold pays no levy
    assert medicare_levy(aud("25000"), aud("39000"), 2, rules) == aud("0")


def round_half_away_check(numerator: int, denominator: int) -> int:
    """Independent half-away rounding with fractions (not the code under test)."""
    from fractions import Fraction

    q = Fraction(numerator, denominator)
    whole = int(abs(q) + Fraction(1, 2))
    return whole if q >= 0 else -whole


def test_no_surcharge_for_a_partner_at_or_below_the_low_income_threshold() -> None:
    result = estimate(tax_input(member(1, salary="200000"), member(2, salary="15000")))
    high, low = result.members
    # Household surcharge income 215,000 is in the second family tier (1.2%).
    assert result.household.surcharge_tier == 2
    assert high.medicare_levy_surcharge == aud("2400") and high.surcharge_rate == pct("1.2")
    assert low.medicare_levy_surcharge == aud("0") and low.surcharge_rate == pct("0")
    assert "No surcharge: own income at or below the low-income threshold." in low.notes


def test_offsets_never_take_the_tax_below_zero() -> None:
    m = estimate(tax_input(member(1, salary="12500"))).members[0]
    assert m.offsets <= m.tax_on_taxable_income
    assert m.low_income_offset == aud("450")
    assert m.offsets == aud("35")  # 7% of 500
    assert m.total_tax >= aud("0")


def test_the_levy_has_a_shade_in_and_never_exceeds_the_full_levy() -> None:
    rules = invented_rules()
    assert medicare_levy(aud("20000"), None, 0, rules) == aud("0")
    assert medicare_levy(aud("21000"), None, 0, rules) == aud("90")  # 9% of 1,000
    assert medicare_levy(aud("30000"), None, 0, rules) == aud("900")  # the full 3%


# --- the rules in isolation --------------------------------------------------------------


def test_bracket_tax_is_zero_at_each_threshold_and_adds_each_band() -> None:
    bands = invented_rules().resident_brackets
    assert bracket_tax(aud("0"), bands) == aud("0")
    assert bracket_tax(aud("12000"), bands) == aud("0")
    assert bracket_tax(aud("12001"), bands) == aud("0.07")
    assert bracket_tax(aud("160000"), bands) == aud("2030") + aud("11440") + aud("24790")
    assert bracket_tax(aud("200000"), bands) == aud("38260") + aud("20400")


def test_the_low_income_offset_tapers_at_two_rates() -> None:
    rules = invented_rules()
    assert low_income_offset(aud("30000"), rules) == aud("450")
    assert low_income_offset(aud("38000"), rules) == aud("210")  # 3% of 8,000 gone
    assert low_income_offset(aud("48000"), rules) == aud("110")  # then 1% of 10,000
    assert low_income_offset(aud("59000"), rules) == aud("0")
    assert low_income_offset(aud("90000"), rules) == aud("0")


# --- foreign income and the foreign income tax offset (#54) ------------------------------------
# The invented rules cap the offset at 75.00.


def test_foreign_source_income_is_added_to_income() -> None:
    base = estimate(tax_input(member(1, salary="30000"))).members[0]
    foreign = estimate(tax_input(member(1, salary="30000", foreign_source_income="50.00"))).members[
        0
    ]
    assert foreign.total_income == base.total_income + aud("50.00")
    assert foreign.taxable_income == base.taxable_income + aud("50")
    # whole dollars per income label, like the others
    cents = estimate(tax_input(member(1, salary="30000", foreign_source_income="50.90"))).members[0]
    assert cents.total_income == base.total_income + aud("50")


def test_a_foreign_income_tax_offset_reduces_tax_by_exactly_that_amount() -> None:
    base = estimate(tax_input(member(1, salary="60000"))).members[0]
    got = estimate(tax_input(member(1, salary="60000", foreign_tax_offset="6.00"))).members[0]
    assert got.offsets == base.offsets + aud("6.00")
    assert got.total_tax == base.total_tax - aud("6.00")
    assert got.net_outcome == base.net_outcome + aud("6.00")
    assert got.low_income_offset == base.low_income_offset  # its own figure is untouched


def test_the_foreign_offset_keeps_its_cents() -> None:
    base = estimate(tax_input(member(1, salary="60000"))).members[0]
    got = estimate(tax_input(member(1, salary="60000", foreign_tax_offset="6.07"))).members[0]
    assert got.total_tax == base.total_tax - aud("6.07")


def test_an_offset_within_the_cap_is_complete_and_one_above_is_limited_and_reported() -> None:
    at_cap = estimate(tax_input(member(1, salary="60000", foreign_tax_offset="75.00")))
    assert not at_cap.incomplete and at_cap.unmodelled == ()
    base = estimate(tax_input(member(1, salary="60000"))).members[0]

    above = estimate(tax_input(member(1, salary="60000", foreign_tax_offset="80.00")))
    assert above.members[0].offsets == base.offsets + aud("75.00")  # the cap, not 80
    assert above.incomplete
    assert [(u.member_id, u.label_key, u.value) for u in above.unmodelled] == [
        (1, "foreign_tax_offset", aud("80.00"))
    ]
    assert any("claimed" in n and "full limit" in n for n in above.members[0].notes)


def test_a_year_without_a_cap_figure_claims_nothing_and_reports_the_offset() -> None:
    rules = replace(invented_rules(), foreign_offset_cap=None)
    base = estimate(tax_input(member(1, salary="60000"), rules=rules)).members[0]
    result = estimate(tax_input(member(1, salary="60000", foreign_tax_offset="6.00"), rules=rules))
    assert result.members[0].offsets == base.offsets
    assert result.incomplete and [u.label_key for u in result.unmodelled] == ["foreign_tax_offset"]


def test_the_foreign_offset_is_non_refundable() -> None:
    # Tax on 18,800 is 476.00; the low income offset (450.00) takes most, leaving 26.00. A 40.00
    # foreign offset can only reduce what is left.
    got = estimate(tax_input(member(1, salary="18800", foreign_tax_offset="40.00"))).members[0]
    assert got.tax_on_taxable_income == aud("476.00")
    assert got.offsets == aud("476.00")  # not 490.00
    # and where the tax is nil, nothing is refunded
    low = estimate(tax_input(member(1, salary="5000", foreign_tax_offset="40.00"))).members[0]
    assert low.offsets == low.tax_on_taxable_income == aud("0")
    assert low.total_tax >= aud("0")


@settings(max_examples=60, deadline=None)
@given(st.integers(0, 200_000_00), st.integers(0, 30_000_00))
def test_the_foreign_offset_never_exceeds_the_cap_or_the_tax(salary: int, offset: int) -> None:
    def text(minor: int) -> str:
        return f"{minor // 100}.{minor % 100:02d}"

    base = estimate(tax_input(member(1, salary=text(salary)))).members[0]
    got = estimate(
        tax_input(member(1, salary=text(salary), foreign_tax_offset=text(offset)))
    ).members[0]
    extra = got.offsets.amount_minor - base.offsets.amount_minor
    assert 0 <= extra <= min(offset, 75_00)
    assert got.offsets <= got.tax_on_taxable_income
    assert got.total_tax.amount_minor == base.total_tax.amount_minor - extra


# --- unmodelled labels and provisional markers -------------------------------------------


def test_a_figure_in_an_unmodelled_label_is_listed_and_marks_the_estimate_incomplete() -> None:
    result = estimate(
        tax_input(
            member(1, salary="50000", government_allowances="1000", seniors_pensioners_offset="25"),
            member(2, salary="40000", life_insurance_bonuses="0"),
        )
    )
    assert result.incomplete
    assert [(u.member_id, u.label_key, u.value) for u in result.unmodelled] == [
        (1, "government_allowances", aud("1000")),
        (1, "seniors_pensioners_offset", aud("25")),
    ]
    # a zero figure is no figure
    assert not estimate(tax_input(member(1, salary="1", employer_lump_sum="0"))).incomplete


def test_every_unmodelled_label_is_reported_when_it_has_a_figure() -> None:
    for label in labels.all_labels():
        if label.modelled or label.direction == labels.MEMO:  # a memo label is on no return
            continue
        result = estimate(tax_input(member(1, salary="1000", **{label.key: "1.00"})))
        assert result.incomplete, label.key
        assert [u.label_key for u in result.unmodelled] == [label.key]


def test_the_engine_reads_every_modelled_label_and_no_unmodelled_one() -> None:
    """A modelled label with a figure changes the result; an unmodelled one never does."""
    # A HELP debt at a surcharge-liable income, so the fringe and super figures that only feed
    # those tests also show.
    fixed: dict[str, str | int | bool] = {"salary": "100000", "help_debt": True}
    base = estimate(tax_input(member(1, **fixed))).members[0]
    for label in labels.all_labels():
        if label.kind != labels.MONEY or label.key == "salary":
            continue
        changed = estimate(tax_input(member(1, **fixed, **{label.key: "1000.00"}))).members[0]
        if label.modelled:
            assert changed != base, f"{label.key} is modelled but changes nothing"
        else:
            assert changed == base, f"{label.key} is not modelled but changes the result"


def test_provisional_markers_are_carried_to_the_result() -> None:
    rules = invented_rules(provisional=(("tax.help.t1", "announced, not yet law"),))
    result = estimate(tax_input(member(1, salary="50000"), rules=rules))
    assert result.has_provisional
    assert result.provisional == (("tax.help.t1", "announced, not yet law"),)
    assert not estimate(tax_input(member(1, salary="50000"))).has_provisional


# --- properties --------------------------------------------------------------------------

amounts = st.integers(0, 5_000_000_00).map(lambda minor: Money(minor, "AUD"))
bool_or_days = st.booleans()


@st.composite
def members_strategy(draw: st.DrawFn, member_id: int) -> MemberInput:
    totals: list[tuple[str, Money | int | bool]] = []
    for key in (
        "salary",
        "interest",
        "dividends_franked",
        "franking_credits",
        "gains_discountable",
        "gains_other",
        "capital_losses",
        "rent_gross",
        "rent_interest",
        "business_income",
        "business_expenses",
        "work_other",
        "other_deductions",
        "reportable_fringe_benefits",
        "tax_withheld",
        "payg_instalments",
    ):
        totals.append((key, draw(amounts)))
    totals.append(("help_debt", draw(bool_or_days)))
    totals.append(("sbito_eligible", draw(bool_or_days)))
    totals.append(("surcharge_exempt_days", draw(st.integers(0, 365))))
    return MemberInput(member_id, date(1980, 1, 1), tuple(totals))


@settings(max_examples=200, deadline=None)
@given(
    members_strategy(1),
    members_strategy(2),
    st.integers(0, 6),
    st.booleans(),
)
def test_the_household_is_the_exact_sum_of_its_members_and_nothing_goes_negative(
    a: MemberInput, b: MemberInput, dependants: int, couple: bool
) -> None:
    result = estimate(tax_input(*((a, b) if couple else (a,)), dependants=dependants))
    house = result.household
    members = result.members
    assert house.total_tax.amount_minor == sum(m.total_tax.amount_minor for m in members)
    assert house.credits.amount_minor == sum(m.credits.amount_minor for m in members)
    assert house.net_outcome.amount_minor == sum(m.net_outcome.amount_minor for m in members)
    assert house.taxable_income.amount_minor == sum(m.taxable_income.amount_minor for m in members)
    for m in members:
        assert m.net_outcome.amount_minor == m.credits.amount_minor - m.total_tax.amount_minor
        assert m.taxable_income.amount_minor % 100 == 0  # whole dollars
        assert m.taxable_income.amount_minor >= 0
        assert 0 <= m.offsets.amount_minor <= m.tax_on_taxable_income.amount_minor
        assert m.medicare_levy.amount_minor >= 0 and m.medicare_levy_surcharge.amount_minor >= 0
        assert m.total_tax.amount_minor == (
            m.tax_on_taxable_income.amount_minor
            - m.offsets.amount_minor
            + m.medicare_levy.amount_minor
            + m.medicare_levy_surcharge.amount_minor
            + m.help_repayment.amount_minor
        )


@settings(max_examples=100, deadline=None)
@given(st.integers(0, 3_000_000_00))
def test_tax_on_taxable_income_never_falls_as_income_rises(minor: int) -> None:
    bands = invented_rules().resident_brackets
    assert bracket_tax(Money(minor + 100, "AUD"), bands) >= bracket_tax(Money(minor, "AUD"), bands)


def test_a_two_member_estimate_does_not_depend_on_the_order_members_are_given() -> None:
    a, b = member(1, salary="70000"), member(2, salary="35000", help_debt=True)
    assert estimate(tax_input(a, b)) == estimate(tax_input(b, a))


def test_the_input_is_not_changed_by_estimating() -> None:
    inp = tax_input(member(1, salary="70000"), member(2, salary="35000"))
    before = inp
    estimate(inp)
    assert inp == before
    assert inp.rules == invented_rules()


def test_a_pure_engine_cannot_be_given_a_float() -> None:
    with pytest.raises(TypeError):
        Money(1.5, "AUD")  # type: ignore[arg-type]


# --- a half cent goes to the even cent, step by step (issue #61) ---------------------------------
#
# Each figure below is INVENTED so that the exact answer lands on a half cent: one case settles up
# and one settles down, so a rule of "always up" or "always down" fails one of the pair.


def cents(money: Money) -> int:
    return money.amount_minor


def test_half_cent_low_income_offset_goes_to_the_even_cent() -> None:
    rules = replace(invented_rules(), lito_taper2_rate=pct("1.5"))
    # 450 - 3% of 8,000 (240) - 1.5% of 1 (0.015) = 209.985 -> 209.98
    assert cents(low_income_offset(aud("38001"), rules)) == 20998
    # ... - 1.5% of 3 (0.045) = 209.955 -> 209.96
    assert cents(low_income_offset(aud("38003"), rules)) == 20996


def test_half_cent_medicare_levy_goes_to_the_even_cent() -> None:
    rules = replace(invented_rules(), medicare_levy_rate=pct("2.5"))
    assert cents(medicare_levy(aud("30001"), None, 0, rules)) == 75002  # 750.025
    assert cents(medicare_levy(aud("30003"), None, 0, rules)) == 75008  # 750.075


def test_half_cent_surcharge_goes_to_the_even_cent() -> None:
    rules = replace(invented_rules(), mls_rate_tier1=pct("0.75"))
    for salary, expected in (("90002", 67502), ("90006", 67504)):  # 675.015 and 675.045
        member_result = estimate(tax_input(member(1, salary=salary), rules=rules)).members[0]
        assert cents(member_result.medicare_levy_surcharge) == expected


def test_half_cent_help_repayment_goes_to_the_even_cent() -> None:
    rules = replace(invented_rules(), help_r1=pct("0.5"))
    assert cents(help_repayment(aud("50001"), rules)) == 0  # 0.005
    assert cents(help_repayment(aud("50003"), rules)) == 2  # 0.015


def test_half_cent_bracket_tax_goes_to_the_even_cent() -> None:
    bands = (Band(aud("0"), pct("0.5")),)
    assert cents(bracket_tax(aud("1"), bands)) == 0  # 0.005
    assert cents(bracket_tax(aud("3"), bands)) == 2  # 0.015
