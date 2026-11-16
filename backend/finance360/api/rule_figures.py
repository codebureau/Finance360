"""Rule figure endpoints (ADR-0022): list a year's figures with their state, capture a
figure typed from the official page, correct it, withdraw it.

Thin: every rule lives in ``core.rulepack_store`` and ``core.figure_capture``. Figures cross the API
as canonical text (``"0.88"`` dollars, ``"16.5%"``), never as JSON numbers. The writes are
owner-only (via ``ContextDep``) and audited. The screen over them is the Rule figures page.

``POST /proposals`` (ADR-0023) reads a pasted official page and returns checked *proposals*:
it stores nothing. A model reads it when one is set up (through the AI gateway); otherwise,
or if the model is capped or failing, the word matcher does. A person confirms a proposal
through the same ``POST`` and ``PUT`` as typed entry, which carry the proposal back
(``ai_log_id`` and ``ai_proposal`` for a model's, ``matched_proposal`` for the matcher's) to be
stored beside the confirmed value and validate it exactly as a typed one.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StrictBool, StrictInt
from sqlalchemy.orm import Session

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.household import service as household
from finance360.capabilities.rulecapture import service as rule_capture
from finance360.config import Settings
from finance360.core import rulepack_store
from finance360.core.errors import FieldError, ValidationError
from finance360.core.models.rule_capture import RuleCapture
from finance360.core.rulepack import fy_label

router = APIRouter(prefix="/api/rule-figures", tags=["rule-figures"])

DEFAULT_JURISDICTION = "au"


class CaptureOut(BaseModel):
    id: int
    quote: str | None = Field(description="The sentence the figure was read from.")
    page_url: str | None
    retrieved_on: date = Field(description="The day the page was read.")
    captured_at: datetime = Field(description="UTC.")
    captured_by: str = Field(description="Who captured it, such as 'owner'.")
    provisional_note: str | None
    method: Literal["typed", "ai", "matched", "carried"] = Field(
        description="Who supplied it: typed by hand, read by AI, matched by words on the page, or "
        "carried from an earlier year and not yet checked against the page."
    )


class PreviousOut(BaseModel):
    financial_year: int
    financial_year_label: str
    value: str


class FigureOut(BaseModel):
    key: str
    label: str
    kind: Literal["money", "rate", "int", "brackets"]
    unit: str | None
    where: str = Field(description="Where on the page to find the figure.")
    page_url: str
    url_confirmed: bool = Field(
        description="False until a person has opened the address and found it correct."
    )
    scheduled_change: str | None = Field(
        description="A known change the maintainer has written down, such as a rate legislated to "
        "fall on a date. Guidance only; it never alters a value."
    )
    plausible_range: str
    entry_hint: str
    financial_year: int
    financial_year_label: str
    state: Literal["shipped", "captured", "missing"] = Field(
        description="Who supplies the figure: the release, a capture, or nobody yet."
    )
    value: str | None = Field(description="The effective figure as canonical text.")
    shipped_value: str | None = Field(
        description="The release's figure, when a capture replaced a different one."
    )
    provisional_note: str | None
    previous: PreviousOut | None = Field(description="The nearest earlier year's figure.")
    capture: CaptureOut | None = Field(
        description="The active capture, if any (withdraw or correct it by this id)."
    )


class NoLongerFitOut(BaseModel):
    capture_id: int
    key: str
    financial_year: int
    financial_year_label: str
    reason: str


class CarryOfferOut(BaseModel):
    source_financial_year: int
    source_financial_year_label: str
    count: int = Field(description="How many missing figures have an earlier figure to start from.")


class CarryProposalOut(BaseModel):
    key: str
    label: str
    kind: Literal["money", "rate", "int", "brackets"]
    unit: str | None
    value: str = Field(description="The earlier year's figure, as canonical text.")
    source_financial_year: int
    source_financial_year_label: str
    note: str = Field(description="The provisional note it is written with.")
    page_url: str
    scheduled_change: str | None


class NoEarlierOut(BaseModel):
    key: str
    label: str


class CarryPreviewOut(BaseModel):
    financial_year: int
    financial_year_label: str
    source_financial_year: int | None
    source_financial_year_label: str | None
    proposals: list[CarryProposalOut]
    no_earlier: list[NoEarlierOut] = Field(
        description="Missing figures with no earlier figure at all: capture each."
    )


class CarryIn(BaseModel):
    jurisdiction: str = DEFAULT_JURISDICTION
    financial_year: StrictInt = Field(description="The year it ends in: 2027 is FY 2026-27.")


class ConfirmPageIn(BaseModel):
    jurisdiction: str = DEFAULT_JURISDICTION
    financial_year: StrictInt = Field(description="The year it ends in: 2027 is FY 2026-27.")
    page_url: str = Field(description="The source page whose carried figures are unchanged.")


class OwnConfirmOut(BaseModel):
    key: str
    label: str
    scheduled_change: str | None


class ConfirmPageOut(BaseModel):
    financial_year: int
    confirmed: list[str] = Field(description="Keys confirmed as unchanged, one audit event each.")
    needs_own_confirm: list[OwnConfirmOut] = Field(
        description="Carried figures on the page with a scheduled change: confirm each on its own."
    )


class YearOut(BaseModel):
    jurisdiction: str
    financial_year: int
    financial_year_label: str
    first_financial_year: int | None = Field(
        description="The earliest year a figure can be captured for (the pack's first)."
    )
    last_financial_year: int | None = Field(
        description="The latest year a figure can be captured for (next year's)."
    )
    rule_pack_version: str | None = Field(
        description="Such as 'au/2026-27/r6+1:a3f9c1' once a figure is captured."
    )
    carry_offer: CarryOfferOut | None = Field(
        description="Set when missing figures have an earlier year's figure to start from."
    )
    figures: list[FigureOut]
    no_longer_fit: list[NoLongerFitOut] = Field(
        description="Captures a release made invalid. They are ignored (the shipped figure "
        "applies) until re-captured."
    )


class AiProposal(BaseModel):
    """What was proposed for one figure, as it came back from a proposal (echoed on Confirm). The
    word matcher gives no confidence."""

    key: str
    fy: StrictInt
    value: str
    quote: str
    provisional: bool
    note: str
    confidence: str | None = None


class ProposeIn(BaseModel):
    jurisdiction: str = DEFAULT_JURISDICTION
    financial_year: StrictInt = Field(description="The year it ends in: 2027 is FY 2026-27.")
    keys: list[str] = Field(
        description="The figures to look for, at most 12 per model call (more are several calls)."
    )
    page_text: str = Field(description="The text of the official page, up to 40,000 characters.")


class ProposalOut(BaseModel):
    key: str
    label: str
    status: Literal["candidate", "not_found"] = Field(
        description="A candidate passed every check; otherwise the person enters the figure."
    )
    reason: str | None = Field(
        description="Why a proposal was rejected. Null when the page did not state the figure."
    )
    value: str | None = Field(description="Canonical figure text (a candidate only).")
    quote: str | None = Field(description="The sentence on the page the figure came from.")
    provisional_note: str | None = Field(
        description="Set when the page says the figure is announced, not yet law."
    )
    note: str
    confidence: Literal["high", "medium", "low"] | None = Field(
        description="The model's own estimate. Display only; it gates nothing. Null when the word "
        "matcher found it."
    )
    jump: str | None = Field(
        description="A large change from last year: the person must say it is right to confirm."
    )
    ai_log_id: int | None = Field(description="The model call it came from; null when matched.")
    proposal: AiProposal | None = Field(description="Send back with Confirm (candidate only).")


class ProposalsOut(BaseModel):
    financial_year: int
    method: Literal["ai", "matched"] = Field(
        description="Who read the page: a model, or the word matcher (not an AI call)."
    )
    fallback: str | None = Field(
        description="Why the word matcher read it though a model is set up (capped or failing)."
    )
    proposals: list[ProposalOut]


class CaptureIn(BaseModel):
    jurisdiction: str = DEFAULT_JURISDICTION
    financial_year: StrictInt = Field(description="The year it ends in: 2027 is FY 2026-27.")
    key: str
    value: str = Field(
        description="Canonical figure text, such as '0.88', '16.5%' or '0:0,18200:16'."
    )
    quote: str | None = None
    page_url: str | None = None
    provisional_note: str | None = Field(
        default=None, description="A note makes the figure provisional (announced, not yet law)."
    )
    retrieved_on: date | None = Field(default=None, description="Defaults to today.")
    confirm_jump: StrictBool = Field(
        default=False,
        description="True once the person has seen last year's figure and says it is right.",
    )
    ai_log_id: int | None = Field(
        default=None, description="Set with ai_proposal when confirming an AI proposal."
    )
    ai_proposal: AiProposal | None = Field(
        default=None, description="The proposal being confirmed, stored beside the confirmed value."
    )
    matched_proposal: AiProposal | None = Field(
        default=None,
        description="Set when confirming a figure the word matcher found (method matched). Not "
        "with ai_log_id: the matcher is not an AI call and has no log entry.",
    )


class CorrectIn(BaseModel):
    value: str
    quote: str | None = None
    page_url: str | None = None
    provisional_note: str | None = None
    retrieved_on: date | None = None
    confirm_jump: StrictBool = False
    ai_log_id: int | None = None
    ai_proposal: AiProposal | None = None
    matched_proposal: AiProposal | None = None


def _figure(session: Session, jurisdiction: str, key: str, fy: int) -> FigureOut:
    for row in _figures(session, jurisdiction, fy):
        if row.key == key:
            return row
    raise ValidationError([FieldError("key", f"{key!r} is not a declared figure.")])


def _figures(session: Session, jurisdiction: str, fy: int) -> list[FigureOut]:
    out: list[FigureOut] = []
    for row in rulepack_store.figures_for_year(session, jurisdiction, fy):
        spec = row.spec
        cap = row.capture
        out.append(
            FigureOut(
                key=spec.key,
                label=spec.label,
                kind=spec.kind,  # type: ignore[arg-type]
                unit=spec.unit,
                where=spec.where,
                page_url=spec.page_url,
                url_confirmed=spec.url_confirmed,
                scheduled_change=spec.scheduled_change,
                plausible_range=row.range_text,
                entry_hint=row.entry_hint,
                financial_year=fy,
                financial_year_label=fy_label(fy),
                state=row.state,  # type: ignore[arg-type]
                value=row.value,
                shipped_value=row.shipped_value,
                provisional_note=row.provisional_note,
                previous=PreviousOut(
                    financial_year=row.previous[0],
                    financial_year_label=fy_label(row.previous[0]),
                    value=row.previous[1],
                )
                if row.previous
                else None,
                capture=CaptureOut(
                    id=cap.id,
                    quote=cap.quote,
                    page_url=cap.page_url,
                    retrieved_on=cap.retrieved_on,
                    captured_at=cap.captured_at,
                    captured_by=cap.captured_by,
                    provisional_note=cap.provisional_note,
                    method=cap.method,  # type: ignore[arg-type]
                )
                if cap
                else None,
            )
        )
    return out


@router.get("", response_model=YearOut, operation_id="listRuleFigures")
def list_rule_figures(
    session: SessionDep, financial_year: int, jurisdiction: str = DEFAULT_JURISDICTION
) -> YearOut:
    """Every figure the pack declares for capture, for one financial year, with who supplies it."""
    pack = rulepack_store.effective_pack(session, jurisdiction)
    today = household.household_today(session)
    years = rulepack_store.capture_years(session, jurisdiction, today)
    offer = (
        rulepack_store.carry_preview(session, jurisdiction, financial_year, today=today)
        if pack is not None
        else None
    )
    return YearOut(
        jurisdiction=jurisdiction,
        financial_year=financial_year,
        financial_year_label=fy_label(financial_year),
        first_financial_year=years[0] if years else None,
        last_financial_year=years[1] if years else None,
        rule_pack_version=pack.version_label if pack is not None else None,
        carry_offer=CarryOfferOut(
            source_financial_year=offer.source_fy,
            source_financial_year_label=fy_label(offer.source_fy),
            count=len(offer.proposals),
        )
        if offer is not None and offer.source_fy is not None
        else None,
        figures=_figures(session, jurisdiction, financial_year),
        no_longer_fit=[
            NoLongerFitOut(
                capture_id=c.id,
                key=c.key,
                financial_year=c.fy,
                financial_year_label=fy_label(c.fy),
                reason=reason,
            )
            for c, reason in rulepack_store.unfit(session, jurisdiction)
        ],
    )


def _input(body: CaptureIn | CorrectIn) -> rulepack_store.CaptureInput:
    if (body.ai_log_id is None) != (body.ai_proposal is None):
        raise ValidationError(
            [FieldError("ai_proposal", "Send the AI log id and the proposal together.")]
        )
    if body.matched_proposal is not None and body.ai_proposal is not None:
        raise ValidationError(
            [FieldError("matched_proposal", "A figure is read by AI or matched, not both.")]
        )
    ai = None
    if body.ai_log_id is not None and body.ai_proposal is not None:
        ai = rulepack_store.AiProvenance(body.ai_log_id, body.ai_proposal.model_dump())
    elif body.matched_proposal is not None:
        ai = rulepack_store.AiProvenance(None, body.matched_proposal.model_dump(), "matched")
    return rulepack_store.CaptureInput(
        value=body.value,
        quote=body.quote,
        page_url=body.page_url,
        provisional_note=body.provisional_note,
        retrieved_on=body.retrieved_on,
        confirm_jump=body.confirm_jump,
        ai=ai,
    )


def _after(session: Session, row: RuleCapture) -> FigureOut:
    return _figure(session, row.jurisdiction, row.key, row.fy)


@router.post(
    "/proposals",
    response_model=ProposalsOut,
    operation_id="proposeRuleFigures",
    responses=ERRORS,  # type: ignore[arg-type]
)
def propose_rule_figures(body: ProposeIn, request: Request, session: SessionDep) -> ProposalsOut:
    """Read a pasted official page and propose the figures asked for. Stores nothing: every
    proposal passed deterministic checks (it parses, is in range, its quote is in the page and holds
    its number) or comes back as not found. Always available: with no model, or one that is capped
    or failing, the word matcher reads the page (``method`` and ``fallback`` say so)."""
    settings: Settings = request.app.state.settings
    proposed = rule_capture.propose(
        session,
        settings.ai,
        body.jurisdiction,
        body.financial_year,
        body.keys,
        body.page_text,
        today=household.household_today(session),
    )
    return ProposalsOut(
        financial_year=body.financial_year,
        method=proposed.method,
        fallback=proposed.fallback,
        proposals=[
            ProposalOut(
                key=r.key,
                label=r.label,
                status=r.status,
                reason=r.reason,
                value=r.value,
                quote=r.quote,
                provisional_note=r.provisional_note,
                note=r.note,
                confidence=r.confidence,  # type: ignore[arg-type]
                jump=r.jump,
                ai_log_id=r.ai_log_id,
                proposal=AiProposal.model_validate(r.proposal) if r.proposal else None,
            )
            for r in proposed.results
        ],
    )


@router.post(
    "",
    status_code=201,
    response_model=FigureOut,
    operation_id="captureRuleFigure",
    responses=ERRORS,  # type: ignore[arg-type]
)
def capture_rule_figure(body: CaptureIn, session: SessionDep, ctx: ContextDep) -> FigureOut:
    """Capture a figure typed from the official page. A large change from last year is refused
    until ``confirm_jump`` is true."""
    row = rulepack_store.capture_figure(
        session,
        ctx,
        body.jurisdiction,
        body.key,
        body.financial_year,
        _input(body),
        today=household.household_today(session),
    )
    return _after(session, row)


@router.put(
    "/{capture_id}",
    response_model=FigureOut,
    operation_id="correctRuleFigure",
    responses=ERRORS,  # type: ignore[arg-type]
)
def correct_rule_figure(
    capture_id: int, body: CorrectIn, session: SessionDep, ctx: ContextDep
) -> FigureOut:
    """Correct a capture: the new row supersedes it, and the old one stays in the history."""
    row = rulepack_store.correct_figure(
        session, ctx, capture_id, _input(body), today=household.household_today(session)
    )
    return _after(session, row)


@router.delete(
    "/{capture_id}",
    response_model=FigureOut,
    operation_id="withdrawRuleFigure",
    responses=ERRORS,  # type: ignore[arg-type]
)
def withdraw_rule_figure(capture_id: int, session: SessionDep, ctx: ContextDep) -> FigureOut:
    """Withdraw a capture: the shipped figure applies again, or the figure is missing."""
    row = rulepack_store.withdraw_figure(session, ctx, capture_id)
    return _after(session, row)


def _preview_out(preview: rulepack_store.CarryPreview) -> CarryPreviewOut:
    return CarryPreviewOut(
        financial_year=preview.fy,
        financial_year_label=fy_label(preview.fy),
        source_financial_year=preview.source_fy,
        source_financial_year_label=fy_label(preview.source_fy)
        if preview.source_fy is not None
        else None,
        proposals=[
            CarryProposalOut(
                key=p.spec.key,
                label=p.spec.label,
                kind=p.spec.kind,  # type: ignore[arg-type]
                unit=p.spec.unit,
                value=p.value,
                source_financial_year=p.source_fy,
                source_financial_year_label=fy_label(p.source_fy),
                note=p.note,
                page_url=p.spec.page_url,
                scheduled_change=p.spec.scheduled_change,
            )
            for p in preview.proposals
        ],
        no_earlier=[NoEarlierOut(key=s.key, label=s.label) for s in preview.no_earlier],
    )


@router.get(
    "/carry",
    response_model=CarryPreviewOut,
    operation_id="previewCarriedRuleFigures",
    responses=ERRORS,  # type: ignore[arg-type]
)
def preview_carried_rule_figures(
    session: SessionDep, financial_year: int, jurisdiction: str = DEFAULT_JURISDICTION
) -> CarryPreviewOut:
    """What "Start from <FY>" would write for a year: each missing figure with the nearest earlier
    year's value. Writes nothing."""
    return _preview_out(
        rulepack_store.carry_preview(
            session, jurisdiction, financial_year, today=household.household_today(session)
        )
    )


