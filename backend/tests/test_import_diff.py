"""The multiset diff and cross-Stream reconciliation, as pure logic (ADR-0013 section 5): unit
and Hypothesis tests, no database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.imports.diff import RowKey, multiset_diff
from finance360.capabilities.imports.reconcile import (
    CashLeg,
    TradeLeg,
    expected_settlement,
    reconcile,
)
from finance360.core.money import Money

D = date(2026, 9, 1)


def _key(day: int = 1, amount: int = 100, kind: str = "deposit") -> RowKey:
    return RowKey(date(2026, 9, day), kind, None, None, None, None, None, (amount, "AUD"))


# --- the multiset diff ---------------------------------------------------------------------


def test_equal_rows_pair_and_the_rest_are_added_or_removed() -> None:
    staged = [(1, _key(1)), (2, _key(2)), (3, _key(3))]
    current = [(10, _key(2)), (11, _key(9))]
    diff = multiset_diff(staged, current)
    assert diff.added == (1, 3)
    assert diff.unchanged == ((2, 10),)
    assert diff.removed == (11,)


def test_identical_rows_are_counted_not_collapsed() -> None:
    same = _key(5)
    staged = [(1, same), (2, same), (3, same)]
    current = [(10, same), (11, same)]
    diff = multiset_diff(staged, current)
    assert (len(diff.added), len(diff.unchanged), len(diff.removed)) == (1, 2, 0)
    shrunk = multiset_diff([(1, same)], current)
    assert (len(shrunk.added), len(shrunk.unchanged), len(shrunk.removed)) == (0, 1, 1)


def test_a_changed_row_is_one_removed_plus_one_added() -> None:
    diff = multiset_diff([(1, _key(1, amount=101))], [(10, _key(1, amount=100))])
    assert (diff.added, diff.removed, diff.unchanged) == ((1,), (10,), ())


def test_a_quantity_matches_whatever_its_trailing_zeros() -> None:
    a = RowKey(D, "buy", 1, Decimal("20"), Decimal("96.1075"), "AUD", (950, "AUD"), None)
    b = RowKey(D, "buy", 1, Decimal("20.000000"), Decimal("96.107500"), "AUD", (950, "AUD"), None)
    assert multiset_diff([(1, a)], [(9, b)]).unchanged == ((1, 9),)


keys = st.builds(
    _key,
    day=st.integers(1, 5),
    amount=st.integers(1, 3),
    kind=st.sampled_from(["deposit", "interest"]),
)


@given(st.lists(keys, max_size=12), st.lists(keys, max_size=12))
def test_the_diff_partitions_both_sides_exactly(
    staged_keys: list[RowKey], cur: list[RowKey]
) -> None:
    staged = list(enumerate(staged_keys))
    current = list(enumerate(cur, start=100))
    diff = multiset_diff(staged, current)
    assert len(diff.added) + len(diff.unchanged) == len(staged)
    assert len(diff.removed) + len(diff.unchanged) == len(current)
    assert len({*diff.added, *(s for s, _ in diff.unchanged)}) == len(staged)
    assert len({*diff.removed, *(c for _, c in diff.unchanged)}) == len(current)
    # applying the diff to the current rows gives exactly the staged rows
    kept = {c for _, c in diff.unchanged}
    result = sorted(
        [k for i, k in current if i in kept] + [k for i, k in staged if i in set(diff.added)]
    )
    assert result == sorted(staged_keys)


# --- reconciliation ------------------------------------------------------------------------


def _buy(
    ref: int, day: int = 3, qty: str = "20", price: str = "96.1075", fee: int = 950
) -> TradeLeg:
    return TradeLeg(
        ref, date(2026, 8, day), "buy", Decimal(qty), Decimal(price), "AUD", Money(fee, "AUD")
    )


def _cash(day: int, kind: str, minor: int) -> CashLeg:
    return CashLeg(date(2026, 8, day), kind, Money(minor, "AUD"))


def test_the_expected_settlement_is_out_for_a_buy_and_in_for_a_sell() -> None:
    assert expected_settlement(_buy(1)) == Money(-192215, "AUD")
    sell = TradeLeg(2, D, "sell", Decimal("-40"), Decimal("6.4"), "AUD", Money(950, "AUD"))
    assert expected_settlement(sell) == Money(25600, "AUD")
    opening = TradeLeg(3, D, "opening_balance", Decimal("5"), Decimal("1"), "AUD", None)
    assert expected_settlement(opening) is None


def test_a_matched_buy_with_a_separate_fee_row_raises_no_warning() -> None:
    cash = [_cash(3, "trade_settlement", -192215), _cash(3, "fee", -950)]
    assert reconcile([_buy(1)], cash) == []


def test_a_trade_with_no_cash_at_all_raises_one_warning_naming_both_parts() -> None:
    [miss] = reconcile([_buy(1)], [])
    assert (miss.ref, miss.settlement, miss.fee) == (1, Money(-192215, "AUD"), Money(-950, "AUD"))


def test_a_missing_fee_row_is_reported_without_blaming_the_settlement() -> None:
    [miss] = reconcile([_buy(1)], [_cash(3, "trade_settlement", -192215)])
    assert miss.settlement is None and miss.fee == Money(-950, "AUD")


def test_the_fee_is_not_folded_into_the_settlement() -> None:
    """If the settlement included the fee it would not match: the fee is its own row."""
    [miss] = reconcile([_buy(1)], [_cash(3, "trade_settlement", -192215 - 950)])
    assert miss.settlement == Money(-192215, "AUD")


def test_each_cash_row_is_matched_once_so_a_fee_is_never_counted_twice() -> None:
    two_buys = [_buy(1), _buy(2)]  # same day, same value, same fee
    one_each = [_cash(3, "trade_settlement", -192215), _cash(3, "fee", -950)]
    misses = reconcile(two_buys, one_each)
    assert [m.ref for m in misses] == [2]  # exactly one warning, for the second buy
    both = one_each + [_cash(3, "trade_settlement", -192215), _cash(3, "fee", -950)]
    assert reconcile(two_buys, both) == []


def test_a_cash_row_on_another_date_or_of_another_amount_does_not_match() -> None:
    assert reconcile([_buy(1)], [_cash(4, "trade_settlement", -192215)])[0].settlement is not None
    assert reconcile([_buy(1)], [_cash(3, "trade_settlement", -192214)])[0].settlement is not None


def test_a_zero_fee_expects_no_fee_row() -> None:
    assert reconcile([_buy(1, fee=0)], [_cash(3, "trade_settlement", -192215)]) == []
