"""Answer ratings (ADR-0032 section 6): the owner's say on whether a Question's answer helped.

Append-only: ``record_rating`` adds a row and never edits or deletes one; the newest row per
Question is current and the earlier ones are the history. A rating never feeds back into Readiness,
which is computed from the data alone: nothing here is read by ``service.evaluate``. Not audited (it
changes no household data), and nothing leaves the instance: not Ask, not MCP, not telemetry.

The note is the owner's words, so it is checked, never edited: one that carries a currency amount or
a run of six or more digits is refused with a calm message and nothing is stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.questions import service
from finance360.core.due import EffortLevel
from finance360.core.errors import FieldError, ValidationError
from finance360.core.figures import has_figure
from finance360.core.models.answer_rating import ANSWERS, NOTE_MAX, AnswerRating

NOTE_HAS_FIGURE = (
    "Please leave amounts and account numbers out of the note: a few words about what you were "
    "hoping to see is plenty."
)
NOTE_TOO_LONG = f"Please keep the note to {NOTE_MAX} characters or fewer."
ANSWER_UNKNOWN = "Choose Yes, Partly or No."


@dataclass(frozen=True)
class Rating:
    id: int
    question_key: str
    answer: str
    note: str | None
    #: The Question's Readiness state when it was rated (a state word, never a score).
    readiness_at_rating: str
    #: UTC.
    created_at: datetime


def _rating(row: AnswerRating) -> Rating:
    return Rating(
        row.id, row.question_key, row.answer, row.note, row.readiness_at_rating, row.created_at
    )


def _checked_note(note: str | None) -> str | None:
    """The note as given, trimmed, or ``None`` when blank. A refusal is never a silent edit."""
    text = (note or "").strip()
    if not text:
        return None
    errors: list[FieldError] = []
    if len(text) > NOTE_MAX:
        errors.append(FieldError("note", NOTE_TOO_LONG))
    elif has_figure(text):
        errors.append(FieldError("note", NOTE_HAS_FIGURE))
    if errors:
        raise ValidationError(errors)
    return text


def record_rating(
    session: Session,
    login_id: int,
    key: str,
    answer: str,
    note: str | None = None,
    *,
    now: datetime | None = None,
    today: date | None = None,
    level: EffortLevel | None = None,
) -> Rating:
    """Add a rating, stamped with the Question's Readiness now. An unregistered key (``ratings``
    included) is a ``NotFoundError``; an unknown answer or an unfit note is a
    ``ValidationError``."""
    view = service.evaluate_one(session, key, today=today, level=level)
    if answer not in ANSWERS:
        raise ValidationError([FieldError("answer", ANSWER_UNKNOWN)])
    row = AnswerRating(
        login_id=login_id,
        question_key=view.question.key,
        answer=answer,
        note=_checked_note(note),
        readiness_at_rating=view.state,
        created_at=now if now is not None else datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(row)
    session.flush()
    return _rating(row)


def rating_history(session: Session, login_id: int) -> dict[str, tuple[Rating, ...]]:
    """Every Question's ratings, newest first (the first is the current one)."""
    rows = session.scalars(
        select(AnswerRating)
        .where(AnswerRating.login_id == login_id)
        .order_by(AnswerRating.id.desc())
    )
    grouped: dict[str, list[Rating]] = {}
    for row in rows:
        grouped.setdefault(row.question_key, []).append(_rating(row))
    return {key: tuple(items) for key, items in grouped.items()}


def latest_ratings(session: Session, login_id: int) -> dict[str, Rating]:
    """The current rating of each Question that has been rated."""
    return {key: items[0] for key, items in rating_history(session, login_id).items()}
