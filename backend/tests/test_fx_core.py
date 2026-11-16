"""The pure FX lookup and conversion: golden answers (unit) and invariants (Hypothesis).

ADR-0009 section 4: exact pair only, the latest ``as_at`` on or before the date, an explicit
``NoRate`` otherwise (never 1.0 or zero), half-even rounding, a documented there-and-back bound,
and no floats anywhere.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from finance360.core.fx import (
    MAX_RATE,
    FxPoint,
    NoRate,
    format_rate,
    parse_rate,
    rate_on_or_before,
)
from finance360.core.money import INT64_MAX, Money, MoneyError, convert, exponent


def _p(id_: int, day: str, rate: str, frm: str = "GBP", to: str = "AUD") -> FxPoint:
    return FxPoint(id_, frm, to, date.fromisoformat(day), Decimal(rate))


ROWS = [
    _p(1, "2026-01-01", "1.9000"),
    _p(2, "2026-03-01", "2.0000"),
    _p(3, "2026-02-01", "1.9500"),  # entered out of order
    _p(4, "2026-02-01", "9.9", "USD"),  # another pair is never used
    _p(5, "2026-02-01", "0.5", "AUD", "GBP"),  # nor the inverse pair
]


def _at(day: str, frm: str = "GBP", to: str = "AUD") -> FxPoint | NoRate:
    return rate_on_or_before(ROWS, frm, to, date.fromisoformat(day))


def _id(result: FxPoint | NoRate) -> int | None:
    return None if isinstance(result, NoRate) else result.id


# --- golden ---------------------------------------------------------------------------------


def test_before_the_first_rate_there_is_none_not_one_or_zero() -> None:
    assert _at("2025-12-31") == NoRate("GBP", "AUD", date(2025, 12, 31))


def test_the_rate_on_the_date_itself_counts_and_a_gap_carries_the_previous_forward() -> None:
    assert _id(_at("2026-01-01")) == 1
    assert _id(_at("2026-01-31")) == 1
    assert _id(_at("2026-02-01")) == 3
    assert _id(_at("2026-02-28")) == 3
    assert _id(_at("2099-01-01")) == 2  # no maximum age


def test_only_the_exact_pair_is_used_no_inverse_and_no_triangulation() -> None:
    assert _id(_at("2026-02-01", "USD", "AUD")) == 4
    assert isinstance(_at("2026-02-01", "EUR", "AUD"), NoRate)
    assert isinstance(_at("2026-02-01", "USD", "GBP"), NoRate)  # not via AUD
    assert _id(_at("2026-02-01", "AUD", "GBP")) == 5  # only because it was stored as such


def test_input_order_does_not_matter() -> None:
    for day in ("2026-01-01", "2026-02-15", "2026-03-01", "2030-01-01"):
        assert rate_on_or_before(ROWS[::-1], "GBP", "AUD", date.fromisoformat(day)) == _at(day)


def test_a_same_date_tie_goes_to_the_most_recently_entered() -> None:
    rows = [_p(7, "2026-05-05", "2.1"), _p(9, "2026-05-05", "2.2"), _p(8, "2026-05-05", "2.3")]
    assert _id(rate_on_or_before(rows, "GBP", "AUD", date(2026, 5, 5))) == 9


def test_conversion_is_half_even_and_exact_beyond_two_places() -> None:
    # 100.00 GBP at 2.0143 = 201.43 AUD exactly
    assert convert(Money(10_000, "GBP"), Decimal("2.0143"), "AUD") == Money(20_143, "AUD")
    # 0.01 GBP at 1.5 = 1.5 minor units -> ties go to even (2); 0.03 at 1.5 = 4.5 -> 4
    assert convert(Money(1, "GBP"), Decimal("1.5"), "AUD") == Money(2, "AUD")
    assert convert(Money(3, "GBP"), Decimal("1.5"), "AUD") == Money(4, "AUD")
    assert convert(Money(-3, "GBP"), Decimal("1.5"), "AUD") == Money(-4, "AUD")
    # ten decimal places survive
    assert convert(Money(10**10, "GBP"), Decimal("1.2345678901"), "AUD") == Money(
        12_345_678_901, "AUD"
    )


def test_a_float_rate_is_refused() -> None:
    with pytest.raises(TypeError):
        convert(Money(100, "GBP"), 2.0143, "AUD")  # type: ignore[arg-type]


def test_rates_parse_exactly_and_reject_what_would_need_rounding() -> None:
    assert parse_rate("2.0143") == Decimal("2.0143")
    assert parse_rate(" 2 ") == Decimal("2")
    assert parse_rate("0.0000000001") == Decimal("1E-10")
    assert parse_rate("1.2345678901") == Decimal("1.2345678901")
    for bad in (
        "",
        "abc",
        "0",
        "0.0",
        "-1",
        "1e3",
        "NaN",
        "1.",
        ".5",
        "1,5",
        "1.23456789012",
        "٢.٥",
        "２",
    ):
        with pytest.raises(MoneyError):
            parse_rate(bad)
    with pytest.raises(MoneyError):
        parse_rate(str(MAX_RATE + Decimal("0.0000000001")))
    assert parse_rate(str(MAX_RATE)) == MAX_RATE


def test_rates_format_without_trailing_zeros_but_two_places() -> None:
    assert format_rate(Decimal("2.0143000000")) == "2.0143"
    assert format_rate(Decimal("2.0000000000")) == "2.00"
    assert format_rate(Decimal("2.5000000000")) == "2.50"
    assert format_rate(Decimal("100.0000000000")) == "100.00"
    assert format_rate(Decimal("0.0000000001")) == "0.0000000001"


# --- Hypothesis -----------------------------------------------------------------------------

START = date(2024, 1, 1)
days = st.integers(0, 120).map(lambda n: START + timedelta(days=n))
CCYS = ["GBP", "USD", "EUR", "JPY", "KWD"]


@st.composite
def current_rows(draw: st.DrawFn) -> list[FxPoint]:
    """Current rows: at most one per (pair, date), like the partial unique index."""
    keys = draw(st.sets(st.tuples(st.sampled_from(CCYS), days), max_size=25))
    rows = [
        FxPoint(i, f, "AUD", d, Decimal(draw(st.integers(1, 10**12))).scaleb(-10))
        for i, (f, d) in enumerate(sorted(keys), start=1)
    ]
    return draw(st.permutations(rows))


def _reference(rows: list[FxPoint], f: str, on: date) -> FxPoint | NoRate:
    eligible = [r for r in rows if r.from_ccy == f and r.as_at <= on]
    return max(eligible, key=lambda r: r.as_at) if eligible else NoRate(f, "AUD", on)


@given(current_rows(), st.sampled_from(CCYS), days)
def test_lookup_matches_a_brute_force_reference_for_gaps_and_any_entry_order(
    rows: list[FxPoint], f: str, on: date
) -> None:
    assert rate_on_or_before(rows, f, "AUD", on) == _reference(rows, f, on)


@given(current_rows(), st.sampled_from(CCYS), days, st.randoms(use_true_random=False))
def test_the_answer_does_not_depend_on_the_order_rates_were_entered(
    rows: list[FxPoint], f: str, on: date, rng: random.Random
) -> None:
    shuffled = list(rows)
    rng.shuffle(shuffled)
    assert rate_on_or_before(shuffled, f, "AUD", on) == rate_on_or_before(rows, f, "AUD", on)


@given(current_rows(), st.sampled_from(CCYS), days, days)
def test_a_later_dated_rate_never_changes_an_earlier_answer(
    rows: list[FxPoint], f: str, on: date, later: date
) -> None:
    extra = FxPoint(10_000, f, "AUD", max(later, on + timedelta(days=1)), Decimal("3"))
    assert rate_on_or_before([*rows, extra], f, "AUD", on) == rate_on_or_before(rows, f, "AUD", on)


@given(current_rows(), st.sampled_from(CCYS), days)
def test_a_missing_rate_is_explicit_never_a_default(rows: list[FxPoint], f: str, on: date) -> None:
    result = rate_on_or_before(rows, f, "AUD", on)
    if isinstance(result, NoRate):
        assert (result.from_ccy, result.to_ccy, result.on) == (f, "AUD", on)
        assert not any(r.from_ccy == f and r.as_at <= on for r in rows)
    else:
        assert result.rate > 0 and result.as_at <= on


# --- source precedence: manual first (ADR-0013 section 6) -------------------------------------


def _s(id_: int, day: str, rate: str, source: str) -> FxPoint:
    return FxPoint(id_, "GBP", "AUD", date.fromisoformat(day), Decimal(rate), source)


def test_a_manual_rate_beats_a_provider_rate_on_the_same_date_whichever_was_entered_last() -> None:
    manual, fetched = _s(1, "2026-10-19", "2.5", "manual"), _s(2, "2026-10-19", "2.0", "rba")
    on = date(2026, 10, 20)
    assert rate_on_or_before([manual, fetched], "GBP", "AUD", on) == manual
    assert rate_on_or_before([fetched, manual], "GBP", "AUD", on) == manual
    later_fetched = _s(3, "2026-10-19", "2.1", "rba")  # even a fetched row with a higher id
    assert rate_on_or_before([later_fetched, manual], "GBP", "AUD", on) == manual


def test_a_newer_provider_rate_beats_an_older_manual_one_and_the_source_is_reported() -> None:
    old_manual, new_fetched = (
        _s(1, "2026-10-01", "2.5", "manual"),
        _s(2, "2026-10-19", "2.0", "rba"),
    )
    found = rate_on_or_before([old_manual, new_fetched], "GBP", "AUD", date(2026, 10, 20))
    assert isinstance(found, FxPoint) and (found.source, found.rate) == ("rba", Decimal("2.0"))


@given(
    st.lists(st.tuples(days, st.sampled_from(["manual", "rba", "other"])), min_size=1, max_size=12),
    days,
    st.randoms(use_true_random=False),
)
def test_precedence_picks_the_latest_date_then_manual_then_the_named_provider_in_any_order(
    keys: list[tuple[date, str]], on: date, rng: random.Random
) -> None:
    unique = sorted(set(keys))
    rows = [_s(i, d.isoformat(), "2", src) for i, (d, src) in enumerate(unique, start=1)]
    shuffled = list(rows)
    rng.shuffle(shuffled)
    found = rate_on_or_before(shuffled, "GBP", "AUD", on)
    eligible = [r for r in rows if r.as_at <= on]
    if not eligible:
        assert isinstance(found, NoRate)
        return
    assert isinstance(found, FxPoint)
    latest = max(r.as_at for r in eligible)
    assert found.as_at == latest
    sources = {r.source for r in eligible if r.as_at == latest}
    best = "manual" if "manual" in sources else "rba" if "rba" in sources else "other"
    assert found.source == best


def _factor(rate: Decimal, frm: str, to: str) -> Fraction:
    """Minor units of ``to`` per minor unit of ``frm``, exactly."""
    return Fraction(rate) * Fraction(10) ** (exponent(to) - exponent(frm))


PAIRS = st.sampled_from([("GBP", "AUD"), ("JPY", "AUD"), ("KWD", "AUD"), ("USD", "JPY")])
minor_amounts = st.integers(-(10**11), 10**11)
# 2**a * 5**b * 10**c: the exact reciprocal is also a finite Decimal.
exact_rates = st.builds(
    lambda a, b, c: Decimal(2**a * 5**b).scaleb(c),
    st.integers(0, 6),
    st.integers(0, 6),
    st.integers(-3, 0),
)


@given(PAIRS, minor_amounts, exact_rates)
def test_there_and_back_at_the_exact_reciprocal_stays_within_the_documented_bound(
    pair: tuple[str, str], x: int, rate: Decimal
) -> None:
    frm, to = pair
    # A result Money can't hold is refused (test_money covers that); the bound is about the rest.
    assume(abs(x * _factor(rate, frm, to)) < INT64_MAX)
    there = convert(Money(x, frm), rate, to)
    back = convert(there, Decimal(1) / rate, frm)  # exact: rate is 2^a 5^b 10^c
    assert (Decimal(1) / rate) * rate == 1
    kb = 1 / _factor(rate, frm, to)
    assert abs(Fraction(back.amount_minor - x)) <= (1 + kb) / 2
    assert there.currency == to and back.currency == frm


@given(PAIRS, minor_amounts, st.integers(1, 10**13))
def test_there_and_back_with_a_rounded_return_rate_adds_only_the_documented_term(
    pair: tuple[str, str], x: int, units: int
) -> None:
    frm, to = pair
    rate = Decimal(units).scaleb(-10)
    back_rate = Decimal(10**30 // units).scaleb(-20)  # the reciprocal, floored at 20 places
    z = convert(convert(Money(x, frm), rate, to), back_rate, frm).amount_minor
    kf = _factor(rate, frm, to)
    kb_actual = _factor(back_rate, to, frm)
    bound = (1 + 1 / kf) / 2 + abs(x) * abs(kf * kb_actual - 1)
    assert abs(Fraction(z - x)) <= bound


@given(PAIRS, minor_amounts, st.integers(1, 10**13))
def test_conversion_is_exact_integer_arithmetic_rounded_once_half_even(
    pair: tuple[str, str], x: int, units: int
) -> None:
    frm, to = pair
    rate = Decimal(units).scaleb(-10)
    exact = Fraction(x) * _factor(rate, frm, to)
    got = convert(Money(x, frm), rate, to).amount_minor
    assert isinstance(got, int) and not isinstance(got, float)
    assert abs(Fraction(got) - exact) <= Fraction(1, 2)
    if abs(Fraction(got) - exact) == Fraction(1, 2):
        assert got % 2 == 0  # a tie goes to the even neighbour
