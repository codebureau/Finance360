"""The Age Pension in the projection engine (ADR-0029 sections 2 to 6): golden answers worked by
hand, the opening-balance timing, couples, and Hypothesis invariants at the engine level.

Every figure is invented or the same published rates ``test_age_pension.py`` uses (FY 2026-27, the
payment rates reported from 20 September 2026). No published Services Australia worked example was
found to cite (their pages block automated reads), so each answer is worked from the rules in its
comment. Replace a case with a published one when the household finds one.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance360.capabilities.projection import input as pinput
from finance360.capabilities.projection.age_pension import AgePensionParams, assess
from finance360.capabilities.projection.engine import ProjectionResult, project
from finance360.capabilities.projection.input import (
    INPUT_VERSION,
    AgePensionInput,
    AgePensionMember,
    AgePensionSpan,
    Inflow,
    InputError,
    Line,
    MemberRef,
    Outflow,
    ProjectionInput,
)
from finance360.core.money import Money, parse_amount
from finance360.core.rate import Rate

AUD = "AUD"
AS_AT = date(2026, 1, 1)
ORDER = ("cash", "investments", "superannuation", "overseas_pensions")


def aud(text: str) -> Money:
    return parse_amount(text, AUD)


def minor(amount: int) -> Money:
    return Money(amount, AUD)


P = AgePensionParams(
    max_basic_single=aud("1135.40"),
    max_basic_couple_each=aud("855.90"),
    pension_supplement_single=aud("88.20"),
    pension_supplement_couple_each=aud("66.50"),
    income_free_area_single=aud("226.00"),
    income_free_area_couple=aud("396.00"),
    income_taper=Rate.from_percent("50"),
    assets_threshold_homeowner_single=aud("333000"),
    assets_threshold_homeowner_couple=aud("499000"),
    assets_threshold_non_homeowner_single=aud("600000"),
    assets_threshold_non_homeowner_couple=aud("766000"),
    assets_taper=Rate.from_percent("0.3"),
    deeming_threshold_single=aud("66800"),
    deeming_threshold_couple=aud("110600"),
    deeming_rate_lower=Rate.from_percent("1.75"),
    deeming_rate_upper=Rate.from_percent("3.75"),
)
ZERO = minor(0)
SINGLE_MAX = 3_181_360  # (1135.40 + 88.20) x 26 = 31,813.60
COUPLE_MAX = 4_796_480  # 2 x (855.90 + 66.50) x 26 = 47,964.80


def pension_input(
    *,
    years: int,
    members: list[tuple[int, int]],
    couple: bool = False,
    homeowner: bool = True,
    other_income: int = 0,
    property_assets: int = 0,
    vehicle_assets: int = 0,
    params: AgePensionParams = P,
) -> AgePensionInput:
    """``members`` is ``(member_id, eligible_from_year)``."""
    return AgePensionInput(
        couple=couple,
        homeowner=homeowner,
        members=tuple(AgePensionMember(m, y) for m, y in members),
        other_income=(minor(other_income),) * years,
        property_assets=(minor(property_assets),) * years,
        vehicle_assets=(minor(vehicle_assets),) * years,
        spans=(AgePensionSpan(0, params),),
    )


def make(
    *,
    lines: list[Line],
    years: int,
    pension: AgePensionInput | None,
    members: list[MemberRef] | None = None,
    inflation: int = 0,
    inflows: list[Inflow] | None = None,
    outflows: list[Outflow] | None = None,
) -> ProjectionInput:
    return ProjectionInput(
        input_version=INPUT_VERSION,
        as_at=AS_AT,
        currency=AUD,
        members=tuple(members or [MemberRef(1, date(1960, 1, 1))]),
        years=years,
        inflation=Rate(inflation),
        lines=tuple(lines),
        inflows=tuple(inflows or []),
        outflows=tuple(outflows or []),
        drawdown_order=ORDER,
        age_pension=pension,
    )


def single_lines(cash: int, *, rate: int = 0) -> list[Line]:
    return [Line(1, "cash", minor(cash), Rate(rate), 0)]


def paid_to(result: ProjectionResult, t: int, member_id: int) -> int:
    year = result.years[t].age_pension
    assert year is not None
    return dict(year.paid)[member_id].amount_minor


# --- golden: a single homeowner, worked by hand ---------------------------------------------


def test_golden_single_homeowner_paid_from_the_eligible_year_on_the_opening_balance() -> None:
    # 200,000.00 in cash at 0% return, 0% inflation, no spending, eligible from year 1.
    # Year 0: nobody is eligible, so no assessment (age_pension is None).
    # Year 1 opens with 20,000,000 cents. Financial = assets = 200,000.00 (home exempt).
    #   Assets test: single homeowner limit 333,000.00 not exceeded -> the maximum, 31,813.60.
    #   Deemed income: 66,800.00 x 1.75% = 1,169.00; (200,000 - 66,800) = 133,200.00 x 3.75%
    #     = 4,995.00; total 6,164.00. Free area 226.00 x 26 = 5,876.00; excess 288.00; x 50% =
    #     144.00 less. Income test: 31,813.60 - 144.00 = 31,669.60. The income test binds.
    #   Credited to cash: 3,166,960 cents, so year 1 closes at 20,000,000 + 3,166,960.
    # Year 2 opens with 23,166,960 cents (231,669.60): deemed 1,169.00 + 164,869.60 x 3.75% =
    #   7,351.61 (735,161 cents); excess 1,475.61; x 50% = 737.805 -> 73,780.5 cents less than
    #   3,181,360 = 3,107,579.5, a tie, half to even -> 3,107,580.
    result = project(
        make(
            lines=single_lines(20_000_000),
            years=3,
            pension=pension_input(years=3, members=[(1, 1)]),
        )
    )
    assert result.years[0].age_pension is None
    y1 = result.years[1].age_pension
    assert y1 is not None
    assert y1.paid == ((1, minor(3_166_960)),)
    assert (y1.assets, y1.financial_assets) == (minor(20_000_000), minor(20_000_000))
    assert (y1.deemed_income, y1.other_income) == (minor(616_400), ZERO)
    assert (y1.maximum, y1.income_test, y1.assets_test) == (
        minor(SINGLE_MAX),
        minor(3_166_960),
        minor(SINGLE_MAX),
    )
    assert (y1.bound, y1.total) == ("income", minor(3_166_960))
    assert (y1.couple, y1.homeowner) == (False, True)
    assert y1.categories == (("cash", minor(20_000_000)),)
    cash_y1 = result.years[1].lines[0]
    assert (cash_y1.credited, cash_y1.closing) == (minor(3_166_960), minor(23_166_960))
    y2 = result.years[2].age_pension
    assert y2 is not None and y2.total == minor(3_107_580)
    assert result.years[2].lines[0].closing == minor(23_166_960 + 3_107_580)


def test_golden_the_assets_test_binds_and_property_and_vehicles_are_not_deemed() -> None:
    # Single non-homeowner: limit 600,000.00. Cash 100,000.00, assessed Property 500,000.00 and a
    # Vehicle 20,000.00 (neither deemed): assets 620,000.00, 20,000.00 over the limit.
    # 20,000.00 x 0.3% a fortnight x 26 = 1,560.00 less -> 30,253.60.
    # Deemed income only on the 100,000.00 cash: 1,169.00 + 33,200.00 x 3.75% = 1,245.00 -> 2,414.00
    # under the 5,876.00 free area, so the income test pays the maximum and the assets test binds.
    result = project(
        make(
            lines=single_lines(10_000_000),
            years=1,
            pension=pension_input(
                years=1,
                members=[(1, 0)],
                homeowner=False,
                property_assets=50_000_000,
                vehicle_assets=2_000_000,
            ),
        )
    )
    y0 = result.years[0].age_pension
    assert y0 is not None
    assert (y0.financial_assets, y0.assets) == (minor(10_000_000), minor(62_000_000))
    assert (y0.property_assets, y0.vehicle_assets) == (minor(50_000_000), minor(2_000_000))
    assert y0.deemed_income == minor(241_400)
    assert y0.income_test == minor(SINGLE_MAX)
    assert (y0.assets_test, y0.bound, y0.total) == (minor(3_025_360), "assets", minor(3_025_360))
    assert y0.homeowner is False


def test_other_income_reduces_the_pension_through_the_income_test() -> None:
    # No assets. Income 10,000.00; free area 5,876.00; excess 4,124.00 x 50% = 2,062.00 less.
    # 31,813.60 - 2,062.00 = 29,751.60.
    result = project(
        make(
            lines=single_lines(0),
            years=1,
            pension=pension_input(years=1, members=[(1, 0)], other_income=1_000_000),
        )
    )
    y0 = result.years[0].age_pension
    assert y0 is not None
    assert (y0.income_test, y0.bound, y0.other_income) == (
        minor(2_975_160),
        "income",
        minor(1_000_000),
    )


def test_a_full_pension_binds_neither_test() -> None:
    result = project(
        make(lines=single_lines(0), years=1, pension=pension_input(years=1, members=[(1, 0)]))
    )
    y0 = result.years[0].age_pension
    assert y0 is not None and (y0.bound, y0.total) == ("none", minor(SINGLE_MAX))


# --- timing: the test reads the OPENING balance -----------------------------------------


def test_the_means_test_reads_the_opening_balance_not_the_grown_or_closing_one() -> None:
    # 200,000.00 cash at 10% return, eligible from year 0. The year assesses the 200,000.00 the
    # year opens with (pension 31,813.60 - 0.5 x (1,169.00 + 4,995.00 - 5,876.00) = 31,669.60 =
    # 3,166,960 cents), not the 220,000.00 it would grow to (deemed 6,914.00, a lower pension).
    result = project(
        make(
            lines=single_lines(20_000_000, rate=100_000),
            years=1,
            pension=pension_input(years=1, members=[(1, 0)]),
        )
    )
    y0 = result.years[0].age_pension
    assert y0 is not None
    assert y0.categories == (("cash", minor(20_000_000)),)
    assert y0.total == minor(3_166_960)
    grown = project(
        make(
            lines=single_lines(22_000_000),
            years=1,
            pension=pension_input(years=1, members=[(1, 0)]),
        )
    )
    other = grown.years[0].age_pension
    assert other is not None and other.total < y0.total  # the later balance would pay less


def test_a_years_pension_does_not_depend_on_that_years_spending() -> None:
    # The circularity ADR-0029 section 3 avoids: year t's pension is fixed by the past, so a
    # larger draw in year t changes year t + 1's pension, never year t's.
    def run(spend: int) -> ProjectionResult:
        return project(
            make(
                lines=single_lines(40_000_000),
                years=3,
                pension=pension_input(years=3, members=[(1, 0)]),
                outflows=[Outflow(minor(spend), 1, 2)],
            )
        )

    light, heavy = run(100_000), run(8_000_000)
    assert [r.age_pension for r in light.years[:2]] == [r.age_pension for r in heavy.years[:2]]
    last_light, last_heavy = light.years[2].age_pension, heavy.years[2].age_pension
    assert last_light is not None and last_heavy is not None
    assert last_heavy.total > last_light.total  # less left after the draw: next year pays more


# --- eligibility, couples and Categories counted --------------------------------------------

TWO = [MemberRef(1, date(1960, 1, 1)), MemberRef(2, date(1962, 1, 1))]


def couple_lines() -> list[Line]:
    return [
        Line(1, "cash", minor(10_000_000), Rate(0), 0),
        Line(2, "cash", minor(0), Rate(0), 0),
        Line(1, "superannuation", minor(30_000_000), Rate(0), 0),
        Line(2, "superannuation", minor(20_000_000), Rate(0), 0),
        Line(1, "overseas_pensions", minor(5_000_000), Rate(0), 0),
    ]


def test_super_counts_from_its_owners_own_age_pension_age_year() -> None:
    # Member 1 is eligible from year 0, Member 2 from year 2. In year 0 the couple's assets are
    # cash 100,000.00 plus Member 1's super 300,000.00 and overseas pension 50,000.00 (counted
    # from Member 1's year 0), but not Member 2's super 200,000.00. From year 2 it is counted.
    result = project(
        make(
            lines=couple_lines(),
            years=3,
            members=TWO,
            pension=pension_input(years=3, members=[(1, 0), (2, 2)], couple=True),
        )
    )
    first, last = result.years[0].age_pension, result.years[2].age_pension
    assert first is not None and last is not None
    assert dict(first.categories) == {
        "cash": minor(10_000_000),
        "superannuation": minor(30_000_000),
        "overseas_pensions": minor(5_000_000),
    }
    assert first.financial_assets == minor(45_000_000)
    assert dict(last.categories)["superannuation"] == minor(50_000_000)
    # Member 1's cash also holds the pension credited in years 0 and 1.
    assert last.financial_assets == sum((a for _, a in last.categories), ZERO)
    assert dict(last.categories)["cash"] > minor(10_000_000)


def test_one_eligible_partner_is_paid_half_the_couple_total_and_both_split_it() -> None:
    # Couple, no assets: the full couple maximum 47,964.80. One eligible partner: half, 23,982.40,
    # credited to that Member only. Both eligible: 23,982.40 each.
    lines = [Line(1, "cash", ZERO, Rate(0), 0), Line(2, "cash", ZERO, Rate(0), 0)]
    one = project(
        make(
            lines=lines,
            years=2,
            members=TWO,
            pension=pension_input(years=2, members=[(1, 0), (2, 1)], couple=True),
        )
    )
    year0 = one.years[0].age_pension
    assert year0 is not None
    assert year0.paid == ((1, minor(2_398_240)),) and year0.total == minor(COUPLE_MAX)
    assert [x.credited.amount_minor for x in one.years[0].lines] == [2_398_240, 0]
    year1 = one.years[1].age_pension
    assert year1 is not None
    assert year1.paid == ((1, minor(2_398_240)), (2, minor(2_398_240)))
    assert [x.credited.amount_minor for x in one.years[1].lines] == [2_398_240, 2_398_240]


def test_an_odd_cent_is_never_lost_between_a_couple() -> None:
    odd = pension_input(years=1, members=[(1, 0), (2, 0)], couple=True, other_income=1_100_002)
    result = project(
        make(
            lines=[Line(1, "cash", ZERO, Rate(0), 0), Line(2, "cash", ZERO, Rate(0), 0)],
            years=1,
            members=TWO,
            pension=odd,
        )
    )
    y0 = result.years[0].age_pension
    assert y0 is not None
    assert sum(a.amount_minor for _, a in y0.paid) == y0.total.amount_minor
    shares = [a.amount_minor for _, a in y0.paid]
    assert y0.total.amount_minor % 2 == 1 and abs(shares[0] - shares[1]) == 1


def test_a_stale_pension_input_is_rejected() -> None:
    good = pension_input(years=2, members=[(1, 0)])
    base = make(lines=single_lines(0), years=2, pension=good)
    with pytest.raises(InputError, match="one amount for each year"):
        replace(base, age_pension=replace(good, other_income=(ZERO,)))
    with pytest.raises(InputError, match="couple has two"):
        replace(base, age_pension=replace(good, couple=True))
    with pytest.raises(InputError, match="start at year 0"):
        replace(base, age_pension=replace(good, spans=(AgePensionSpan(1, P),)))
    with pytest.raises(InputError, match="cash line"):
        make(
            lines=[Line(1, "investments", ZERO, Rate(0), 0)],
            years=2,
            pension=good,
        )


def test_a_later_span_applies_from_its_year() -> None:
    higher = replace(P, max_basic_single=aud("1235.40"))
    spans = (AgePensionSpan(0, P), AgePensionSpan(1, higher))
    ap = replace(pension_input(years=2, members=[(1, 0)]), spans=spans)
    result = project(make(lines=single_lines(0), years=2, pension=ap))
    assert paid_to(result, 0, 1) == SINGLE_MAX
    assert paid_to(result, 1, 1) == SINGLE_MAX + 100_00 * 26


def test_no_pension_input_is_exactly_the_old_engine() -> None:
    base = make(lines=single_lines(5_000_000, rate=30_000), years=4, pension=None)
    result = project(base)
    assert all(row.age_pension is None for row in result.years)


def test_the_input_round_trips_exactly_with_a_pension() -> None:
    ap = pension_input(
        years=3,
        members=[(1, 0), (2, 2)],
        couple=True,
        other_income=123_456,
        property_assets=9_900_000,
        vehicle_assets=1_000_000,
    )
    base = make(lines=couple_lines(), years=3, members=TWO, pension=ap)
    assert pinput.from_json(pinput.to_json(base)) == base


# --- invariants -----------------------------------------------------------------------------

amounts = st.integers(min_value=0, max_value=3_000_000_000)


def single_run(cash: int, extra: int, property_: int, income_: int) -> ProjectionResult:
    return project(
        make(
            lines=[
                Line(1, "cash", minor(cash), Rate(0), 0),
                Line(1, "investments", minor(extra), Rate(0), 0),
            ],
            years=1,
            pension=pension_input(
                years=1, members=[(1, 0)], property_assets=property_, other_income=income_
            ),
        )
    )


def year0_total(result: ProjectionResult) -> int:
    year = result.years[0].age_pension
    assert year is not None
    return year.total.amount_minor


@settings(max_examples=100, deadline=None)
@given(
    cash=amounts,
    extra=amounts,
    property_=amounts,
    income_=amounts,
    more_cash=st.integers(0, 500_000_000),
    more_property=st.integers(0, 500_000_000),
    more_income=st.integers(0, 50_000_000),
)
def test_the_pension_never_rises_as_assets_or_income_rise(
    cash: int,
    extra: int,
    property_: int,
    income_: int,
    more_cash: int,
    more_property: int,
    more_income: int,
) -> None:
    base = year0_total(single_run(cash, extra, property_, income_))
    assert year0_total(single_run(cash + more_cash, extra, property_, income_)) <= base
    assert year0_total(single_run(cash, extra + more_cash, property_, income_)) <= base
    assert year0_total(single_run(cash, extra, property_ + more_property, income_)) <= base
    assert year0_total(single_run(cash, extra, property_, income_ + more_income)) <= base
    assert 0 <= base <= SINGLE_MAX


@settings(max_examples=60, deadline=None)
@given(
    cash=amounts,
    invest=amounts,
    super_=amounts,
    spend=st.integers(1, 2_000_000_000),
    rate=st.integers(-50_000, 150_000),
    eligible=st.tuples(st.integers(0, 6), st.integers(0, 6)),
    property_=st.integers(0, 2_000_000_000),
    income_=st.integers(0, 20_000_000),
)
def test_no_negative_holdings_and_every_identity_holds_with_a_pension(
    cash: int,
    invest: int,
    super_: int,
    spend: int,
    rate: int,
    eligible: tuple[int, int],
    property_: int,
    income_: int,
) -> None:
    lines = [
        Line(m, key, minor(amount), Rate(rate), 0)
        for m, shares in ((1, (cash, invest, super_)), (2, (invest, cash, super_)))
        for key, amount in zip(("cash", "investments", "superannuation"), shares, strict=True)
    ]
    ap = pension_input(
        years=5,
        members=[(1, eligible[0]), (2, eligible[1])],
        couple=True,
        property_assets=property_,
        other_income=income_,
    )
    result = project(
        make(
            lines=lines, years=5, members=TWO, pension=ap, outflows=[Outflow(minor(spend), 0, None)]
        )
    )
    for row in result.years:
        for line in row.lines:
            assert line.closing.amount_minor >= 0
            assert line.closing == line.opening + line.growth + line.credited - line.debited - (
                line.withdrawn
            )
        assert row.shortfall.amount_minor >= 0
        withdrawn = sum((x.withdrawn for x in row.lines), ZERO)
        assert withdrawn + row.shortfall == row.need
        year = row.age_pension
        if year is not None:
            assert sum((a for _, a in year.paid), ZERO) <= year.total
            assert year.total <= year.maximum
            # Only a Member's own cash line is credited, by exactly what they were paid.
            paid = dict(year.paid)
            for line in row.lines:
                expected = paid.get(line.member_id, ZERO) if line.category_key == "cash" else ZERO
                assert line.credited == expected


@settings(max_examples=60, deadline=None)
@given(cash=amounts, eligible=st.integers(0, 4))
def test_the_pension_changes_only_the_cash_lines_it_is_paid_into(cash: int, eligible: int) -> None:
    lines = [
        Line(1, "cash", minor(cash), Rate(20_000), 0),
        Line(2, "cash", ZERO, Rate(20_000), 0),
        Line(1, "superannuation", minor(5_000_000), Rate(50_000), 0),
        Line(2, "superannuation", minor(7_000_000), Rate(50_000), 0),
    ]
    with_pension = project(
        make(
            lines=lines,
            years=5,
            members=TWO,
            pension=pension_input(years=5, members=[(1, eligible), (2, eligible)], couple=True),
        )
    )
    without = project(make(lines=lines, years=5, members=TWO, pension=None))
    for a, b in zip(with_pension.years, without.years, strict=True):
        for x, y in zip(a.lines, b.lines, strict=True):
            if x.category_key == "superannuation":
                assert (x.opening, x.growth, x.credited, x.closing) == (
                    y.opening,
                    y.growth,
                    y.credited,
                    y.closing,
                )


def test_the_working_agrees_with_the_pure_function() -> None:
    # The engine adds nothing to the maths: its totals are ``assess`` on the same figures.
    result = project(
        make(
            lines=single_lines(35_000_000),
            years=1,
            pension=pension_input(years=1, members=[(1, 0)], property_assets=10_000_000),
        )
    )
    y0 = result.years[0].age_pension
    assert y0 is not None
    working = assess(
        P,
        couple=False,
        homeowner=True,
        assets=minor(45_000_000),
        financial_assets=minor(35_000_000),
        other_income=ZERO,
    )
    assert y0.total == working.pension and y0.bound == working.bound


def test_an_input_saved_before_the_pension_upcasts_to_the_same_input_and_result() -> None:
    # A version 2 snapshot has no ``age_pension``; a version 1 one has no ``debits`` either. Each
    # loads to exactly the input it described (the pure upcasters, ADR-0029 section 6) and the
    # engine reproduces its result unchanged.
    base = make(
        lines=single_lines(5_000_000, rate=30_000),
        years=4,
        pension=None,
        outflows=[Outflow(minor(400_000), 1, None)],
    )
    current = pinput.to_payload(base)
    v2 = {k: v for k, v in current.items() if k != "age_pension"} | {"input_version": 2}
    v1 = {k: v for k, v in v2.items() if k != "debits"} | {"input_version": 1}
    for old in (v2, v1):
        loaded = pinput.from_payload(old)
        assert loaded == base and loaded.age_pension is None
        assert loaded.input_version == INPUT_VERSION
        assert project(loaded) == project(base)
    assert pinput.to_json(pinput.from_payload(v2)) == pinput.to_json(base)
