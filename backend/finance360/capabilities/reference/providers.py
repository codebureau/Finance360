"""Reference-data provider protocols, canonical candidates and registry (ADR-0013 section 6).

A provider is a plug-in (``plugins/providers/``) that turns an outside source into **canonical
candidates**: a ``PriceSeries`` of raw closes or an ``FxFetch`` of foreign to AUD quotes, with
exact ``Decimal`` values. It does no database work and no audit; only the reference service writes
them, with provenance (ADR-0003). Nothing beyond this canonical layer knows which provider
supplied a value, except through the ``source`` and ``provenance_note`` the service records.

Plug-ins import this module and register from the composition root (``main.py``), so ``api/`` and
``jobs/`` never import ``plugins``.

Floats never cross this boundary. A provider that receives floats converts them itself, once, and
says how it rounds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


class ProviderError(Exception):
    """The provider could not answer. The message is safe to show and to store: no secrets."""


class ProviderThrottled(ProviderError):  # noqa: N818 - reads as the condition, like HTTP 429
    """The provider is rate limiting us. Treated like any error: back off once, then give up."""


@dataclass(frozen=True)
class PriceBar:
    """One trading day's raw (unadjusted) Close, exact."""

    as_at: date
    close: Decimal


@dataclass(frozen=True)
class PriceSeries:
    """What a price provider returns for one symbol: its currency and the bars it has.

    Days with no trading (weekends, holidays) are simply absent. Nothing is filled forward.
    ``currency`` is what the provider says the symbol is quoted in (``None`` if it cannot tell,
    which the service refuses).
    """

    currency: str | None
    bars: tuple[PriceBar, ...]


class PriceProvider(Protocol):
    """Fetches daily closes for a provider symbol."""

    #: Stable registry key, stored as ``price.source`` and ``fetch_run.provider``.
    key: str
    #: Shown to the owner and recorded in each Price's ``provenance_note``.
    name: str
    #: The Instrument identifier scheme holding this provider's symbol (``yahoo``).
    identifier_scheme: str

    def fetch_closes(self, symbol: str, start: date, end: date) -> PriceSeries:
        """Raw Closes for ``symbol`` from ``start`` to ``end``, both inclusive.

        Raise ``ProviderError`` (``ProviderThrottled`` when rate limited) if it cannot answer.
        """
        ...


@dataclass(frozen=True)
class FxQuote:
    """One day's foreign to AUD rate, already in the stored direction and at scale 10.

    ``note`` records what the provider published (the pre-inversion figure), for provenance.
    """

    from_ccy: str
    as_at: date
    rate: Decimal
    note: str


@dataclass(frozen=True)
class FxFetch:
    """What an FX provider returns: quotes, and the requested currencies it does not publish."""

    quotes: tuple[FxQuote, ...]
    unsupported: tuple[str, ...] = ()


class FxProvider(Protocol):
    """Fetches foreign to AUD rates."""

    key: str
    name: str

    def fetch_rates(self, currencies: Sequence[str], start: date, end: date) -> FxFetch:
        """Rates for ``currencies`` from ``start`` to ``end`` inclusive, or ``ProviderError``."""
        ...


# --- registry ------------------------------------------------------------------------------

_PRICE_PROVIDERS: dict[str, PriceProvider] = {}
_FX_PROVIDERS: dict[str, FxProvider] = {}


def register_price_provider(provider: PriceProvider) -> None:
    """Add a price provider; registering again under the same key replaces it (each app instance
    registers the provider variant its own settings select)."""
    _PRICE_PROVIDERS[provider.key] = provider


def register_fx_provider(provider: FxProvider) -> None:
    _FX_PROVIDERS[provider.key] = provider


def unregister(key: str) -> None:
    """For tests that register a stand-in."""
    _PRICE_PROVIDERS.pop(key, None)
    _FX_PROVIDERS.pop(key, None)


def price_provider(key: str) -> PriceProvider | None:
    return _PRICE_PROVIDERS.get(key)


def fx_provider(key: str) -> FxProvider | None:
    return _FX_PROVIDERS.get(key)


def price_provider_keys() -> list[str]:
    return sorted(_PRICE_PROVIDERS)


def fx_provider_keys() -> list[str]:
    return sorted(_FX_PROVIDERS)
