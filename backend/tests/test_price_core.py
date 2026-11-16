"""The pure Price lookup and text helpers (ADR-0013 section 2): unit, golden and Hypothesis."""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.core.money import MoneyError
from finance360.core.price import (
    MAX_PRICE,
    NoPrice,
    PricePoint,
    format_price,
    parse_price,
    price_on_or_before,
)

D = date(2026, 9, 1)


def _p(id_: int, day: int, value: str, source: str = "yfinance", inst: int = 1) -> PricePoint:
    return PricePoint(id_, inst, D + timedelta(days=day), Decimal(value), source)


# --- lookup: golden cases ------------------------------------------------------------------


def test_the_exact_date_wins() -> None:
    rows = [_p(1, 0, "10"), _p(2, 1, "11"), _p(3, 2, "12")]
    assert price_on_or_before(rows, 1, D + timedelta(days=1)) == rows[1]


def test_a_gap_uses_the_latest_earlier_price_and_carries_its_as_at() -> None:
    rows = [_p(1, 0, "10"), _p(2, 1, "11"), _p(3, 5, "15")]
    found = price_on_or_before(rows, 1, D + timedelta(days=4))
    assert found == rows[1]
    assert isinstance(found, PricePoint) and found.as_at == D + timedelta(days=1)


def test_a_future_date_uses_the_last_price_not_a_later_one() -> None:
    rows = [_p(1, 0, "10"), _p(2, 3, "13")]
    assert price_on_or_before(rows, 1, D + timedelta(days=365)) == rows[1]
    # a Price after the date is never used, even if it is the only one
    assert price_on_or_before([_p(9, 5, "99")], 1, D) == NoPrice(1, D)


def test_before_the_first_price_is_an_explicit_no_price_not_zero() -> None:
    result = price_on_or_before([_p(1, 2, "10")], 1, D)
    assert result == NoPrice(1, D)
    assert not hasattr(result, "value")


def test_no_rows_is_no_price() -> None:
    assert price_on_or_before([], 1, D) == NoPrice(1, D)


def test_another_instruments_price_is_never_used() -> None:
    assert price_on_or_before([_p(1, 0, "10", inst=2)], 1, D) == NoPrice(1, D)


def test_a_same_date_tie_goes_to_manual_whatever_the_order_or_ids() -> None:
    provider = _p(5, 0, "10.1000", "yfinance")
    manual = _p(1, 0, "10.2000", "manual")
    for rows in ([provider, manual], [manual, provider]):
        assert price_on_or_before(rows, 1, D) == manual


def test_precedence_only_breaks_ties_the_latest_date_still_wins() -> None:
    manual_old = _p(1, 0, "10", "manual")
    provider_new = _p(2, 1, "11", "yfinance")
    assert price_on_or_before([manual_old, provider_new], 1, D + timedelta(days=1)) == provider_new


def test_an_unlisted_source_ranks_after_the_listed_ones_by_name() -> None:
    a, b, c = _p(1, 0, "1", "zeta"), _p(2, 0, "2", "alpha"), _p(3, 0, "3", "yfinance")
    assert price_on_or_before([a, b, c], 1, D) == c
    assert price_on_or_before([a, b], 1, D) == b


def test_a_same_date_tie_goes_manual_then_fetched_then_import_whatever_the_order() -> None:
    manual = _p(1, 0, "10.3", "manual")
    fetched = _p(2, 0, "10.2", "yfinance")
    imported = _p(3, 0, "10.1", "import")
    for rows in itertools.permutations([manual, fetched, imported]):
        assert price_on_or_before(rows, 1, D) == manual
        assert price_on_or_before([r for r in rows if r != manual], 1, D) == fetched
    assert price_on_or_before([imported, fetched], 1, D) == fetched


def test_an_unknown_future_source_ranks_above_import_without_being_listed() -> None:
    imported, future = _p(9, 0, "1", "import"), _p(1, 0, "2", "zzprov")
    for rows in ([imported, future], [future, imported]):
        assert price_on_or_before(rows, 1, D) == future


