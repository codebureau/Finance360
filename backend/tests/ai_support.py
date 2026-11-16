"""Test helpers for the AI gateway and the Ask panel (ADR-0021). No test ever calls a real provider:
a scripted ``FakeProvider`` is registered under the key the settings select.

Every name, number and e-mail below is invented.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.ai import providers
from finance360.capabilities.ai.providers import ProviderRequest, ProviderResponse
from finance360.capabilities.household import service as household
from finance360.capabilities.imports import account_identifiers
from finance360.capabilities.position import accounts
from finance360.core.ai_settings import AiSettings
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue

CTX = AuditContext("owner", "login:1", "api")

#: Invented, and chosen so none appears in the Guide or in any prompt text the product writes.
HOUSEHOLD = "Quokkaville Household"
MEMBER = "Zelda Quimbly"
MEMBER_FIRST = "Zelda"
OTHER_MEMBER = "Barnaby Fitzgrubb"
ACCOUNT = "Wombat Everyday Saver"
INSTITUTION = "Bandicoot Bank"
IDENTIFIER = "062-000 98765432"
EMAIL = "zelda@quokkaville.example"
VOCABULARY = (HOUSEHOLD, MEMBER, MEMBER_FIRST, OTHER_MEMBER, ACCOUNT, INSTITUTION, IDENTIFIER)


class FakeProvider:
    """Answers from a script. A reply is text, or an exception to raise. Every request is kept."""

    fixed_model: str | None = None

    def __init__(
        self,
        replies: str | Exception | list[str | Exception],
        *,
        key: str = "fake",
        input_tokens: int = 1000,
        output_tokens: int = 100,
        cache_read_tokens: int = 0,
    ) -> None:
        self.key = key
        self.replies = list(replies) if isinstance(replies, list) else [replies]
        self.usage = (input_tokens, output_tokens, cache_read_tokens)
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return ProviderResponse(reply, self.usage[0], self.usage[1], self.usage[2])

    def say(self, *replies: str | Exception) -> None:
        """Replace the script."""
        self.replies = list(replies)

    @property
    def everything_sent(self) -> str:
        """All text of all requests, as the provider saw it."""
        return "\n".join(
            [b.text for r in self.requests for b in r.system]
            + [m.text for r in self.requests for m in r.messages]
        )


def reply(answered: bool, answer: str = "", sources: list[Any] | None = None) -> str:
    return json.dumps({"answered": answered, "answer": answer, "sources": sources or []})


def cited(answer: str, slug: str = "start-here") -> str:
    return reply(True, answer, [{"kind": "guide", "ref": slug}])


def settings(provider: str | None = "fake", **changes: Any) -> AiSettings:
    return replace(AiSettings(provider=provider), **changes)


@pytest.fixture
def fake() -> Iterator[FakeProvider]:
    """A registered ``FakeProvider`` that answers a cited reply; unregistered afterwards."""
    provider = FakeProvider(cited("Start with the Guide."))
    providers.register(provider)
    yield provider
    providers.unregister(provider.key)


def household_with_names(factory: sessionmaker[Session]) -> None:
    """The invented household the redaction tests use: a Household, two Members, an Account with an
    identifier and its institution."""
    with factory.begin() as s:
        seed_catalogue(s)
        household.ensure_household(s, HOUSEHOLD)
        member = household.add_member(s, CTX, MEMBER, date(1984, 3, 12)).id
        household.add_member(s, CTX, OTHER_MEMBER, date(1986, 5, 1))
        account = accounts.create_account(
            s,
            CTX,
            accounts.AccountInput(
                name=ACCOUNT,
                category_key="cash",
                account_type_key="savings_account",
                owners=[accounts.OwnerInput(member)],
            ),
        ).id
        account_identifiers.add_identifier(
            s,
            CTX,
            account,
            account_identifiers.IdentifierInput("bsb_account", IDENTIFIER, INSTITUTION),
        )
