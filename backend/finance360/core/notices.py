"""Notices: what "moved the picture" means, and the words (ADR-0033). Pure: no I/O, no clock.

A **Notice** is made when a write moved the net Position by at least ``NOTICE_MIN_NET_CHANGE`` or
changed any Question's Readiness. Exact arithmetic, tolerant judgment (AGENTS.md rule 3): the exact
delta is stored, the tolerance only decides whether to speak. Wording is composed from constants,
the Account's display name and Money, never from a note, a document or an identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from finance360.core.money import Money, format_amount
from finance360.core.questions import COULD_NOT_CHECK, STATE_WORDS, Readiness

#: The smallest net Position change that is worth a line: one whole unit of the base currency, so a
#: re-saved identical figure or rounding noise makes nothing.
NOTICE_MIN_NET_CHANGE = 100
#: A Notice is deleted (lazily, when the next one is written) this long after it was made.
NOTICE_EXPIRY_DAYS = 30
#: How many undismissed Notices Home shows.
HOME_LIMIT = 10

VALUATION_ADDED = "valuation.added"
VALUATION_CORRECTED = "valuation.corrected"

#: The fixed set of routes a Notice may link to (Question screens come from the registry).
POSITION_PATH = "/position"

Direction = Literal["up", "down"]


@dataclass(frozen=True)
class QuestionState:
    key: str
    wording: str
    screen: str
    state: Readiness


@dataclass(frozen=True)
class Picture:
    """The net Position (Base currency) and each Question's Readiness at one moment."""

    net: Money
    questions: tuple[QuestionState, ...]


@dataclass(frozen=True)
class Effect:
    """What a write unlocked: the line in words, the exact net change and where to look."""

    text: str
    delta: Money | None
    link_path: str


def direction(delta: Money | None) -> Direction | None:
    if delta is None or delta.amount_minor == 0:
        return None
    return "up" if delta.amount_minor > 0 else "down"


def _amount_words(money: Money) -> str:
    plain = format_amount(Money(abs(money.amount_minor), money.currency))
    whole, dot, frac = plain.partition(".")
    grouped = f"{int(whole):,}{dot}{frac}"
    return f"${grouped}" if money.currency == "AUD" else f"{grouped} {money.currency}"


def _net_line(delta: Money) -> str:
    return f"Net position moved by {_amount_words(delta)} ({direction(delta)})."


def _question_line(before: QuestionState, after: QuestionState) -> str:
    return f"{after.wording} went from {STATE_WORDS[before.state]} to {STATE_WORDS[after.state]}."


def _changed(before: Picture, after: Picture) -> list[tuple[QuestionState, QuestionState]]:
    """Questions whose Readiness differs. A side that could not be checked is a fault in
    checking, not a change the write made, so it is never announced."""
    earlier = {q.key: q for q in before.questions}
    changed: list[tuple[QuestionState, QuestionState]] = []
    for q in after.questions:
        was = earlier.get(q.key)
        if was is None or was.state == q.state:
            continue
        if COULD_NOT_CHECK in (was.state, q.state):
            continue
        changed.append((was, q))
    return changed


def diff(before: Picture, after: Picture) -> Effect | None:
    """The Effect of a write, or ``None`` when it did not move the picture."""
    delta = after.net - before.net
    moved = abs(delta.amount_minor) >= NOTICE_MIN_NET_CHANGE
    changed = _changed(before, after)
    if not moved and not changed:
        return None
    lines: list[str] = []
    if moved:
        lines.append(_net_line(delta))
    lines.extend(_question_line(was, now) for was, now in changed)
    link = POSITION_PATH if moved else changed[0][1].screen
    return Effect(" ".join(lines), delta if moved else None, link)


def headline(kind: str, account_name: str) -> str:
    """The sentence, from the kind and the Account's display name only."""
    verbs = {VALUATION_ADDED: "added", VALUATION_CORRECTED: "corrected"}
    return f"You {verbs[kind]} a Valuation for {account_name}."
