"""Rate: parts per million, parsed exactly, never rounded and never a float (ADR-0015 section 4)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.core.rate import PPM, Rate, RateError

ppms = st.integers(-PPM + 1, PPM)


@pytest.mark.parametrize(
    ("text", "ppm"),
    [
        ("6.5", 65_000),
        ("0", 0),
        ("-2.25", -22_500),
        ("100", PPM),
        ("-99.9999", -999_999),
        ("0.0001", 1),
        ("-0.0001", -1),
        (" 3 ", 30_000),
        ("2.50", 25_000),
    ],
)
def test_percent_strings_parse_to_exact_ppm(text: str, ppm: int) -> None:
    assert Rate.from_percent(text) == Rate(ppm)


def test_a_decimal_parses_the_same_as_its_string() -> None:
    assert Rate.from_percent(Decimal("6.5")) == Rate(65_000)
    assert Rate.from_percent(Decimal("6.50E+0")) == Rate(65_000)
    assert Rate.from_percent(Decimal("1E+1")) == Rate(100_000)


@pytest.mark.parametrize("text", ["0.00001", "6.50001", "-2.123456", "0.00005"])
def test_precision_finer_than_four_places_of_percent_is_rejected_not_rounded(text: str) -> None:
    with pytest.raises(RateError, match="at most 4 decimal places"):
        Rate.from_percent(text)


@pytest.mark.parametrize("text", ["", "abc", "6.5%", "1e2", "NaN", "Infinity", "--1", "1.", ".5"])
def test_not_a_percentage_is_rejected(text: str) -> None:
    with pytest.raises(RateError):
        Rate.from_percent(text)


@pytest.mark.parametrize("text", ["100.0001", "-100", "-100.5", "250"])
def test_rates_are_capped_at_plus_100_percent_and_above_minus_100_percent(text: str) -> None:
    with pytest.raises(RateError):
        Rate.from_percent(text)


def test_a_float_raises() -> None:
    with pytest.raises(TypeError):
        Rate.from_percent(6.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Rate(6.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Rate(True)  # a bool is not a count of ppm


def test_a_nan_or_infinite_decimal_is_rejected() -> None:
    for bad in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(RateError):
            Rate.from_percent(bad)


@pytest.mark.parametrize(
    ("ppm", "text"),
    [(65_000, "6.5"), (0, "0"), (-22_500, "-2.25"), (PPM, "100"), (1, "0.0001"), (-1, "-0.0001")],
)
def test_the_percent_string_is_canonical(ppm: int, text: str) -> None:
    assert Rate(ppm).to_percent() == text


@given(ppms)
def test_a_rate_round_trips_through_its_percent_string(ppm: int) -> None:
    rate = Rate(ppm)
    assert Rate.from_percent(rate.to_percent()) == rate
    assert Rate.from_percent(Decimal(rate.to_percent())) == rate


@given(
    st.integers(-(10**6), 10**6),
    st.integers(5, 9),
)
def test_any_fifth_decimal_place_is_rejected(whole_ppm: int, fifth: int) -> None:
    # A percent with a non-zero 5th place is finer than a ppm.
    text = f"{abs(whole_ppm) // 10**4}.{abs(whole_ppm) % 10**4:04d}{fifth}"
    with pytest.raises(RateError):
        Rate.from_percent(text)
