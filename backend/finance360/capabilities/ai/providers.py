"""The Provider Protocol and registry (ADR-0021 sections 2 and 4).

A provider turns an already-redacted request into text and token counts. It sees nothing else:
no database, no vocabulary, no Capability. Providers live in ``plugins/ai_providers`` and are
registered from ``main.py``, the composition root, like Importers and price providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class SystemBlock:
    text: str
    #: A stable prefix worth caching (the Guide), so repeats cost far less.
    cache: bool = False


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    system: tuple[SystemBlock, ...]
    messages: tuple[Message, ...]
    max_tokens: int


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ProviderError(Exception):
    """The provider could not answer. ``message`` is calm, fixed wording safe to show: it never
    carries the request, the response body or a credential."""

    def __init__(
        self, message: str = "The AI service did not answer. Try again in a moment."
    ) -> None:
        super().__init__(message)
        self.message = message


class Provider(Protocol):
    key: str
    #: A provider that serves one fixed model (the offline demo) names it here; else None.
    fixed_model: str | None

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


_PROVIDERS: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    """Add a provider; registering again under the same key replaces it."""
    _PROVIDERS[provider.key] = provider


def get(key: str) -> Provider | None:
    return _PROVIDERS.get(key)


def keys() -> list[str]:
    return sorted(_PROVIDERS)


def unregister(key: str) -> None:
    """For tests that register a stand-in."""
    _PROVIDERS.pop(key, None)
