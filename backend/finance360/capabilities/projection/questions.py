"""The projection's Question and its own input (ADR-0032 section 8): ``on-track-for-retirement``.

It names the Household's and Position's inputs by key (they own the data, so two Questions can
share one). ``retirement.freshness`` is owned here, because only Projection knows which Accounts
it uses: it weighs stale Valuations only for the Accounts in ``PROJECTED_CATEGORIES``, and its
total is the value of those Accounts, so a share is "of what the projection uses". A stale loan
or property lowers ``own-and-owe``, not this. Property, vehicles and loans are not in
``due_rules``; investments and overseas pensions have no Valuation Due rule today (they sit in the
denominator only): add its key to ``due_rules`` when one exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from finance360.capabilities.position import due as position_due
from finance360.capabilities.position import questions as position_questions
from finance360.capabilities.projection.assumptions import PROJECTED_CATEGORIES
from finance360.core.due import IMPORTANT, DueItem, EffortLevel
from finance360.core.questions import InputResult, Question, QuestionInput

ON_TRACK_FOR_RETIREMENT = "on-track-for-retirement"
FRESHNESS = "retirement.freshness"


def _freshness_check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    return position_questions.freshness_result(session, today, items, PROJECTED_CATEGORIES)


INPUTS: tuple[QuestionInput, ...] = (
    QuestionInput(
        FRESHNESS,
        "Fresh balances",
        IMPORTANT,
        (position_due.MANUAL_VALUATION, position_due.SUPER_VALUATION),
        _freshness_check,
    ),
)

QUESTIONS: tuple[Question, ...] = (
    Question(
        key=ON_TRACK_FOR_RETIREMENT,
        wording="Are we on track for retirement?",
        screen="/projection",
        area="Where we're heading",
        inputs=("household.birth_dates", "position.figures", FRESHNESS),
        ask_prompt="Are we on track for retirement, and how does the Projection work it out?",
    ),
)
