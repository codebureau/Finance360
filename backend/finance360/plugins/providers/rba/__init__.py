"""The ``rba`` FX provider (ADR-0013 section 6): foreign to AUD rates from the Reserve Bank of
Australia's daily exchange rate table, inverted once at ingestion (``parse.py``).

Only the currencies asked for are returned; a currency the table does not publish is reported as
``unsupported`` so the service can say so rather than fail. The network sits behind ``FetchCsv``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from finance360.capabilities.reference.providers import FxFetch, FxQuote
from finance360.plugins.providers.rba.parse import invert, parse_f11
from finance360.plugins.providers.rba.sample import sample_f11
from finance360.plugins.providers.rba.transport import FetchCsv, fetch_f11

KEY = "rba"
NAME = "Reserve Bank of Australia (F11.1 daily exchange rates)"
SAMPLE_NAME = "Reserve Bank of Australia (F11.1), offline sample data"


class RbaProvider:
    key = KEY

    def __init__(self, fetch: FetchCsv | None = None, name: str = NAME) -> None:
        self.name = name
        # Looked up when the provider is built, not at import, so a test can swap the transport.
        self._fetch = fetch if fetch is not None else fetch_f11

    def fetch_rates(self, currencies: Sequence[str], start: date, end: date) -> FxFetch:
        table = parse_f11(self._fetch())
        quotes: list[FxQuote] = []
        for ccy in currencies:
            for as_at, published in sorted(table.published.get(ccy, {}).items()):
                if not start <= as_at <= end:
                    continue
                rate = invert(published)
                if rate is None:
                    continue
                quotes.append(
                    FxQuote(
                        ccy,
                        as_at,
                        rate,
                        f"RBA F11.1: A$1 = {_plain(published)} {ccy}; inverted once "
                        "(half-even, 10 d.p.)",
                    )
                )
        unsupported = tuple(c for c in currencies if not table.published.get(c))
        return FxFetch(tuple(quotes), unsupported)


def _plain(value: Decimal) -> str:
    return format(value, "f")


def make_provider(*, sample: bool = False) -> RbaProvider:
    """The live provider, or (``sample``) the same code over an invented offline table."""
    if sample:
        return RbaProvider(sample_f11, SAMPLE_NAME)
    return RbaProvider()
