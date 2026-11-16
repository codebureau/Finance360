"""Income stream endpoints (ADR-0017 section 2). Thin: every rule lives in
``capabilities.projection.income_streams``.

Amounts are plain decimal strings in AUD, never JSON numbers. M3 allows only the ``salary`` type and
only sole ownership; anything else is rejected with a field error.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.projection import income, income_streams
from finance360.core.models.income_stream import IncomeStream
from finance360.core.money import format_amount

router = APIRouter(prefix="/api/income-streams", tags=["income streams"])


class StreamOwnerOut(BaseModel):
    member_id: int
    member_name: str
    share_bp: int = Field(description="Basis points; 10000 is 100%. Sole only for now.")


class IncomeStreamOut(BaseModel):
    id: int
    name: str
    notes: str | None
    type_key: str
    type_label: str
    tax_kind: str = Field(description="The type's tax-relevant kind: employment for a salary.")
    ownership: str
    owners: list[StreamOwnerOut]
    gross_annual: str = Field(description="Today's dollars a year, before tax, excluding super.")
    take_home_annual: str | None = Field(description="Today's dollars a year, after tax.")
    start_on: date | None
    start_event: str | None = Field(description="A Life event of the owner, such as 'retirement'.")
    end_on: date | None
    end_event: str | None
    credit_category: str


class OwnerIn(BaseModel):
    member_id: int
    share_bp: int | None = Field(default=None, description="Omit, or 10000, for a sole stream.")


class IncomeStreamIn(BaseModel):
    name: str = ""
    type_key: str = Field(default="salary", description="Only 'salary' is available for now.")
    notes: str | None = None
    ownership: str = "sole"
    owners: list[OwnerIn] = []
    gross_annual: str = Field(default="", description="A decimal amount, today's dollars a year.")
    take_home_annual: str | None = Field(
        default=None, description="A decimal amount after tax. Required for a salary."
    )
    start_on: date | None = None
    start_event: str | None = None
    end_on: date | None = None
    end_event: str | None = Field(
        default=None, description="Omit both end fields: a salary ends at 'retirement'."
    )
    credit_category: str = "cash"


def _out(stream: IncomeStream) -> IncomeStreamOut:
    kind = income.TYPES.get(stream.type_key)
    take_home = stream.take_home_annual
    return IncomeStreamOut(
        id=stream.id,
        name=stream.name,
        notes=stream.notes,
        type_key=stream.type_key,
        type_label=kind.label if kind else stream.type_key,
        tax_kind=kind.tax_kind if kind else "",
        ownership=stream.ownership,
        owners=[
            StreamOwnerOut(member_id=o.member_id, member_name=o.member.name, share_bp=o.share_bp)
            for o in stream.owners
        ],
        gross_annual=format_amount(stream.gross_annual),
        take_home_annual=format_amount(take_home) if take_home is not None else None,
        start_on=stream.start_on,
        start_event=stream.start_event,
        end_on=stream.end_on,
        end_event=stream.end_event,
        credit_category=stream.credit_category,
    )


def _input(body: IncomeStreamIn) -> income_streams.StreamInput:
    return income_streams.StreamInput(
        name=body.name,
        type_key=body.type_key,
        gross_annual=body.gross_annual,
        take_home_annual=body.take_home_annual,
        owners=[income_streams.OwnerInput(o.member_id, o.share_bp) for o in body.owners],
        ownership=body.ownership,
        notes=body.notes,
        start_on=body.start_on,
        start_event=body.start_event,
        end_on=body.end_on,
        end_event=body.end_event,
        credit_category=body.credit_category,
    )


@router.get("", response_model=list[IncomeStreamOut], operation_id="listIncomeStreams")
def list_streams(session: SessionDep) -> list[IncomeStreamOut]:
    return [_out(s) for s in income_streams.list_streams(session)]


@router.get(
    "/{stream_id}",
    response_model=IncomeStreamOut,
    operation_id="getIncomeStream",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_stream(stream_id: int, session: SessionDep) -> IncomeStreamOut:
    return _out(income_streams.get_stream(session, stream_id))


@router.post(
    "",
    status_code=201,
    response_model=IncomeStreamOut,
    operation_id="createIncomeStream",
    responses=ERRORS,  # type: ignore[arg-type]
)
def create_stream(body: IncomeStreamIn, session: SessionDep, ctx: ContextDep) -> IncomeStreamOut:
    return _out(income_streams.create_stream(session, ctx, _input(body)))


@router.put(
    "/{stream_id}",
    response_model=IncomeStreamOut,
    operation_id="updateIncomeStream",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_stream(
    stream_id: int, body: IncomeStreamIn, session: SessionDep, ctx: ContextDep
) -> IncomeStreamOut:
    return _out(income_streams.update_stream(session, ctx, stream_id, _input(body)))


@router.delete(
    "/{stream_id}",
    status_code=204,
    operation_id="deleteIncomeStream",
    responses=ERRORS,  # type: ignore[arg-type]
)
def delete_stream(stream_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    income_streams.delete_stream(session, ctx, stream_id)
    return Response(status_code=204)
