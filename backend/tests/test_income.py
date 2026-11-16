"""Income streams in a projection (ADR-0017 s4 and s5): the pure flows.

Money maths, so: a hand-checked golden year of salary plus employer super, exact rounding (once,
half to even), and Hypothesis properties. Invented figures only.
"""

from __future__ import annotations

from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from finance360.capabilities.projection import income, life_events, rules
from finance360.capabilities.projection.income import RatesFor, Span, Stream
from finance360.core.money import Money
from finance360.core.rate import PPM, Rate
from finance360.core.rulepack import Age, RulePack

SETTINGS = settings(max_examples=200, deadline=None)
AS_AT = date(2026, 10, 25)


def aud(minor: int) -> Money:
    return Money(minor, "AUD")


def salary(gross: int, take_home: int, **over: object) -> Stream:
    fields: dict[str, object] = {
        "id": 1,
        "name": "Sample salary",
        "type_key": "salary",
        "member_id": 7,
        "gross_annual": aud(gross),
        "take_home_annual": aud(take_home),
        "credit_category": "cash",
    }
    return Stream(**(fields | over))  # type: ignore[arg-type]


def pct(text: str) -> Rate:
    return Rate.from_percent(text)


# --- golden: one hand-checked year of salary plus SG -----------------------------------------


def test_golden_year_zero_of_salary_and_super() -> None:
    # Gross 100,000.00, take-home 78,000.00, SG 12%, contributions tax 15%, no growth.
    # Employer super = 100,000.00 x 0.12 x 0.85 = 10,200.00 exactly.
    stream = salary(10_000_000, 7_800_000)
    got = income.flows(
        stream, Span(0, 1), Rate.zero(), 5, RatesFor([pct("12")] * 5, [pct("15")] * 5)
    )
    assert got == [
        income.Inflow(7, "cash", aud(7_800_000), 0, 1),
        income.Inflow(7, "superannuation", aud(1_020_000), 0, 1),
    ]


def test_golden_year_two_with_wage_growth_rounds_once() -> None:
    # Year 2 at 1.5% real growth: gross = 100,000.00 x 1.015^2 = 103,022.50 exactly, take-home =
    # 78,000.00 x 1.030225 = 80,357.55 exactly. Employer super = 103,022.50 x 0.12 x 0.85 =
    # 10,508.295, which is 1,050,829.5 minor units: a tie, rounded half to even once to 1,050,830.
    stream = salary(10_000_000, 7_800_000)
    got = income.flows(
        stream, Span(2, 3), pct("1.5"), 5, RatesFor([pct("12")] * 5, [pct("15")] * 5)
    )
    assert got == [
        income.Inflow(7, "cash", aud(8_035_755), 2, 3),
        income.Inflow(7, "superannuation", aud(1_050_830), 2, 3),
    ]


def test_super_is_one_rounding_of_the_whole_product_not_a_chain_of_roundings() -> None:
    # Gross 10,000.13 = 1,000,013 minor. In one go: x 0.12 x 0.85 = x 0.102 = 102,001.326, which
    # rounds to 102,001. Chained, rounding the SG first (120,001.56 -> 120,002), then the tax
    # (102,001.7 -> 102,002), is a minor unit out. The projection rounds once.
    got = income.super_contribution(aud(1_000_013), Rate.zero(), 0, pct("12"), pct("15"))
    assert got == aud(102_001)


def test_a_zero_sg_rate_or_a_full_contributions_tax_pays_no_super() -> None:
    gross = aud(10_000_000)
    assert income.super_contribution(gross, Rate.zero(), 0, pct("0"), pct("15")) == aud(0)
    assert income.super_contribution(gross, Rate.zero(), 0, pct("12"), pct("100")) == aud(0)


def test_super_rates_outside_zero_to_one_hundred_percent_are_rejected() -> None:
    import pytest

    gross = aud(10_000_000)
    for sg, tax in ((pct("-1"), pct("15")), (pct("12"), pct("-5"))):
        with pytest.raises(income.IncomeError):
            income.super_contribution(gross, Rate.zero(), 0, sg, tax)


def test_wage_growth_is_one_scale_from_the_base_never_compounded() -> None:
    # 100,000.00 at 3.3333% for 10 years: one scale of the base, compared with the exact integer.
    base = aud(10_000_000)
    rate = Rate(33_333)
    exact = (10_000_000 * (PPM + 33_333) ** 10) / PPM**10
    got = income.grown(base, rate, 10).amount_minor
    assert abs(got - exact) <= 0.5
    assert income.grown(base, Rate.zero(), 40) == base
    assert income.grown(base, rate, 0) == base


