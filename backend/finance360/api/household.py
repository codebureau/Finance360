"""Household and Member endpoints. Thin: every rule lives in ``capabilities.household``."""

from __future__ import annotations

from datetime import date
from typing import Any

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


class FactOut(BaseModel):
    """One effective fact. ``source`` is ``entered`` or ``default``; ``label`` is its wording."""

    value: str | bool | int | None
    source: str
    label: str


class PropertyChoiceOut(BaseModel):
    id: int
    name: str


class HouseholdFactsOut(BaseModel):
    """The Household facts as a rule may use them (ADR-0029 section 1), with the stored values so
    the screen can offer "reset". ``state`` is ``ok``, ``needs_home_choice`` or ``not_modelled``."""

    couple_status: FactOut
    homeowner: FactOut
    principal_home_account_id: FactOut
    #: The principal home's name when there is one.
    principal_home_name: str | None
    stored_couple_status: str | None
    stored_homeowner: bool | None
    stored_principal_home_account_id: int | None
    state: str
    reasons: list[str]
    #: The open Property Accounts the principal home can be picked from.
    property_accounts: list[PropertyChoiceOut]


class HouseholdOut(BaseModel):
    name: str
    time_zone: str
    effort_level: str
    members: list[MemberOut]
    facts: HouseholdFactsOut


class HouseholdFactsIn(BaseModel):
    """Only the facts present are changed; ``null`` resets one to not entered."""

    couple_status: str | None = None
    homeowner: bool | None = None
    principal_home_account_id: int | None = None


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


def _fact_out(fact: service.facts.Fact[Any]) -> FactOut:
    return FactOut(value=fact.value, source=fact.source, label=fact.label)


def _facts_out(session: Session) -> HouseholdFactsOut:
    household = service.get_household(session)
    effective = service.effective_facts(session)
    choices = service.open_property_accounts(session)
    names = {a.id: a.name for a in choices}
    home_id = effective.principal_home_account_id.value
    return HouseholdFactsOut(
        couple_status=_fact_out(effective.couple_status),
        homeowner=_fact_out(effective.homeowner),
        principal_home_account_id=_fact_out(effective.principal_home_account_id),
        principal_home_name=names.get(home_id) if home_id is not None else None,
        stored_couple_status=household.couple_status,
        stored_homeowner=household.homeowner,
        stored_principal_home_account_id=household.principal_home_account_id,
        state=effective.state,
        reasons=list(effective.reasons),
        property_accounts=[PropertyChoiceOut(id=a.id, name=a.name) for a in choices],
    )


def _household_out(session: Session) -> HouseholdOut:
    household = service.get_household(session)
    members = [_member_out(m) for m in service.list_members(session)]
    return HouseholdOut(
        facts=_facts_out(session),
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


@router.put(
    "/facts",
    response_model=HouseholdOut,
    operation_id="updateHouseholdFacts",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_household_facts(
    body: HouseholdFactsIn, session: SessionDep, ctx: ContextDep
) -> HouseholdOut:
    given = body.model_fields_set
    service.set_household_facts(
        session,
        ctx,
        **{name: getattr(body, name) for name in given},
    )
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
