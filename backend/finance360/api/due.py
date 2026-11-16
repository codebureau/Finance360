"""What's due endpoints (ADR-0020). Thin: the rules live with their owners, the logic in
``capabilities.due``. The Effort level is changed through the Household endpoint."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from finance360.api.deps import ContextDep, SessionDep
from finance360.api.household import ERRORS
from finance360.capabilities.due import service
from finance360.core.due import DueItem

router = APIRouter(prefix="/api/due", tags=["due"])


class DueActionOut(BaseModel):
    kind: str = Field(description="keep_mine or use_shipped: what the button does.")
    label: str
    capture_id: int = Field(description="The captured Rule figure the button acts on.")


class DueItemOut(BaseModel):
    rule_key: str
    subject_key: str
    importance: str = Field(description="important or optional: a fixed property of the rule.")
    title: str
    effect: str
    due_on: date
    link: str
    suits: list[str]
    actions: list[DueActionOut]


class SnoozedItemOut(BaseModel):
    snooze_id: int
    #: Null: skipped for this period only.
    until_on: date | None
    item: DueItemOut


class FailedRuleOut(BaseModel):
    rule_key: str
    name: str


class WhatsDueOut(BaseModel):
    level: str
    today: date
    items: list[DueItemOut]
    snoozed: list[SnoozedItemOut]
    failed_rules: list[FailedRuleOut]


class SnoozeIn(BaseModel):
    rule_key: str
    subject_key: str
    snooze: str = Field(description="week, month or skip (this period only).")


class SnoozeOut(BaseModel):
    id: int
    rule_key: str
    subject_key: str
    until_on: date | None


def _item_out(item: DueItem) -> DueItemOut:
    return DueItemOut(
        rule_key=item.rule_key,
        subject_key=item.subject_key,
        importance=item.importance,
        title=item.title,
        effect=item.effect,
        due_on=item.due_on,
        link=item.link,
        suits=list(item.suits),
        actions=[
            DueActionOut(kind=a.kind, label=a.label, capture_id=a.capture_id) for a in item.actions
        ],
    )


@router.get("", response_model=WhatsDueOut, operation_id="getWhatsDue")
def get_whats_due(session: SessionDep) -> WhatsDueOut:
    result = service.whats_due(session)
    return WhatsDueOut(
        level=result.level,
        today=result.today,
        items=[_item_out(i) for i in result.items],
        snoozed=[
            SnoozedItemOut(snooze_id=s.snooze_id, until_on=s.until_on, item=_item_out(s.item))
            for s in result.snoozed
        ],
        failed_rules=[FailedRuleOut(rule_key=f.rule_key, name=f.name) for f in result.failed_rules],
    )


@router.put(
    "/snoozes",
    response_model=SnoozeOut,
    operation_id="snoozeDueItem",
    responses=ERRORS,  # type: ignore[arg-type]
)
def snooze_item(body: SnoozeIn, session: SessionDep, ctx: ContextDep) -> SnoozeOut:
    row = service.snooze(session, ctx, body.rule_key, body.subject_key, body.snooze)
    return SnoozeOut(
        id=row.id, rule_key=row.rule_key, subject_key=row.subject_key, until_on=row.until_on
    )


@router.delete(
    "/snoozes/{snooze_id}",
    status_code=204,
    operation_id="unsnoozeDueItem",
    responses=ERRORS,  # type: ignore[arg-type]
)
def unsnooze_item(snooze_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    service.unsnooze(session, ctx, snooze_id)
    return Response(status_code=204)
