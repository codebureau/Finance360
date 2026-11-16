"""The Ask panel's backend (ADR-0021, amended by ADR-0023).

Ask is two steps. ``find`` is **retrieval**: keyword search over the Guide's Passages, no AI,
no cost, nothing sent anywhere. ``answer`` is the optional **answering** step: the server re-runs
retrieval itself (the client never names Passages or text, so it cannot choose what reaches a
model) and asks the model to write an answer over the best Passages and What's due. It explains
and points, never changes data, and treats the model's reply as *data to validate*: this module
builds every link label from the Guide itself and drops any source outside the Guide's closed
set. An answer left with no valid source becomes a fixed "I couldn't find that".

Reads the Guide, What's due (through the ``due`` service) and the gateway. No other Capability.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from finance360.capabilities.ai import gateway, providers
from finance360.capabilities.ai.errors import AiOff
from finance360.capabilities.ask import corpus, prompt, search
from finance360.capabilities.ask.guide import START_SLUG, Guide, load
from finance360.capabilities.ask.types import Turn
from finance360.capabilities.due import service as due_service
from finance360.core.ai_settings import AiSettings
from finance360.core.errors import FieldError, ValidationError

TASK = "ask"
WHY = "Ask panel question"
QUESTION_MAX = 500
HISTORY_MAX = 6
TURN_MAX = 3000
ANSWER_MAX = 2000
SOURCES_MAX = 4
#: How many Passages the panel shows (ADR-0023 section 4).
SHOWN = 3
SCREEN_PATTERN = re.compile(r"^/[A-Za-z0-9/$_-]{0,80}$")
#: Shown when the Guide does not cover it (ADR-0021 section 5). The link is added as a source.
NOT_FOUND = "I couldn't find that in the Guide. Start here may help."
START_LABEL = "Start here"


@dataclass(frozen=True)
class Source:
    kind: str  # "guide" or "screen"
    ref: str
    #: Built from the Guide's page title or link text, never by the model.
    label: str
    #: An in-app path the user can follow.
    link: str


@dataclass(frozen=True)
class AskResult:
    answer: str
    sources: tuple[Source, ...]
    #: True when the answer is backed by at least one valid source.
    answered: bool
    #: The model that wrote it; empty for the fixed "couldn't find that".
    model: str = ""


@dataclass(frozen=True)
class PassageView:
    """One search hit as the panel shows it."""

    page: str
    #: The page's title, which the link is labelled with.
    label: str
    heading: str
    excerpt: str
    link: str


@dataclass(frozen=True)
class Found:
    passages: tuple[PassageView, ...]
    #: Set when there are no Passages: the fixed "couldn't find that" with its Start here link.
    message: str
    start: Source | None


def _invalid(field: str, message: str) -> ValidationError:
    return ValidationError([FieldError(field, message)])


def _validate(
    guide: Guide,
    question: str,
    history: Sequence[Turn],
    screen: str | None,
    guide_slug: str | None,
) -> str:
    question = question.strip()
    if not question:
        raise _invalid("question", "Enter a question.")
    if len(question) > QUESTION_MAX:
        raise _invalid("question", f"Keep the question to {QUESTION_MAX} characters.")
    if len(history) > HISTORY_MAX:
        raise _invalid("history", f"At most {HISTORY_MAX} earlier messages.")
    for i, turn in enumerate(history):
        if turn.role != ("user" if i % 2 == 0 else "assistant") or len(turn.text) > TURN_MAX:
            raise _invalid("history", "Earlier messages must alternate, starting with a question.")
    if history and history[-1].role != "assistant":
        raise _invalid("history", "Earlier messages must end with an answer.")
    if screen is not None and not SCREEN_PATTERN.fullmatch(screen):
        raise _invalid("screen", "This is not a screen path.")
    if guide_slug is not None and guide.page(guide_slug) is None:
        raise _invalid("guide_slug", "This is not a Guide page.")
    return question


def _parse(text: str) -> dict[str, Any] | None:
    """The reply's JSON object, tolerating a code fence or a line of chatter around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def _valid_sources(guide: Guide, raw: object) -> tuple[Source, ...]:
    if not isinstance(raw, list):
        return ()
    found: list[Source] = []
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, object], item)
        kind, ref = entry.get("kind"), entry.get("ref")
        source: Source | None = None
        if kind == "guide" and isinstance(ref, str) and (page := guide.page(ref)) is not None:
            source = Source("guide", page.slug, page.title, page.path)
        elif kind == "screen" and isinstance(ref, str) and ref in guide.screens:
            source = Source("screen", ref, guide.screens[ref], ref)
        if source is not None and source not in found:
            found.append(source)
    return tuple(found[:SOURCES_MAX])


