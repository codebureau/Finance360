"""The Ask panel and AI status/log endpoints (ADR-0021, ADR-0023). Thin: the logic is in
``capabilities.ask`` and ``capabilities.ai``. Nothing here changes household data.

Ask is two calls. ``POST /api/ask`` is keyword search over the Guide: no model, no AI log
row, always available. ``POST /api/ask/answer`` is the optional written answer; a refused or
failed one is answered with a status and a calm message, and its AI log row is committed by the
gateway on its own, so it is kept either way.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from finance360.api.deps import SessionDep
from finance360.api.household import MessageBody
from finance360.capabilities.ai import errors as ai_errors
from finance360.capabilities.ai import service as ai_service
from finance360.capabilities.ask import service
from finance360.capabilities.ask.types import Turn
from finance360.config import Settings

router = APIRouter(prefix="/api", tags=["ask"])


class AiStatusOut(BaseModel):
    enabled: bool = Field(description="Whether a model may be called now for this task.")
    reason: str | None = Field(
        description="Why not: off (no model set up on the host), monthly_cap or daily_limit."
    )
    resets_on: date | None = Field(description="The date a cap lifts, when capped.")
    retrieval: Literal["keyword"] = Field(
        description="How Ask finds Passages. Keyword search is always on."
    )
    answering: bool = Field(
        description="Whether a model is set up to write answers. False is Search only."
    )


class AiLogOut(BaseModel):
    id: int
    occurred_at: datetime
    task: str
    data_class: str
    provider: str
    model: str
    outcome: str
    sent: str = Field(description="The redacted text exactly as it left this instance.")
    public_parts: str = Field(description="JSON: name, sha256 and note of each public part.")
    received: str | None = Field(description="The reply as received, still tokenised.")
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd_micros: int = Field(description="Millionths of a US dollar.")
    cost_estimated: bool
    why: str


class TurnIn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class AskIn(BaseModel):
    question: str
    history: list[TurnIn] = Field(
        default_factory=list[TurnIn], description="Earlier messages of this session, oldest first."
    )
    screen: str | None = Field(default=None, description="The route pattern, never an id.")
    guide_slug: str | None = Field(default=None, description="Set only on a Guide page.")


class SourceOut(BaseModel):
    kind: str = Field(description="guide or screen.")
    ref: str
    label: str
    link: str


class PassageOut(BaseModel):
    page: str = Field(description="The Guide page's slug.")
    label: str = Field(description="The Guide page's title.")
    heading: str = Field(description="The section's heading.")
    excerpt: str
    link: str


class PassagesOut(BaseModel):
    found: bool = Field(description="False when nothing in the Guide matched.")
    passages: list[PassageOut]
    message: str = Field(description="The fixed 'couldn't find that' wording when nothing matched.")
    start: SourceOut | None = Field(description="Start here, offered when nothing matched.")


class AskOut(BaseModel):
    answer: str
    sources: list[SourceOut]
    answered: bool = Field(description="False when the Guide does not cover the question.")
    model: str = Field(description="The model that wrote the answer; empty when none did.")


class AskRefusedBody(BaseModel):
    message: str
    reason: str = Field(description="off, monthly_cap or daily_limit.")
    resets_on: date | None


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("/ai/status", response_model=AiStatusOut, operation_id="getAiStatus")
def get_ai_status(request: Request, session: SessionDep, task: str = "ask") -> AiStatusOut:
    """Whether a task may call the model. ``task`` is ``ask`` or ``rule_capture``: the daily
    question limit stops only Ask."""
    status = ai_service.status(session, _settings(request).ai, task)
    return AiStatusOut(
        enabled=status.enabled,
        reason=status.reason,
        resets_on=status.resets_on,
        retrieval="keyword",
        answering=status.answering,
    )


@router.get("/ai/log", response_model=list[AiLogOut], operation_id="listAiLog")
def list_ai_log(session: SessionDep, limit: int = ai_service.DEFAULT_LOG_LIMIT) -> list[AiLogOut]:
    return [
        AiLogOut.model_validate(row, from_attributes=True)
        for row in ai_service.log_entries(session, limit)
    ]


@router.post("/ask", response_model=PassagesOut, operation_id="askQuestion")
def ask_question(body: AskIn, request: Request) -> PassagesOut:
    """Search the Guide for the best Passages. Not an AI call: no model, nothing logged."""
    settings = _settings(request)
    found = service.find(
        settings.guide_dir,
        settings.context_file,
        body.question,
        history=[Turn(t.role, t.text) for t in body.history],
        screen=body.screen,
        guide_slug=body.guide_slug,
    )
    return PassagesOut(
        found=bool(found.passages),
        passages=[
            PassageOut(
                page=p.page, label=p.label, heading=p.heading, excerpt=p.excerpt, link=p.link
            )
            for p in found.passages
        ],
        message=found.message,
        start=_source(found.start) if found.start else None,
    )


def _source(s: service.Source) -> SourceOut:
    return SourceOut(kind=s.kind, ref=s.ref, label=s.label, link=s.link)


@router.post(
    "/ask/answer",
    response_model=AskOut,
    operation_id="answerQuestion",
    responses={
        409: {"model": AskRefusedBody},
        429: {"model": MessageBody},
        502: {"model": MessageBody},
    },
)
def answer_question(body: AskIn, request: Request, session: SessionDep) -> AskOut | JSONResponse:
    """A written answer over the best Passages, which the server retrieves itself. Needs a model."""
    settings = _settings(request)
    try:
        result = service.answer(
            session,
            settings.ai,
            settings.guide_dir,
            settings.context_file,
            body.question,
            history=[Turn(t.role, t.text) for t in body.history],
            screen=body.screen,
            guide_slug=body.guide_slug,
        )
    except ai_errors.AiOff:
        refused = AskRefusedBody(
            message="Written answers are not set up on this server.", reason="off", resets_on=None
        )
        return JSONResponse(refused.model_dump(mode="json"), status_code=409)
    except ai_errors.CapReached as capped:
        refused = AskRefusedBody(
            message=capped.message, reason=capped.reason, resets_on=capped.resets_on
        )
        return JSONResponse(refused.model_dump(mode="json"), status_code=409)
    except ai_errors.Busy as busy:
        return JSONResponse({"message": busy.message}, status_code=429)
    except ai_errors.AiUnavailable as unavailable:
        return JSONResponse({"message": unavailable.message}, status_code=502)
    return AskOut(
        answer=result.answer,
        sources=[_source(s) for s in result.sources],
        answered=result.answered,
        model=result.model,
    )
