"""Exposure vocabulary and Exposure profile endpoints. Thin: every rule lives in
``capabilities.reference.exposure_profiles``.

Weights cross the API as percent strings (``"62.5"``), never as JSON floats. The API only ever
enters ``manual`` profiles.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.household.service import household_today
from finance360.capabilities.reference import exposure_profiles as service
from finance360.core.exposure import percent_string
from finance360.core.models.exposure import ExposureProfile
from finance360.core.weight import Weight

router = APIRouter(tags=["exposure"])


class LabelOut(BaseModel):
    key: str
    name: str


class DimensionOut(BaseModel):
    key: str
    name: str
    labels: list[LabelOut] = Field(description="In seed order.")


class WeightOut(BaseModel):
    dimension_key: str
    label_key: str
    percent: str = Field(description="Exact percent string, e.g. 62.5.")


class TrimmedOut(BaseModel):
    label_key: str
    percent: str


class ResolutionOut(BaseModel):
    dimension_key: str
    total_percent: str
    difference_percent: str = Field(description="Total minus 100: negative is a shortfall.")
    outcome: Literal["exact", "short", "over"]
    not_classified_percent: str = Field(description="A shortfall that resolves to Not classified.")
    trimmed: list[TrimmedOut] = Field(description="Trimmed from the largest weight when over 100.")


class ProfileOut(BaseModel):
    id: int
    instrument_id: int
    as_at: date
    source: str
    retrieved_on: date
    provenance_note: str | None
    recorded_at: datetime = Field(description="UTC.")
    status: Literal["current", "corrected"]
    superseded_at: datetime | None
    superseded_by_id: int | None
    weights: list[WeightOut]
    resolutions: list[ResolutionOut]


class WeightIn(BaseModel):
    dimension_key: str = ""
    label_key: str = ""
    percent: str = Field(default="", description="Percent, at most 4 decimal places.")


class ProfileIn(BaseModel):
    as_at: date
    provenance_note: str | None = None
    weights: list[WeightIn] = []


def _out(session: Session, profile: ExposureProfile) -> ProfileOut:
    return ProfileOut(
        id=profile.id,
        instrument_id=profile.instrument_id,
        as_at=profile.as_at,
        source=profile.source,
        retrieved_on=profile.retrieved_on,
        provenance_note=profile.provenance_note,
        recorded_at=profile.recorded_at,
        status="current" if profile.superseded_at is None else "corrected",
        superseded_at=profile.superseded_at,
        superseded_by_id=profile.superseded_by_id,
        weights=[
            WeightOut(
                dimension_key=w.dimension_key,
                label_key=w.label_key,
                percent=Weight(w.weight_ppm).to_percent(),
            )
            for w in profile.weights
        ],
        resolutions=[
            ResolutionOut(
                dimension_key=r.dimension_key,
                total_percent=percent_string(r.total_ppm),
                difference_percent=percent_string(r.difference_ppm),
                outcome="exact"
                if r.difference_ppm == 0
                else "short"
                if r.difference_ppm < 0
                else "over",
                not_classified_percent=percent_string(r.not_classified_ppm),
                trimmed=[TrimmedOut(label_key=k, percent=percent_string(p)) for k, p in r.trimmed],
            )
            for r in service.resolutions(session, profile)
        ],
    )


@router.get(
    "/api/exposure/vocabulary",
    response_model=list[DimensionOut],
    operation_id="listExposureVocabulary",
)
def vocabulary(session: SessionDep) -> list[DimensionOut]:
    """Each Dimension with its Exposure labels, in seed order."""
    return [
        DimensionOut(
            key=d.key, name=d.name, labels=[LabelOut(key=x.key, name=x.name) for x in d.labels]
        )
        for d in service.vocabulary(session)
    ]


@router.get(
    "/api/instruments/{instrument_id}/exposure-profiles",
    response_model=list[ProfileOut],
    operation_id="listExposureProfiles",
    responses=ERRORS,  # type: ignore[arg-type]
)
def list_profiles(instrument_id: int, session: SessionDep) -> list[ProfileOut]:
    """The whole history by date, superseded versions included (status says which)."""
    return [_out(session, p) for p in service.list_profiles(session, instrument_id)]


@router.post(
    "/api/instruments/{instrument_id}/exposure-profiles",
    status_code=201,
    response_model=ProfileOut,
    operation_id="recordExposureProfile",
    responses=ERRORS,  # type: ignore[arg-type]
)
def record_profile(
    instrument_id: int, body: ProfileIn, session: SessionDep, ctx: ContextDep
) -> ProfileOut:
    """Record a manual profile. One already current at the same ``as_at`` is superseded and stays
    in the history. A Dimension total more than one point from 100% is rejected (422)."""
    data = service.ProfileInput(
        as_at=body.as_at,
        provenance_note=body.provenance_note,
        retrieved_on=household_today(session),
        weights=tuple(
            service.WeightInput(w.dimension_key, w.label_key, w.percent) for w in body.weights
        ),
    )
    return _out(session, service.record_profile(session, ctx, instrument_id, data))
