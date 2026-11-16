"""Backup endpoints (ADR-0011): owner only. Thin: all logic lives in ``core.backup``.

``POST /api/backup/run`` starts a manual Backup and returns at once with the ``running`` row;
the work continues in the background and the outcome appears in ``GET /api/backup/status``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from finance360.api.deps import OwnerDep, SessionDep
from finance360.api.household import MessageBody
from finance360.core import backup
from finance360.core.models.backup import BackupRun

router = APIRouter(prefix="/api/backup", tags=["backup"])


class BackupRunOut(BaseModel):
    id: int
    reason: Literal["nightly", "manual", "pre-deploy", "maintenance"]
    started_at: datetime = Field(description="UTC.")
    finished_at: datetime | None = Field(description="UTC. Null while running.")
    status: Literal["running", "success", "failed"]
    snapshot_id: str | None
    size_bytes: int | None
    shipped: bool = Field(description="True once copied to the off-site repo.")
    error: str | None = Field(description="Short and sanitised; never a key.")


class BackupStatusOut(BaseModel):
    configured: bool = Field(description="False when no Backup key file is set up.")
    remote_configured: bool = Field(description="False means local repo only: nothing off-site.")
    stale: bool = Field(description="Set up, but no success within the last 36 hours.")
    stale_after_hours: int
    latest: BackupRunOut | None = Field(description="The latest Backup run, any outcome.")
    last_success: BackupRunOut | None
    maintenance: BackupRunOut | None = Field(description="The latest weekly prune and check.")


def _out(run: BackupRun | None) -> BackupRunOut | None:
    if run is None:
        return None
    return BackupRunOut.model_validate(run, from_attributes=True)


@router.get("/status", response_model=BackupStatusOut, operation_id="getBackupStatus")
def get_status(request: Request, session: SessionDep, _: OwnerDep) -> BackupStatusOut:
    state = backup.status(session, request.app.state.settings)
    return BackupStatusOut(
        configured=state.configured,
        remote_configured=state.remote_configured,
        stale=state.stale,
        stale_after_hours=int(backup.STALE_AFTER.total_seconds() // 3600),
        latest=_out(state.latest),
        last_success=_out(state.last_success),
        maintenance=_out(state.maintenance),
    )


@router.post(
    "/run",
    status_code=202,
    response_model=BackupRunOut,
    operation_id="runBackup",
    responses={409: {"model": MessageBody}},  # type: ignore[arg-type]
)
def run_now(
    request: Request, tasks: BackgroundTasks, session: SessionDep, _: OwnerDep
) -> BackupRunOut | JSONResponse:
    settings = request.app.state.settings
    engine = request.app.state.engine
    if not backup.configured(settings):
        return JSONResponse(
            {"message": "Backups are not set up on this instance (no Backup key file)."},
            status_code=409,
        )
    try:
        # On the request's own session: the auth step may hold SQLite's write lock until we
        # return, so a second connection here would wait out busy_timeout and fail.
        run_id = backup.claim_in(session, "manual")
    except backup.BackupBusy as busy:
        return JSONResponse({"message": str(busy)}, status_code=409)
    run = _out(session.get(BackupRun, run_id))
    if run is None:
        raise RuntimeError("The claimed backup run vanished.")
    # Runs after the response, i.e. after the request session has committed the claim.
    tasks.add_task(backup.execute, settings, engine, run_id, "manual")
    return run
