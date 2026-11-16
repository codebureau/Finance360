"""Sustainable retirement income (B2): the search over the engine.

Golden answers are worked by hand in minor units (cents); the property tests check monotonicity
and that the answer is the exact boundary. All members and amounts are invented.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance360.capabilities.projection.engine import project
from finance360.capabilities.projection.input import (
    INPUT_VERSION,
    Line,
    MemberRef,
    Outflow,
    ProjectionInput,
)
from finance360.capabilities.projection.spending import LAST_RETIREMENT, START, Phase
from finance360.capabilities.projection.sustainable import (
    SEARCH_CEILING_MINOR,
    SustainableSpending,
)
from finance360.capabilities.projection.sustainable import (
    sustainable_spending as search,
)
from finance360.core.money import Money
from finance360.core.rate import PPM, Rate

AUD = "AUD"


def aud(minor: int) -> Money:
    return Money(minor, AUD)


def make(
    starts: list[int],
    *,
    years: int,
    return_ppm: int = 0,
    inflation_ppm: int = 0,
    accessible_from: int = 0,
) -> ProjectionInput:
    """One line per start, spread over Members 1..n, all in one Category."""
    return ProjectionInput(
        input_version=INPUT_VERSION,
        as_at=date(2026, 1, 1),
        currency=AUD,
        members=tuple(MemberRef(n + 1, date(1960, 1, 1)) for n in range(len(starts))),
        years=years,
        inflation=Rate(inflation_ppm),
        lines=tuple(
            Line(n + 1, "cash", aud(start), Rate(return_ppm), accessible_from)
            for n, start in enumerate(starts)
        ),
        inflows=(),
        outflows=(),
        drawdown_order=("cash",),
    )


def sustainable_spending(base: ProjectionInput, from_year: int) -> SustainableSpending:
    """One constant amount from ``from_year``: a single phase from the last retirement, which is
    the search B2 made before spending phases."""
    return search(base, (Phase(LAST_RETIREMENT, 0, aud(1)),), from_year)


def lasts(base: ProjectionInput, minor: int, from_year: int) -> bool:
    outflows = (Outflow(aud(minor), from_year, None),) if minor else ()
    return project(replace(base, outflows=outflows)).lasts_beyond_horizon


# --- golden answers -----------------------------------------------------------------------


def test_golden_flat_balance_is_shared_evenly_over_the_years() -> None:
    # Zero real rate, 1_000_000 over 5 years: 200_000 a year uses it exactly.
    base = make([1_000_000], years=5)
    result = sustainable_spending(base, 0)
    assert (result.amount, result.capped) == (aud(200_000), False)
    assert lasts(base, 200_000, 0)
    assert not lasts(base, 200_001, 0)  # 1_000_005 needed against 1_000_000


@pytest.mark.parametrize(
    ("balance", "years", "expected"),
    [
        (1_000_002, 3, 333_334),  # 3 x 333_334 = 1_000_002 exactly
        (1_000_001, 3, 333_333),  # 3 x 333_334 = 1_000_002 is one unit short
        (777_777, 7, 111_111),  # 7 x 111_111 = 777_777 exactly
        (1_000_000, 8, 125_000),  # 8 x 125_000 = 1_000_000 exactly
        (999_999, 8, 124_999),  # 8 x 125_000 is one unit more than the balance
    ],
)
def test_golden_zero_real_rate_is_the_balance_divided_by_the_years(
    balance: int, years: int, expected: int
) -> None:
    result = sustainable_spending(make([balance], years=years), 0)
    assert result.amount == aud(expected)
    assert expected == balance // years


def test_golden_growth_case_worked_by_hand() -> None:
    # 10% real, 1_000_000, two years, spending from year 0, flows at the end of the year.
    # Year 0: 1_100_000 - S. Year 1: (1_100_000 - S) * 1.1 must cover S, so S <= 1_210_000 / 2.1
    # = 576_190.47...  At S = 576_190: 523_810 * 1.1 = 576_191 >= 576_190. At 576_191:
    # 523_809 * 1.1 = 576_189.9 -> 576_190 < 576_191, short.
    base = make([1_000_000], years=2, return_ppm=100_000)
    result = sustainable_spending(base, 0)
    assert result.amount == aud(576_190)
    assert lasts(base, 576_190, 0)
    assert not lasts(base, 576_191, 0)


def test_golden_spending_that_starts_later_is_the_same_search_over_fewer_years() -> None:
    # Zero real rate, 900_000, ten years, spending from year 4: six years of spending.
    base = make([900_000], years=10)
    assert sustainable_spending(base, 4).amount == aud(150_000)
    assert not lasts(base, 150_001, 4)


def test_the_entered_outflows_are_ignored() -> None:
    base = replace(make([1_000_000], years=5), outflows=(Outflow(aud(999_999), 0, None),))
    assert sustainable_spending(base, 0).amount == aud(200_000)


def test_a_split_balance_is_found_across_members_lines() -> None:
    # Two lines, 600_001 and 400_000: the sum is 1_000_001, so 200_000 a year over five years.
    assert sustainable_spending(make([600_001, 400_000], years=5), 0).amount == aud(200_000)


# --- edges --------------------------------------------------------------------------------


def test_no_money_means_zero_sustainable_spending() -> None:
    result = sustainable_spending(make([0], years=10), 0)
    assert (result.amount, result.capped) == (aud(0), False)


def test_less_than_one_unit_a_year_is_zero_not_negative() -> None:
    # 3 minor units over 5 years: even 1 a year fails in year 3.
    result = sustainable_spending(make([3], years=5), 0)
    assert (result.amount, result.capped) == (aud(0), False)


def test_spending_that_starts_after_the_horizon_hits_the_ceiling() -> None:
    result = sustainable_spending(make([0], years=5), 5)
    assert result.amount == aud(SEARCH_CEILING_MINOR)
    assert result.capped is True


def test_a_balance_that_can_pay_the_ceiling_is_reported_as_capped() -> None:
    base = make([SEARCH_CEILING_MINOR * 3], years=2)
    result = sustainable_spending(base, 0)
    assert (result.amount, result.capped) == (aud(SEARCH_CEILING_MINOR), True)


def test_just_under_the_ceiling_is_found_exactly_and_not_capped() -> None:
    # Zero real rate, one year: the whole balance can be spent.
    base = make([SEARCH_CEILING_MINOR - 1], years=1)
    result = sustainable_spending(base, 0)
    assert (result.amount, result.capped) == (aud(SEARCH_CEILING_MINOR - 1), False)


def test_inaccessible_money_does_not_count_before_it_opens() -> None:
    # The only line opens in year 3; spending from year 0 falls short at once.
    base = make([1_000_000], years=5, accessible_from=3)
    assert sustainable_spending(base, 0).amount == aud(0)
    # Spending from year 3: two years of spending (years 3 and 4).
    assert sustainable_spending(base, 3).amount == aud(500_000)


# --- determinism --------------------------------------------------------------------------


def test_the_search_is_deterministic() -> None:
    base = make([1_234_567, 765_433], years=37, return_ppm=65_000, inflation_ppm=25_000)
    first = sustainable_spending(base, 5)
    assert all(sustainable_spending(base, 5) == first for _ in range(3))


# --- properties ---------------------------------------------------------------------------

starts_st = st.lists(st.integers(0, 50_000_000), min_size=1, max_size=3)
years_st = st.integers(1, 40)
ppm_st = st.integers(-50_000, 150_000)  # -5% to 15% nominal, in parts per million


@settings(max_examples=150, deadline=None)
@given(
    starts=starts_st,
    years=years_st,
    return_ppm=ppm_st,
    inflation_ppm=st.integers(0, 60_000),
    from_year=st.integers(0, 45),
)
def test_the_answer_is_the_exact_boundary(
    starts: list[int], years: int, return_ppm: int, inflation_ppm: int, from_year: int
) -> None:
    base = make(starts, years=years, return_ppm=return_ppm, inflation_ppm=inflation_ppm)
    result = sustainable_spending(base, from_year)
    minor = result.amount.amount_minor
    assert lasts(base, minor, from_year)
    if not result.capped:
        assert not lasts(base, minor + 1, from_year)


@settings(max_examples=150, deadline=None)
@given(
    starts=starts_st,
    extra=st.integers(0, 50_000_000),
    years=years_st,
    return_ppm=ppm_st,
    inflation_ppm=st.integers(0, 60_000),
    from_year=st.integers(0, 45),
)
def test_a_larger_starting_balance_never_lowers_the_sustainable_amount(
    starts: list[int],
    extra: int,
    years: int,
    return_ppm: int,
    inflation_ppm: int,
    from_year: int,
) -> None:
    base = make(starts, years=years, return_ppm=return_ppm, inflation_ppm=inflation_ppm)
    richer = make(
        [starts[0] + extra, *starts[1:]],
        years=years,
        return_ppm=return_ppm,
        inflation_ppm=inflation_ppm,
    )
    assert (
        sustainable_spending(richer, from_year).amount.amount_minor
        >= sustainable_spending(base, from_year).amount.amount_minor
    )


@settings(max_examples=150, deadline=None)
@given(
    starts=starts_st,
    more=st.integers(0, 80_000),
    years=years_st,
    return_ppm=ppm_st,
    inflation_ppm=st.integers(0, 60_000),
    from_year=st.integers(0, 45),
)
def test_a_higher_return_never_lowers_the_sustainable_amount(
    starts: list[int],
    more: int,
    years: int,
    return_ppm: int,
    inflation_ppm: int,
    from_year: int,
) -> None:
    low = make(starts, years=years, return_ppm=return_ppm, inflation_ppm=inflation_ppm)
    high = make(starts, years=years, return_ppm=return_ppm + more, inflation_ppm=inflation_ppm)
    assert PPM + return_ppm + more > 0
    assert (
        sustainable_spending(high, from_year).amount.amount_minor
        >= sustainable_spending(low, from_year).amount.amount_minor
    )


# --- several phases (ADR-0017 section 6) ---------------------------------------------------


def phases(*items: tuple[str, int, int]) -> tuple[Phase, ...]:
    return tuple(Phase(anchor, offset, aud(minor)) for anchor, offset, minor in items)


def test_golden_two_phases_keep_their_ratio() -> None:
    # Zero real rate, 1_000_000 over 6 years from year 0. Phase A (4 years) is twice phase B (2
    # years): 4 * 2x + 2 * x = 10x <= 1_000_000, so B = 100_000 and the headline (A) is 200_000.
    base = make([1_000_000], years=6)
    plan = phases((LAST_RETIREMENT, 0, 20_000), (LAST_RETIREMENT, 4, 10_000))
    result = search(base, plan, 0)
    assert (result.amount, result.capped) == (aud(200_000), False)


def test_phases_from_the_start_are_held_not_scaled() -> None:
    # 1_000_000 over 6 years. A held 100_000 a year for the first 2 years leaves 800_000 for the
    # 4 years from the last retirement (year 2): 200_000 a year.
    base = make([1_000_000], years=6)
    plan = phases((START, 0, 100_000), (LAST_RETIREMENT, 2, 1))
    assert search(base, plan, 0).amount == aud(200_000)


def test_with_no_phase_from_the_retirement_one_is_added_beside_the_held_ones() -> None:
    base = make([1_000_000], years=5)
    plan = phases((START, 0, 100_000))
    # The start phase runs to the horizon (no later phase), and the added one is at year 2 and
    # replaces it from there: 2 * 100_000 + 3 * x <= 1_000_000.
    assert search(base, plan, 2).amount == aud(266_666)


phase_st = st.tuples(
    st.sampled_from([START, LAST_RETIREMENT]), st.integers(0, 12), st.integers(1, 5_000_000)
)


@settings(max_examples=120, deadline=None)
@given(
    starts=starts_st,
    items=st.lists(phase_st, min_size=1, max_size=4, unique_by=lambda i: (i[0], i[1])),
    years=years_st,
    return_ppm=ppm_st,
    inflation_ppm=st.integers(0, 60_000),
    from_year=st.integers(0, 15),
)
def test_with_several_phases_the_answer_is_the_exact_boundary(
    starts: list[int],
    items: list[tuple[str, int, int]],
    years: int,
    return_ppm: int,
    inflation_ppm: int,
    from_year: int,
) -> None:
    """The search stays monotone with several phases: the answer lasts, and one more does not."""
    from finance360.capabilities.projection import spending

    base = make(starts, years=years, return_ppm=return_ppm, inflation_ppm=inflation_ppm)
    plan = phases(*items)
    result = search(base, plan, from_year)

    def lasts_at(minor: int) -> bool:
        scaled = spending.scale_from_retirement(plan, minor, AUD)
        outflows = spending.outflows(scaled, from_year)
        return project(replace(base, outflows=outflows)).lasts_beyond_horizon

    minor = result.amount.amount_minor
    if minor > 0:  # 0 can also mean the held start phases fall short on their own
        assert lasts_at(minor)
    if not result.capped:
        assert not lasts_at(minor + 1)
    # Monotone: every smaller first amount also lasts, every larger one does not (spot checks).
    for probe in {minor // 2, minor} if minor > 0 else set():
        assert lasts_at(probe)
    for probe in {minor + 1, minor * 2 + 2}:
        if not result.capped:
            assert not lasts_at(probe)
