"""Display preference endpoints (ADR-0031 section 3). Thin: the logic lives in
``capabilities.preferences``. The map belongs to the signed-in Login; a write is not audited."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from finance360.api.deps import PrincipalDep, SessionDep
from finance360.api.household import ERRORS
from finance360.capabilities.preferences import service

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


class PreferencesOut(BaseModel):
    preferences: dict[str, str] = Field(
        description="The Login's Display preferences: view key to chosen form."
    )


class PreferencesIn(BaseModel):
    preferences: dict[str, str | None] = Field(
        description="A partial map: a value sets the key, null forgets it."
    )


@router.get("", response_model=PreferencesOut, operation_id="getPreferences")
def get_preferences(session: SessionDep, principal: PrincipalDep) -> PreferencesOut:
    return PreferencesOut(preferences=service.get_preferences(session, principal.login_id))


@router.put(
    "",
    response_model=PreferencesOut,
    operation_id="putPreferences",
    responses=ERRORS,  # type: ignore[arg-type]
)
def put_preferences(
    body: PreferencesIn, session: SessionDep, principal: PrincipalDep
) -> PreferencesOut:
    return PreferencesOut(
        preferences=service.put_preferences(session, principal.login_id, body.preferences)
    )
