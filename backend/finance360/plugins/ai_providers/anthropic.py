"""The Anthropic provider: the official SDK, no agent framework (ADR-0021 section 4).

It receives an already-redacted request and returns text and token counts. The API key is read once
into the SDK client; it is not in ``repr``, logs, errors or anything the client sees. An SDK error
is turned into fixed wording: the exception text is never passed on.
"""

from __future__ import annotations

import logging

import anthropic

from finance360.capabilities.ai.providers import ProviderError, ProviderRequest, ProviderResponse

log = logging.getLogger(__name__)

#: Seconds to wait for the model; the SDK retries a transient failure twice.
TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2


class AnthropicProvider:
    key = "anthropic"
    fixed_model: str | None = None

    def __init__(self, api_key: str, client: anthropic.Anthropic | None = None) -> None:
        # ``client`` lets a test supply one over a stub transport; production builds its own.
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES
        )

    def __repr__(self) -> str:
        return "AnthropicProvider()"

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        system: list[anthropic.types.TextBlockParam] = []
        for block in request.system:
            item: anthropic.types.TextBlockParam = {"type": "text", "text": block.text}
            if block.cache:
                item["cache_control"] = {"type": "ephemeral"}
            system.append(item)
        messages: list[anthropic.types.MessageParam] = []
        for message in request.messages:
            if message.role == "user":
                messages.append({"role": "user", "content": message.text})
            else:
                messages.append({"role": "assistant", "content": message.text})
        try:
            reply = self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=system,
                messages=messages,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise ProviderError(
                "The AI service refused the key. Check the AI settings on the host."
            ) from None
        except anthropic.APIError as problem:
            log.warning("Anthropic call failed (%s)", type(problem).__name__)
            raise ProviderError() from None
        usage = reply.usage
        return ProviderResponse(
            text="".join(b.text for b in reply.content if b.type == "text"),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        )


def make_provider(api_key: str | None) -> AnthropicProvider:
    if not api_key:  # Settings.from_env already refuses this; defence in depth
        raise ValueError("The anthropic AI provider needs an API key.")
    return AnthropicProvider(api_key)
