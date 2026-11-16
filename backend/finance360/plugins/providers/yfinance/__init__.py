"""The ``yfinance`` price provider (ADR-0013 section 6): raw daily Closes for an Instrument's
``yahoo`` symbol, mapped into the canonical ``PriceSeries``.

Unofficial and expected to break (ADR-0003): swapping it is one plug-in plus the configured key.
The network sits behind ``HistoryFn`` (``transport.py``), so tests and the review instance use a
stand-in and never reach Yahoo."""

from __future__ import annotations

from datetime import date

from finance360.capabilities.reference.providers import PriceBar, PriceSeries
from finance360.plugins.providers.yfinance.convert import close_to_decimal
from finance360.plugins.providers.yfinance.sample import sample_history
from finance360.plugins.providers.yfinance.transport import HistoryFn, yfinance_history

KEY = "yfinance"
NAME = "Yahoo Finance (yfinance)"
SAMPLE_NAME = "Yahoo Finance (yfinance), offline sample data"


class YFinanceProvider:
    key = KEY
    identifier_scheme = "yahoo"

    def __init__(self, history: HistoryFn | None = None, name: str = NAME) -> None:
        self.name = name
        # Looked up when the provider is built, not at import, so a test can swap the transport.
        self._history = history if history is not None else yfinance_history

    def fetch_closes(self, symbol: str, start: date, end: date) -> PriceSeries:
        raw = self._history(symbol, start, end)
        bars: list[PriceBar] = []
        for as_at, close in raw.rows:
            value = close_to_decimal(close)
            if value is not None:  # NaN, zero or negative: a gap, never a repaired number
                bars.append(PriceBar(as_at, value))
        return PriceSeries(raw.currency, tuple(bars))


def make_provider(*, sample: bool = False) -> YFinanceProvider:
    """The live provider, or (``sample``) the same code over invented offline data."""
    if sample:
        return YFinanceProvider(sample_history, SAMPLE_NAME)
    return YFinanceProvider()
