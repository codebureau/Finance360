"""Line debits in the engine (ADR-0028): golden answers worked by hand, then Hypothesis invariants.

Every figure is minor units (cents), calculated on paper; all members, ids and amounts are invented.
The no-debit goldens in ``test_projection_engine.py`` are unchanged by this slice (only the recorded
engine version moved), which is the proof that a run without debits gives the same figures.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_projection_engine import SETTINGS, aud, closings, make, projection_inputs

from finance360.capabilities.projection import input as pinput
from finance360.capabilities.projection.engine import DebitRow, project
from finance360.capabilities.projection.input import (
    Inflow,
    InputError,
    Line,
    LineDebit,
    Outflow,
    ProjectionInput,
)
from finance360.core.money import Money
from finance360.core.rate import Rate


def debit(
    year: int,
    amount: int,
    *,
    member: int = 1,
    category: str = "superannuation",
    reason: str = "div293",
    ref: str = "FY2025-26",
) -> LineDebit:
    return LineDebit(member, category, year, aud(amount), reason, ref)


def with_debits(base: ProjectionInput, *debits: LineDebit) -> ProjectionInput:
    return replace(base, debits=debits)


# --- golden --------------------------------------------------------------------------------


def test_golden_a_debit_leaves_the_line_and_the_household() -> None:
    # Super 10,000.00 at 0%, not drawable for 5 years, no spending. Year 1 pays Division 293 of
    # 300.00 from the line: 1,000,000 -> 1,000,000 (y0) -> 970,000 (y1) -> 970,000 (y2).
    base = make(
        lines=[Line(1, "superannuation", aud(1_000_000), Rate(0), 5)],
        years=3,
        order=("superannuation",),
    )
    result = project(with_debits(base, debit(1, 30_000)))
    assert closings(result, 1, "superannuation") == [1_000_000, 970_000, 970_000]
    year1 = result.years[1]
    assert year1.lines[0].debited == aud(30_000) and year1.lines[0].withdrawn == aud(0)
    assert year1.debits == (
        DebitRow(1, "superannuation", "div293", "FY2025-26", aud(30_000), aud(30_000), aud(0)),
    )
    # It is not spending: nothing is needed and nothing is short.
    assert year1.need == aud(0) and year1.shortfall == aud(0)
    assert result.years[0].debits == () and result.years[0].lines[0].debited == aud(0)


def test_golden_a_debit_is_paid_from_growth_and_credits_of_its_own_line() -> None:
    # Year 0: super 1,000.00 grows 10% to 1,100.00 and is credited 50.00 = 1,150.00 available.
    # A debit of 1,200.00 pays 1,150.00 and leaves 50.00 unpaid. The 50.00 becomes the year's need,
    # met from cash 400.00 (the first Category).
    base = make(
        lines=[
            Line(1, "cash", aud(40_000), Rate(0), 0),
            Line(1, "superannuation", aud(100_000), Rate(100_000), 9),
        ],
        years=1,
        inflows=[Inflow(1, "superannuation", aud(5_000), 0, None)],
        order=("cash", "superannuation"),
    )
    row = project(with_debits(base, debit(0, 120_000))).years[0]
    sup = next(x for x in row.lines if x.category_key == "superannuation")
    cash = next(x for x in row.lines if x.category_key == "cash")
    assert (sup.growth, sup.credited, sup.debited, sup.closing) == (
        aud(10_000),
        aud(5_000),
        aud(115_000),
        aud(0),
    )
    assert row.debits[0].requested == aud(120_000)
    assert (row.debits[0].paid, row.debits[0].unpaid) == (aud(115_000), aud(5_000))
    assert row.need == aud(5_000) and row.shortfall == aud(0)
    assert (cash.withdrawn, cash.closing) == (aud(5_000), aud(35_000))


def test_golden_the_unpaid_part_is_drawn_down_and_can_be_short() -> None:
    # Super 200.00 cannot pay 300.00: 100.00 is paid personally. Cash 40.00 covers part of it, so
    # 60.00 is the shortfall. Nothing is borrowed or carried: year 1's need is 0 again.
    base = make(
        lines=[
            Line(1, "cash", aud(4_000), Rate(0), 0),
            Line(1, "superannuation", aud(20_000), Rate(0), 9),
        ],
        years=2,
        order=("cash", "superannuation"),
    )
    result = project(with_debits(base, debit(0, 30_000)))
    row = result.years[0]
    assert row.lines[1].closing == aud(0) and row.lines[1].debited == aud(20_000)
    assert (row.need, row.shortfall) == (aud(10_000), aud(6_000))
    assert row.lines[0].withdrawn == aud(4_000) and row.lines[0].closing == aud(0)
    assert result.funds_last_to_age is not None and not result.lasts_beyond_horizon
    assert (result.years[1].need, result.years[1].shortfall) == (aud(0), aud(0))


def test_golden_the_unpaid_part_adds_to_spending_in_the_same_need() -> None:
    # Spending 50.00 plus 30.00 unpaid = a need of 80.00, drawn from investments 500.00.
    base = make(
        lines=[
            Line(1, "investments", aud(50_000), Rate(0), 0),
            Line(1, "superannuation", aud(0), Rate(0), 9),
        ],
        years=1,
        outflows=[Outflow(aud(5_000), 0, None)],
        order=("investments", "superannuation"),
    )
    row = project(with_debits(base, debit(0, 3_000))).years[0]
    assert row.debits[0].paid == aud(0) and row.debits[0].unpaid == aud(3_000)
    assert row.need == aud(8_000) and row.shortfall == aud(0)
    assert row.lines[0].closing == aud(42_000)


def test_golden_two_debits_on_one_line_are_paid_in_canonical_order() -> None:
    # Super 100.00; "A" 70.00 is paid in full, then "B" 70.00 gets the 30.00 left.
    base = make(
        lines=[Line(1, "superannuation", aud(10_000), Rate(0), 9)],
        years=1,
        order=("superannuation",),
    )
    rows = (
        project(with_debits(base, debit(0, 7_000, ref="B"), debit(0, 7_000, ref="A")))
        .years[0]
        .debits
    )
    assert [(d.ref, d.paid, d.unpaid) for d in rows] == [
        ("A", aud(7_000), aud(0)),
        ("B", aud(3_000), aud(4_000)),
    ]


def test_a_debit_ignores_the_access_year_of_its_line() -> None:
    # Super is not drawable until year 5, but the fund still pays Division 293 in year 0.
    base = make(
        lines=[Line(1, "superannuation", aud(10_000), Rate(0), 5)],
        years=1,
        order=("superannuation",),
    )
    assert project(with_debits(base, debit(0, 4_000))).years[0].lines[0].closing == aud(6_000)


def test_an_empty_debit_list_changes_no_figure() -> None:
    base = make(
        lines=[Line(1, "investments", aud(10_000_000), Rate(50_000), 0)],
        years=5,
        outflows=[Outflow(aud(500_000), 0, None)],
    )
    plain = project(base)
    assert plain == project(with_debits(base))
    assert all(x.debited == aud(0) for row in plain.years for x in row.lines)
    assert all(row.debits == () for row in plain.years)


# --- the input -----------------------------------------------------------------------------


def test_debits_are_validated() -> None:
    base = make(
        lines=[Line(1, "superannuation", aud(100), Rate(0), 0)], years=2, order=("superannuation",)
    )
    bad = [
        debit(0, 0),  # not above 0
        debit(0, -5),
        debit(2, 5),  # past the horizon
        debit(-1, 5),
        debit(0, 5, member=9),  # no such line
        debit(0, 5, category="cash"),
        debit(0, 5, reason="gift"),  # not a known reason
        debit(0, 5, ref=""),
        debit(0, 5, ref="x" * 41),
    ]
    for item in bad:
        with pytest.raises(InputError):
            with_debits(base, item)
    with pytest.raises(InputError):
        with_debits(base, LineDebit(1, "superannuation", 0, Money(5, "USD"), "div293", "x"))
    assert with_debits(base, debit(1, 5, ref="x" * 40)).debits[0].ref == "x" * 40


def test_debits_are_held_in_canonical_order_and_serialised_as_money() -> None:
    base = make(
        lines=[Line(1, "superannuation", aud(100), Rate(0), 0)], years=3, order=("superannuation",)
    )
    shuffled = with_debits(base, debit(2, 5), debit(0, 9, ref="B"), debit(0, 9, ref="A"))
    assert [(d.year, d.ref) for d in shuffled.debits] == [(0, "A"), (0, "B"), (2, "FY2025-26")]
    payload = pinput.to_payload(shuffled)
    assert payload["debits"][0] == {
        "amount": {"ccy": "AUD", "minor": 9},
        "category_key": "superannuation",
        "member_id": 1,
        "reason": "div293",
        "ref": "A",
        "year": 0,
    }
    assert pinput.from_json(pinput.to_json(shuffled)) == shuffled


def test_a_version_1_snapshot_upcasts_to_an_input_with_no_debits() -> None:
    base = make(lines=[Line(1, "cash", aud(100), Rate(0), 0)], years=2)
    old = {k: v for k, v in pinput.to_payload(base).items() if k != "debits"} | {"input_version": 1}
    loaded = pinput.from_payload(old)
    assert loaded == base and loaded.debits == () and loaded.input_version == pinput.INPUT_VERSION
    assert project(loaded) == project(base)


# --- Hypothesis ----------------------------------------------------------------------------


@st.composite
def inputs_with_debits(draw: st.DrawFn) -> ProjectionInput:
    base = draw(projection_inputs())
    keys = [(x.member_id, x.category_key) for x in base.lines]
    debits: list[LineDebit] = []
    for _ in range(draw(st.integers(1, 6))):
        member, category = draw(st.sampled_from(keys))
        debits.append(
            LineDebit(
                member,
                category,
                draw(st.integers(0, base.years - 1)),
                aud(draw(st.integers(1, 10**10))),
                draw(st.sampled_from(pinput.DEBIT_REASONS)),
                draw(st.text("abc", min_size=1, max_size=3)),
            )
        )
    return replace(base, debits=tuple(debits))


@SETTINGS
@given(inputs_with_debits())
def test_debits_never_take_a_line_below_zero_and_the_unpaid_part_is_in_need(
    projection_input: ProjectionInput,
) -> None:
    result = project(projection_input)
    for row in result.years:
        spending = sum(
            o.amount.amount_minor
            for o in projection_input.outflows
            if o.from_year
            <= row.t
            < (projection_input.years if o.until_year is None else o.until_year)
        )
        unpaid = sum(d.unpaid.amount_minor for d in row.debits)
        # requested = paid + unpaid, and nothing is paid that the line did not have.
        for d in row.debits:
            assert d.requested.amount_minor == d.paid.amount_minor + d.unpaid.amount_minor
            assert d.paid.amount_minor >= 0 and d.unpaid.amount_minor >= 0
        assert len(row.debits) == sum(1 for d in projection_input.debits if d.year == row.t)
        # The unpaid part is in need, and need is still met by withdrawals or is the shortfall.
        assert row.need.amount_minor == spending + unpaid
        assert sum(x.withdrawn.amount_minor for x in row.lines) + row.shortfall.amount_minor == (
            row.need.amount_minor
        )
        for x in row.lines:
            paid_here = sum(
                d.paid.amount_minor
                for d in row.debits
                if (d.member_id, d.category_key) == (x.member_id, x.category_key)
            )
            assert x.debited.amount_minor == paid_here
            # The line never goes negative, and the identity gains ``debited``.
            for money in (x.opening, x.credited, x.debited, x.withdrawn, x.closing):
                assert money.amount_minor >= 0
            assert x.closing.amount_minor == (
                x.opening.amount_minor
                + x.growth.amount_minor
                + x.credited.amount_minor
                - x.debited.amount_minor
                - x.withdrawn.amount_minor
            )


@SETTINGS
@given(inputs_with_debits())
def test_a_debit_input_survives_a_json_round_trip_and_stays_deterministic(
    projection_input: ProjectionInput,
) -> None:
    again = pinput.from_json(pinput.to_json(projection_input))
    assert again == projection_input
    assert project(again) == project(projection_input)
