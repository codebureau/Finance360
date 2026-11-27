"""The projection's Question (ADR-0032 section 8): ``on-track-for-retirement``.

It names its inputs by key. The Household and Position own the data and register the inputs, so
two Questions can share one (both start from Position's figures); this module imports neither.
"""

from __future__ import annotations

from finance360.core.questions import Question

ON_TRACK_FOR_RETIREMENT = "on-track-for-retirement"

QUESTIONS: tuple[Question, ...] = (
    Question(
        key=ON_TRACK_FOR_RETIREMENT,
        wording="Are we on track for retirement?",
        screen="/projection",
        area="Where we're heading",
        inputs=("household.birth_dates", "position.figures", "position.freshness"),
        ask_prompt="Are we on track for retirement, and how does the Projection work it out?",
    ),
)
