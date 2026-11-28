"""Questions: evaluate the registry into Readiness, count and next step (ADR-0032 sections 2, 3, 7).

Read-only and computed on every request: nothing is stored, so a card cannot disagree with the
data. Staleness is judged once, by the Due rules: the current Due items (already filtered by
Effort level and Snoozes) are passed to each input. One input whose check raises is reported as
Could not check, by name, and never blanks the rest. ``Readiness`` can be asked before and after a
write by a later notice (#102); this module is that seam and adds no store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date

from sqlalchemy.orm import Session

from finance360.capabilities.due import service as due
from finance360.core import questions as core
from finance360.core.due import IMPORTANT, OPTIONAL, DueItem, EffortLevel, Importance
from finance360.core.errors import NotFoundError
from finance360.core.money import Money
from finance360.core.questions import (
    COULD_NOT_CHECK,
    NOT_READY,
    NOT_STARTED,
    PARTLY_READY,
    READY,
    InputResult,
    InputState,
    Question,
    QuestionInput,
    Readiness,
    Reason,
)

log = logging.getLogger(__name__)

READY_REASON = "Everything this answer needs is current."
SMALL_GAPS_REASON = "Some small gaps do not change the answer."


@dataclass(frozen=True)
class InputView:
    key: str
    name: str
    importance: Importance
    state: Readiness
    #: Every reason, in next-step order (the Question page lists them all).
    reasons: tuple[Reason, ...]
    total: Money | None
    #: Reasons whose own state is not Ready, in order: what actually needs attention.
    material: tuple[Reason, ...]
    #: Whether it weighs on the Question's state and count at this Effort level (an ``optional``
    #: input does not at Light).
    asked: bool = True


@dataclass(frozen=True)
class NextStep:
    input_key: str
    input_name: str
    reason: Reason


@dataclass(frozen=True)
class QuestionView:
    question: Question
    state: Readiness
    #: The inputs that are not Ready.
    count: int
    #: One line: why the state is what it is.
    reason: str
    next_step: NextStep | None
    inputs: tuple[InputView, ...]


@dataclass(frozen=True)
class Questions:
    today: date
    level: EffortLevel
    headline: str
    questions: tuple[QuestionView, ...]


def _could_not_check(question_input: QuestionInput) -> InputView:
    return InputView(
        question_input.key,
        question_input.name,
        question_input.importance,
        COULD_NOT_CHECK,
        (),
        None,
        (),
    )


def _evaluate_input(
    session: Session,
    question_input: QuestionInput,
    today: date,
    level: EffortLevel,
    items: tuple[DueItem, ...],
) -> InputView:
    try:
        result: InputResult = question_input.check(session, today, level, items)
        state = core.rate_input(result)
    except Exception:
        log.exception("Question input %s could not be checked", question_input.key)
        return _could_not_check(question_input)
    reasons = core.ordered(result.reasons)
    # A Not started or blocking input shows its reasons whole (its next step is the first input);
    # otherwise only a reason that is itself above a threshold needs attention.
    if state in (NOT_STARTED, NOT_READY) and (result.empty or result.blocking):
        material = reasons
    else:
        material = tuple(r for r in reasons if core.rate_reason(r, result.total) != READY)
    return InputView(
        question_input.key,
        question_input.name,
        question_input.importance,
        state,
        reasons,
        result.total,
        material,
    )


def _next_step(views: tuple[InputView, ...]) -> NextStep | None:
    """The first reason by ADR-0032 section 3: Not ready before Partly ready, then the Question's
    input order, then (inside an input) the reasons' own order."""
    rank = {NOT_READY: 0, NOT_STARTED: 1, PARTLY_READY: 2}
    candidates = [
        (rank[v.state], index, v) for index, v in enumerate(views) if v.state in rank and v.material
    ]
    if not candidates:
        return None
    _, _, view = min(candidates, key=lambda c: (c[0], c[1]))
    return NextStep(view.key, view.name, view.material[0])


def _line(state: Readiness, next_step: NextStep | None, views: tuple[InputView, ...]) -> str:
    if state == COULD_NOT_CHECK:
        names = [v.name for v in views if v.state == COULD_NOT_CHECK]
        return "Could not check " + ", ".join(names) + "."
    if next_step is not None:
        return next_step.reason.title
    if state == READY and any(v.reasons for v in views):
        return SMALL_GAPS_REASON
    return READY_REASON


def _view(question: Question, views: tuple[InputView, ...], level: EffortLevel) -> QuestionView:
    views = tuple(
        replace(v, asked=not (v.importance == OPTIONAL and level == "light")) for v in views
    )
    considered = [v for v in views if v.asked]
    state = core.combine([InputState(v.importance, v.state) for v in considered])
    count = sum(1 for v in considered if core.needs_attention(v.state, v.importance))
    next_step = _next_step(tuple(considered))
    return QuestionView(
        question, state, count, _line(state, next_step, tuple(considered)), next_step, views
    )


def evaluate(
    session: Session, *, today: date | None = None, level: EffortLevel | None = None
) -> Questions:
    """Every registered Question's Readiness. ``today`` and ``level`` default to the Household's;
    tests inject them."""
    today = today if today is not None else due.due_today(session)
    level = level if level is not None else due.current_level(session)
    whats_due = due.whats_due(session, today=today, level=level)
    failed = {f.rule_key for f in whats_due.failed_rules}
    cache: dict[str, InputView] = {}

    def input_view(key: str) -> InputView:
        if key in cache:
            return cache[key]
        question_input = core.get_input(key)
        if question_input is None:
            log.error("Question input %s is not registered", key)
            view = InputView(key, key, IMPORTANT, COULD_NOT_CHECK, (), None, ())
        elif failed.intersection(question_input.due_rules):
            # A Due rule it weighs could not be checked: never assume the input is fine.
            view = _could_not_check(question_input)
        else:
            items = tuple(i for i in whats_due.items if i.rule_key in question_input.due_rules)
            view = _evaluate_input(session, question_input, today, level, items)
        cache[key] = view
        return view

    views = tuple(_view(q, tuple(input_view(k) for k in q.inputs), level) for q in core.questions())
    return Questions(
        today, level, core.headline([(v.question.wording, v.state) for v in views]), views
    )


def evaluate_one(
    session: Session, key: str, *, today: date | None = None, level: EffortLevel | None = None
) -> QuestionView:
    """One Question, from the same evaluation as the Home cards, so its inputs, order and states
    are exactly what the card counts. An unknown key is a ``NotFoundError``."""
    for view in evaluate(session, today=today, level=level).questions:
        if view.question.key == key:
            return view
    raise NotFoundError(f"There is no question '{key}'.")
