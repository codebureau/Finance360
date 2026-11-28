"""Questions endpoint (ADR-0032). Thin: the registry lives with its owners, the Readiness logic in
``core.questions`` and the evaluation in ``capabilities.questions``. Read-only and never stored."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, Field

from finance360.api.deps import SessionDep
from finance360.capabilities.questions import service
from finance360.core.questions import STATE_WORDS, needs_attention

router = APIRouter(prefix="/api/questions", tags=["questions"])


class NextStepOut(BaseModel):
    input_key: str
    input_name: str
    kind: str = Field(description="missing, stale or incomplete.")
    title: str = Field(description="What Finance360 is using now.")
    effect: str = Field(description="What fixing it improves.")
    link: str = Field(description="The app path where it is fixed.")


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


class ReasonOut(BaseModel):
    kind: str = Field(description="missing, stale or incomplete.")
    title: str = Field(description="What Finance360 is using now.")
    effect: str = Field(description="What fixing it improves.")
    link: str = Field(description="The app path where it is fixed.")
    material: bool = Field(
        description="False for a small gap: listed, but it does not change the state."
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


class QuestionDetailOut(QuestionOut):
    inputs: list[InputOut] = Field(description="In the order they are best provided.")


class QuestionsOut(BaseModel):
    today: date
    level: str
    headline: str
    questions: list[QuestionOut]


def _question_out(v: service.QuestionView) -> QuestionOut:
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
    )


@router.get("", response_model=QuestionsOut, operation_id="getQuestions")
def get_questions(session: SessionDep) -> QuestionsOut:
    result = service.evaluate(session)
    return QuestionsOut(
        today=result.today,
        level=result.level,
        headline=result.headline,
        questions=[_question_out(v) for v in result.questions],
    )


@router.get("/{key}", response_model=QuestionDetailOut, operation_id="getQuestion")
def get_question(key: str, session: SessionDep) -> QuestionDetailOut:
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
        )
        for i in view.inputs
    ]
    return QuestionDetailOut(**_question_out(view).model_dump(), inputs=inputs)
