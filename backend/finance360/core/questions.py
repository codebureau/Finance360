"""Questions: types, the two registries and the readiness arithmetic (ADR-0032 sections 1 to 3).

Pure types and arithmetic: no database models, no Capability, no clock. A **Question** and a
**Question input** are registered at start-up from ``main.py`` (the composition root, like Due
rules): the Capability that answers a Question contributes it from
``capabilities/<owner>/questions.py``, and the data's owner contributes the input. The ``questions``
Capability evaluates the registries and never imports an owner.

**Readiness** is how far a Question can be answered from what the household has given. It is
computed on every request and never stored, and it is never a number: no score, no percentage. The
shares below are thresholds inside a judgment, held as parts per million and compared in integer
arithmetic on exact Money, so a boundary is exact (``sum * 1_000_000 > share * total``), where the
sum is a group's summed ``affected`` (ADR-0032 section 2).

Wording is about accuracy, never about the household: neither a reason's ``title`` nor its
``effect`` may say overdue, late or failed (ADR-0020).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from finance360.core.due import IMPORTANT, OPTIONAL, DueItem, EffortLevel, Importance
from finance360.core.money import Money

# --- the five states -------------------------------------------------------------------------

Readiness = Literal["ready", "partly_ready", "not_ready", "not_started", "could_not_check"]
READY: Readiness = "ready"
PARTLY_READY: Readiness = "partly_ready"
NOT_READY: Readiness = "not_ready"
NOT_STARTED: Readiness = "not_started"
COULD_NOT_CHECK: Readiness = "could_not_check"

#: The words the owner sees for each state.
STATE_WORDS: dict[Readiness, str] = {
    READY: "Ready",
    PARTLY_READY: "Partly ready",
    NOT_READY: "Not ready",
    NOT_STARTED: "Not started",
    COULD_NOT_CHECK: "Could not check",
}

# --- thresholds (named, tolerant, ADR-0032 section 2) ------------------------------------------

#: Parts per million: a share is a judgment, so it is never shown as a figure.
PPM = 1_000_000
#: A gap above 5% of an input's value makes the answer rest materially on it.
MATERIAL_SHARE_PPM = 50_000
#: Value the answer leaves out, above 25% of the total, would mislead.
MISLEADING_SHARE_PPM = 250_000

# --- reasons and inputs -------------------------------------------------------------------------

ReasonKind = Literal["missing", "stale", "incomplete"]
MISSING: ReasonKind = "missing"
STALE: ReasonKind = "stale"
INCOMPLETE: ReasonKind = "incomplete"
_KIND_ORDER: dict[ReasonKind, int] = {MISSING: 0, STALE: 1, INCOMPLETE: 2}


@dataclass(frozen=True, slots=True)
class Reason:
    """One thing that stops an answer being fully ready. ``affected`` is the exact value (Base
    currency) the answer uses wrongly or leaves out, or ``None`` when that value is unknown."""

    kind: ReasonKind
    subject_key: str
    #: What Finance360 is using now.
    title: str
    #: What fixing it improves (accuracy wording).
    effect: str
    affected: Money | None
    #: The app path where it is fixed. Required.
    link: str

    def __post_init__(self) -> None:
        if not self.link.startswith("/"):
            raise ValueError(f"{self.subject_key}: a reason needs an app path as its link.")


@dataclass(frozen=True, slots=True)
class InputResult:
    """What one input found. ``empty``: no data of this kind at all. ``blocking``: the answer
    cannot be computed. ``total``: the input's whole value in the Base currency, or ``None``."""

    empty: bool = False
    blocking: bool = False
    total: Money | None = None
    reasons: tuple[Reason, ...] = ()


InputCheck = Callable[[Session, date, EffortLevel, Sequence[DueItem]], InputResult]


