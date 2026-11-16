"""Spending phases as pure functions (ADR-0017 section 6). No database.

Phases never overlap, a clash resolves to the later anchor, the scaling the sustainable-income
search uses is monotone, and the validation names the field. All amounts are invented.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance360.capabilities.projection.input import Outflow
from finance360.capabilities.projection.spending import (
    ANCHORS,
    LAST_RETIREMENT,
    START,
    Phase,
    ordered,
    outflows,
    parse,
    pick,
    scale_from_retirement,
    schedule,
    start_year,
)
from finance360.core.money import Money

AUD = "AUD"


def aud(minor: int) -> Money:
    return Money(minor, AUD)


def phase(anchor: str, offset: int, minor: int) -> Phase:
    return Phase(anchor, offset, aud(minor))


# --- timeline ------------------------------------------------------------------------------


def test_one_retirement_phase_is_the_single_outflow_from_the_last_retirement() -> None:
    # What annual_spending always meant: it is why the conversion changes no projection.
    assert outflows([phase(LAST_RETIREMENT, 0, 7_000_000)], 20) == (
        Outflow(aud(7_000_000), 20, None),
    )


def test_each_phase_runs_until_the_next_starts_and_the_last_to_the_horizon() -> None:
    plan = [phase(LAST_RETIREMENT, 10, 5_500_000), phase(LAST_RETIREMENT, 0, 7_000_000)]
    assert outflows(plan, 20) == (
        Outflow(aud(7_000_000), 20, 30),
        Outflow(aud(5_500_000), 30, None),
    )


def test_retiring_earlier_moves_a_retirement_anchored_phase_and_not_a_start_one() -> None:
    plan = [phase(START, 3, 100), phase(LAST_RETIREMENT, 2, 200)]
    assert [(o.from_year, o.until_year) for o in outflows(plan, 20)] == [(3, 22), (22, None)]
    assert [(o.from_year, o.until_year) for o in outflows(plan, 12)] == [(3, 14), (14, None)]


def test_nothing_is_spent_before_the_first_phase() -> None:
    (only,) = outflows([phase(LAST_RETIREMENT, 0, 100)], 20)
    assert only.from_year == 20  # years 0 to 19 have no outflow at all


def test_on_a_same_year_clash_the_later_anchor_wins_and_the_other_is_empty() -> None:
    plan = [phase(START, 20, 100), phase(LAST_RETIREMENT, 0, 200)]
    scheduled = schedule(plan, 20)
    assert [(s.phase.anchor, s.empty) for s in scheduled] == [
        (START, True),
        (LAST_RETIREMENT, False),
    ]
    assert outflows(plan, 20) == (Outflow(aud(200), 20, None),)


def test_a_stored_zero_phase_spends_nothing_but_still_ends_the_one_before() -> None:
    plan = [phase(LAST_RETIREMENT, 0, 100), phase(LAST_RETIREMENT, 5, 0)]
    assert outflows(plan, 10) == (Outflow(aud(100), 10, 15),)


def test_no_phases_is_no_outflows() -> None:
    assert outflows([], 10) == ()


# --- property: phases never overlap --------------------------------------------------------

phases_st = st.lists(
    st.tuples(st.sampled_from(ANCHORS), st.integers(0, 40), st.integers(0, 1_000_000)),
    max_size=6,
    unique_by=lambda p: (p[0], p[1]),
)


@settings(max_examples=200, deadline=None)
@given(items=phases_st, retirement=st.integers(0, 45))
def test_phases_never_overlap_and_the_latest_started_phase_always_pays(
    items: list[tuple[str, int, int]], retirement: int
) -> None:
    plan = [phase(*i) for i in items]
    flows = outflows(plan, retirement)
    for year in range(0, 100):
        paying = [
            f.amount.amount_minor
            for f in flows
            if f.from_year <= year and (f.until_year is None or year < f.until_year)
        ]
        assert len(paying) <= 1, "two phases pay in one year"
        started = [p for p in plan if start_year(p, retirement) <= year]
        if not started:
            assert paying == []  # nothing before the first phase
            continue
        latest = max(started, key=lambda p: (start_year(p, retirement), ANCHORS.index(p.anchor)))
        expected = latest.annual_amount.amount_minor
        assert (paying[0] if paying else 0) == expected


@given(items=phases_st, retirement=st.integers(0, 45))
def test_the_order_of_the_list_never_changes_the_outflows(
    items: list[tuple[str, int, int]], retirement: int
) -> None:
    plan = [phase(*i) for i in items]
    assert outflows(plan, retirement) == outflows(list(reversed(plan)), retirement)
    assert ordered(plan) == ordered(list(reversed(plan)))


# --- scaling for the sustainable-income search ---------------------------------------------


def test_scaling_holds_the_ratio_and_makes_the_first_retirement_phase_the_given_amount() -> None:
    plan = [phase(LAST_RETIREMENT, 0, 7_000_000), phase(LAST_RETIREMENT, 10, 5_500_000)]
    scaled = scale_from_retirement(plan, 3_500_000, AUD)
    assert [p.annual_amount.amount_minor for p in scaled] == [3_500_000, 2_750_000]


def test_scaling_leaves_start_phases_alone() -> None:
    plan = [
        phase(START, 0, 9_999),
        phase(LAST_RETIREMENT, 0, 1_000),
        phase(LAST_RETIREMENT, 5, 500),
    ]
    scaled = {
        (p.anchor, p.offset_years): p.annual_amount.amount_minor
        for p in scale_from_retirement(plan, 2_000, AUD)
    }
    assert scaled == {(START, 0): 9_999, (LAST_RETIREMENT, 0): 2_000, (LAST_RETIREMENT, 5): 1_000}


def test_scaling_rounds_each_phase_once_half_to_even() -> None:
    plan = [phase(LAST_RETIREMENT, 0, 2), phase(LAST_RETIREMENT, 1, 1)]
    # The second is half the first: 1.5 rounds to the even 2, and 2.5 also to the even 2.
    assert [p.annual_amount.amount_minor for p in scale_from_retirement(plan, 3, AUD)] == [3, 2]
    assert [p.annual_amount.amount_minor for p in scale_from_retirement(plan, 5, AUD)] == [5, 2]
    assert [p.annual_amount.amount_minor for p in scale_from_retirement(plan, 4, AUD)] == [4, 2]


def test_with_no_retirement_phase_one_is_added_at_offset_zero() -> None:
    scaled = scale_from_retirement([phase(START, 0, 100)], 40, AUD)
    assert sorted((p.anchor, p.offset_years, p.annual_amount.amount_minor) for p in scaled) == [
        (LAST_RETIREMENT, 0, 40),
        (START, 0, 100),
    ]


def test_a_stored_zero_first_phase_becomes_the_given_amount_and_the_rest_are_held() -> None:
    plan = [phase(LAST_RETIREMENT, 0, 0), phase(LAST_RETIREMENT, 3, 70)]
    assert [p.annual_amount.amount_minor for p in scale_from_retirement(plan, 9, AUD)] == [9, 70]


@settings(max_examples=200, deadline=None)
@given(
    items=st.lists(
        st.tuples(st.integers(0, 30), st.integers(1, 10_000_000)),
        min_size=1,
        max_size=5,
        unique_by=lambda i: i[0],
    ),
    low=st.integers(0, 10**9),
    more=st.integers(0, 10**9),
)
def test_every_scaled_amount_is_non_decreasing_in_the_first_amount(
    items: list[tuple[int, int]], low: int, more: int
) -> None:
    """What keeps the sustainable-income bisection monotone: more first-phase money never means
    less in any phase."""
    plan = [phase(LAST_RETIREMENT, offset, minor) for offset, minor in items]
    a = scale_from_retirement(plan, low, AUD)
    b = scale_from_retirement(plan, low + more, AUD)
    assert all(
        y.annual_amount.amount_minor >= x.annual_amount.amount_minor
        for x, y in zip(ordered(a), ordered(b), strict=True)
    )


# --- layering ------------------------------------------------------------------------------


def test_the_scenarios_list_replaces_the_default_as_a_whole() -> None:
    default = [phase(LAST_RETIREMENT, 0, 100), phase(LAST_RETIREMENT, 10, 50)]
    own = [phase(LAST_RETIREMENT, 0, 200)]
    resolved = pick(default, own)
    assert resolved is not None
    assert (resolved.phases, resolved.layer) == (tuple(own), "scenario")


def test_a_scenario_with_no_list_inherits_and_no_list_anywhere_is_unset() -> None:
    default = [phase(LAST_RETIREMENT, 10, 50), phase(LAST_RETIREMENT, 0, 100)]
    resolved = pick(default, [])
    assert resolved is not None
    assert (resolved.phases, resolved.layer) == (ordered(default), "household")
    assert pick([], []) is None


# --- validation ----------------------------------------------------------------------------


def test_a_good_list_parses_in_the_order_given() -> None:
    phases, errors = parse([(LAST_RETIREMENT, 10, "55000"), (START, 0, "1,000.50")])
    assert errors == []
    assert [(p.anchor, p.offset_years, p.annual_amount.amount_minor) for p in phases] == [
        (LAST_RETIREMENT, 10, 5_500_000),
        (START, 0, 100_050),
    ]


@pytest.mark.parametrize(
    ("entries", "fields"),
    [
        ([("sometime", 0, "100")], ["spending_phases.0.anchor"]),
        ([(LAST_RETIREMENT, -1, "100")], ["spending_phases.0.offset_years"]),
        ([(LAST_RETIREMENT, 121, "100")], ["spending_phases.0.offset_years"]),
        ([(LAST_RETIREMENT, 0, "0")], ["spending_phases.0.annual_amount"]),
        ([(LAST_RETIREMENT, 0, "-5")], ["spending_phases.0.annual_amount"]),
        ([(LAST_RETIREMENT, 0, "1.234")], ["spending_phases.0.annual_amount"]),
        ([(LAST_RETIREMENT, 0, "lots")], ["spending_phases.0.annual_amount"]),
        (
            [(LAST_RETIREMENT, 0, "100"), (START, 0, "100"), (LAST_RETIREMENT, 0, "50")],
            ["spending_phases.2.offset_years"],  # the duplicate is the later one
        ),
        (
            # A bad amount does not hide the duplicate: both are reported at once.
            [(LAST_RETIREMENT, 0, "100"), (LAST_RETIREMENT, 0, "0")],
            ["spending_phases.1.offset_years", "spending_phases.1.annual_amount"],
        ),
        (
            [("x", -1, "0"), (LAST_RETIREMENT, 0, "100"), (LAST_RETIREMENT, 0, "100")],
            [
                "spending_phases.0.anchor",
                "spending_phases.0.offset_years",
                "spending_phases.0.annual_amount",
                "spending_phases.2.offset_years",
            ],
        ),
    ],
)
def test_every_problem_is_reported_against_its_own_field(
    entries: list[tuple[str, int, str]], fields: list[str]
) -> None:
    _, errors = parse(entries)
    assert [e.field for e in errors] == fields
