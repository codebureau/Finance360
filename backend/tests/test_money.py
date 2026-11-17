"""Money, rounding, conversion and allocation (ADR-0009 section 1). No floats, even in tests."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from finance360.core.money import (
    AUD,
    CURRENCY_EXPONENTS,
    INT64_MAX,
    INT64_MIN,
    CurrencyMismatchError,
    Money,
    MoneyError,
    allocate,
    ato_offset_reduced,
    ato_scale,
    ato_whole_dollars,
    convert,
    exponent,
    parse_amount,
    round_div,
    scale,
)

currencies = st.sampled_from(sorted(CURRENCY_EXPONENTS))
amounts = st.integers(INT64_MIN, INT64_MAX)
small = st.integers(-(10**12), 10**12)
money = st.builds(Money, amounts, currencies)


def money_in(ccy: str) -> st.SearchStrategy[Money]:
    return st.builds(Money, small, st.just(ccy))


# ---- construction -------------------------------------------------------------------------------


def test_aud_is_the_base_currency_with_two_decimals() -> None:
    assert AUD == "AUD"
    assert exponent("AUD") == 2
    assert exponent("JPY") == 0


@pytest.mark.parametrize("bad", [1.5, 0.0, float("nan"), float("inf")])
def test_float_amount_raises(bad: float) -> None:
    with pytest.raises(TypeError):
        Money(bad, "AUD")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [True, "12", None, Decimal("1")])
def test_non_int_amount_raises(bad: object) -> None:
    with pytest.raises(TypeError):
        Money(bad, "AUD")  # type: ignore[arg-type]


@pytest.mark.parametrize("ccy", ["XXX", "aud", "", "AU", None])
def test_unknown_currency_raises(ccy: object) -> None:
    with pytest.raises(MoneyError):
        Money(1, ccy)  # type: ignore[arg-type]


@pytest.mark.parametrize("amount", [INT64_MAX + 1, INT64_MIN - 1])
def test_amount_must_fit_signed_64_bit(amount: int) -> None:
    with pytest.raises(MoneyError):
        Money(amount, "AUD")


def test_64_bit_bounds_are_accepted() -> None:
    assert Money(INT64_MAX, "AUD").amount_minor == INT64_MAX
    assert Money(INT64_MIN, "AUD").amount_minor == INT64_MIN


def test_overflowing_arithmetic_raises_rather_than_wrapping() -> None:
    with pytest.raises(MoneyError):
        Money(INT64_MAX, "AUD") + Money(1, "AUD")
    with pytest.raises(MoneyError):
        Money(INT64_MAX, "AUD") * 2


def test_money_is_immutable_and_hashable() -> None:
    m = Money(5, "AUD")
    with pytest.raises(AttributeError):
        m.amount_minor = 6  # type: ignore[misc]
    assert {m: 1}[Money(5, "AUD")] == 1


# ---- arithmetic ---------------------------------------------------------------------------------


def test_adding_different_currencies_is_an_error() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(1, "AUD") + Money(1, "USD")
    with pytest.raises(CurrencyMismatchError):
        Money(1, "AUD") - Money(1, "USD")
    with pytest.raises(CurrencyMismatchError):
        assert Money(1, "AUD") < Money(1, "USD")


def test_money_equality_across_currencies_is_false_not_an_error() -> None:
    assert Money(1, "AUD") != Money(1, "USD")


def test_multiplying_by_a_float_or_decimal_is_refused() -> None:
    with pytest.raises(TypeError):
        Money(100, "AUD") * 1.5  # type: ignore[operator]
    with pytest.raises(TypeError):
        Money(100, "AUD") * Decimal("1.5")  # type: ignore[operator]


@given(a=money_in("AUD"), b=money_in("AUD"), c=money_in("AUD"))
def test_addition_is_commutative_associative_with_identity_and_inverse(
    a: Money, b: Money, c: Money
) -> None:
    assert a + b == b + a
    assert (a + b) + c == a + (b + c)
    assert a + Money.zero("AUD") == a
    assert a + (-a) == Money.zero("AUD")
    assert a - b == a + (-b)


@given(a=money_in("USD"), n=st.integers(-1000, 1000), m=st.integers(-1000, 1000))
def test_integer_multiplication_is_exact_and_distributes(a: Money, n: int, m: int) -> None:
    assert (a * n).amount_minor == a.amount_minor * n
    assert n * a == a * n
    assert a * (n + m) == a * n + a * m


@given(a=money_in("JPY"), b=money_in("JPY"))
def test_ordering_matches_amounts(a: Money, b: Money) -> None:
    assert (a < b) == (a.amount_minor < b.amount_minor)
    assert (a <= b) == (a.amount_minor <= b.amount_minor)
    assert (a > b) == (a.amount_minor > b.amount_minor)
    assert (a >= b) == (a.amount_minor >= b.amount_minor)


@given(a=money, ccy=currencies)
def test_different_currency_never_adds(a: Money, ccy: str) -> None:
    if ccy != a.currency:
        with pytest.raises(CurrencyMismatchError):
            a + Money(0, ccy)


# ---- rounding -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("num", "den", "expected"),
    [
        (1, 2, 0), (3, 2, 2), (5, 2, 2), (7, 2, 4),  # exact halves go to even
        (-1, 2, 0), (-3, 2, -2), (-5, 2, -2),
        (1, 3, 0), (2, 3, 1), (-1, 3, 0), (-2, 3, -1),
        (6, 3, 2), (0, 7, 0), (1, -2, 0), (3, -2, -2), (-3, -2, 2),
    ],
)  # fmt: skip
def test_round_div_is_half_to_even(num: int, den: int, expected: int) -> None:
    assert round_div(num, den) == expected


def test_round_div_by_zero_raises() -> None:
    with pytest.raises(ZeroDivisionError):
        round_div(1, 0)


@given(n=st.integers(-(10**30), 10**30), d=st.integers(1, 10**12))
def test_round_div_is_within_half_and_exact_when_divisible(n: int, d: int) -> None:
    q = round_div(n, d)
    assert abs(Fraction(n, d) - q) <= Fraction(1, 2)
    if n % d == 0:
        assert q == n // d


@given(n=st.integers(-(10**30), 10**30), d=st.integers(1, 10**12))
def test_round_div_sign_symmetry(n: int, d: int) -> None:
    assert round_div(-n, d) == -round_div(n, d)  # half-even is symmetric about zero


# ---- FX conversion ------------------------------------------------------------------------------


def test_convert_golden_values() -> None:
    # 100.00 USD at 1.5 AUD per USD = 150.00 AUD
    assert convert(Money(10_000, "USD"), Decimal("1.5"), "AUD") == Money(15_000, "AUD")
    # 1000 JPY at 0.01 AUD per JPY = 10.00 AUD (exponent 0 -> 2)
    assert convert(Money(1000, "JPY"), Decimal("0.01"), "AUD") == Money(1000, "AUD")
    # 10.00 AUD at 100 JPY per AUD = 1000 JPY (exponent 2 -> 0)
    assert convert(Money(1000, "AUD"), Decimal("100"), "JPY") == Money(1000, "JPY")
    # 0.01 AUD x 0.5 = 0.005 -> tie, rounds to even (0.00); 0.03 x 0.5 = 0.015 -> 0.02
    assert convert(Money(1, "AUD"), Decimal("0.5"), "NZD") == Money(0, "NZD")
    assert convert(Money(3, "AUD"), Decimal("0.5"), "NZD") == Money(2, "NZD")
    # negative amounts round symmetrically
    assert convert(Money(-3, "AUD"), Decimal("0.5"), "NZD") == Money(-2, "NZD")
    # a 3-decimal currency: 1.00 AUD x 0.2 = 0.200 KWD
    assert convert(Money(100, "AUD"), Decimal("0.2"), "KWD") == Money(200, "KWD")


@pytest.mark.parametrize("rate", [1.5, 2, "1.5", None])
def test_convert_refuses_non_decimal_rates(rate: object) -> None:
    with pytest.raises(TypeError):
        convert(Money(1, "AUD"), rate, "USD")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "rate", [Decimal("0"), Decimal("-1.2"), Decimal("NaN"), Decimal("Infinity")]
)
def test_convert_refuses_zero_negative_and_non_finite_rates(rate: Decimal) -> None:
    with pytest.raises(MoneyError):
        convert(Money(1, "AUD"), rate, "USD")


def test_convert_to_unknown_currency_raises() -> None:
    with pytest.raises(MoneyError):
        convert(Money(1, "AUD"), Decimal("1"), "XXX")


rates = st.decimals(
    min_value=Decimal("0.0000000001"), max_value=Decimal("1000000"), places=10, allow_nan=False
)


@given(m=money_in("USD"), rate=rates, to=currencies)
def test_convert_equals_exact_rational_product_rounded_once(
    m: Money, rate: Decimal, to: str
) -> None:
    exact = (
        Fraction(m.amount_minor) * Fraction(rate) * Fraction(10) ** (exponent(to) - exponent("USD"))
    )
    result = convert(m, rate, to)
    assert result.currency == to
    assert abs(Fraction(result.amount_minor) - exact) <= Fraction(1, 2)
    if abs(Fraction(result.amount_minor) - exact) == Fraction(1, 2):
        assert result.amount_minor % 2 == 0  # ties go to even


@given(m=money_in("USD"), to=currencies)
def test_convert_at_one_between_equal_exponent_currencies_is_identity(m: Money, to: str) -> None:
    if exponent(to) == exponent("USD"):
        assert convert(m, Decimal("1"), to).amount_minor == m.amount_minor


@given(
    x=st.integers(-(10**12), 10**12),
    i=st.integers(0, 6),
    j=st.integers(0, 6),
    n=st.integers(0, 4),
    a=currencies,
    b=currencies,
)
def test_fx_round_trip_error_is_within_the_documented_bound(
    x: int, i: int, j: int, n: int, a: str, b: str
) -> None:
    # r = 2^i * 5^j / 10^n has a terminating reciprocal, so the return rate is exact.
    r = Decimal(2**i * 5**j).scaleb(-n)
    back = Decimal(10**n) / Decimal(2**i * 5**j)
    assert r * back == 1
    kf = Fraction(r) * Fraction(10) ** (exponent(b) - exponent(a))
    # The bound is about rounding, not range: skip pairs whose converted amount can't fit in
    # int64 (e.g. KRW -> BHD at a large rate). Overflow raising MoneyError is tested separately.
    assume(abs(x * kf) <= INT64_MAX // 2)
    out = convert(Money(x, a), r, b)
    z = convert(out, back, a).amount_minor
    kb = 1 / kf
    assert 2 * abs(z - x) <= 1 + kb


def test_convert_overflow_raises_rather_than_wrapping() -> None:
    # KRW (0 decimals) -> BHD (3 decimals) at a large rate exceeds int64.
    with pytest.raises(MoneyError):
        convert(Money(10**12, "KRW"), Decimal("1000000"), "BHD")


# ---- allocation ---------------------------------------------------------------------------------

weights_st = st.dictionaries(
    st.integers(1, 50), st.integers(0, 10_000), min_size=1, max_size=8
).filter(lambda w: sum(w.values()) > 0)


def test_allocate_golden_values() -> None:
    # 100 across thirds: 34 / 33 / 33, the leftover cent to the lowest id on a tie
    parts = allocate(Money(100, "AUD"), {3: 1, 1: 1, 2: 1})
    assert parts == {1: Money(34, "AUD"), 2: Money(33, "AUD"), 3: Money(33, "AUD")}
    # even joint split of an odd cent: the extra goes to the lower Member id
    assert allocate(Money(101, "AUD"), {7: 5000, 4: 5000}) == {
        4: Money(51, "AUD"),
        7: Money(50, "AUD"),
    }
    # largest remainder wins over id: 10 at 1:2 -> exact 3.33 / 6.67 -> 3 / 7
    assert allocate(Money(10, "AUD"), {1: 1, 2: 2}) == {1: Money(3, "AUD"), 2: Money(7, "AUD")}
    # largest remainder beats id: 7 at 1:1:2 -> 1.75 / 1.75 / 3.5 -> floors 1/1/3, then the two
    # leftover units go to the .75 remainders (ids 1 and 2), not to the .5 (id 3)
    assert allocate(Money(7, "AUD"), {1: 1, 2: 1, 3: 2}) == {
        1: Money(2, "AUD"),
        2: Money(2, "AUD"),
        3: Money(3, "AUD"),
    }
    # ...so a larger remainder beats a lower id: 4 at 1:3:3 -> .57 / 1.71 / 1.71 -> 0 / 2 / 2
    assert allocate(Money(4, "AUD"), {1: 1, 2: 3, 3: 3}) == {
        1: Money(0, "AUD"),
        2: Money(2, "AUD"),
        3: Money(2, "AUD"),
    }
    # negative totals mirror positive ones
    assert allocate(Money(-101, "AUD"), {4: 5000, 7: 5000}) == {
        4: Money(-51, "AUD"),
        7: Money(-50, "AUD"),
    }
    assert allocate(Money(0, "AUD"), {1: 1}) == {1: Money(0, "AUD")}


def test_allocate_rejects_bad_input() -> None:
    with pytest.raises(MoneyError):
        allocate(Money(1, "AUD"), {})
    with pytest.raises(MoneyError):
        allocate(Money(1, "AUD"), {1: 0, 2: 0})
    with pytest.raises(MoneyError):
        allocate(Money(1, "AUD"), {1: -1, 2: 2})
    with pytest.raises(TypeError):
        allocate(Money(1, "AUD"), {1: 0.5})  # type: ignore[dict-item]


@given(total=amounts, weights=weights_st, ccy=currencies)
def test_allocation_parts_sum_exactly_to_the_whole(
    total: int, weights: dict[int, int], ccy: str
) -> None:
    parts = allocate(Money(total, ccy), weights)
    assert set(parts) == set(weights)
    assert sum(p.amount_minor for p in parts.values()) == total
    assert all(p.currency == ccy for p in parts.values())


@given(total=amounts, weights=weights_st)
def test_each_part_is_within_one_unit_of_its_exact_share(
    total: int, weights: dict[int, int]
) -> None:
    parts = allocate(Money(total, "AUD"), weights)
    weight_sum = sum(weights.values())
    for key, part in parts.items():
        exact = Fraction(total * weights[key], weight_sum)
        assert abs(Fraction(part.amount_minor) - exact) < 1
        if weights[key] == 0:
            assert part.amount_minor == 0


@given(total=small, weights=weights_st, data=st.data())
def test_allocation_is_independent_of_input_order(
    total: int, weights: dict[int, int], data: st.DataObject
) -> None:
    keys = data.draw(st.permutations(list(weights)))
    shuffled = {k: weights[k] for k in keys}
    assert allocate(Money(total, "AUD"), shuffled) == allocate(Money(total, "AUD"), weights)


@given(total=small, weights=weights_st)
def test_negative_total_is_the_mirror_image(total: int, weights: dict[int, int]) -> None:
    pos = allocate(Money(total, "AUD"), weights)
    neg = allocate(Money(-total, "AUD"), weights)
    assert neg == {k: -v for k, v in pos.items()}


@given(total=st.integers(0, 10**9), n=st.integers(1, 12))
def test_equal_split_gives_extra_units_to_lowest_ids(total: int, n: int) -> None:
    parts = allocate(Money(total, "AUD"), {i: 1 for i in range(1, n + 1)})
    in_order = [parts[i].amount_minor for i in range(1, n + 1)]
    assert in_order == sorted(in_order, reverse=True)
    assert max(in_order) - min(in_order) <= 1


@given(total=small)
def test_basis_point_ownership_shares_sum_exactly(total: int) -> None:
    parts = allocate(Money(total, "AUD"), {1: 3333, 2: 3333, 3: 3334})
    assert sum(p.amount_minor for p in parts.values()) == total


@pytest.mark.parametrize(
    "text", ["\u0661\u0662\u0663", "12\u0663.50", "\uff11\uff12\uff13", "1,\u06623"]
)
def test_non_ascii_digits_are_not_an_amount(text: str) -> None:
    with pytest.raises(MoneyError):
        parse_amount(text, "AUD")


# ---- scale (ADR-0015 section 4) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("minor", "num", "den", "expected"),
    [
        (100, 21, 20, 105),  # exact
        (11_576_250, 1_050_000, 1_000_000, 12_155_062),  # 12_155_062.5: a tie goes to the even 62
        (1, 1, 2, 0),  # 0.5 -> 0 (even)
        (3, 1, 2, 2),  # 1.5 -> 2 (even)
        (5, 1, 2, 2),  # 2.5 -> 2 (even)
        (-5, 1, 2, -2),  # symmetric
        (7, 1_000_000, 1_000_000, 7),
        (0, 1_050_000, 1_000_000, 0),
    ],
)
def test_scale_rounds_once_half_to_even(minor: int, num: int, den: int, expected: int) -> None:
    assert scale(Money(minor, "AUD"), num, den) == Money(expected, "AUD")


@given(m=money_in("AUD"), num=st.integers(0, 3 * 10**6), den=st.integers(1, 3 * 10**6))
def test_scale_is_within_half_a_minor_unit_of_the_exact_product(
    m: Money, num: int, den: int
) -> None:
    got = scale(m, num, den).amount_minor
    exact = Fraction(m.amount_minor * num, den)
    assert abs(Fraction(got) - exact) <= Fraction(1, 2)


@given(m=money_in("AUD"), k=st.integers(1, 10**6))
def test_scale_by_a_whole_ratio_is_exact(m: Money, k: int) -> None:
    assert scale(m, k, k) == m


def test_scale_keeps_the_currency_and_rejects_floats_and_bad_denominators() -> None:
    assert scale(Money(100, "JPY"), 3, 2).currency == "JPY"
    with pytest.raises(TypeError):
        scale(Money(100, "AUD"), 1.5, 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        scale(Money(100, "AUD"), 1, 2.0)  # type: ignore[arg-type]
    for bad in (0, -1):
        with pytest.raises(MoneyError):
            scale(Money(100, "AUD"), 1, bad)


def test_scale_overflow_raises_and_never_wraps() -> None:
    with pytest.raises(MoneyError):
        scale(Money(INT64_MAX, "AUD"), 2, 1)


# ---- named ATO rounding steps (ADR-0019 section 3) -----------------------------------------------


def test_the_ato_step_sends_a_half_cent_to_the_even_cent_both_ways() -> None:
    # Invented amounts landing exactly on a half cent: the notice goes up on one, down on another.
    assert ato_scale(Money(1, "AUD"), 5, 2).amount_minor == 2  # 2.5 -> 2
    assert ato_scale(Money(1, "AUD"), 7, 2).amount_minor == 4  # 3.5 -> 4
    assert ato_scale(Money(-1, "AUD"), 5, 2).amount_minor == -2
    assert ato_scale(Money(-1, "AUD"), 7, 2).amount_minor == -4
    assert ato_scale(Money(1, "AUD"), 1, 3).amount_minor == 0  # not a half: nearest


def test_a_tapering_offset_rounds_the_whole_formula_once_to_the_even_cent() -> None:
    # 700.00 less 5% of 4,246.90 is 700.00 - 212.345 = 487.655: up to 487.66 (even).
    # 700.00 less 5% of 4,246.70 is 700.00 - 212.335 = 487.665: down to 487.66 (even).
    def lito(excess_cents: int) -> int:
        reduction = ((Money(excess_cents, "AUD"), 50_000),)
        return ato_offset_reduced(Money(70_000, "AUD"), reduction, 1_000_000).amount_minor

    assert lito(424_690) == 48_766
    assert lito(424_670) == 48_766


def test_ato_whole_dollars_drops_the_cents_toward_zero() -> None:
    assert ato_whole_dollars(Money(12345, "AUD")) == Money(12300, "AUD")
    assert ato_whole_dollars(Money(99, "AUD")) == Money(0, "AUD")
    assert ato_whole_dollars(Money(-12345, "AUD")) == Money(-12300, "AUD")
    assert ato_whole_dollars(Money(500, "JPY")) == Money(500, "JPY")


def test_ato_scale_refuses_a_float_and_a_bad_denominator() -> None:
    with pytest.raises(TypeError):
        ato_scale(Money(1, "AUD"), 1.5, 1)  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        ato_scale(Money(1, "AUD"), 1, 0)


@given(
    st.integers(-(10**12), 10**12),
    st.integers(-(10**6), 10**6),
    st.integers(1, 10**9),
)
def test_ato_scale_is_the_exact_ratio_rounded_to_the_nearest_cent(
    amount: int, numerator: int, denominator: int
) -> None:
    got = ato_scale(Money(amount, "AUD"), numerator, denominator).amount_minor
    exact = Fraction(amount * numerator, denominator)
    assert abs(Fraction(got) - exact) <= Fraction(1, 2)
    if abs(Fraction(got) - exact) == Fraction(1, 2):
        assert got % 2 == 0  # a half goes to the even cent


@given(st.integers(-(10**12), 10**12))
def test_ato_whole_dollars_never_moves_an_amount_by_a_dollar_or_more(amount: int) -> None:
    got = ato_whole_dollars(Money(amount, "AUD")).amount_minor
    assert got % 100 == 0 and abs(got) <= abs(amount) and abs(amount - got) < 100
