"""Household and Member endpoints. Thin: every rule lives in ``capabilities.household``."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# The shared dependencies moved to ``api.deps`` (they now need the auth seam). Re-exported here so
# the other routers keep importing them from this module.
from finance360.api.deps import ContextDep as ContextDep
from finance360.api.deps import SessionDep as SessionDep
from finance360.api.deps import get_audit_context as get_audit_context
from finance360.api.deps import get_session as get_session
from finance360.capabilities.household import service
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.household import Member

router = APIRouter(prefix="/api/household", tags=["household"])


class FieldErrorOut(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    errors: list[FieldErrorOut]


class MessageBody(BaseModel):
    message: str


class MemberOut(BaseModel):
    id: int
    name: str
    date_of_birth: date


class HouseholdOut(BaseModel):
    name: str
    time_zone: str
    effort_level: str
    members: list[MemberOut]


class HouseholdIn(BaseModel):
    name: str = ""
    time_zone: str | None = Field(
        default=None, description="IANA name, e.g. Australia/Melbourne. Omit to leave unchanged."
    )
    effort_level: str | None = Field(
        default=None, description="light, steady or thorough. Omit to leave unchanged."
    )


class MemberIn(BaseModel):
    name: str = ""
    date_of_birth: date | None = None


ERRORS = {
    404: {"model": MessageBody},
    409: {"model": MessageBody},
    422: {"model": ErrorBody},
}


def _member_out(member: Member) -> MemberOut:
    return MemberOut(id=member.id, name=member.name, date_of_birth=member.date_of_birth)


def _household_out(session: Session) -> HouseholdOut:
    household = service.get_household(session)
    members = [_member_out(m) for m in service.list_members(session)]
    return HouseholdOut(
        name=household.name,
        time_zone=household.time_zone,
        effort_level=household.effort_level,
        members=members,
    )


@router.get("", response_model=HouseholdOut, operation_id="getHousehold")
def get_household(session: SessionDep) -> HouseholdOut:
    return _household_out(session)


@router.put(
    "",
    response_model=HouseholdOut,
    operation_id="updateHousehold",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_household(body: HouseholdIn, session: SessionDep, ctx: ContextDep) -> HouseholdOut:
    service.rename_household(session, ctx, body.name)
    if body.time_zone is not None:
        service.set_time_zone(session, ctx, body.time_zone)
    if body.effort_level is not None:
        service.set_effort_level(session, ctx, body.effort_level)
    return _household_out(session)


@router.post(
    "/members",
    status_code=201,
    response_model=MemberOut,
    operation_id="addMember",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_member(body: MemberIn, session: SessionDep, ctx: ContextDep) -> MemberOut:
    return _member_out(service.add_member(session, ctx, body.name, body.date_of_birth))


@router.put(
    "/members/{member_id}",
    response_model=MemberOut,
    operation_id="updateMember",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_member(
    member_id: int, body: MemberIn, session: SessionDep, ctx: ContextDep
) -> MemberOut:
    return _member_out(
        service.update_member(session, ctx, member_id, body.name, body.date_of_birth)
    )


@router.delete(
    "/members/{member_id}",
    status_code=204,
    operation_id="deleteMember",
    responses=ERRORS,  # type: ignore[arg-type]
)
def delete_member(member_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    service.delete_member(session, ctx, member_id)
    return Response(status_code=204)


def _invalid_field(loc: tuple[str | int, ...]) -> str:
    return str(loc[-1]) if len(loc) > 1 else "body"


def register_error_handlers(app: FastAPI) -> None:
    """Map domain errors to the ``{"errors": [{field, message}]}`` shape the UI shows."""

    @app.exception_handler(ValidationError)
    def _validation(_: Request, exc: ValidationError) -> JSONResponse:
        errors = [{"field": e.field, "message": e.message} for e in exc.errors]
        return JSONResponse({"errors": errors}, status_code=422)

    @app.exception_handler(RequestValidationError)
    def _malformed(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Malformed input (e.g. "2020-13-45" as a date) that never reached the service.
        errors = [
            {
                "field": _invalid_field(tuple(err["loc"])),
                "message": "Enter a valid date as YYYY-MM-DD."
                if "date" in err["type"]
                else "This value is not valid.",
            }
            for err in exc.errors()
        ]
        return JSONResponse({"errors": errors}, status_code=422)

    @app.exception_handler(NotFoundError)
    def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"message": str(exc)}, status_code=404)

    @app.exception_handler(service.MemberInUseError)
    def _in_use(_: Request, exc: service.MemberInUseError) -> JSONResponse:
        return JSONResponse({"message": str(exc)}, status_code=409)
