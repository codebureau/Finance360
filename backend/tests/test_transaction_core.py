"""Transaction vocabulary and the exact trade value (ADR-0013 section 2): unit, golden
and Hypothesis tests. No database."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, localcontext

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.imports import importer
from finance360.core import transaction as core_transaction
from finance360.core.models import transaction as model
from finance360.core.money import Money, MoneyError


@pytest.mark.parametrize(
    ("quantity", "price", "expected"),
    [
        # golden values from the synthetic sample exports
        ("20", "96.1075", "1922.15"),
        ("30", "98.8957", "2966.87"),  # 2966.871 rounds down
        ("40", "6.4000", "256.00"),
        ("100", "6.2000", "620.00"),
        ("50", "12.3456", "617.28"),
        # half-even ties: 0.005 -> 0.00 (even), 0.015 -> 0.02, 0.025 -> 0.02
        ("1", "0.005", "0.00"),
        ("3", "0.005", "0.02"),
        ("5", "0.005", "0.02"),
        # a sell's negative quantity gives the same, never negative, value
        ("-40", "6.4000", "256.00"),
    ],
)
def test_trade_value_golden(quantity: str, price: str, expected: str) -> None:
    value = core_transaction.trade_value(Decimal(quantity), Decimal(price), "AUD")
    assert value == Money(int(Decimal(expected).scaleb(2)), "AUD")


def test_trade_value_refuses_more_precision_than_the_stored_scale() -> None:
    with pytest.raises(MoneyError):
        core_transaction.trade_value(Decimal("1.0000001"), Decimal("1"), "AUD")


@given(
    quantity=st.decimals(min_value="-1000000", max_value="1000000", places=6),
    price=st.decimals(min_value="0", max_value="100000", places=6),
)
def test_trade_value_equals_the_exact_product_rounded_half_even_once(
    quantity: Decimal, price: Decimal
) -> None:
    with localcontext() as ctx:
        ctx.prec = 60  # wide enough that the reference product is exact
        exact = abs(quantity) * price
        reference = exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    value = core_transaction.trade_value(quantity, price, "AUD")
    assert value.amount_minor >= 0
    assert Decimal(value.amount_minor).scaleb(-2) == reference


def test_the_vocabulary_matches_the_importer_protocol_and_the_model() -> None:
    assert core_transaction.STREAMS == importer.STREAMS
    assert core_transaction.TRADE_KINDS == importer.TRADE_KINDS
    assert core_transaction.CASH_KINDS == importer.CASH_KINDS
    assert model._TRADE_KINDS == core_transaction.TRADE_KINDS  # pyright: ignore[reportPrivateUsage]
    assert model._CASH_KINDS == core_transaction.CASH_KINDS  # pyright: ignore[reportPrivateUsage]
