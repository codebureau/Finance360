"""AI providers (ADR-0021 section 2): the real ones, behind one Protocol.

``FACTORIES`` maps the ``FINANCE360_AI_PROVIDER`` value to a function that builds the provider from
the API key. ``main.py``, the composition root, registers the one selected. With none selected there
is no model: Ask searches the Guide and Rule figures are read by the word matcher (ADR-0023). The
offline ``sample`` provider is gone; tests use a scripted ``FakeProvider``.
"""

from __future__ import annotations

from collections.abc import Callable

from finance360.capabilities.ai.providers import Provider
from finance360.plugins.ai_providers import anthropic

FACTORIES: dict[str, Callable[[str | None], Provider]] = {
    "anthropic": anthropic.make_provider,
}
