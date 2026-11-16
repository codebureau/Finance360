"""The pure best-Valuation selector: golden answers (unit) and invariants (Hypothesis).

ADR-0009 section 3: the latest ``as_at`` on or before the date wins; source rank
(statement > manual > derived > estimate) only breaks a same-date tie; no row is an explicit
``NoValuation``, never zero.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from finance360.core.money import Money, format_amount, parse_amount
from finance360.core.valuation import (
    SOURCE_RANK,
    SOURCES,
    NoValuation,
    ValuationPoint,
    best_on_or_before,
)

AUD = "AUD"


def _p(id_: int, day: str, source: str, minor: int) -> ValuationPoint:
    return ValuationPoint(id_, date.fromisoformat(day), source, Money(minor, AUD))


# --- golden ---------------------------------------------------------------------------------

ROWS = [
    _p(1, "2026-01-31", "manual", 10_000),
    _p(2, "2026-02-28", "estimate", 20_000),
    _p(3, "2026-02-28", "statement", 25_000),
    _p(4, "2026-03-31", "estimate", 30_000),
]


def _on(day: str, rows: list[ValuationPoint] = ROWS) -> ValuationPoint | NoValuation:
    return best_on_or_before(rows, date.fromisoformat(day))


def _id(result: ValuationPoint | NoValuation) -> int | None:
    return None if isinstance(result, NoValuation) else result.id


def test_before_the_first_valuation_there_is_none() -> None:
    result = _on("2026-01-30")
    assert result == NoValuation(date(2026, 1, 30))


def test_no_rows_at_all_is_none_not_zero() -> None:
    result = best_on_or_before([], date(2026, 1, 1))
    assert isinstance(result, NoValuation)
    assert result.on == date(2026, 1, 1)


def test_the_valuation_on_the_date_itself_counts() -> None:
    assert _id(_on("2026-01-31")) == 1


def test_a_gap_carries_the_previous_valuation_forward() -> None:
    assert _id(_on("2026-02-15")) == 1
    assert _id(_on("2026-03-30")) == 3


def test_same_date_ties_go_to_the_higher_ranked_source() -> None:
    assert _id(_on("2026-02-28")) == 3  # statement beats estimate


def test_a_later_estimate_beats_an_earlier_statement() -> None:
    assert _id(_on("2026-03-31")) == 4  # latest as_at wins; rank is only a tie-break


def test_future_valuations_are_ignored_and_a_far_future_date_gets_the_latest() -> None:
    assert _id(_on("2026-02-01")) == 1  # the later rows are in the future relative to the date
    assert _id(_on("2099-01-01")) == 4


def test_the_full_source_order_on_one_date() -> None:
    day = "2026-05-05"
    rows = [_p(i, day, s, 100 * i) for i, s in enumerate(["estimate", "derived", "manual"], 1)]
    assert _id(_on(day, rows)) == 3  # manual
    assert _id(_on(day, rows[:2])) == 2  # derived beats estimate
    rows.append(_p(9, day, "statement", 1))
    assert _id(_on(day, rows)) == 9  # statement beats all


def test_a_zero_valuation_is_a_value_not_an_absence() -> None:
    rows = [_p(1, "2026-01-01", "manual", 5_000), _p(2, "2026-02-01", "manual", 0)]
    result = _on("2026-03-01", rows)
    assert isinstance(result, ValuationPoint) and result.amount == Money.zero(AUD)


def test_a_closed_account_keeps_its_history_and_reads_zero_from_the_close_date() -> None:
    rows = [_p(1, "2026-01-01", "statement", 5_000), _p(2, "2026-06-30", "manual", 0)]
    assert _id(_on("2026-06-29", rows)) == 1
    assert _id(_on("2026-06-30", rows)) == 2
    assert _id(_on("2027-01-01", rows)) == 2


def test_input_order_does_not_matter() -> None:
    shuffled = ROWS[::-1]
    for day in ("2026-01-31", "2026-02-28", "2026-03-31", "2030-01-01"):
        assert _on(day, shuffled) == _on(day)


# --- Hypothesis -----------------------------------------------------------------------------

START = date(2024, 1, 1)
days = st.integers(0, 120).map(lambda n: START + timedelta(days=n))


@st.composite
def current_rows(draw: st.DrawFn) -> list[ValuationPoint]:
    """Current rows only: at most one per (date, source), like the partial unique index."""
    keys = draw(st.sets(st.tuples(days, st.sampled_from(SOURCES)), max_size=25))
    rows = [
        ValuationPoint(i, d, s, Money(draw(st.integers(-(10**9), 10**9)), AUD))
        for i, (d, s) in enumerate(sorted(keys), start=1)
    ]
    return draw(st.permutations(rows))


def _reference(rows: list[ValuationPoint], on: date) -> ValuationPoint | NoValuation:
    eligible = [r for r in rows if r.as_at <= on]
    if not eligible:
        return NoValuation(on)
    latest = max(r.as_at for r in eligible)
    return max((r for r in eligible if r.as_at == latest), key=lambda r: SOURCE_RANK[r.source])


@given(current_rows(), days)
def test_matches_a_brute_force_reference_for_any_entry_order_and_gaps(
    rows: list[ValuationPoint], on: date
) -> None:
    assert best_on_or_before(rows, on) == _reference(rows, on)


@given(current_rows(), days, st.randoms(use_true_random=False))
def test_the_answer_does_not_depend_on_the_order_rows_were_entered(
    rows: list[ValuationPoint], on: date, rng: random.Random
) -> None:
    shuffled = list(rows)
    rng.shuffle(shuffled)
    assert best_on_or_before(shuffled, on) == best_on_or_before(rows, on)


@given(current_rows(), days)
def test_the_answer_is_never_in_the_future_and_nothing_later_is_skipped(
    rows: list[ValuationPoint], on: date
) -> None:
    result = best_on_or_before(rows, on)
    if isinstance(result, NoValuation):
        assert all(r.as_at > on for r in rows)
    else:
        assert result.as_at <= on
        assert not any(result.as_at < r.as_at <= on for r in rows)


@given(current_rows(), days, days)
def test_adding_a_later_dated_row_never_changes_earlier_answers(
    rows: list[ValuationPoint], on: date, later: date
) -> None:
    """Gaps and out-of-order entry: a row dated after ``on`` can't affect the answer for ``on``."""
    extra = ValuationPoint(10_000, max(later, on + timedelta(days=1)), "statement", Money(1, AUD))
    assert best_on_or_before([*rows, extra], on) == best_on_or_before(rows, on)


