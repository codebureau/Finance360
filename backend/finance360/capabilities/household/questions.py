"""The Household's Question input (ADR-0032 section 8): ``household.birth_dates``.

The projection runs on each Member's age, so without a Member and their date of birth it cannot be
computed: the input is ``blocking``. (A Member's date of birth is required when they are added, so
in practice this is the Household with no Members yet.)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from finance360.capabilities.household import service
from finance360.core.due import IMPORTANT, DueItem, EffortLevel
from finance360.core.questions import MISSING, InputResult, QuestionInput, Reason

BIRTH_DATES = "household.birth_dates"


def _check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    if service.list_members(session):
        return InputResult()
    return InputResult(
        blocking=True,
        reasons=(
            Reason(
                kind=MISSING,
                subject_key="members",
                title="Your Household has no Members yet",
                effect="Each Member's date of birth sets the ages the projection runs on.",
                affected=None,
                link="/household",
            ),
        ),
    )


INPUTS: tuple[QuestionInput, ...] = (
    QuestionInput(BIRTH_DATES, "Dates of birth", IMPORTANT, (), _check),
)
