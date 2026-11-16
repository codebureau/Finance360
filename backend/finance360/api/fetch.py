"""Fetch endpoints (ADR-0013 section 6): status of the daily fetch of Prices and FX rates, the run
history, and "Fetch now". Thin: every rule lives in ``capabilities.reference.fetch``.

``POST /api/fetch/run`` claims a run per configured dataset and returns at once (202); the work
continues in the background and its outcome appears in ``GET /api/fetch/status``. Owner only.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from finance360.api.deps import OwnerDep, SessionDep
from finance360.api.household import MessageBody
from finance360.capabilities.household.service import household_today
from finance360.capabilities.reference import fetch as service
from finance360.config import Settings
from finance360.core.models.fetch_run import FetchRun

router = APIRouter(prefix="/api/fetch", tags=["fetch"])

Dataset = Literal["prices", "fx"]
RunStatus = Literal["running", "success", "partial", "failed"]


class TargetResultOut(BaseModel):
    target: str = Field(description="An Instrument name or a currency code.")
    symbol: str | None = Field(description="The provider symbol used, if any.")
    outcome: Literal["ok", "error", "skipped"]
    added: int
    revised: int
    message: str | None


class FetchRunOut(BaseModel):
    id: int
    dataset: Dataset
    provider: str
    trigger: Literal["scheduled", "catch-up", "manual"]
    started_at: datetime = Field(description="UTC.")
    finished_at: datetime | None = Field(description="UTC. Null while running.")
    status: RunStatus
    added: int = Field(description="New Prices or FX rates written.")
    revised: int = Field(description="Existing provider rows superseded by a revised value.")
    error: str | None = Field(description="Short summary of what failed; never a secret.")


class FetchRunDetailOut(FetchRunOut):
    results: list[TargetResultOut]


class DatasetStatusOut(BaseModel):
    dataset: Dataset
    label: str
    provider: str | None = Field(description="The configured provider key; null when none is set.")
    configured: bool
    latest: FetchRunOut | None
    last_success: FetchRunOut | None = Field(description="The latest run that fetched something.")
    last_error: FetchRunOut | None = Field(description="The latest run that recorded an error.")


class StaleInstrumentOut(BaseModel):
    instrument_id: int
    name: str
    latest_as_at: date | None = Field(description="Date of its newest Price; null if it has none.")


class FetchStatusOut(BaseModel):
    datasets: list[DatasetStatusOut]
    fetch_time: str = Field(description="HH:MM, Household local time, of the daily fetch.")
    sample_data: bool = Field(description="True when providers serve invented offline data.")
    stale_after_days: int
    stale_instruments: list[StaleInstrumentOut]


def _run(run: FetchRun | None) -> FetchRunOut | None:
    if run is None:
        return None
    return FetchRunOut.model_validate(run, from_attributes=True)


def _detail(run: FetchRun) -> FetchRunDetailOut:
    base: dict[str, Any] = FetchRunOut.model_validate(run, from_attributes=True).model_dump()
    return FetchRunDetailOut(
        **base, results=[TargetResultOut.model_validate(r) for r in run.detail or []]
    )


def _configured(settings: Settings) -> dict[str, str | None]:
    return {"prices": settings.price_provider, "fx": settings.fx_provider}


@router.get("/status", response_model=FetchStatusOut, operation_id="getFetchStatus")
def get_status(request: Request, session: SessionDep) -> FetchStatusOut:
    settings: Settings = request.app.state.settings
    datasets: list[DatasetStatusOut] = []
    for dataset, key in _configured(settings).items():
        state = service.dataset_state(session, dataset)
        datasets.append(
            DatasetStatusOut(
                dataset=dataset,  # type: ignore[arg-type]
                label=service.DATASET_LABELS[dataset],
                provider=key,
                configured=key is not None,
                latest=_run(state.latest),
                last_success=_run(state.last_success),
                last_error=_run(state.last_error),
            )
        )
    stale = service.stale_instruments(session, household_today(session), settings.price_stale_days)
    return FetchStatusOut(
        datasets=datasets,
        fetch_time=settings.fetch_time.strftime("%H:%M"),
        sample_data=settings.provider_transport == "sample",
        stale_after_days=settings.price_stale_days,
        stale_instruments=[
            StaleInstrumentOut(
                instrument_id=s.instrument_id, name=s.name, latest_as_at=s.latest_as_at
            )
            for s in stale
        ],
    )


@router.get("/runs", response_model=list[FetchRunOut], operation_id="listFetchRuns")
def list_runs(
    session: SessionDep, dataset: Dataset | None = None, limit: int = 30
) -> list[FetchRunOut]:
    limit = max(1, min(limit, 100))
    return [r for run in service.list_runs(session, dataset, limit) if (r := _run(run)) is not None]


@router.get(
    "/runs/{run_id}",
    response_model=FetchRunDetailOut,
    operation_id="getFetchRun",
    responses={404: {"model": MessageBody}},  # type: ignore[arg-type]
)
def get_run(run_id: int, session: SessionDep) -> FetchRunDetailOut | JSONResponse:
    run = service.get_run(session, run_id)
    if run is None:
        return JSONResponse({"message": f"Fetch run {run_id} does not exist."}, status_code=404)
    return _detail(run)


def _run_claimed(engine: Engine, claimed: list[tuple[int, str, str]], today: date) -> None:
    """Background: run each claimed dataset in turn. Failures are recorded, never raised."""
    for run_id, dataset, key in claimed:
        service.execute(engine, run_id, dataset, key, today)


@router.post(
    "/run",
    status_code=202,
    response_model=list[FetchRunOut],
    operation_id="runFetch",
    responses={409: {"model": MessageBody}},  # type: ignore[arg-type]
)
def run_now(
    request: Request, tasks: BackgroundTasks, session: SessionDep, _: OwnerDep
) -> list[FetchRunOut] | JSONResponse:
    settings: Settings = request.app.state.settings
    configured = [(d, k) for d, k in _configured(settings).items() if k]
    if not configured:
        return JSONResponse(
            {"message": "No price or FX provider is configured on this instance."},
            status_code=409,
        )
    claimed: list[tuple[int, str, str]] = []
    try:
        # On the request's own session (see ``claim_in``), and all-or-nothing: if one dataset is
        # already running, none is claimed.
        with session.begin_nested():
            for dataset, key in configured:
                claimed.append((service.claim_in(session, dataset, key, "manual"), dataset, key))
    except service.FetchBusy as busy:
        return JSONResponse({"message": str(busy)}, status_code=409)
    today = household_today(session)
    runs = [r for run_id, _d, _k in claimed if (r := _run(service.get_run(session, run_id)))]
    # Runs after the response, i.e. after the request session has committed the claims.
    tasks.add_task(_run_claimed, request.app.state.engine, claimed, today)
    return runs
