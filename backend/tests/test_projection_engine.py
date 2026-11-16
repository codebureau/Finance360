"""The projection engine (ADR-0015): golden answers worked by hand, then Hypothesis invariants.

Every golden figure below is minor units (cents) and was calculated on paper, year by year; the
comments show the working. All members, ids and amounts are invented.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance360.capabilities.projection import input as pinput
from finance360.capabilities.projection.engine import (
    ENGINE_VERSION,
    ProjectionResult,
    category_totals,
    project,
    reference_member_id,
)
from finance360.capabilities.projection.input import (
    INPUT_VERSION,
    Inflow,
    Line,
    MemberRef,
    Outflow,
    ProjectionInput,
)
from finance360.core.money import Money
from finance360.core.rate import PPM, Rate

AUD = "AUD"
AS_AT = date(2026, 1, 1)


def aud(minor: int) -> Money:
    return Money(minor, AUD)


def make(
    *,
    lines: list[Line],
    years: int,
    members: list[MemberRef] | None = None,
    inflation: int = 0,
    inflows: list[Inflow] | None = None,
    outflows: list[Outflow] | None = None,
    order: tuple[str, ...] = ("cash", "investments", "superannuation"),
    as_at: date = AS_AT,
) -> ProjectionInput:
    return ProjectionInput(
        input_version=INPUT_VERSION,
        as_at=as_at,
        currency=AUD,
        members=tuple(members or [MemberRef(1, date(1960, 1, 1))]),
        years=years,
        inflation=Rate(inflation),
        lines=tuple(lines),
        inflows=tuple(inflows or []),
        outflows=tuple(outflows or []),
        drawdown_order=order,
    )


def closings(result: ProjectionResult, member_id: int, category: str) -> list[int]:
    return [
        x.closing.amount_minor
        for row in result.years
        for x in row.lines
        if (x.member_id, x.category_key) == (member_id, category)
    ]


# --- engine version -----------------------------------------------------------------------

#: The version the golden answers below were computed with. Changing a golden answer, or anything
#: else that moves a figure, forces a deliberate bump of ENGINE_VERSION in the same change.
GOLDEN_ENGINE_VERSION = 2


def test_golden_answers_were_computed_with_this_engine_version() -> None:
    assert GOLDEN_ENGINE_VERSION == ENGINE_VERSION
    result = project(make(lines=[Line(1, "cash", aud(100), Rate(0), 0)], years=1))
    assert (result.engine_version, result.input_version) == (ENGINE_VERSION, INPUT_VERSION)


# --- golden: a flat return worked by hand -------------------------------------------------


def test_golden_flat_return_five_percent_no_inflation() -> None:
    # 100,000.00 at 5% nominal, 0% inflation, no flows: factor 1.05 each year, rounded once.
    #   y0 10_000_000 * 1.05 = 10_500_000
    #   y1 10_500_000 * 1.05 = 11_025_000
    #   y2 11_025_000 * 1.05 = 11_576_250
    #   y3 11_576_250 * 1.05 = 12_155_062.5 -> a tie, half to even -> 12_155_062
    #   y4 12_155_062   * 1.05 = 12_762_815.1 -> 12_762_815
    result = project(
        make(lines=[Line(1, "investments", aud(10_000_000), Rate(50_000), 0)], years=5)
    )
    assert closings(result, 1, "investments") == [
        10_500_000,
        11_025_000,
        11_576_250,
        12_155_062,
        12_762_815,
    ]
    assert result.lasts_beyond_horizon and result.funds_last_to_age is None
    assert [x.growth.amount_minor for x in result.years[3].lines] == [12_155_062 - 11_576_250]


def test_golden_a_growth_tie_rounds_to_the_even_neighbour_either_way() -> None:
    # 0.05 at 10%: 5 * 1.1 = 5.5 -> 6 (even, so up); then 6 * 1.1 = 6.6 -> 7.
    # 0.25 at 10%: 25 * 1.1 = 27.5 -> 28 (even); 0.15 at 10%: 15 * 1.1 = 16.5 -> 16 (even, so down).
    result = project(
        make(
            lines=[
                Line(1, "cash", aud(5), Rate(100_000), 0),
                Line(1, "investments", aud(25), Rate(100_000), 0),
                Line(1, "superannuation", aud(15), Rate(100_000), 0),
            ],
            years=2,
        )
    )
    assert closings(result, 1, "cash") == [6, 7]
    assert closings(result, 1, "investments")[0] == 28
    assert closings(result, 1, "superannuation")[0] == 16


def test_golden_real_growth_is_the_exact_factor_not_return_minus_inflation() -> None:
    # 100,000.00 at 6% nominal with 2% inflation: real factor 1.06 / 1.02, not 1.04.
    #   y0 10_000_000 * 106 / 102 = 10_392_156.86 -> 10_392_157
    #   y1 10_392_157 * 106 / 102 = 10_799_692.57 -> 10_799_693
    result = project(
        make(
            lines=[Line(1, "investments", aud(10_000_000), Rate(60_000), 0)],
            years=2,
            inflation=20_000,
        )
    )
    assert closings(result, 1, "investments") == [10_392_157, 10_799_693]
    assert 10_392_157 != 10_400_000  # r - i would give 4.00%


# --- golden: a hand-checked depletion age -------------------------------------------------


def test_golden_depletion_age() -> None:
    # Born 1960-01-01, so 66 at the as-at date. Cash 10,000.00 at 10% nominal, 0% inflation,
    # spending 3,000.00 a year from year 0, met at the end of the year after growth:
    #   y0 1_000_000 * 1.1 = 1_100_000 - 300_000 =   800_000
    #   y1   800_000 * 1.1 =   880_000 - 300_000 =   580_000
    #   y2   580_000 * 1.1 =   638_000 - 300_000 =   338_000
    #   y3   338_000 * 1.1 =   371_800 - 300_000 =    71_800
    #   y4    71_800 * 1.1 =    78_980: only 78_980 can be paid, 221_020 is short.
    # Spending is met in full through year 3, so the first shortfall is year 4, at age 66 + 4.
    result = project(
        make(
            lines=[Line(1, "cash", aud(1_000_000), Rate(100_000), 0)],
            years=6,
            outflows=[Outflow(aud(300_000), 0, None)],
        )
    )
    assert closings(result, 1, "cash") == [800_000, 580_000, 338_000, 71_800, 0, 0]
    assert [r.shortfall.amount_minor for r in result.years] == [0, 0, 0, 0, 221_020, 300_000]
    assert result.years[4].lines[0].withdrawn == aud(78_980)
    assert result.funds_last_to_age == 70
    assert not result.lasts_beyond_horizon
    assert result.reference_member_id == 1


def test_a_horizon_before_depletion_lasts_beyond_the_horizon() -> None:
    result = project(
        make(
            lines=[Line(1, "cash", aud(1_000_000), Rate(100_000), 0)],
            years=4,
            outflows=[Outflow(aud(300_000), 0, None)],
        )
    )
    assert result.funds_last_to_age is None and result.lasts_beyond_horizon


# --- golden: super becomes drawable in its owner's retirement year ------------------------


def test_golden_super_is_drawable_only_from_its_accessible_year() -> None:
    # Cash 1,000.00 and super 10,000.00 (drawable from year 2), no growth, spending 4,000.00.
    #   y0 cash pays 100_000, super is locked: shortfall 300_000
    #   y1 cash is empty, super is locked: shortfall 400_000 (super untouched at 1_000_000)
    #   y2 super is open: pays 400_000, closing 600_000, shortfall 0
    #   y3 pays 400_000, closing 200_000
    #   y4 pays the last 200_000, shortfall 200_000
    result = project(
        make(
            lines=[
                Line(1, "cash", aud(100_000), Rate(0), 0),
                Line(1, "superannuation", aud(1_000_000), Rate(0), 2),
            ],
            years=5,
            outflows=[Outflow(aud(400_000), 0, None)],
        )
    )
    assert closings(result, 1, "cash") == [0, 0, 0, 0, 0]
    assert closings(result, 1, "superannuation") == [1_000_000, 1_000_000, 600_000, 200_000, 0]
    assert [r.shortfall.amount_minor for r in result.years] == [300_000, 400_000, 0, 0, 200_000]
    assert result.funds_last_to_age == 66  # the very first year is already short


def test_golden_a_later_unlock_gives_a_later_depletion_age() -> None:
    # Cash 8,000.00 covers years 0 and 1 (4,000.00 each); super opens in year 2 and lasts two years.
    result = project(
        make(
            lines=[
                Line(1, "cash", aud(800_000), Rate(0), 0),
                Line(1, "superannuation", aud(800_000), Rate(0), 2),
            ],
            years=6,
            outflows=[Outflow(aud(400_000), 0, None)],
        )
    )
    assert [r.shortfall.amount_minor for r in result.years] == [0, 0, 0, 0, 400_000, 400_000]
    assert result.funds_last_to_age == 70


# --- drawdown order and Members' proportional split ---------------------------------------


def test_drawdown_follows_the_order_and_splits_across_members_by_balance() -> None:
    members = [MemberRef(1, date(1960, 1, 1)), MemberRef(2, date(1962, 1, 1))]
    result = project(
        make(
            members=members,
            lines=[
                Line(1, "cash", aud(300), Rate(0), 0),
                Line(2, "cash", aud(100), Rate(0), 0),
                Line(1, "investments", aud(1_000), Rate(0), 0),
            ],
            years=1,
            outflows=[Outflow(aud(500), 0, None)],
        )
    )
    # Cash (400) is drawn first, 300:100, then 100 comes from investments.
    by = {(x.member_id, x.category_key): x for x in result.years[0].lines}
    assert by[(1, "cash")].withdrawn == aud(300) and by[(2, "cash")].withdrawn == aud(100)
    assert by[(1, "investments")].withdrawn == aud(100)
    assert result.years[0].shortfall == aud(0)


def test_a_partial_category_draw_splits_in_proportion_to_balances() -> None:
    members = [MemberRef(1, date(1960, 1, 1)), MemberRef(2, date(1962, 1, 1))]
    result = project(
        make(
            members=members,
            lines=[Line(1, "cash", aud(300), Rate(0), 0), Line(2, "cash", aud(100), Rate(0), 0)],
            years=1,
            outflows=[Outflow(aud(200), 0, None)],
        )
    )
    by = {x.member_id: x for x in result.years[0].lines}
    assert (by[1].withdrawn, by[2].withdrawn) == (aud(150), aud(50))


# --- inflows, ranges and the reference Member ---------------------------------------------


def test_an_inflow_is_credited_over_its_half_open_range_and_earns_nothing_that_year() -> None:
    result = project(
        make(
            lines=[Line(1, "investments", aud(1_000), Rate(100_000), 0)],
            years=4,
            inflows=[Inflow(1, "investments", aud(500), 1, 3)],
        )
    )
    # y0 1000*1.1 = 1100; y1 1100*1.1 = 1210 + 500 = 1710; y2 1710*1.1 = 1881 + 500 = 2381;
    # y3 (range ended) 2381*1.1 = 2619.1 -> 2619
    assert closings(result, 1, "investments") == [1_100, 1_710, 2_381, 2_619]
    assert [r.lines[0].credited.amount_minor for r in result.years] == [0, 500, 500, 0]


def test_the_reference_member_is_the_youngest_and_a_tie_goes_to_the_lowest_id() -> None:
    members = (
        MemberRef(7, date(1990, 5, 5)),
        MemberRef(3, date(1990, 5, 5)),
        MemberRef(9, date(1980, 1, 1)),
    )
    projection_input = make(
        members=list(members),
        lines=[Line(3, "cash", aud(0), Rate(0), 0)],
        years=2,
        as_at=date(2026, 1, 1),
    )
    assert reference_member_id(projection_input) == 3
    ages = dict(project(projection_input).years[1].ages)
    assert ages == {3: 35 + 1, 7: 35 + 1, 9: 46 + 1}  # 35 and 46 on 2026-01-01


def test_ages_and_year_starts_follow_the_as_at_anniversary() -> None:
    result = project(
        make(
            members=[MemberRef(1, date(2000, 2, 29))],
            lines=[Line(1, "cash", aud(0), Rate(0), 0)],
            years=3,
            as_at=date(2028, 2, 29),
        )
    )
    assert [r.starts_on for r in result.years] == [
        date(2028, 2, 29),
        date(2029, 2, 28),  # no 29 February: the 28th
        date(2030, 2, 28),
    ]
    assert [dict(r.ages)[1] for r in result.years] == [28, 29, 30]


def test_category_totals_are_plain_sums_of_lines() -> None:
    members = [MemberRef(1, date(1960, 1, 1)), MemberRef(2, date(1962, 1, 1))]
    result = project(
        make(
            members=members,
            lines=[Line(1, "cash", aud(300), Rate(0), 0), Line(2, "cash", aud(100), Rate(0), 0)],
            years=1,
            outflows=[Outflow(aud(200), 0, None)],
        )
    )
    (cash,) = category_totals(result.years[0], ("cash", "investments"))
    assert (cash.opening, cash.withdrawn, cash.closing) == (aud(400), aud(200), aud(200))


# --- Hypothesis: strategies ---------------------------------------------------------------

CATEGORIES = ("cash", "investments", "superannuation", "overseas_pensions")


@st.composite
def projection_inputs(draw: st.DrawFn, *, flows: bool = True) -> ProjectionInput:
    member_ids = draw(st.lists(st.integers(1, 50), min_size=1, max_size=3, unique=True))
    members = tuple(
        MemberRef(
            m,
            date(draw(st.integers(1940, 2010)), draw(st.integers(1, 12)), draw(st.integers(1, 28))),
        )
        for m in member_ids
    )
    order = tuple(draw(st.permutations(CATEGORIES)))[: draw(st.integers(1, 4))]
    years = draw(st.integers(1, 40))
    lines = tuple(
        Line(
            m,
            c,
            aud(draw(st.integers(0, 10**10))),
            Rate(draw(st.integers(-500_000, 500_000))),
            draw(st.integers(0, years + 2)),
        )
        for m in member_ids
        for c in order
        if draw(st.booleans()) or c == order[0]
    )
    keys = [(x.member_id, x.category_key) for x in lines]

    def span() -> tuple[int, int | None]:
        start = draw(st.integers(0, years))
        end = draw(st.one_of(st.none(), st.integers(start, years + 3)))
        return start, end

    inflows: list[Inflow] = []
    outflows: list[Outflow] = []
    if flows:
        for _ in range(draw(st.integers(0, 3))):
            member, category = draw(st.sampled_from(keys))
            start, end = span()
            inflows.append(Inflow(member, category, aud(draw(st.integers(1, 10**8))), start, end))
        for _ in range(draw(st.integers(0, 3))):
            start, end = span()
            outflows.append(Outflow(aud(draw(st.integers(1, 10**9))), start, end))
    return ProjectionInput(
        input_version=INPUT_VERSION,
        as_at=date(
            draw(st.integers(2020, 2035)), draw(st.integers(1, 12)), draw(st.integers(1, 28))
        ),
        currency=AUD,
        members=members,
        years=years,
        inflation=Rate(draw(st.integers(-50_000, 300_000))),
        lines=lines,
        inflows=tuple(inflows),
        outflows=tuple(outflows),
        drawdown_order=order,
    )


SETTINGS = settings(max_examples=200, deadline=None)


# --- Hypothesis: invariants ---------------------------------------------------------------


@SETTINGS
@given(projection_inputs())
def test_no_balance_or_withdrawal_is_ever_negative(projection_input: ProjectionInput) -> None:
    for row in project(projection_input).years:
        assert row.need.amount_minor >= 0 and row.shortfall.amount_minor >= 0
        for x in row.lines:
            for money in (x.opening, x.credited, x.withdrawn, x.closing):
                assert money.amount_minor >= 0
            assert x.opening.amount_minor + x.growth.amount_minor >= 0


@SETTINGS
@given(projection_inputs())
def test_the_per_year_identities_hold_exactly(projection_input: ProjectionInput) -> None:
    result = project(projection_input)
    previous: dict[tuple[int, str], int] = {
        (x.member_id, x.category_key): x.start.amount_minor for x in projection_input.lines
    }
    for row in result.years:
        for x in row.lines:
            assert (
                x.closing.amount_minor
                == x.opening.amount_minor
                + x.growth.amount_minor
                + x.credited.amount_minor
                - x.withdrawn.amount_minor
            )
            # Each year opens exactly where the last closed.
            assert x.opening.amount_minor == previous[(x.member_id, x.category_key)]
        previous = {(x.member_id, x.category_key): x.closing.amount_minor for x in row.lines}
        withdrawn = sum(x.withdrawn.amount_minor for x in row.lines)
        assert withdrawn + row.shortfall.amount_minor == row.need.amount_minor


@SETTINGS
@given(projection_inputs())
def test_growth_rounds_once_to_within_half_a_minor_unit_of_the_exact_real_factor(
    projection_input: ProjectionInput,
) -> None:
    by_line = {(x.member_id, x.category_key): x.return_rate.ppm for x in projection_input.lines}
    denominator = PPM + projection_input.inflation.ppm
    for row in project(projection_input).years:
        for x in row.lines:
            numerator = PPM + by_line[(x.member_id, x.category_key)]
            grown = x.opening.amount_minor + x.growth.amount_minor
            # |grown - opening * numerator / denominator| <= 1/2
            assert 2 * abs(grown * denominator - x.opening.amount_minor * numerator) <= denominator


@SETTINGS
@given(projection_inputs())
def test_withdrawals_respect_access_and_the_drawdown_order(
    projection_input: ProjectionInput,
) -> None:
    access = {(x.member_id, x.category_key): x.accessible_from_year for x in projection_input.lines}
    order = projection_input.drawdown_order
    for row in project(projection_input).years:
        for x in row.lines:
            if row.t < access[(x.member_id, x.category_key)]:
                assert x.withdrawn.amount_minor == 0  # locked lines are never drawn
        # A category is drawn on only once every earlier accessible category is emptied.
        for later_index, later in enumerate(order):
            drawn = any(x.withdrawn.amount_minor > 0 for x in row.lines if x.category_key == later)
            if not drawn:
                continue
            for earlier in order[:later_index]:
                for x in row.lines:
                    if x.category_key == earlier and row.t >= access[(x.member_id, earlier)]:
                        assert x.closing.amount_minor == 0


@SETTINGS
@given(projection_inputs())
def test_projection_is_deterministic_including_after_a_json_round_trip(
    projection_input: ProjectionInput,
) -> None:
    result = project(projection_input)
    assert project(projection_input) == result
    assert project(pinput.from_json(pinput.to_json(projection_input))) == result


@SETTINGS
@given(projection_inputs(flows=False))
def test_balances_are_unchanged_when_return_equals_inflation_and_there_are_no_flows(
    projection_input: ProjectionInput,
) -> None:
    flat = ProjectionInput(
        input_version=INPUT_VERSION,
        as_at=projection_input.as_at,
        currency=AUD,
        members=projection_input.members,
        years=projection_input.years,
        inflation=projection_input.inflation,
        lines=tuple(
            Line(
                x.member_id,
                x.category_key,
                x.start,
                projection_input.inflation,
                x.accessible_from_year,
            )
            for x in projection_input.lines
        ),
        inflows=(),
        outflows=(),
        drawdown_order=projection_input.drawdown_order,
    )
    result = project(flat)
    assert result.lasts_beyond_horizon
    for row in result.years:
        for x, start in zip(row.lines, flat.lines, strict=True):
            assert x.closing == start.start and x.growth.amount_minor == 0


@SETTINGS
@given(projection_inputs())
def test_the_headline_age_is_the_reference_members_age_at_the_first_shortfall(
    projection_input: ProjectionInput,
) -> None:
    result = project(projection_input)
    first = next((r for r in result.years if r.shortfall.amount_minor > 0), None)
    if first is None:
        assert result.funds_last_to_age is None and result.lasts_beyond_horizon
    else:
        assert not result.lasts_beyond_horizon
        assert result.funds_last_to_age == dict(first.ages)[result.reference_member_id]


@pytest.mark.parametrize("years", [1, 120])
def test_the_horizon_limits_run(years: int) -> None:
    result = project(make(lines=[Line(1, "cash", aud(1), Rate(0), 0)], years=years))
    assert len(result.years) == years