@dataclass(frozen=True, slots=True)
class QuestionInput:
    """A thing a Question needs, owned by the Capability that owns the data. ``due_rules`` are the
    Due rule keys whose items it weighs; the service passes in those items already filtered by
    Effort level and Snoozes, so staleness is judged once, by the Due rule."""

    key: str
    name: str
    importance: Importance
    due_rules: tuple[str, ...]
    #: Deterministic and read-only.
    check: InputCheck


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    #: The Question in the owner's words: the bold lead of its line on the Guide page "What
    #: Finance360 answers" (a test keeps them equal).
    wording: str
    #: The app path that answers it.
    screen: str
    #: The sidebar group its screen sits in.
    area: str
    #: Question input keys, in the order they are best provided.
    inputs: tuple[str, ...]
    #: The canned Ask question: words only, no figure, no name (a test refuses any digit).
    ask_prompt: str

    def __post_init__(self) -> None:
        if self.key == "ratings":
            raise ValueError("'ratings' is reserved (ADR-0032 section 6).")
        if not self.inputs:
            raise ValueError(f"{self.key}: a Question needs at least one input.")


# --- rating (pure) ----------------------------------------------------------------------------


def exceeds(affected: Money, total: Money, share_ppm: int) -> bool:
    """Whether ``affected / total`` is strictly above ``share_ppm`` parts per million: an exact
    integer comparison, so 5% exactly is not above 5%. ``total`` must be positive."""
    if affected.currency != total.currency:
        raise ValueError("A share compares amounts in one currency.")
    return abs(affected.amount_minor) * PPM > share_ppm * total.amount_minor


def _weighable(total: Money | None) -> bool:
    return total is not None and total.amount_minor > 0


def _group(kind: ReasonKind) -> ReasonKind:
    """The group a reason belongs to: ``missing`` alone, and ``stale`` with ``incomplete`` (the
    answer uses an old or partial figure). The two groups make different claims, so they never
    blend (ADR-0032 section 2)."""
    return MISSING if kind == MISSING else STALE


def group_sum(reasons: Sequence[Reason], group: ReasonKind, currency: str) -> Money:
    """The exact sum of the known ``affected`` values of one group's reasons. A reason with an
    unknown value adds nothing."""
    total = Money.zero(currency)
    for reason in reasons:
        if _group(reason.kind) == group and reason.affected is not None:
            total += Money(abs(reason.affected.amount_minor), currency)
    return total


def _group_state(result: InputResult, group: ReasonKind) -> Readiness:
    """One group's state from its summed share of ``result.total`` (strict boundaries). Only the
    ``missing`` group can be Not ready: an old figure makes an answer approximate, not wrong."""
    total = result.total
    if total is None:
        return READY
    summed = group_sum(result.reasons, group, total.currency)
    if group == MISSING and exceeds(summed, total, MISLEADING_SHARE_PPM):
        return NOT_READY
    if exceeds(summed, total, MATERIAL_SHARE_PPM):
        return PARTLY_READY
    return READY


_SEVERITY: dict[Readiness, int] = {
    READY: 0,
    NOT_STARTED: 0,
    PARTLY_READY: 1,
    COULD_NOT_CHECK: 2,
    NOT_READY: 3,
}


def rate_input(result: InputResult) -> Readiness:
    """An input's state: empty is Not started, blocking is Not ready, else the worse of its two
    groups. A reason with unknown value, or a total that cannot weigh the reasons, gives at most
    Partly ready."""
    if result.empty:
        return NOT_STARTED
    if result.blocking:
        return NOT_READY
    if not result.reasons:
        return READY
    if not _weighable(result.total):
        return PARTLY_READY
    worst: Readiness = PARTLY_READY if any(r.affected is None for r in result.reasons) else READY
    for group in (MISSING, STALE):
        state = _group_state(result, group)
        if _SEVERITY[state] > _SEVERITY[worst]:
            worst = state
    return worst


def material_reasons(result: InputResult) -> tuple[Reason, ...]:
    """The reasons that count, in next-step order. A Not started or blocking input shows its
    reasons whole (its next step is the first input). Otherwise a group above the material share
    makes every reason in it material (together they are why), a group at or below makes none
    material (they are "Small gaps"), and a reason with unknown value is always material (it is
    why the input is Partly ready)."""
    reasons = ordered(result.reasons)
    if result.empty or result.blocking or not _weighable(result.total):
        return reasons
    counted = {group for group in (MISSING, STALE) if _group_state(result, group) != READY}
    return tuple(r for r in reasons if r.affected is None or _group(r.kind) in counted)


