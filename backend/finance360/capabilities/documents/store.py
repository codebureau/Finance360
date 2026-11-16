"""Saved Document profiles: the store, activation and runtime loading (ADR-0027 section 5).

A profile saved in the instance is a ``document_profile`` row (append-only; its status is derived
from set-once timestamps). **Shipped profiles are not rows.** This module owns the lifecycle and
keeps the running process's registry in step with the database:

- ``propose`` validates and stores a **Proposed profile**; it is inactive and reads nothing.
- ``activate`` (owner only; the caller has already re-tested it) ends the key's previously active
  row and activates this one. ``retire`` ends a proposed or active row. The registry changes only
  after the transaction commits, so it never shows a change the database lost.
- ``load_active`` runs at start-up: it registers every active row after the shipped profiles. A row
  that fails to validate or register is **skipped, logged by key and reason only, and recorded for
  Settings as "failed to load"**. Nothing about a saved row can stop the app starting.

The Library supplies the test document, so the orchestration that reads it (test, propose,
activate) is in ``capabilities.library.service``, which sits above this package.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from finance360.capabilities.documents import registry
from finance360.capabilities.documents.profile import Profile, ProfileError, load_profile
from finance360.capabilities.documents.reading import (
    AMBIGUOUS,
    OK,
    WARNINGS,
    Reading,
)
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.document_profile import ACTIVE, PROPOSED, RETIRED, DocumentProfile

log = logging.getLogger(__name__)

#: The most JSON text a pasted profile may be. Far above any real profile; keeps a paste bounded.
MAX_JSON = 200_000
REPLACED, RETIRED_BY_OWNER = "replaced", "retired"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as every timestamp here


def status_of(row: DocumentProfile) -> str:
    """``proposed``, ``active`` or ``retired``, derived from the set-once timestamps."""
    if row.ended_at is not None:
        return RETIRED
    return ACTIVE if row.activated_at is not None else PROPOSED


# --- validation ------------------------------------------------------------------------------


def parse_profile(text: str) -> tuple[Profile, dict[str, Any]]:
    """The draft JSON text as a validated Profile (schema, the no-digit-run rule, the Fill target's
    fields and kinds) and its parsed JSON. Raises ``ValidationError`` on field ``profile``; the
    message names a key or path, never anything the text said."""
    if len(text) > MAX_JSON:
        raise ValidationError([FieldError("profile", "The profile is too long.")])
    try:
        raw = json.loads(text)
    except ValueError:
        raise ValidationError([FieldError("profile", "The profile is not valid JSON.")]) from None
    profile = validate_json(raw)
    return profile, raw


def validate_json(raw: object) -> Profile:
    """Validate parsed JSON as ``parse_profile`` does. Raises ``ValidationError``."""
    try:
        profile = load_profile(raw)
        registry.validate(profile)
    except ProfileError as problem:
        raise ValidationError([FieldError("profile", str(problem))]) from None
    return profile


def acceptable(reading: Reading) -> bool:
    """A Reading good enough to save or activate a profile: ``ok``, or ``warnings`` (which already
    means every required figure was found; a missing one is ``needs_repair``)."""
    return reading.outcome in (OK, WARNINGS)


def snapshot_of(reading: Reading) -> dict[str, Any]:
    """What a test leaves behind: the outcome, each field's state and each check's result.

    **No value, no text from the document**: not a figure, not the matched label, not a check
    message. A Reading is never stored (CONTEXT.md); this records only that the profile worked."""
    fields: list[dict[str, Any]] = [
        {"key": f.key, "state": "found" if f.how == "read" else "absent", "page": f.page}
        for f in reading.found
    ]
    for n in reading.not_found:
        state = "ambiguous" if n.reason == AMBIGUOUS else "not_found"
        fields.append(
            {
                "key": n.key,
                "state": state,
                "reason": n.reason,
                "required": n.required,
            }
        )
    return {
        "outcome": reading.outcome,
        "fields": fields,
        "checks": [
            {"kind": c.kind, "status": c.status, "fields": list(c.fields)} for c in reading.checks
        ],
    }


# --- reading rows ----------------------------------------------------------------------------


def get(session: Session, profile_id: int) -> DocumentProfile:
    row = session.get(DocumentProfile, profile_id)
    if row is None:
        raise NotFoundError(f"Document profile {profile_id} is not saved.")
    return row


def list_rows(session: Session) -> list[DocumentProfile]:
    """Every saved row, the newest first."""
    return list(
        session.scalars(
            select(DocumentProfile).order_by(
                DocumentProfile.created_at.desc(), DocumentProfile.id.desc()
            )
        )
    )


def active_row(session: Session, key: str) -> DocumentProfile | None:
    return session.scalars(
        select(DocumentProfile).where(
            DocumentProfile.key == key,
            DocumentProfile.activated_at.is_not(None),
            DocumentProfile.ended_at.is_(None),
        )
    ).one_or_none()


def profile_of(row: DocumentProfile) -> Profile:
    """The row's profile, validated again (it may predate a change to the schema or its target).
    Raises ``ProfileError``."""
    profile = load_profile(row.profile_json)
    registry.validate(profile)
    if (profile.key, profile.version, profile.target) != (row.key, row.version, row.target):
        raise ProfileError("The saved profile does not match its own key, version and target.")
    return profile


# --- writes ----------------------------------------------------------------------------------


def _summary(row: DocumentProfile) -> dict[str, Any]:
    return {
        "key": row.key,
        "version": row.version,
        "target": row.target,
        "source": row.source,
        "status": status_of(row),
    }


def propose(
    session: Session,
    context: AuditContext,
    *,
    profile: Profile,
    raw: dict[str, Any],
    source: str,
    document_id: int,
    document_sha256: str,
    reading: Reading,
    token_id: int | None = None,
) -> DocumentProfile:
    """Save a validated profile as a **Proposed profile**: inactive, with its test snapshot.

    Refused unless the Reading is acceptable (ADR-0027 section 5), or when this key and version
    are already saved (a revision is a new version). Audited."""
    if not acceptable(reading):
        raise ValidationError(
            [FieldError("profile", "The profile did not read the document, so it was not saved.")]
        )
    if session.scalars(
        select(DocumentProfile.id).where(
            DocumentProfile.key == profile.key, DocumentProfile.version == profile.version
        )
    ).first():
        raise ValidationError(
            [
                FieldError(
                    "profile",
                    f"Version {profile.version} of '{profile.key}' is already saved; "
                    "raise the version number.",
                )
            ]
        )
    row = DocumentProfile(
        key=profile.key,
        version=profile.version,
        target=profile.target,
        profile_json=raw,
        source=source,
        proposed_by=context.actor_ref,
        proposed_token_id=token_id,
        created_at=_now(),
        test_document_id=document_id,
        test_document_sha256=document_sha256,
        test_snapshot=snapshot_of(reading),
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="document_profile",
        entity_id=row.id,
        action="create",
        summary=f"Proposed Document profile {row.key} v{row.version} ({source})",
        after=_summary(row),
    )
    return row


def _after_commit(session: Session, change: Callable[[], None]) -> None:
    """Run ``change`` (a registry update) once this transaction has committed."""

    def run(_session: Session) -> None:
        change()

    event.listen(session, "after_commit", run, once=True)


def activate(session: Session, context: AuditContext, row: DocumentProfile) -> Profile:
    """Switch a Proposed profile on. The caller has re-tested it. Ends the key's previously
    active saved row (``replaced``), then activates this one; the registry follows the commit.
    Raises ``ValidationError`` when it is not proposed or no longer validates."""
    if status_of(row) != PROPOSED:
        raise ValidationError([FieldError("status", "Only a proposed profile can be activated.")])
    try:
        profile = profile_of(row)
    except ProfileError as problem:
        raise ValidationError([FieldError("profile", str(problem))]) from None
    now, who = _now(), context.actor_ref
    previous = active_row(session, row.key)
    if previous is not None:
        before = _summary(previous)
        previous.ended_at, previous.ended_by, previous.ended_reason = now, who, REPLACED
        session.flush()
        audit.record(
            session,
            context,
            entity_type="document_profile",
            entity_id=previous.id,
            action="supersede",
            summary=f"Replaced Document profile {previous.key} v{previous.version} "
            f"with v{row.version}",
            before=before,
            after=_summary(previous),
        )
    before = _summary(row)
    row.activated_at, row.activated_by = now, who
    session.flush()
    audit.record(
        session,
        context,
        entity_type="document_profile",
        entity_id=row.id,
        action="update",
        summary=f"Activated Document profile {row.key} v{row.version}",
        before=before,
        after=_summary(row),
    )
    row_id = row.id

    def register() -> None:
        registry.register_saved(profile)
        registry.clear_failed(row_id)

    _after_commit(session, register)
    return profile


def retire(session: Session, context: AuditContext, row: DocumentProfile) -> None:
    """End a proposed or active profile. Retiring an active one unregisters it from the running
    process (and the shipped profile of that key, if there is one, reads again)."""
    state = status_of(row)
    if state == RETIRED:
        raise ValidationError([FieldError("status", "This profile is already retired.")])
    before = _summary(row)
    row.ended_at, row.ended_by, row.ended_reason = _now(), context.actor_ref, RETIRED_BY_OWNER
    session.flush()
    audit.record(
        session,
        context,
        entity_type="document_profile",
        entity_id=row.id,
        action="close",
        summary=f"Retired Document profile {row.key} v{row.version}",
        before=before,
        after=_summary(row),
    )
    if state == ACTIVE:
        key, row_id = row.key, row.id

        def unregister() -> None:
            registry.unregister_saved(key)
            registry.clear_failed(row_id)

        _after_commit(session, unregister)


# --- start-up --------------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadReport:
    loaded: tuple[tuple[int, str], ...]  # (row id, key)
    failed: tuple[tuple[int, str, str], ...]  # (row id, key, reason)


def load_active(session: Session) -> LoadReport:
    """Register every active saved profile after the shipped ones; skip and record any that fail.

    **Never raises for a row**: a corrupt or invalid profile, a target that no longer exists, or a
    row the database cannot even decode is logged by id, key and reason only and shown in Settings
    as failed to load; every other profile still loads and the app still starts."""
    registry.clear_saved()
    loaded: list[tuple[int, str]] = []
    failed: list[tuple[int, str, str]] = []
    # Plain columns only: a row is then identified even if its JSON cannot be decoded below.
    heads = session.execute(
        select(DocumentProfile.id, DocumentProfile.key)
        .where(DocumentProfile.activated_at.is_not(None), DocumentProfile.ended_at.is_(None))
        .order_by(DocumentProfile.key, DocumentProfile.id)
    ).all()
    for row_id, key in heads:
        try:
            row = session.get(DocumentProfile, row_id)
            if row is None:  # cannot happen: the rows are append-only
                continue
            registry.register_saved(profile_of(row))
            loaded.append((row_id, key))
        except Exception as problem:  # noqa: BLE001 - contain anything a stored row can do
            reason = reason_of(problem)
            registry.mark_failed(row_id, key, reason)
            failed.append((row_id, key, reason))
            log.warning("Document profile %r (row %d) failed to load: %s", key, row_id, reason)
    return LoadReport(tuple(loaded), tuple(failed))


def reason_of(problem: Exception) -> str:
    """Why a row failed, safe to show: a profile error's own message names keys and paths only;
    anything else is reduced to its type, since its text could echo stored content."""
    if isinstance(problem, ProfileError):
        return str(problem)
    return f"The saved profile could not be read ({type(problem).__name__})."