def test_the_registry_holds_salary_only_with_its_kind_and_indexation() -> None:
    assert set(income.TYPES) == {"salary"}
    kind = income.TYPES["salary"]
    assert (kind.tax_kind, kind.indexation, kind.employer_super) == (
        "employment",
        "real_wage_growth",
        True,
    )
    assert kind.tax_kind in income.TAX_KINDS


# --- the years a stream is in force ----------------------------------------------------------


def test_a_stream_with_no_start_begins_now_and_one_with_no_end_runs_to_the_horizon() -> None:
    stream = salary(1_000_000, 800_000)
    span = income.span_of(stream, {"retirement": 12}, AS_AT)
    assert span == Span(0, None)
    assert span.in_force(0) and span.in_force(500)


def test_a_dated_start_and_end_use_the_first_year_starting_on_or_after() -> None:
    stream = salary(1_000_000, 800_000, start_on=date(2028, 10, 25), end_on=date(2031, 3, 1))
    span = income.span_of(stream, {}, AS_AT)
    assert span == Span(2, 5)  # the 2028-10-25 anniversary is year 2; 2031-03-01 falls in year 4
    assert [t for t in range(8) if span.in_force(t)] == [2, 3, 4]


def test_a_stream_already_past_its_end_is_never_in_force() -> None:
    stream = salary(1_000_000, 800_000, end_event="retirement")
    span = income.span_of(stream, {"retirement": 0}, AS_AT)
    assert not span.in_force(0)
    assert income.flows(stream, span, Rate.zero(), 10, None) == []


# --- properties ------------------------------------------------------------------------------

money_minor = st.integers(min_value=1, max_value=10**11)
unit_rates = st.integers(min_value=0, max_value=PPM).map(Rate)
growths = st.integers(min_value=-300_000, max_value=300_000).map(Rate)


@SETTINGS
@given(money_minor, growths, st.integers(0, 60), unit_rates, unit_rates)
def test_contributions_never_exceed_the_gross_pay_of_the_year(
    gross: int, growth: Rate, t: int, sg: Rate, tax: Rate
) -> None:
    grown_gross = income.grown(aud(gross), growth, t)
    contribution = income.super_contribution(aud(gross), growth, t, sg, tax)
    assert 0 <= contribution.amount_minor <= grown_gross.amount_minor


@SETTINGS
@given(money_minor, growths, st.integers(0, 60), unit_rates, unit_rates)
def test_a_contribution_is_the_exact_product_rounded_once(
    gross: int, growth: Rate, t: int, sg: Rate, tax: Rate
) -> None:
    num = (PPM + growth.ppm) ** t * sg.ppm * (PPM - tax.ppm)
    den = PPM**t * PPM * PPM
    twice = 2 * gross * num
    quotient, remainder = divmod(twice, 2 * den)  # round half to even, in integers
    expected = quotient + (1 if remainder > den or (remainder == den and quotient % 2) else 0)
    got = income.super_contribution(aud(gross), growth, t, sg, tax)
    assert got.amount_minor == expected


def _pack(age: Age) -> RulePack:
    from finance360.core.rulepack import AgeRule

    fixed = [
        AgeRule("preservation_age", "p", "s", lambda _d: Age(60)),
        AgeRule("unrestricted_super_age", "u", "s", lambda _d: Age(65)),
        AgeRule("age_pension_age", "a", "s", lambda _d: age),
    ]
    return RulePack("t", "t", 1, (), {2027: {}}, tuple(fixed))


@SETTINGS
@given(
    st.dates(min_value=date(1950, 1, 1), max_value=date(2000, 12, 31)),
    st.integers(min_value=0, max_value=80),
)
def test_a_stream_ending_at_retirement_ends_in_the_members_retirement_year(
    born_on: date, retirement_age: int
) -> None:
    used = rules.member_ages(_pack(Age(67)), [(7, born_on)], {})
    events = life_events.resolve([(7, born_on)], {7: retirement_age}, used, AS_AT)
    retirement_year = life_events.member_years(events)[7][life_events.RETIREMENT]
    stream = salary(1_000_000, 800_000, end_event="retirement")
    span = income.span_of(stream, life_events.member_years(events)[7], AS_AT)
    assert span.until_year == retirement_year
    horizon = 130
    flows = income.flows(stream, span, Rate.zero(), horizon, None)
    # Credited in every year before retirement and in none from it on.
    assert sorted(f.from_year for f in flows) == list(range(0, min(retirement_year, horizon)))
