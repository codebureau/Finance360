"""The ``reference`` data class (ADR-0022 section 5): a pasted official page keeps its amounts but
is still redacted for names; the exemption from amount stripping is refused anywhere else.

The golden test records exactly what a scripted provider received. Every name and number here is
invented.
"""

from __future__ import annotations

import pytest
from ai_support import (
    ACCOUNT,
    EMAIL,
    IDENTIFIER,
    INSTITUTION,
    MEMBER,
    FakeProvider,
    household_with_names,
    settings,
)
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.ai import gateway, providers
from finance360.capabilities.ai.gateway import AiRequest, DataClass, Part, UndeclaredPartError
from finance360.capabilities.ai.redact import Redactor, Vocabulary

PAGE = (
    f"Signed in as {MEMBER} <{EMAIL}> | {ACCOUNT} at {INSTITUTION}, account {IDENTIFIER}.\n"
    "The invented rate is $0.88 per km; the cap is $1,234.50 and the limit is 5,000 km.\n"
    "Reference 123456789012 for FY 2026-27, announced 2026-07-01, A$2k, 99 cents."
)


@pytest.fixture
def provider() -> FakeProvider:
    fake = FakeProvider("{}", key="fake")
    providers.register(fake)
    return fake


def reference_request(page: str = PAGE) -> AiRequest:
    return AiRequest(
        "rule_capture",
        "Rule figure capture",
        gateway.REFERENCE,
        [
            Part("instructions", "Find the figures."),
            Part("specs", "FIGURE key=tax.car.rate_per_km"),
            Part("page_text", page, role="user"),
        ],
        500,
    )


def call(factory: sessionmaker[Session], request: AiRequest) -> None:
    with factory.begin() as s:
        gateway.complete(s, settings("fake"), request)


@pytest.fixture(autouse=True)
def _unregister() -> object:
    yield
    providers.unregister("fake")


def test_the_page_keeps_its_figures_and_loses_every_name_the_household_knows(
    session_factory: sessionmaker[Session], provider: FakeProvider
) -> None:
    household_with_names(session_factory)
    call(session_factory, reference_request())
    (received,) = provider.requests
    page = received.messages[0].text
    # Every figure, date and number is exactly as pasted.
    for kept in ("$0.88", "$1,234.50", "5,000 km", "123456789012", "2026-27", "2026-07-01"):
        assert kept in page
    assert "A$2k" in page and "99 cents" in page and "[AMOUNT]" not in page
    # No name, e-mail address, institution or Account identifier survives.
    for hidden in (MEMBER, EMAIL, ACCOUNT, INSTITUTION, IDENTIFIER, "98765432"):
        assert hidden not in page
    assert "[MEMBER_1]" in page and "[EMAIL_1]" in page and "[ACCOUNT_1]" in page
    assert "[ENTITY_1]" in page and "[NUMBER_1]" in page


def test_the_public_parts_are_sent_as_they_are(
    session_factory: sessionmaker[Session], provider: FakeProvider
) -> None:
    call(session_factory, reference_request())
    (received,) = provider.requests
    assert [b.text for b in received.system] == [
        "Find the figures.",
        "FIGURE key=tax.car.rate_per_km",
    ]


def test_the_same_page_in_the_guidance_class_is_still_stripped_of_amounts(
    session_factory: sessionmaker[Session], provider: FakeProvider
) -> None:
    household_with_names(session_factory)
    call(
        session_factory,
        AiRequest(
            "ask",
            "Ask panel question",
            gateway.GUIDANCE,
            [Part("conversation", PAGE, role="user")],
            500,
        ),
    )
    (received,) = provider.requests
    text = received.messages[0].text
    for stripped in ("$0.88", "$1,234.50", "123456789012", "A$2k"):
        assert stripped not in text
    assert "[AMOUNT]" in text and "[NUMBER_" in text


def test_the_log_records_the_page_as_sent_with_its_figures(
    session_factory: sessionmaker[Session], provider: FakeProvider
) -> None:
    from sqlalchemy import select

    from finance360.core.models.ai_log import AiLog

    household_with_names(session_factory)
    call(session_factory, reference_request())
    with session_factory.begin() as s:
        row = s.scalars(select(AiLog)).one()
    assert row.data_class == "reference" and row.task == "rule_capture"
    assert "$1,234.50" in row.sent and MEMBER not in row.sent and EMAIL not in row.sent


@pytest.mark.parametrize(
    "data_class",
    [
        # Another class cannot keep a part's figures, whatever it is called...
        DataClass("guidance", {"conversation": False}, figures_kept=frozenset({"conversation"})),
        DataClass("analysis", {"page_text": False}, figures_kept=frozenset({"page_text"})),
        # ...nor can reference keep them for any part but the pasted page...
        DataClass(
            "reference",
            {"instructions": True, "specs": True, "page_text": False, "other": False},
            figures_kept=frozenset({"other"}),
        ),
        DataClass(
            "reference",
            {"instructions": True, "specs": True, "page_text": False, "other": False},
            figures_kept=frozenset({"page_text", "other"}),
        ),
        # ...and a public part has nothing to redact in the first place.
        DataClass(
            "reference",
            {"instructions": True, "specs": True, "page_text": True},
            figures_kept=frozenset({"page_text"}),
        ),
    ],
)
def test_the_exemption_from_amount_stripping_is_refused_anywhere_else(
    session_factory: sessionmaker[Session], provider: FakeProvider, data_class: DataClass
) -> None:
    parts = [Part(name, "$1,234.50") for name in data_class.parts]
    request = AiRequest("other", "Test", data_class, parts, 100)
    with pytest.raises(UndeclaredPartError):
        call(session_factory, request)
    assert provider.requests == []  # refused before the provider was touched


def test_reference_refuses_a_part_it_does_not_declare(
    session_factory: sessionmaker[Session], provider: FakeProvider
) -> None:
    request = AiRequest(
        "rule_capture",
        "Rule figure capture",
        gateway.REFERENCE,
        [Part("household_balances", "$1,234.50")],
        100,
    )
    with pytest.raises(UndeclaredPartError):
        call(session_factory, request)
    assert provider.requests == []


def test_the_reference_class_declares_the_page_as_its_only_non_public_part() -> None:
    assert dict(gateway.REFERENCE.parts) == {
        "instructions": True,
        "specs": True,
        "page_text": False,
    }
    assert gateway.REFERENCE.figures_kept == {"page_text"}
    assert gateway.GUIDANCE.figures_kept == frozenset()


def test_redactor_keeps_figures_only_when_told_and_shares_tokens_across_parts() -> None:
    redactor = Redactor(Vocabulary((("MEMBER", "Zelda Quimbly"),)))
    text = "Zelda Quimbly sees $12.50 and ref 123456789."
    assert redactor.redact(text) == "[MEMBER_1] sees [AMOUNT] and ref [NUMBER_1]."
    assert redactor.redact(text, keep_figures=True) == "[MEMBER_1] sees $12.50 and ref 123456789."
    assert redactor.restore("[MEMBER_1] has it.") == "Zelda Quimbly has it."