# --- Money text helpers used at the API boundary --------------------------------------------


def test_amounts_parse_and_format_exactly() -> None:
    assert parse_amount("1234.56", "AUD") == Money(123_456, "AUD")
    assert parse_amount(" -1,234.5 ", "AUD") == Money(-123_450, "AUD")
    assert parse_amount("7", "AUD") == Money(700, "AUD")
    assert parse_amount("500", "JPY") == Money(500, "JPY")
    assert parse_amount("1.234", "KWD") == Money(1_234, "KWD")
    assert format_amount(Money(123_456, "AUD")) == "1234.56"
    assert format_amount(Money(-5, "AUD")) == "-0.05"
    assert format_amount(Money(500, "JPY")) == "500"
    assert format_amount(Money(1_234, "KWD")) == "1.234"


def test_over_precise_and_malformed_amounts_are_rejected_not_rounded() -> None:
    import pytest

    from finance360.core.money import MoneyError

    for text, ccy in [("12.345", "AUD"), ("1.5", "JPY"), ("", "AUD"), ("abc", "AUD"),
                      ("1e3", "AUD"), ("NaN", "AUD"), ("1.2.3", "AUD"), ("--1", "AUD"),
                      (".5", "AUD"), ("5.", "AUD")]:  # fmt: skip
        with pytest.raises(MoneyError):
            parse_amount(text, ccy)


@given(st.integers(-(2**62), 2**62), st.sampled_from(sorted(["AUD", "JPY", "KWD", "USD", "GBP"])))
def test_format_then_parse_round_trips_exactly(minor: int, currency: str) -> None:
    money = Money(minor, currency)
    assert parse_amount(format_amount(money), currency) == money
