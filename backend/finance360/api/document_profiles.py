"""``/api/document-profiles``: list, show, test, save, activate and retire Document profiles
(ADR-0027). Thin: every rule is in ``capabilities.documents.store`` and
``capabilities.library.service``.

Shipped profiles are listed read-only beside the saved ones (a saved profile is a Proposed profile
until the owner activates it). Testing a draft stores nothing; saving stores the profile and a
snapshot with no figures. Saving, activating and retiring need the owner's Session.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from finance360.api.deps import SessionDep
from finance360.api.documents import ReadingOut, reading_out
from finance360.api.household import ERRORS, ContextDep
from finance360.api.uploads import DataDirDep
from finance360.capabilities.documents import service as documents
from finance360.capabilities.documents import store
from finance360.capabilities.documents.service import Profile
from finance360.capabilities.library import profiles as library
from finance360.core.errors import NotFoundError
from finance360.core.models.document_profile import DocumentProfile

router = APIRouter(prefix="/api/document-profiles", tags=["document-profiles"])

FAILED_TO_LOAD = "failed_to_load"
SHIPPED = "shipped"


class ProfileSumOut(BaseModel):
    total: str
    plus: list[str]


class ProfileFieldOut(BaseModel):
    key: str
    type: str = Field(description="'money', 'fy' or 'text'.")
    labels: list[str]
    pair: str = Field(description="How its value is found beside the label.")
    section: str | None
    required: bool
    absent_means_zero: bool
    negative_ok: bool
    repeats: bool = Field(description="The label may repeat: every row is read and added up.")
    zero_in_sums: bool = Field(description="A row the document omits counts as zero in a sum.")


class ProfileSectionOut(BaseModel):
    key: str
    start: str
    end: str | None


class SnapshotFieldOut(BaseModel):
    key: str
    state: str = Field(description="'found', 'absent', 'not_found' or 'ambiguous'.")
    reason: str | None = None
    required: bool | None = None
    page: int | None = None


class SnapshotCheckOut(BaseModel):
    kind: str
    status: str
    fields: list[str]


class SnapshotOut(BaseModel):
    """What a test left behind: never a value or any text from the document."""

    outcome: str
    fields: list[SnapshotFieldOut]
    checks: list[SnapshotCheckOut]


class ProfileSummaryOut(BaseModel):
    id: int | None = Field(description="The saved row's id; null for a shipped profile.")
    key: str
    version: int
    title: str
    target: str
    target_title: str
    source: str = Field(description="'shipped', 'pasted', 'mcp' or 'ai'.")
    status: str = Field(
        description="'shipped', 'proposed', 'active', 'retired' or 'failed_to_load' ('draft' "
        "while a pasted profile is only being tested)."
    )
    fills: list[str] = Field(description="The target form's fields it reads.")
    sums: list[ProfileSumOut] = Field(description="Its checks: each total must equal its parts.")
    overrides_shipped: bool = Field(
        description="An active saved profile that replaces the shipped one with its key."
    )
    overridden: bool = Field(description="A shipped profile an active saved one replaces.")
    failure: str | None = Field(description="Why a saved profile failed to load.")
    created_at: datetime | None


class ProfileDocumentOut(BaseModel):
    id: int
    file_name: str


class ProfileDetailOut(ProfileSummaryOut):
    claim: list[str] = Field(description="The phrases a document must all contain to be claimed.")
    sections: list[ProfileSectionOut]
    fields: list[ProfileFieldOut]
    profile_json: str = Field(description="The profile as JSON, for reading.")
    test_snapshot: SnapshotOut | None
    test_document: ProfileDocumentOut | None = Field(
        description="The Library document it was tested on, if it is still there."
    )
    proposed_by: str | None
    activated_at: datetime | None
    ended_at: datetime | None
    ended_reason: str | None


class ProfileTestIn(BaseModel):
    profile_json: str = Field(description="The draft profile, as JSON text.")
    document_id: int = Field(description="The Library document to try it on.")


class ProfileTestOut(BaseModel):
    profile: ProfileSummaryOut
    reading: ReadingOut = Field(description="Live; never stored.")
    acceptable: bool = Field(
        description="True when it reads the document ('ok', or 'warnings' with every required "
        "figure found): the profile can be saved or activated."
    )
    also_claimed_by: list[str] = Field(
        description="Other profiles for the same form that also claim this document."
    )


class ProfileActivatedOut(BaseModel):
    profile: ProfileDetailOut
    also_claimed_by: list[str]


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None else None


def _target_title(key: str) -> str:
    target = documents.get_target(key)
    return target.title if target is not None else key


def _facts(profile: Profile) -> dict[str, Any]:
    return {
        "key": profile.key,
        "version": profile.version,
        "title": profile.title,
        "target": profile.target,
        "target_title": _target_title(profile.target),
        "fills": [f.key for f in profile.fields],
        "sums": [ProfileSumOut(total=s.total, plus=list(s.plus)) for s in profile.sums],
    }


def _shipped_summary(profile: Profile) -> ProfileSummaryOut:
    return ProfileSummaryOut(
        id=None,
        source=SHIPPED,
        status=SHIPPED,
        overrides_shipped=False,
        overridden=documents.saved_profile(profile.key) is not None,
        failure=None,
        created_at=None,
        **_facts(profile),
    )


def _row_profile(row: DocumentProfile) -> tuple[Profile | None, str | None]:
    """The row's profile and, when it cannot be loaded, why."""
    try:
        return store.profile_of(row), None
    except Exception as problem:  # noqa: BLE001 - a stored row may be anything; show, never raise
        return None, store.reason_of(problem)


