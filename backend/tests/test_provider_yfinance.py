"""The yfinance provider plug-in: the float to Decimal boundary, the provider mapping, the
transport over a fake ticker (a recorded synthetic frame), and the offline sample. Nothing here
reaches the network (the transport takes a ``ticker_factory``)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.reference.providers import ProviderError, ProviderThrottled
from finance360.core.price import MAX_PRICE, parse_price
from finance360.plugins.providers.yfinance import YFinanceProvider, make_provider
from finance360.plugins.providers.yfinance.convert import PRICE_PLACES, close_to_decimal
from finance360.plugins.providers.yfinance.sample import sample_history
from finance360.plugins.providers.yfinance.transport import RawHistory, yfinance_history

# --- the float boundary ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (34.5678, "34.5678"),
        (
            34.560001373291016,
            "34.5600",
        ),  # 32-bit noise from a provider: removed by the 4 d.p. rounding
        (98.0, "98.0000"),
        (0.0001, "0.0001"),
        (1234567.8912, "1234567.8912"),
        (5, "5.0000"),  # an int is fine
    ],
)
def test_a_float_close_becomes_the_exact_decimal_a_person_would_write(
    raw: float, expected: str
) -> None:
    assert close_to_decimal(raw) == Decimal(expected)


def test_rounding_is_half_even_on_the_decimal_text_not_the_binary_value() -> None:
    # 0.00005 and 0.00015 are exactly representable as text; a tie goes to the even neighbour.
    assert close_to_decimal(1.00005) == Decimal("1.0000")
    assert close_to_decimal(1.00015) == Decimal("1.0002")
    assert close_to_decimal(1.00025) == Decimal("1.0002")


@pytest.mark.parametrize(
    "raw", [math.nan, math.inf, -math.inf, 0.0, -1.5, 0.00004, None, "abc", True, 1e300]
)
def test_a_close_that_is_not_a_usable_price_is_a_gap(raw: object) -> None:
    value = close_to_decimal(raw)
    assert value is None or value > MAX_PRICE  # 1e300 converts exactly but the service drops it


def test_a_numpy_float_is_accepted() -> None:
    import numpy as np

    assert close_to_decimal(np.float64(12.3456)) == Decimal("12.3456")
    assert close_to_decimal(np.float32(34.56)) == Decimal("34.5600")


@given(st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("100000"), places=PRICE_PLACES))
def test_a_price_written_to_four_places_round_trips_exactly(price: Decimal) -> None:
    """The invariant that matters: a quote like 34.5678 is never changed by float travel."""
    assert close_to_decimal(float(price)) == price


@given(st.floats(min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False))
def test_any_usable_float_converts_to_at_most_four_places_within_half_a_unit(raw: float) -> None:
    value = close_to_decimal(raw)
    assert value is not None and value > 0
    assert -value.as_tuple().exponent <= PRICE_PLACES  # type: ignore[operator]
    assert abs(value - Decimal(repr(raw))) <= Decimal("0.00005")
    parse_price(format(value, "f"))  # and the stored-price validator accepts it unchanged


# --- the provider ---------------------------------------------------------------------------


def test_the_provider_maps_raw_rows_to_exact_bars_and_drops_unusable_closes() -> None:
    rows = (
        (date(2026, 10, 5), 98.4),
        (date(2026, 10, 6), math.nan),  # a gap, not a repaired number
        (date(2026, 10, 7), 98.7123),
    )
    provider = YFinanceProvider(lambda symbol, start, end: RawHistory("AUD", rows))
    series = provider.fetch_closes("SBME.AX", date(2026, 10, 5), date(2026, 10, 7))
    assert series.currency == "AUD"
    assert [(b.as_at, b.close) for b in series.bars] == [
        (date(2026, 10, 5), Decimal("98.4000")),
        (date(2026, 10, 7), Decimal("98.7123")),
    ]
    assert (provider.key, provider.identifier_scheme) == ("yfinance", "yahoo")


# --- the transport, over a fake ticker ------------------------------------------------------


class FakeTicker:
    """Stands in for ``yfinance.Ticker``: returns a recorded synthetic frame, records the call."""

    def __init__(self, frame: pd.DataFrame | Exception, currency: str | None = "AUD") -> None:
        self.frame = frame
        self.currency = currency
        self.history_kwargs: dict[str, object] = {}

    def history(self, **kwargs: object) -> pd.DataFrame:
        self.history_kwargs = kwargs
        if isinstance(self.frame, Exception):
            raise self.frame
        return self.frame

    def get_history_metadata(self) -> dict[str, object]:
        return {"currency": self.currency} if self.currency else {}


def _frame() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-10-05", tz="Australia/Sydney"),
            pd.Timestamp("2026-10-06", tz="Australia/Sydney"),
        ]
    )
    return pd.DataFrame({"Close": [98.4, 98.7123], "Volume": [10, 20]}, index=index)


def test_the_transport_asks_for_raw_unadjusted_daily_closes_with_an_inclusive_end() -> None:
    ticker = FakeTicker(_frame())
    raw = yfinance_history(
        "SBME.AX", date(2026, 10, 5), date(2026, 10, 6), ticker_factory=lambda s: ticker
    )
    assert raw == RawHistory("AUD", ((date(2026, 10, 5), 98.4), (date(2026, 10, 6), 98.7123)))
    assert ticker.history_kwargs["auto_adjust"] is False  # raw Close, not adjusted (ADR-0013)
    assert ticker.history_kwargs["actions"] is False
    assert ticker.history_kwargs["interval"] == "1d"
    assert ticker.history_kwargs["start"] == "2026-10-05"
    assert ticker.history_kwargs["end"] == "2026-10-07"  # yfinance's end is exclusive
    import yfinance

    assert yfinance.config.debug.hide_exceptions is False  # errors surface as exceptions


def test_the_transport_maps_rate_limiting_to_a_throttled_error() -> None:
    from yfinance import exceptions as yf_exceptions

    ticker = FakeTicker(yf_exceptions.YFRateLimitError())
    with pytest.raises(ProviderThrottled):
        yfinance_history("X", date(2026, 10, 5), date(2026, 10, 6), ticker_factory=lambda s: ticker)


def test_the_transport_treats_no_price_data_as_a_gap_not_an_error() -> None:
    from yfinance import exceptions as yf_exceptions

    ticker = FakeTicker(yf_exceptions.YFPricesMissingError("X", "1d"), currency=None)
    raw = yfinance_history(
        "X", date(2026, 10, 3), date(2026, 10, 4), ticker_factory=lambda s: ticker
    )
    assert raw == RawHistory(None, ())


def test_the_transport_turns_any_other_failure_into_a_safe_provider_error() -> None:
    ticker = FakeTicker(ConnectionError("https://secret.example/?token=abc123 refused"))
    with pytest.raises(ProviderError) as caught:
        yfinance_history("X", date(2026, 10, 5), date(2026, 10, 6), ticker_factory=lambda s: ticker)
    assert "ConnectionError" in str(caught.value)
    assert "abc123" not in str(caught.value)  # the library's message is not passed on


# --- the offline sample ---------------------------------------------------------------------


def test_the_sample_transport_serves_weekdays_only_in_the_symbols_currency() -> None:
    start = date(2026, 10, 1)
    raw = sample_history("SBME.AX", start, start + timedelta(days=13))
    assert raw.currency == "AUD"
    assert len(raw.rows) == 10
    assert all(d.weekday() < 5 for d, _ in raw.rows)
    assert sample_history("VOD.L", start, start).currency == "GBP"
    assert sample_history("QQQ", start, start).currency == "USD"


def test_the_sample_provider_is_deterministic_and_passes_through_the_real_conversion() -> None:
    provider = make_provider(sample=True)
    a = provider.fetch_closes("SBME.AX", date(2026, 10, 1), date(2026, 10, 9))
    assert a == provider.fetch_closes("SBME.AX", date(2026, 10, 1), date(2026, 10, 9))
    assert all(-b.close.as_tuple().exponent == PRICE_PLACES for b in a.bars)  # type: ignore[operator]
    assert "offline sample" in provider.name


def test_the_sample_transport_simulates_a_failing_provider() -> None:
    with pytest.raises(ProviderThrottled):
        make_provider(sample=True).fetch_closes("FAILING.AX", date(2026, 10, 1), date(2026, 10, 2))
