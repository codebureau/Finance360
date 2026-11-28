"""Questions endpoints (ADR-0032). Thin: the registry lives with its owners, the Readiness logic in
``core.questions`` and the evaluation and Answer ratings in ``capabilities.questions``. Readiness is
never stored; an Answer rating is, apart from it, and never changes it."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from finance360.api.deps import PrincipalDep, SessionDep
from finance360.capabilities.questions import ratings, service
from finance360.core.models.answer_rating import NOTE_MAX
from finance360.core.questions import STATE_WORDS, needs_attention

router = APIRouter(prefix="/api/questions", tags=["questions"])


class NextStepOut(BaseModel):
    input_key: str
    input_name: str
    kind: str = Field(description="missing, stale or incomplete.")
    title: str = Field(description="What Finance360 is using now.")
    effect: str = Field(description="What fixing it improves.")
    link: str = Field(description="The app path where it is fixed.")


class RatingOut(BaseModel):
    answer: Literal["yes", "partly", "no"]
    answer_word: str = Field(description="Yes, Partly or No.")
    note: str | None = Field(description="What the owner was hoping to see; never a figure.")
    readiness_at_rating: str = Field(
        description="The Question's Readiness state when it was rated."
    )
    readiness_word: str
    created_at: datetime = Field(description="UTC.")


class RatingIn(BaseModel):
    answer: Literal["yes", "partly", "no"]
    note: str | None = Field(
        default=None,
        description=f"Optional, at most {NOTE_MAX} characters. Refused if it carries a currency "
        "amount or a run of six or more digits.",
    )


def _rating_out(r: ratings.Rating) -> RatingOut:
    return RatingOut(
        answer=r.answer,  # type: ignore[arg-type]
        answer_word=r.answer.capitalize(),
        note=r.note,
        readiness_at_rating=r.readiness_at_rating,
        readiness_word=STATE_WORDS.get(r.readiness_at_rating, r.readiness_at_rating),  # type: ignore[call-overload]
        created_at=r.created_at,
    )


class QuestionOut(BaseModel):
    key: str
    wording: str
    screen: str
    area: str
    ask_prompt: str
    state: str = Field(
        description="ready, partly_ready, not_ready, not_started or could_not_check. "
        "Never a number: there is no score."
    )
    state_word: str
    count: int = Field(description="How many of the Question's inputs are not Ready.")
    reason: str = Field(description="One line: why the state is what it is.")
    next_step: NextStepOut | None
    rating: RatingOut | None = Field(
        description="The current Answer rating, or null while the Question is unrated. "
        "Separate from the state: the two are never combined."
    )


class ReasonOut(BaseModel):
    kind: str = Field(description="missing, stale or incomplete.")
    title: str = Field(description="What Finance360 is using now.")
    effect: str = Field(description="What fixing it improves.")
    link: str = Field(description="The app path where it is fixed.")
    material: bool = Field(
        description="False for a small gap: listed, but it does not change the state."
    )


class SnoozedOut(BaseModel):
    title: str
    link: str
    until_on: date | None = Field(
        description="The day the Snooze ends; null when skipped this time."
    )


class InputOut(BaseModel):
    key: str
    name: str
    importance: str = Field(description="important or optional.")
    state: str = Field(description="The input's Readiness, from the same check as the card.")
    state_word: str
    asked: bool = Field(
        description="False when the Effort level does not ask for it (an optional input at Light): "
        "it is shown but weighs on nothing."
    )
    counted: bool = Field(description="Whether it is one of the inputs the card's count includes.")
    reasons: list[ReasonOut] = Field(description="Every reason, in next-step order.")
    snoozed: list[SnoozedOut] = Field(
        description="Gaps the owner has Snoozed: listed, and never counted towards the state."
    )


class QuestionDetailOut(QuestionOut):
    inputs: list[InputOut] = Field(description="In the order they are best provided.")
    earlier_ratings: list[RatingOut] = Field(description="Earlier Answer ratings, newest first.")


class QuestionsOut(BaseModel):
    today: date
    level: str
    headline: str
    questions: list[QuestionOut]


def _question_out(v: service.QuestionView, rating: ratings.Rating | None) -> QuestionOut:
    return QuestionOut(
        key=v.question.key,
        wording=v.question.wording,
        screen=v.question.screen,
        area=v.question.area,
        ask_prompt=v.question.ask_prompt,
        state=v.state,
        state_word=STATE_WORDS[v.state],
        count=v.count,
        reason=v.reason,
        next_step=(
            NextStepOut(
                input_key=v.next_step.input_key,
                input_name=v.next_step.input_name,
                kind=v.next_step.reason.kind,
                title=v.next_step.reason.title,
                effect=v.next_step.reason.effect,
                link=v.next_step.reason.link,
            )
            if v.next_step is not None
            else None
        ),
        rating=_rating_out(rating) if rating is not None else None,
    )


@router.get("", response_model=QuestionsOut, operation_id="getQuestions")
def get_questions(session: SessionDep, principal: PrincipalDep) -> QuestionsOut:
    result = service.evaluate(session)
    latest = ratings.latest_ratings(session, principal.login_id)
    return QuestionsOut(
        today=result.today,
        level=result.level,
        headline=result.headline,
        questions=[_question_out(v, latest.get(v.question.key)) for v in result.questions],
    )


class RatingRowOut(BaseModel):
    key: str
    wording: str
    rating: RatingOut | None = Field(description="Null while the Question is unrated.")


class RatingsOut(BaseModel):
    items: list[RatingRowOut] = Field(description="Every Question, in card order.")


# Declared before ``/{key}``: ``ratings`` is a reserved word, never a Question key (ADR-0032 s6).
@router.get("/ratings", response_model=RatingsOut, operation_id="getRatings")
def get_ratings(session: SessionDep, principal: PrincipalDep) -> RatingsOut:
    """Every Question with its current Answer rating (null if unrated), for the ratings screen."""
    latest = ratings.latest_ratings(session, principal.login_id)
    return RatingsOut(
        items=[
            RatingRowOut(
                key=v.question.key,
                wording=v.question.wording,
                rating=_rating_out(latest[v.question.key]) if v.question.key in latest else None,
            )
            for v in service.evaluate(session).questions
        ]
    )


@router.post(
    "/{key}/ratings",
    response_model=RatingOut,
    status_code=201,
    operation_id="rateQuestion",
    responses={404: {"description": "No such Question."}},
)
def rate_question(
    key: str, body: RatingIn, session: SessionDep, principal: PrincipalDep
) -> RatingOut:
    """Add an Answer rating (Yes, Partly or No, and an optional note). Append-only: the newest is
    current. 404 for a key that is not registered; 422 for a note with an amount or six digits."""
    return _rating_out(
        ratings.record_rating(session, principal.login_id, key, body.answer, body.note)
    )


@router.get("/{key}", response_model=QuestionDetailOut, operation_id="getQuestion")
def get_question(key: str, session: SessionDep, principal: PrincipalDep) -> QuestionDetailOut:
    """One Question with its inputs in the order to provide them: the same evaluation, so the same
    statuses and count as its Home card. 404 for a key that is not registered."""
    view = service.evaluate_one(session, key)
    inputs = [
        InputOut(
            key=i.key,
            name=i.name,
            importance=i.importance,
            state=i.state,
            state_word=STATE_WORDS[i.state],
            asked=i.asked,
            counted=i.asked and needs_attention(i.state, i.importance),
            reasons=[
                ReasonOut(
                    kind=r.kind,
                    title=r.title,
                    effect=r.effect,
                    link=r.link,
                    material=r in i.material,
                )
                for r in i.reasons
            ],
            snoozed=[
                SnoozedOut(title=g.title, link=g.link, until_on=g.until_on) for g in i.snoozed
            ],
        )
        for i in view.inputs
    ]
    history = ratings.rating_history(session, principal.login_id).get(key, ())
    return QuestionDetailOut(
        **_question_out(view, history[0] if history else None).model_dump(),
        inputs=inputs,
        earlier_ratings=[_rating_out(r) for r in history[1:]],
    )