def _row_summary(row: DocumentProfile) -> ProfileSummaryOut:
    profile, why = _row_profile(row)
    status = store.status_of(row)
    failure = documents.failed_profiles().get(row.id, (None, None))[1]
    if status == "active" and (failure is not None or profile is None):
        status, failure = FAILED_TO_LOAD, failure or why
    facts: dict[str, Any] = (
        _facts(profile)
        if profile is not None
        else {
            "key": row.key,
            "version": row.version,
            "title": row.key,
            "target": row.target,
            "target_title": _target_title(row.target),
            "fills": [],
            "sums": [],
        }
    )
    return ProfileSummaryOut(
        id=row.id,
        source=row.source,
        status=status,
        overrides_shipped=status == "active" and documents.shipped_profile(row.key) is not None,
        overridden=False,
        failure=failure,
        created_at=_utc(row.created_at),
        **facts,
    )


def _detail_fields(profile: Profile | None) -> dict[str, Any]:
    if profile is None:
        return {"claim": [], "sections": [], "fields": []}
    return {
        "claim": list(profile.claim),
        "sections": [
            ProfileSectionOut(key=s.key, start=s.start, end=s.end) for s in profile.sections
        ],
        "fields": [
            ProfileFieldOut(
                key=f.key,
                type=f.type,
                labels=list(f.labels),
                pair=f.pair,
                section=f.section,
                required=f.required,
                absent_means_zero=f.absent_means_zero,
                negative_ok=f.negative_ok,
                repeats=f.repeats,
                zero_in_sums=f.zero_in_sums,
            )
            for f in profile.fields
        ],
    }


def _shipped_detail(profile: Profile) -> ProfileDetailOut:
    return ProfileDetailOut(
        **_shipped_summary(profile).model_dump(),
        **_detail_fields(profile),
        profile_json=json.dumps(documents.to_json(profile), indent=2),
        test_snapshot=None,
        test_document=None,
        proposed_by=None,
        activated_at=None,
        ended_at=None,
        ended_reason=None,
    )


def _row_detail(session: Session, row: DocumentProfile) -> ProfileDetailOut:
    profile, _ = _row_profile(row)
    document = library.document_by_sha256(session, row.test_document_sha256)
    return ProfileDetailOut(
        **_row_summary(row).model_dump(),
        **_detail_fields(profile),
        profile_json=json.dumps(row.profile_json, indent=2),
        test_snapshot=SnapshotOut(**row.test_snapshot),
        test_document=(
            ProfileDocumentOut(id=document.id, file_name=document.file_name)
            if document is not None
            else None
        ),
        proposed_by=row.proposed_by,
        activated_at=_utc(row.activated_at),
        ended_at=_utc(row.ended_at),
        ended_reason=row.ended_reason,
    )


def _test_out(tested: library.ProfileTest, summary: ProfileSummaryOut) -> ProfileTestOut:
    return ProfileTestOut(
        profile=summary,
        reading=reading_out(tested.reading),
        acceptable=tested.acceptable,
        also_claimed_by=list(tested.also_claimed_by),
    )