@dataclass(frozen=True, slots=True)
class InputState:
    """An input's rated outcome, as the Question combines it."""

    importance: Importance
    state: Readiness


def combine(inputs: Sequence[InputState]) -> Readiness:
    """A Question's state from its inputs (ADR-0032 section 2).

    Any Not ready gives Not ready; else any Could not check gives Could not check (an unchecked
    input is never assumed fine); else any Partly ready gives Partly ready; else, if every
    important input is Not started, Not started; once started, an important input still Not
    started counts as Partly ready; else Ready. An ``optional`` input that is Not started asks for
    nothing, so it changes nothing.
    """
    states = [i.state for i in inputs]
    if NOT_READY in states:
        return NOT_READY
    if COULD_NOT_CHECK in states:
        return COULD_NOT_CHECK
    if PARTLY_READY in states:
        return PARTLY_READY
    important = [i.state for i in inputs if i.importance == IMPORTANT]
    if important and all(s == NOT_STARTED for s in important):
        return NOT_STARTED
    if NOT_STARTED in important:
        return PARTLY_READY
    return READY


def needs_attention(state: Readiness, importance: Importance) -> bool:
    """Whether an input counts toward the Question's count (the inputs that are not Ready). An
    optional input that is Not started is not asking for anything."""
    if state == READY:
        return False
    return not (state == NOT_STARTED and importance == OPTIONAL)


def reason_order(reason: Reason) -> tuple[int, int, int]:
    """Within one input: ``missing`` before ``stale`` before ``incomplete``; then the larger
    ``affected`` first, an unknown value last."""
    if reason.affected is None:
        return (_KIND_ORDER[reason.kind], 1, 0)
    return (_KIND_ORDER[reason.kind], 0, -abs(reason.affected.amount_minor))


def ordered(reasons: Sequence[Reason]) -> tuple[Reason, ...]:
    return tuple(sorted(reasons, key=reason_order))


def headline(rows: Sequence[tuple[str, Readiness]]) -> str:
    """One sentence over the Question cards, computed from the states: no gauge, no score
    (ADR-0032 section 5). ``rows`` are (wording, state) in registry order."""
    total = len(rows)
    if total == 0:
        return "No questions are set up yet."
    ready = sum(1 for _, state in rows if state == READY)
    if ready == total:
        return f"All {total} questions are ready." if total > 1 else "The question is ready."
    verb = "is" if ready == 1 else "are"
    phrases: dict[Readiness, str] = {
        PARTLY_READY: "is partly ready",
        NOT_READY: "is not ready",
        NOT_STARTED: "has not been started",
        COULD_NOT_CHECK: "could not be checked",
        READY: "is ready",
    }
    rest = [f"{wording} {phrases[state]}" for wording, state in rows if state != READY]
    return f"{ready} of {total} questions {verb} ready; " + ", ".join(rest)


# --- the registries -----------------------------------------------------------------------------

_QUESTIONS: dict[str, Question] = {}
_INPUTS: dict[str, QuestionInput] = {}


def register_question(question: Question) -> None:
    """Register a Question. Registering the same key again replaces it (every app start
    registers); the order of first registration is the registry order."""
    _QUESTIONS[question.key] = question


def register_input(question_input: QuestionInput) -> None:
    _INPUTS[question_input.key] = question_input


def unregister_question(key: str) -> None:
    _QUESTIONS.pop(key, None)


def unregister_input(key: str) -> None:
    _INPUTS.pop(key, None)


def questions() -> list[Question]:
    """Every registered Question, in registry order."""
    return list(_QUESTIONS.values())


def get_question(key: str) -> Question | None:
    return _QUESTIONS.get(key)


def get_input(key: str) -> QuestionInput | None:
    return _INPUTS.get(key)


def inputs() -> list[QuestionInput]:
    return [_INPUTS[key] for key in sorted(_INPUTS)]
