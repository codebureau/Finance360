"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from finance360.core.migrate import schema_version

router = APIRouter()


class Health(BaseModel):
    status: str
    git_sha: str
    version: str
    schema_version: str


@router.get("/health", response_model=Health, operation_id="getHealth")
def get_health(request: Request) -> Health:
    return Health(
        status="healthy",
        git_sha=request.app.state.settings.git_sha,
        version=request.app.state.settings.app_version,
        schema_version=schema_version(request.app.state.engine) or "unmigrated",
    )
