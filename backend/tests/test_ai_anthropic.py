"""The Anthropic provider maps a redacted request onto the SDK and the reply back, without the
network: the SDK talks to a stub transport. The key is synthetic."""

from __future__ import annotations

import json

import anthropic
import httpx2 as httpx
import pytest

from finance360.capabilities.ai.providers import (
    Message,
    ProviderError,
    ProviderRequest,
    SystemBlock,
)
from finance360.plugins.ai_providers.anthropic import AnthropicProvider

KEY = "test-key-not-real-0000"
REQUEST = ProviderRequest(
    model="claude-haiku-5-5",
    system=(SystemBlock("Rules."), SystemBlock("THE GUIDE", cache=True)),
    messages=(Message("user", "Hi"), Message("assistant", "Hello."), Message("user", "Next?")),
    max_tokens=600,
)
OK = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-5-5",
    "content": [{"type": "text", "text": '{"answered": false}'}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 12,
        "output_tokens": 7,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 30,
    },
}


def provider(handler: httpx.MockTransport) -> AnthropicProvider:
    client = anthropic.Anthropic(
        api_key=KEY, http_client=httpx.Client(transport=handler), max_retries=0
    )
    return AnthropicProvider(KEY, client)


def test_the_request_goes_out_as_the_sdk_expects_and_the_reply_and_usage_come_back() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK)

    reply = provider(httpx.MockTransport(handle)).complete(REQUEST)
    body = json.loads(seen[0].content)
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 600
    assert body["system"] == [
        {"type": "text", "text": "Rules."},
        {"type": "text", "text": "THE GUIDE", "cache_control": {"type": "ephemeral"}},
    ]
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("user", "Hi"),
        ("assistant", "Hello."),
        ("user", "Next?"),
    ]
    assert "tools" not in body  # the model is given no tools
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ('{"answered": false}', 12, 7)
    assert (reply.cache_read_tokens, reply.cache_write_tokens) == (900, 30)


@pytest.mark.parametrize(
    ("status", "wording"),
    [(401, "refused the key"), (403, "refused the key"), (500, "did not answer"), (429, "did not")],
)
def test_a_failure_becomes_fixed_wording_that_never_carries_the_body_or_the_key(
    status: int, wording: str
) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"type": "error", "error": {"type": "x", "message": f"bad {KEY} detail"}},
        )

    with pytest.raises(ProviderError) as caught:
        provider(httpx.MockTransport(handle)).complete(REQUEST)
    assert wording in caught.value.message
    assert KEY not in caught.value.message and "detail" not in caught.value.message
    assert caught.value.__cause__ is None