def _start(guide: Guide) -> Source:
    page = guide.page(START_SLUG)
    return Source("guide", START_SLUG, START_LABEL, page.path if page else "/guide")


def _not_found(guide: Guide) -> AskResult:
    return AskResult(NOT_FOUND, (_start(guide),), False)


def _retrieve(
    guide_dir: Path, context_file: Path | None, question: str, limit: int
) -> tuple[list[corpus.Passage], list[corpus.Corpus]]:
    sources = corpus.corpora(guide_dir)
    return search.search(sources, question, limit, context_file), sources


def find(
    guide_dir: Path,
    context_file: Path | None,
    question: str,
    *,
    history: Sequence[Turn] = (),
    screen: str | None = None,
    guide_slug: str | None = None,
) -> Found:
    """Retrieval only: the best Passages for ``question``. Raises ``ValidationError`` for bad input.
    No session, no provider: this is not an AI call."""
    guide = load(guide_dir)
    question = _validate(guide, question, history, screen, guide_slug)
    hits, _ = _retrieve(guide_dir, context_file, question, SHOWN)
    if not hits:
        return Found((), NOT_FOUND, _start(guide))
    return Found(
        tuple(
            PassageView(p.page, p.page_title, p.shown_heading, corpus.excerpt(p), p.link)
            for p in hits
        ),
        "",
        None,
    )


def answer(
    session: Session,
    settings: AiSettings,
    guide_dir: Path,
    context_file: Path | None,
    question: str,
    *,
    history: Sequence[Turn] = (),
    screen: str | None = None,
    guide_slug: str | None = None,
) -> AskResult:
    """The written answer over the best Passages, retrieved here. Raises ``ValidationError`` for bad
    input and the gateway's ``AiError`` subclasses when the call is refused or fails (off, capped,
    busy, provider down)."""
    guide = load(guide_dir)
    question = _validate(guide, question, history, screen, guide_slug)
    if settings.provider is None or providers.get(settings.provider) is None:
        raise AiOff
    hits, sources = _retrieve(guide_dir, context_file, question, prompt.MAX_PASSAGES)
    passages = corpus.answering_passages(hits, {c.name: c for c in sources})
    if not passages:
        # Nothing to write an answer over: do not spend a call, and do not let a model improvise.
        return _not_found(guide)
    due = due_service.whats_due(session).items[: prompt.MAX_DUE_ITEMS]
    result = gateway.complete(
        session,
        settings,
        gateway.AiRequest(
            task=TASK,
            why=WHY,
            data_class=gateway.GUIDANCE,
            parts=prompt.build_parts(
                guide,
                passages,
                screen=screen,
                guide_slug=guide_slug,
                due=due,
                history=history,
                question=question,
            ),
            max_tokens=prompt.MAX_OUTPUT_TOKENS,
        ),
    )
    reply = _parse(result.text)
    if reply is None:
        gateway.mark_unsupported(session, result.log_id)
        return _not_found(guide)
    if reply.get("answered") is not True:
        return _not_found(guide)  # the model said it does not know: a legitimate answer
    text = reply.get("answer")
    cited = _valid_sources(guide, reply.get("sources"))
    if not isinstance(text, str) or not text.strip() or not cited:
        gateway.mark_unsupported(session, result.log_id)
        return _not_found(guide)
    return AskResult(result.restore(text.strip())[:ANSWER_MAX], cited, True, result.model)
