"""Testing, saving and activating Document profiles over Library documents (ADR-0027 sections 2
and 5).

The Library sits above Document intake, so the steps that need both live here, in their own module
so that nothing else in the Library (and so nothing in tax or position, which reach the Library) can
reach Document intake through it. The API calls this module as it calls ``library.service``.

A Reading is returned to the caller and forgotten; only ``documents.store.snapshot_of`` (no values)
is ever saved.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.documents import loader, matcher, store
from finance360.capabilities.documents import registry as document_registry
from finance360.capabilities.documents import service as documents_service
from finance360.capabilities.documents.model import Document
from finance360.capabilities.documents.profile import Profile, ProfileError
from finance360.capabilities.documents.reading import Reading
from finance360.capabilities.library.service import (
    FIXTURE_CONTEXT,
    FIXTURE_FILENAME,
    DocumentFileError,
    add_document,
    document_file,
    get_document,
)
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, ValidationError
from finance360.core.models.document_profile import DocumentProfile
from finance360.core.models.library import LibraryDocument

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProfileTest:
    """A profile read over one Library document, for the screen: the Reading (with figures, to
    show beside the document), whether it is good enough to save or activate, and which other
    registered profiles for the same target also claim the document."""

    profile: Profile
    raw: dict[str, Any]
    reading: Reading
    document: LibraryDocument
    acceptable: bool
    also_claimed_by: tuple[str, ...]


def document_by_sha256(session: Session, sha256: str) -> LibraryDocument | None:
    return session.scalars(
        select(LibraryDocument).where(LibraryDocument.sha256 == sha256)
    ).one_or_none()


def _load_for_reading(session: Session, data_dir: Path, document: LibraryDocument) -> Document:
    """The document as the matcher reads it. Raises ``ValidationError`` on field ``document``
    with a message that holds nothing from the file."""
    try:
        _, content = document_file(session, data_dir, document.id)
    except DocumentFileError as problem:
        raise ValidationError([FieldError("document", str(problem))]) from None
    try:
        if document.media_type == "application/pdf":
            return loader.load_pdf(content)
        if document.media_type in ("text/plain", "text/csv"):
            return loader.load_text(content.decode("utf-8", errors="replace"))
    except loader.LoadError as problem:
        raise ValidationError([FieldError("document", problem.message)]) from None
    raise ValidationError([FieldError("document", "This document has no text to read.")])


def _test(
    session: Session,
    data_dir: Path,
    profile: Profile,
    raw: dict[str, Any],
    document: LibraryDocument,
) -> ProfileTest:
    loaded = _load_for_reading(session, data_dir, document)
    reading = documents_service.read_with_profile(loaded, profile)
    others = tuple(
        p.key
        for p in document_registry.for_target(profile.target)
        if p.key != profile.key and matcher.claims(loaded, p)
    )
    return ProfileTest(profile, raw, reading, document, store.acceptable(reading), others)


def test_profile_on_document(
    session: Session, data_dir: Path, profile_text: str, document_id: int
) -> ProfileTest:
    """Validate the draft JSON and read the document with it. Stores nothing."""
    profile, raw = store.parse_profile(profile_text)
    return _test(session, data_dir, profile, raw, get_document(session, document_id))


def propose_profile(
    session: Session,
    context: AuditContext,
    data_dir: Path,
    profile_text: str,
    document_id: int,
    *,
    source: str = "pasted",
) -> tuple[DocumentProfile, ProfileTest]:
    """Test the draft as ``test_profile_on_document`` does, then save it as a Proposed profile
    (refused unless it read the document). The Reading itself is not stored."""
    tested = test_profile_on_document(session, data_dir, profile_text, document_id)
    row = store.propose(
        session,
        context,
        profile=tested.profile,
        raw=tested.raw,
        source=source,
        document_id=tested.document.id,
        document_sha256=tested.document.sha256,
        reading=tested.reading,
    )
    return row, tested


def review_profile(session: Session, data_dir: Path, row: DocumentProfile) -> ProfileTest:
    """Re-run a saved profile over its test document, live. Raises ``ValidationError`` when the
    row no longer validates or its test document is no longer in the Library."""
    try:
        profile = store.profile_of(row)
    except ProfileError as problem:
        raise ValidationError([FieldError("profile", str(problem))]) from None
    document = document_by_sha256(session, row.test_document_sha256)
    if document is None:
        raise ValidationError(
            [FieldError("document", "The document it was tested on is not in the Library.")]
        )
    return _test(session, data_dir, profile, row.profile_json, document)


def activate_profile(
    session: Session, context: AuditContext, data_dir: Path, profile_id: int
) -> ProfileTest:
    """Activate a Proposed profile, only if it still reads its test document (``ok``, or
    ``warnings`` with every required figure found). Another profile claiming the document is
    returned as a warning, not a refusal (the first claiming key reads)."""
    row = store.get(session, profile_id)
    if store.status_of(row) != "proposed":
        raise ValidationError([FieldError("status", "Only a proposed profile can be activated.")])
    tested = review_profile(session, data_dir, row)
    if not tested.acceptable:
        raise ValidationError(
            [
                FieldError(
                    "profile",
                    "This profile no longer reads its test document, so it was not activated.",
                )
            ]
        )
    store.activate(session, context, row)
    return tested


def load_fixture_document_profiles(session: Session, data_dir: Path) -> int:
    """Save the synthetic household's Proposed profiles, once, while none is saved.

    Each entry gives a profile file and the Library document to test it on (added to the Library
    if it is not there, like an upload). It goes through ``propose_profile``, so it is validated and
    tested exactly as a pasted one is; an entry that does not pass is logged and skipped."""
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file():
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("document_profiles", [])
    if not entries or session.scalar(select(func.count()).select_from(DocumentProfile)):
        return 0
    done = 0
    for e in entries:
        profile_file, test_file = data_dir / e["file"], data_dir / e["test_document"]
        if not (profile_file.is_file() and test_file.is_file()):
            continue
        added = add_document(
            session,
            FIXTURE_CONTEXT,
            data_dir,
            file_name=test_file.name,
            media_type=e["media_type"],
            content=test_file.read_bytes(),
        )
        try:
            propose_profile(
                session,
                FIXTURE_CONTEXT,
                data_dir,
                profile_file.read_text(encoding="utf-8"),
                added.document.id,
            )
        except ValidationError as problem:
            log.warning("Fixture Document profile %r was not saved: %s", e["file"], problem)
            continue
        done += 1
    return done
