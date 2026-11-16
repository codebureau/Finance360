"""What's due: Due rule and Due item types, the Effort level and the registry (ADR-0020).

Pure types and arithmetic: no database models, no Capability, no clock. A **Due rule** is owned
by the package that knows the data (``capabilities/position/due.py``, ``capabilities/tax/due.py``)
and registered at start-up from ``main.py``, the composition root, like Importers. The ``due``
Capability evaluates the registry; it never imports the owners.

Wording is about accuracy ("your Position uses a balance from April"), never about failure: neither
a ``title`` nor an ``effect`` may say overdue, late or missed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

#: How much the user chooses to be asked (ADR-0020 section 3). Scales what is asked of areas
#: already in use; it is not interest and hides no screen.
EffortLevel = Literal["light", "steady", "thorough"]
LEVELS: tuple[EffortLevel, ...] = ("light", "steady", "thorough")
DEFAULT_LEVEL: EffortLevel = "light"

#: A fixed property of the rule, chosen by its author: what ignoring it costs in accuracy.
Importance = Literal["important", "optional"]
IMPORTANT: Importance = "important"
OPTIONAL: Importance = "optional"

#: The Guide starting points a rule matters to.
SUITS = ("tax", "investments", "whole")

#: The Guide marker every rule's line carries (ADR-0020 section 5).
GUIDE_MARKER = "Shown in [What's due](/)."

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True, slots=True)
class Every:
    """Refresh at least this often: due once ``days`` have passed since the last figure."""

    days: int

    def __post_init__(self) -> None:
        if self.days < 1:
            raise ValueError("A cadence is at least one day.")


@dataclass(frozen=True, slots=True)
class FromDate:
    """A yearly event that starts on a fixed day (not a rhythm to scale)."""

    month: int
    day: int

    def __post_init__(self) -> None:
        date(2001, self.month, self.day)  # raises on an impossible day

    def on(self, year: int) -> date:
        return date(year, self.month, self.day)


Cadence = Every | FromDate

#: Spoken forms of the common day counts; anything else reads "every N days".
_EVERY_WORDS = {
    1: "straight away",
    31: "monthly",
    92: "every 3 months",
    183: "every 6 months",
    366: "yearly",
}


def describe(cadence: Cadence | None) -> str:
    """The words the Guide uses for one cadence; ``None`` is "not asked"."""
    if cadence is None:
        return "not asked"
    if isinstance(cadence, FromDate):
        return f"from {cadence.day} {_MONTHS[cadence.month - 1]}"
    return _EVERY_WORDS.get(cadence.days, f"every {cadence.days} days")


@dataclass(frozen=True, slots=True)
class DueAction:
    """A button on an item that resolves it in place (ADR-0022 section 2). ``kind`` is a fixed
    word the screen maps to a call; ``capture_id`` is the captured Rule figure it acts on."""

    kind: str
    label: str
    capture_id: int


@dataclass(frozen=True, slots=True)
class DueItem:
    """One thing to refresh. ``subject_key`` names the thing **and the period**, so snoozing one
    occurrence does not silence the next (``account:12@2026-09-30``, ``member:2@fy2026``)."""

    rule_key: str
    subject_key: str
    importance: Importance
    #: What Finance360 is using now ("Your Position uses a super balance from April").
    title: str
    #: What refreshing improves.
    effect: str
    due_on: date
    #: An app path where it is fixed.
    link: str
    suits: tuple[str, ...]
    #: Buttons that resolve the item without leaving What's due (none for most rules).
    actions: tuple[DueAction, ...] = ()


Interested = Callable[[Session], bool]
Check = Callable[[Session, date, EffortLevel], tuple[DueItem, ...]]


@dataclass(frozen=True, slots=True)
class DueRule:
    key: str
    importance: Importance
    #: Each Effort level to its cadence, or ``None`` ("not asked at this level").
    cadence: Mapping[EffortLevel, Cadence | None]
    suits: tuple[str, ...]
    #: The bold lead of this rule's line in the Guide.
    guide_item: str
    #: Implicit interest: data of this rule's kind exists.
    interested: Interested
    #: Deterministic and read-only. ``today`` (Household time zone) and ``level`` are passed in.
    #: Returns nothing when the cadence for ``level`` is ``None``.
    check: Check

    def __post_init__(self) -> None:
        if set(self.cadence) != set(LEVELS):
            raise ValueError(f"{self.key}: give a cadence (or None) for every Effort level.")
        if not self.suits or not set(self.suits) <= set(SUITS):
            raise ValueError(f"{self.key}: suits must be some of {SUITS}.")


def cadence_string(rule: DueRule) -> str:
    """``Light: every 3 months · Steady: monthly · Thorough: monthly``, generated from the table so
    the Guide cannot drift from it."""
    return " · ".join(f"{level.capitalize()}: {describe(rule.cadence[level])}" for level in LEVELS)


# --- the registry --------------------------------------------------------------------------

_RULES: dict[str, DueRule] = {}


def register(rule: DueRule) -> None:
    """Register a rule. Registering the same key again replaces it (every app start registers)."""
    _RULES[rule.key] = rule


def unregister(key: str) -> None:
    _RULES.pop(key, None)


def rules() -> list[DueRule]:
    """Every registered rule, by key."""
    return [_RULES[key] for key in sorted(_RULES)]


def get(key: str) -> DueRule | None:
    return _RULES.get(key)
