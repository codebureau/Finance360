"""Settings for the daily fetch: off by default, validated loudly (ADR-0013 section 6)."""

from __future__ import annotations

from datetime import time

import pytest

from finance360.config import Settings


def test_fetching_is_off_by_default_and_the_defaults_are_the_documented_ones() -> None:
    s = Settings.from_env({})
    assert (s.price_provider, s.fx_provider) == (None, None)
    assert s.provider_transport == "live"
    assert s.fetch_time == time(18, 30)
    assert s.price_stale_days == 7


def test_empty_values_mean_not_set() -> None:
    s = Settings.from_env(
        {
            "FINANCE360_PRICE_PROVIDER": "  ",
            "FINANCE360_FX_PROVIDER": "",
            "FINANCE360_FETCH_TIME": "",
        }
    )
    assert (s.price_provider, s.fx_provider, s.fetch_time) == (None, None, time(18, 30))


def test_providers_time_transport_and_staleness_are_read_from_the_environment() -> None:
    s = Settings.from_env(
        {
            "FINANCE360_PRICE_PROVIDER": "YFinance",
            "FINANCE360_FX_PROVIDER": "rba",
            "FINANCE360_PROVIDER_TRANSPORT": "Sample",
            "FINANCE360_FETCH_TIME": "07:05",
            "FINANCE360_PRICE_STALE_DAYS": "10",
        }
    )
    assert (s.price_provider, s.fx_provider) == ("yfinance", "rba")
    assert (s.provider_transport, s.fetch_time, s.price_stale_days) == ("sample", time(7, 5), 10)


@pytest.mark.parametrize(
    ("var", "bad"),
    [
        ("FINANCE360_FETCH_TIME", "6pm"),
        ("FINANCE360_FETCH_TIME", "25:00"),
        ("FINANCE360_PROVIDER_TRANSPORT", "mock"),
        ("FINANCE360_PRICE_STALE_DAYS", "0"),
        ("FINANCE360_PRICE_STALE_DAYS", "seven"),
        ("FINANCE360_PRICE_STALE_DAYS", "400"),
    ],
)
def test_a_bad_value_fails_loudly_and_names_the_variable(var: str, bad: str) -> None:
    with pytest.raises(ValueError, match=var):
        Settings.from_env({var: bad})
