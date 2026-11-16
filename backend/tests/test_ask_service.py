"""The Ask service (ADR-0021 sections 1 and 5, ADR-0023 section 4): retrieval finds Passages with no
model; answering writes over the best Passages, validates what the model proposes, and never changes
data. A scripted provider stands in for the model, so CI holds no key and calls nothing. Every name
is invented.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date

import pytest
from ai_support import (
    ACCOUNT,
    CTX,
    MEMBER,
    FakeProvider,
    cited,
    household_with_names,
    reply,
    settings,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.ai import providers
from finance360.capabilities.ai.errors import AiOff
from finance360.capabilities.ask import prompt
from finance360.capabilities.ask.service import NOT_FOUND, Source, answer, find
from finance360.capabilities.ask.types import Turn
from finance360.capabilities.household import service as household
from finance360.config import SOURCE_CONTEXT_FILE, SOURCE_GUIDE_DIR
from finance360.core import due as core_due
from finance360.core.audit import AuditContext
from finance360.core.errors import ValidationError
from finance360.core.models import audit as _audit
from finance360.core.models.account import Account
from finance360.core.models.ai_log import AiLog
from finance360.core.models.audit import AuditEvent
from finance360.main import register_due_rules

START = Source("guide", "start-here", "Start here", "/guide")
_ = (_audit, AuditContext)


@pytest.fixture(autouse=True)
def rules() -> Iterator[None]:
    saved = dict(core_due._RULES)
    core_due._RULES.clear()
    register_due_rules()
    yield
    core_due._RULES.clear()
    core_due._RULES.update(saved)


@pytest.fixture
def names(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    household_with_names(session_factory)
    return session_factory


def do_ask(
    factory: sessionmaker[Session],
    provider: FakeProvider,
    question: str,
    **kw: object,
):  # type: ignore[no-untyped-def]
    providers.register(provider)
    try:
        with factory.begin() as s:
            return answer(
                s,
                settings(provider.key),
                SOURCE_GUIDE_DIR,
                SOURCE_CONTEXT_FILE,
                question,
                **kw,  # type: ignore[arg-type]
            )
    finally:
        providers.unregister(provider.key)


def last_row(factory: sessionmaker[Session]) -> AiLog:
    with factory.begin() as s:
        row = s.scalars(select(AiLog).order_by(AiLog.id.desc())).first()
        assert row is not None
        s.expunge(row)
        return row


# --- a cited answer ----------------------------------------------------------------------------


def test_a_cited_answer_carries_links_labelled_from_the_guide_not_by_the_model(
    names: sessionmaker[Session],
) -> None:
    model = reply(
        True,
        "Rule figures are the government's yearly rates.",
        [
            {"kind": "guide", "ref": "rule-figures", "label": "Click here for free money"},
            {"kind": "screen", "ref": "/rule-figures", "link": "https://evil.example"},
        ],
    )
    result = do_ask(names, FakeProvider(model), "What is a Rule figure?")
    assert result.answered is True
    assert result.answer == "Rule figures are the government's yearly rates."
    assert [(s.kind, s.ref, s.label, s.link) for s in result.sources] == [
        (
            "guide",
            "rule-figures",
            "Rule figures: keeping official rates current",
            "/guide/rule-figures",
        ),
        ("screen", "/rule-figures", "Rule figures", "/rule-figures"),
    ]


def test_a_name_in_the_question_reaches_the_model_as_a_token_and_comes_back_as_the_name(
    names: sessionmaker[Session],
) -> None:
    fake = FakeProvider(cited("Check [MEMBER_1]'s [ACCOUNT_1] on the Position screen."))
    result = do_ask(names, fake, f"Is {MEMBER} up to date on {ACCOUNT}?")
    assert result.answer == f"Check {MEMBER}'s {ACCOUNT} on the Position screen."
    assert MEMBER not in fake.everything_sent and ACCOUNT not in fake.everything_sent
    assert json.loads(last_row(names).received or "{}")["answer"].startswith("Check [MEMBER_1]")


def test_an_answer_may_carry_a_name_with_quotes_without_breaking_the_reply(
    names: sessionmaker[Session],
) -> None:
    with names.begin() as s:
        household.add_member(s, CTX, 'Pat "Patsy" Quirk', date(1990, 1, 1))
    fake = FakeProvider(cited("Hello [MEMBER_1]."))
    result = do_ask(names, fake, 'Ask about Pat "Patsy" Quirk')
    assert result.answer == 'Hello Pat "Patsy" Quirk.'


# --- the model proposes, code disposes ---------------------------------------------------------


def test_a_source_outside_the_guide_is_dropped_and_the_valid_one_kept(
    names: sessionmaker[Session],
) -> None:
    model = reply(
        True,
        "See the tax page.",
        [
            {"kind": "guide", "ref": "no-such-page"},
            {"kind": "screen", "ref": "/definitely-not-a-screen"},
            {"kind": "screen", "ref": "https://evil.example/"},
            {"kind": "pdf", "ref": "tax"},
            "not even an object",
            {"kind": "guide", "ref": "tax"},
        ],
    )
    result = do_ask(names, FakeProvider(model), "Tell me about tax")
    assert [(s.kind, s.ref) for s in result.sources] == [("guide", "tax")]
    assert last_row(names).outcome == "ok"


def test_an_answer_with_no_valid_source_becomes_i_couldnt_find_that_in_the_guide(
    names: sessionmaker[Session],
) -> None:
    for model in (
        reply(True, "Trust me.", [{"kind": "guide", "ref": "invented"}]),
        reply(True, "Trust me.", []),
        reply(True, "Trust me."),
        json.dumps({"answered": True, "answer": "Trust me.", "sources": "start-here"}),
        reply(True, "   ", [{"kind": "guide", "ref": "tax"}]),
    ):
        result = do_ask(names, FakeProvider(model), "Where do I start with the Guide?")
        assert result.answered is False
        assert (
            result.answer == NOT_FOUND == "I couldn't find that in the Guide. Start here may help."
        )
        assert result.sources == (START,)
        assert last_row(names).outcome == "unsupported"


def test_a_reply_that_is_not_json_is_the_same_not_found_and_is_logged_unsupported(
    names: sessionmaker[Session],
) -> None:
    for model in ("I think you should look at the tax page.", "{broken json", "[1, 2]", ""):
        result = do_ask(names, FakeProvider(model), "Where do I start with the Guide?")
        assert (result.answered, result.answer) == (False, NOT_FOUND)
        assert last_row(names).outcome == "unsupported"


def test_a_json_reply_in_a_code_fence_is_accepted(names: sessionmaker[Session]) -> None:
    fenced = "Here you go:\n```json\n" + cited("Yes.", "tax") + "\n```"
    result = do_ask(names, FakeProvider(fenced), "Tax?")
    assert (result.answered, result.answer) == (True, "Yes.")


def test_when_the_model_says_it_does_not_know_the_answer_is_the_fixed_not_found_logged_ok(
    names: sessionmaker[Session],
) -> None:
    result = do_ask(names, FakeProvider(reply(False, "I guess maybe", [])), "Meaning of the Guide?")
    assert (result.answered, result.answer, result.sources) == (False, NOT_FOUND, (START,))
    assert last_row(names).outcome == "ok"


def test_the_answer_is_cut_to_a_sane_length(names: sessionmaker[Session]) -> None:
    result = do_ask(names, FakeProvider(cited("x" * 5000)), "Long Guide answer?")
    assert len(result.answer) == 2000


# --- what the model is given -------------------------------------------------------------------


def test_the_prompt_carries_passages_whereabouts_and_due_wording_with_names_removed(
    names: sessionmaker[Session],
) -> None:
    with names.begin() as s:
        household.set_effort_level(s, CTX, "thorough")
    fake = FakeProvider(cited("ok"))
    do_ask(
        names,
        fake,
        "What next?",
        screen="/accounts/$accountId",
        guide_slug="path-tax",
        history=[Turn("user", "Hi"), Turn("assistant", "Hello.")],
    )
    (request,) = fake.requests
    system = "\n".join(b.text for b in request.system)
    assert "### PASSAGE page=" in system and "PASSAGES FROM THE GUIDE" in system
    assert system.count("### PASSAGE") <= prompt.MAX_PASSAGES  # the best few, never the whole Guide
    assert "### PAGE slug=" not in system
    assert "The user is on the screen /accounts/$accountId." in system
    assert "reading the Guide page path-tax" in system
    assert "WHAT'S DUE" in system and "[ACCOUNT_1]" in system  # the Account is named, then hidden
    assert ACCOUNT not in system
    assert [(m.role, m.text) for m in request.messages] == [
        ("user", "Hi"),
        ("assistant", "Hello."),
        ("user", "What next?"),
    ]
    assert request.max_tokens == 600
    assert request.model == "claude-haiku-5-5"
    assert request.system[1].cache is False  # passages differ per question: nothing to cache


def test_at_most_ten_due_items_are_sent_important_first() -> None:
    items = [
        core_due.DueItem(
            rule_key="r",
            subject_key=str(n),
            importance="optional" if n < 11 else "important",
            title=f"Item {n}",
            effect="e",
            due_on=__import__("datetime").date(2026, 1, 1),
            link="/",
            suits=(),
        )
        for n in range(15)
    ]
    text = prompt.render_due(sorted(items, key=lambda i: i.importance != "important"))
    assert text.count("\n- ") == 10 and text.splitlines()[1].startswith("- [important]")


def test_a_question_the_guide_has_no_passage_for_never_reaches_the_model(
    names: sessionmaker[Session],
) -> None:
    fake = FakeProvider(cited("I made this up."))
    result = do_ask(names, fake, "Zzzz qqqq xylophone?")
    assert (result.answered, result.answer, result.sources) == (False, NOT_FOUND, (START,))
    assert fake.requests == [] and last_row_count(names) == 0


def test_the_model_is_given_only_the_best_passages_and_the_answer_names_the_model(
    names: sessionmaker[Session],
) -> None:
    fake = FakeProvider(cited("Capture it on the Rule figures screen.", "rule-figures"))
    result = do_ask(names, fake, "How do I capture a Rule figure?")
    system = "\n".join(b.text for b in fake.requests[0].system)
    assert "heading=How to capture one" in system
    assert "heading=Ask a question" not in system and "Page slugs:" in system
    assert system.count("### PASSAGE") == prompt.MAX_PASSAGES
    assert result.model == "claude-haiku-5-5" == last_row(names).model


def test_asking_changes_no_household_data_only_the_ai_log_grows(
    names: sessionmaker[Session],
) -> None:
    def counts() -> tuple[int, int, int]:
        with names.begin() as s:
            return (
                s.scalar(select(func.count()).select_from(AuditEvent)) or 0,
                s.scalar(select(func.count()).select_from(Account)) or 0,
                s.scalar(select(func.count()).select_from(AiLog)) or 0,
            )

    before = counts()
    do_ask(names, FakeProvider(cited("ok")), "Change my balance to 5 dollars")
    assert counts() == (before[0], before[1], before[2] + 1)


# --- input validation, in the shared 422 shape -------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"question": "   "}, "question"),
        ({"question": "q" * 501}, "question"),
        ({"history": [Turn("assistant", "x")]}, "history"),
        ({"history": [Turn("user", "x")]}, "history"),
        ({"history": [Turn("user", "x"), Turn("user", "y")]}, "history"),
        ({"history": [Turn("user", "q"), Turn("assistant", "a")] * 4}, "history"),
        ({"history": [Turn("user", "x" * 3001), Turn("assistant", "a")]}, "history"),
        ({"screen": "/Accounts/4?x=1"}, "screen"),
        ({"screen": "accounts"}, "screen"),
        ({"screen": "/" + "a" * 100}, "screen"),
        ({"guide_slug": "not-a-page"}, "guide_slug"),
    ],
)
def test_bad_input_is_a_validation_error_naming_the_field_and_the_provider_is_not_called(
    names: sessionmaker[Session], kwargs: dict[str, object], field: str
) -> None:
    fake = FakeProvider(cited("ok"))
    args = {"question": "Fine, where is the Guide?", **kwargs}
    with pytest.raises(ValidationError) as caught:
        do_ask(names, fake, args.pop("question"), **args)  # type: ignore[arg-type]
    assert caught.value.errors[0].field == field
    assert fake.requests == []


def test_a_question_at_the_limit_and_six_earlier_messages_are_allowed(
    names: sessionmaker[Session],
) -> None:
    history = [Turn("user", "q"), Turn("assistant", "a")] * 3
    result = do_ask(
        names, FakeProvider(cited("ok")), "Guide " + "q" * 494, history=history, guide_slug="tax"
    )
    assert result.answered is True


def test_with_ai_off_asking_raises_off_and_logs_nothing(names: sessionmaker[Session]) -> None:
    with names.begin() as s, pytest.raises(AiOff):
        answer(s, settings(None), SOURCE_GUIDE_DIR, SOURCE_CONTEXT_FILE, "Where is the Guide?")
    with names.begin() as s, pytest.raises(AiOff):  # even for a question with no Passage
        answer(s, settings(None), SOURCE_GUIDE_DIR, SOURCE_CONTEXT_FILE, "Zzzz qqqq xylophone?")
    assert last_row_count(names) == 0


def last_row_count(factory: sessionmaker[Session]) -> int:
    with factory.begin() as s:
        return s.scalar(select(func.count()).select_from(AiLog)) or 0


# --- retrieval: the Search only step, with no model anywhere ------------------------------------


def test_find_returns_the_best_passages_with_links_and_logs_nothing(
    names: sessionmaker[Session],
) -> None:
    found = find(SOURCE_GUIDE_DIR, SOURCE_CONTEXT_FILE, "How do I capture a Rule figure?")
    assert 1 <= len(found.passages) <= 3 and found.message == "" and found.start is None
    top = found.passages[0]
    assert (top.page, top.link) == ("rule-figures", "/guide/rule-figures")
    assert top.label == "Rule figures: keeping official rates current"
    assert top.heading and top.excerpt and "](" not in top.excerpt
    assert last_row_count(names) == 0  # not an AI call: no row, no provider


def test_find_with_no_hits_is_the_fixed_not_found_with_the_start_here_link() -> None:
    found = find(SOURCE_GUIDE_DIR, SOURCE_CONTEXT_FILE, "Zzzz qqqq xylophone?")
    assert (found.passages, found.message, found.start) == ((), NOT_FOUND, START)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"question": "   "}, "question"),
        ({"question": "q" * 501}, "question"),
        ({"history": [Turn("user", "x")]}, "history"),
        ({"screen": "accounts"}, "screen"),
        ({"guide_slug": "not-a-page"}, "guide_slug"),
    ],
)
def test_find_validates_its_input_like_the_answer_step(
    kwargs: dict[str, object], field: str
) -> None:
    args = {"question": "Fine?", **kwargs}
    with pytest.raises(ValidationError) as caught:
        find(SOURCE_GUIDE_DIR, SOURCE_CONTEXT_FILE, args.pop("question"), **args)  # type: ignore[arg-type]
    assert caught.value.errors[0].field == field
