"""The only code that talks to Yahoo Finance, behind a one-function seam (``HistoryFn``).

Tests never reach the network: they pass a fake ``ticker_factory`` or a fake ``HistoryFn``. This
module returns raw floats; ``convert.py`` turns them into exact decimals.
"""

from __future__ import annotations

import importlib
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance360.capabilities.reference.providers import ProviderError, ProviderThrottled


@dataclass(frozen=True)
class RawHistory:
    """What a transport returns: the quote currency and ``(trading date, raw Close float)`` rows."""

    currency: str | None
    rows: tuple[tuple[date, float], ...]


#: ``(symbol, start, end)`` with both ends inclusive.
HistoryFn = Callable[[str, date, date], RawHistory]

TIMEOUT_SECONDS = 20


def _cache_dir() -> Path:
    """A writable place for yfinance's timezone and cookie caches. The container's root file system
    is read-only, so this is the tmpfs, never the data volume (nothing here belongs in a Backup)."""
    return Path(tempfile.gettempdir()) / "finance360-yfinance"


def yfinance_history(
    symbol: str,
    start: date,
    end: date,
    *,
    ticker_factory: Callable[[str], Any] | None = None,
) -> RawHistory:
    """Daily **raw** Closes (``auto_adjust=False``, no dividend or split actions) from yfinance."""
    # Heavy (pandas) and untyped: imported only when a fetch happens, and held as Any.
    yf: Any = importlib.import_module("yfinance")

    # Errors must surface as exceptions (the default logs and returns an empty frame, which would
    # make a rate limit look like "no data"). This is yfinance's own global switch.
    yf.config.debug.hide_exceptions = False
    factory: Callable[[str], Any]
    if ticker_factory is None:
        cache = _cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache))
        factory = yf.Ticker
    else:
        factory = ticker_factory
    ticker: Any = factory(symbol)
    try:
        frame: Any = ticker.history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # yfinance's end is exclusive
            interval="1d",
            auto_adjust=False,
            actions=False,
            timeout=TIMEOUT_SECONDS,
        )
        metadata: dict[str, Any] = ticker.get_history_metadata() or {}
    except yf.exceptions.YFRateLimitError:
        raise ProviderThrottled("Yahoo Finance is rate limiting requests.") from None
    except (yf.exceptions.YFPricesMissingError, yf.exceptions.YFTickerMissingError):
        return RawHistory(None, ())  # no bars for the range: a gap, not an error
    except Exception as error:  # noqa: BLE001 - library and network errors vary widely
        raise ProviderError(f"Yahoo Finance could not be read ({type(error).__name__}).") from None
    closes: Any = frame["Close"]
    rows = tuple((stamp.date(), float(close)) for stamp, close in closes.items())
    currency = metadata.get("currency")
    return RawHistory(currency if isinstance(currency, str) else None, rows)