def _draft_summary(profile: Profile) -> ProfileSummaryOut:
    """A profile under test: not saved, so it has no row and no status yet."""
    return ProfileSummaryOut(
        id=None,
        source="pasted",
        status="draft",
        overrides_shipped=documents.shipped_profile(profile.key) is not None,
        overridden=False,
        failure=None,
        created_at=None,
        **_facts(profile),
    )


@router.get(
    "",
    response_model=list[ProfileSummaryOut],
    operation_id="listDocumentProfiles",
    responses=ERRORS,  # type: ignore[arg-type]
)
def list_profiles(session: SessionDep) -> list[ProfileSummaryOut]:
    """Every profile: the shipped ones, then the saved ones, the newest first."""
    return [
        *(_shipped_summary(p) for p in documents.shipped_profiles()),
        *(_row_summary(r) for r in store.list_rows(session)),
    ]


@router.get(
    "/shipped/{key}",
    response_model=ProfileDetailOut,
    operation_id="getShippedDocumentProfile",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_shipped(key: str) -> ProfileDetailOut:
    profile = documents.shipped_profile(key)
    if profile is None:
        raise NotFoundError(f"No shipped Document profile '{key}'.")
    return _shipped_detail(profile)


@router.post(
    "/test",
    response_model=ProfileTestOut,
    operation_id="testDocumentProfile",
    responses=ERRORS,  # type: ignore[arg-type]
)
def test_profile(body: ProfileTestIn, session: SessionDep, data_dir: DataDirDep) -> ProfileTestOut:
    """Validate a pasted draft and read a Library document with it. Nothing is stored."""
    tested = library.test_profile_on_document(
        session, data_dir, body.profile_json, body.document_id
    )
    return _test_out(tested, _draft_summary(tested.profile))


@router.post(
    "",
    status_code=201,
    response_model=ProfileDetailOut,
    operation_id="saveDocumentProfile",
    responses=ERRORS,  # type: ignore[arg-type]
)
def save_proposed(
    body: ProfileTestIn, session: SessionDep, ctx: ContextDep, data_dir: DataDirDep
) -> ProfileDetailOut:
    """Test a pasted draft and save it as a Proposed profile (inactive). Refused unless it reads
    the document."""
    row, _ = library.propose_profile(
        session, ctx, data_dir, body.profile_json, body.document_id, source="pasted"
    )
    return _row_detail(session, row)


@router.get(
    "/{profile_id}",
    response_model=ProfileDetailOut,
    operation_id="getDocumentProfile",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_profile(profile_id: int, session: SessionDep) -> ProfileDetailOut:
    return _row_detail(session, store.get(session, profile_id))


@router.get(
    "/{profile_id}/review",
    response_model=ProfileTestOut,
    operation_id="reviewDocumentProfile",
    responses=ERRORS,  # type: ignore[arg-type]
)
def review(profile_id: int, session: SessionDep, data_dir: DataDirDep) -> ProfileTestOut:
    """The saved profile re-run, live, over the document it was tested on. Nothing is stored."""
    row = store.get(session, profile_id)
    tested = library.review_profile(session, data_dir, row)
    return _test_out(tested, _row_summary(row))


@router.post(
    "/{profile_id}/activate",
    response_model=ProfileActivatedOut,
    operation_id="activateDocumentProfile",
    responses=ERRORS,  # type: ignore[arg-type]
)
def activate(
    profile_id: int, session: SessionDep, ctx: ContextDep, data_dir: DataDirDep
) -> ProfileActivatedOut:
    """Switch a Proposed profile on, after re-testing it on its document (refused unless it still
    reads it). Another profile claiming the document is a warning, not a refusal."""
    tested = library.activate_profile(session, ctx, data_dir, profile_id)
    row = store.get(session, profile_id)
    return ProfileActivatedOut(
        profile=_row_detail(session, row), also_claimed_by=list(tested.also_claimed_by)
    )


@router.post(
    "/{profile_id}/retire",
    response_model=ProfileDetailOut,
    operation_id="retireDocumentProfile",
    responses=ERRORS,  # type: ignore[arg-type]
)
def retire(profile_id: int, session: SessionDep, ctx: ContextDep) -> ProfileDetailOut:
    """Retire a proposed or active profile. An active one stops reading at once, and the shipped
    profile with its key, if any, reads again."""
    row = store.get(session, profile_id)
    store.retire(session, ctx, row)
    return _row_detail(session, row)