def test_a_newer_import_price_beats_an_older_manual_one() -> None:
    manual_old, import_new = _p(1, 0, "10", "manual"), _p(2, 3, "11", "import")
    assert price_on_or_before([manual_old, import_new], 1, D + timedelta(days=3)) == import_new
    # ... but not before its own date: the manual one still holds there
    assert price_on_or_before([manual_old, import_new], 1, D + timedelta(days=2)) == manual_old


def test_the_configured_precedence_can_be_changed() -> None:
    manual, provider = _p(1, 0, "1", "manual"), _p(2, 0, "2", "yfinance")
    assert price_on_or_before([manual, provider], 1, D, ("yfinance", "manual")) == provider


# --- lookup: invariants --------------------------------------------------------------------

_sources = st.sampled_from(["manual", "yfinance", "import", "other"])
_rows = st.lists(
    st.builds(
        PricePoint,
        id=st.integers(1, 10_000),
        instrument_id=st.integers(1, 2),
        as_at=st.dates(D, D + timedelta(days=15)),
        value=st.decimals(Decimal("0.000001"), Decimal("999"), places=6),
        source=_sources,
    ),
    max_size=25,
    unique_by=lambda r: r.id,
)
_on = st.dates(D - timedelta(days=3), D + timedelta(days=20))


@given(_rows, st.integers(1, 2), _on)
def test_the_answer_matches_a_plain_restatement_of_the_rule(
    rows: list[PricePoint], inst: int, on: date
) -> None:
    result = price_on_or_before(rows, inst, on)
    eligible = [r for r in rows if r.instrument_id == inst and r.as_at <= on]
    if not eligible:
        assert result == NoPrice(inst, on)
        return
    assert isinstance(result, PricePoint)
    assert result.as_at <= on and result.instrument_id == inst
    latest = max(r.as_at for r in eligible)
    assert result.as_at == latest  # nothing later but still on or before was skipped
    same_day = [r for r in eligible if r.as_at == latest]
    if any(r.source == "manual" for r in same_day):
        assert result.source == "manual"  # manual first on a tie


@given(_rows, st.integers(1, 2), _on, st.randoms())
def test_the_answer_never_depends_on_the_order_of_rows(
    rows: list[PricePoint], inst: int, on: date, rnd: object
) -> None:
    shuffled = list(rows)
    rnd.shuffle(shuffled)  # type: ignore[attr-defined]
    assert price_on_or_before(shuffled, inst, on) == price_on_or_before(rows, inst, on)


@given(_rows, st.integers(1, 2), _on)
def test_a_later_date_never_returns_an_earlier_price(
    rows: list[PricePoint], inst: int, on: date
) -> None:
    a = price_on_or_before(rows, inst, on)
    b = price_on_or_before(rows, inst, on + timedelta(days=5))
    if isinstance(a, PricePoint):
        assert isinstance(b, PricePoint) and b.as_at >= a.as_at


# --- text ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [("34.5678", "34.5678"), (" 1 ", "1"), ("0.000001", "0.000001"), ("2.10", "2.10")],
)
def test_parse_price_is_exact(text: str, value: str) -> None:
    assert parse_price(text) == Decimal(value)


@pytest.mark.parametrize(
    "text",
    ["", "abc", "-1", "0", "0.0", "1.0000001", "1e3", "١٢", "NaN", "1,5", str(MAX_PRICE + 1)],
)
def test_parse_price_rejects_instead_of_rounding(text: str) -> None:
    with pytest.raises(MoneyError):
        parse_price(text)


@given(st.decimals(Decimal("0.000001"), MAX_PRICE, places=6))
def test_format_then_parse_round_trips_exactly(value: Decimal) -> None:
    assert parse_price(format_price(value)) == value


def test_format_price_keeps_at_least_two_places_and_drops_trailing_zeros() -> None:
    assert format_price(Decimal("34.500000")) == "34.50"
    assert format_price(Decimal("34")) == "34.00"
    assert format_price(Decimal("0.012300")) == "0.0123"