@router.post(
    "/carry",
    response_model=CarryPreviewOut,
    operation_id="carryRuleFigures",
    responses=ERRORS,  # type: ignore[arg-type]
)
def carry_rule_figures(body: CarryIn, session: SessionDep, ctx: ContextDep) -> CarryPreviewOut:
    """Accept "Start from <FY>": one provisional ``carried`` capture per missing figure that has an
    earlier figure, all in one transaction. Returns what was written."""
    return _preview_out(
        rulepack_store.carry_figures(
            session,
            ctx,
            body.jurisdiction,
            body.financial_year,
            today=household.household_today(session),
        )
    )


@router.post(
    "/confirm-page",
    response_model=ConfirmPageOut,
    operation_id="confirmCarriedPage",
    responses=ERRORS,  # type: ignore[arg-type]
)
def confirm_carried_page(
    body: ConfirmPageIn, session: SessionDep, ctx: ContextDep
) -> ConfirmPageOut:
    """Confirm every carried figure from one source page as unchanged, in one transaction with one
    audit event per figure. A figure with a scheduled change is left for its own Confirm."""
    result = rulepack_store.confirm_page(
        session,
        ctx,
        body.jurisdiction,
        body.financial_year,
        body.page_url,
        today=household.household_today(session),
    )
    return ConfirmPageOut(
        financial_year=body.financial_year,
        confirmed=[row.key for row in result.confirmed],
        needs_own_confirm=[
            OwnConfirmOut(key=s.key, label=s.label, scheduled_change=s.scheduled_change)
            for s in result.needs_own_confirm
        ],
    )


@router.post(
    "/{capture_id}/confirm",
    response_model=FigureOut,
    operation_id="confirmCarriedRuleFigure",
    responses=ERRORS,  # type: ignore[arg-type]
)
def confirm_carried_rule_figure(capture_id: int, session: SessionDep, ctx: ContextDep) -> FigureOut:
    """Confirm one carried figure as unchanged: the carried row is superseded by a typed one with
    the same value, the spec's page and today's date."""
    row = rulepack_store.confirm_unchanged(
        session, ctx, capture_id, today=household.household_today(session)
    )
    return _after(session, row)
