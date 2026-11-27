"""Questions endpoint (ADR-0032). Thin: the registry lives with its owners, the Readiness logic in
``core.questions`` and the evaluation in ``capabilities.questions``. Read-only and never stored."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, Field

from finance360.api.deps import SessionDep
from finance360.capabilities.questions import service
from finance360.core.questions import STATE_WORDS

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


class QuestionsOut(BaseModel):
    today: date
    level: str
    headline: str
    questions: list[QuestionOut]


@router.get("", response_model=QuestionsOut, operation_id="getQuestions")
def get_questions(session: SessionDep) -> QuestionsOut:
    result = service.evaluate(session)
    return QuestionsOut(
        today=result.today,
        level=result.level,
        headline=result.headline,
        questions=[
            QuestionOut(
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
            for v in result.questions
        ],
    )
