"""Account identifier service (ADR-0013 section 5a).

Placement: ADR-0013 section 5a sits inside the Import flow and the identifiers exist to resolve an
Account at staging, so the service lives in ``imports``, which may not import ``position``. It
reads the Account table model from ``core``.

Identifiers are sensitive real values. The only function that returns one unmasked is ``reveal``;
audit snapshots store the masked form; no error message or summary contains a value.

A change supersedes (the mixin); a removal supersedes with no replacement. The caller owns the
transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance360.capabilities.imports.importer import AccountReference
from finance360.core import audit
from finance360.core.account_identifier import (
    INSTITUTION_MAX,
    KINDS,
    VALUE_MAX,
    mask_value,
    normalise_institution,
    normalise_value,
    tidy_value,
)
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.account import Account
from finance360.core.models.account_identifier import AccountIdentifier

#: Same file as ``household.FIXTURE_FILENAME``; ``imports`` does not import ``household``.
FIXTURE_FILENAME = "household.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")

_CLASH = "That identifier is already recorded for {where}. Each identifier names one Account."


@dataclass(frozen=True)
class IdentifierInput:
    kind: str
    value: str
    institution: str | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def snapshot(ident: AccountIdentifier) -> dict[str, Any]:
    """The audit snapshot: the value is **masked**, never the real thing."""
    return {
        "id": ident.id,
        "account_id": ident.account_id,
        "kind": ident.kind,
        "value": mask_value(ident.value),
        "institution": ident.institution,
    }


def _account(session: Session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise NotFoundError(f"Account {account_id} does not exist.")
    return account


def _current(account_id: int | None = None) -> Any:
    query = select(AccountIdentifier).where(AccountIdentifier.superseded_at.is_(None))
    if account_id is not None:
        query = query.where(AccountIdentifier.account_id == account_id)
    return query.order_by(AccountIdentifier.id)


# --- reads ---------------------------------------------------------------------------------


def list_identifiers(session: Session, account_id: int) -> list[AccountIdentifier]:
    _account(session, account_id)
    return list(session.scalars(_current(account_id)))


def _owned(session: Session, account_id: int, identifier_id: int) -> AccountIdentifier:
    ident = session.get(AccountIdentifier, identifier_id)
    if ident is None or ident.account_id != account_id or ident.superseded_at is not None:
        raise NotFoundError(f"Identifier {identifier_id} does not belong to Account {account_id}.")
    return ident


def reveal(session: Session, account_id: int, identifier_id: int) -> str:
    """The unmasked value. The one deliberate way out, behind its own explicit call."""
    return _owned(session, account_id, identifier_id).value


def find_accounts(session: Session, ref: AccountReference) -> list[Account]:
    """The Accounts a file's reference resolves to: none, one, or several (never a guess)."""
    query = _current().where(
        AccountIdentifier.kind == ref.kind,
        AccountIdentifier.value_norm == normalise_value(ref.value),
    )
    if ref.institution:
        wanted = normalise_institution(ref.institution)
        query = query.where(AccountIdentifier.institution_norm.in_(("", wanted)))
    ids = sorted({i.account_id for i in session.scalars(query)})
    return [a for a in (session.get(Account, i) for i in ids) if a is not None]


# --- mutations -----------------------------------------------------------------------------


def _check(data: IdentifierInput) -> tuple[str, str, str | None]:
    errors: list[FieldError] = []
    kind = data.kind.strip().lower()
    if kind not in KINDS:
        errors.append(FieldError("kind", f"Choose one of: {', '.join(KINDS)}."))
    value = tidy_value(data.value)
    if not value:
        errors.append(FieldError("value", "Enter the identifier."))
    elif len(value) > VALUE_MAX:
        errors.append(FieldError("value", f"At most {VALUE_MAX} characters."))
    institution = " ".join((data.institution or "").split()) or None
    if institution is not None and len(institution) > INSTITUTION_MAX:
        errors.append(FieldError("institution", f"At most {INSTITUTION_MAX} characters."))
    if errors:
        raise ValidationError(errors)
    return kind, value, institution


def _clash(session: Session, kind: str, value: str, institution: str | None) -> AccountIdentifier:
    return session.scalars(
        _current().where(
            AccountIdentifier.kind == kind,
            AccountIdentifier.value_norm == normalise_value(value),
            AccountIdentifier.institution_norm == normalise_institution(institution),
        )
    ).one()


def _clash_error(session: Session, account_id: int, other: AccountIdentifier) -> ValidationError:
    if other.account_id == account_id:
        where = "this Account"
    else:
        found = session.get(Account, other.account_id)
        where = found.name if found else "another Account"
    return ValidationError([FieldError("value", _CLASH.format(where=where))])


def add_identifier(
    session: Session, context: AuditContext, account_id: int, data: IdentifierInput
) -> AccountIdentifier:
    account = _account(session, account_id)
    kind, value, institution = _check(data)
    existing = session.scalars(
        _current().where(
            AccountIdentifier.kind == kind,
            AccountIdentifier.value_norm == normalise_value(value),
            AccountIdentifier.institution_norm == normalise_institution(institution),
        )
    ).first()
    if existing is not None:
        raise _clash_error(session, account_id, existing)
    ident = AccountIdentifier(
        account_id=account_id,
        kind=kind,
        value=value,
        value_norm=normalise_value(value),
        institution=institution,
        institution_norm=normalise_institution(institution),
        recorded_at=_now(),
    )
    try:
        with session.begin_nested():
            session.add(ident)
    except IntegrityError:  # a concurrent add won the race: the database is the arbiter
        raise _clash_error(session, account_id, _clash(session, kind, value, institution)) from None
    audit.record(
        session,
        context,
        entity_type="account_identifier",
        entity_id=ident.id,
        action="create",
        after=snapshot(ident),
        summary=f"Added {kind} {mask_value(value)} to {account.name}",
    )
    return ident


def remove_identifier(
    session: Session, context: AuditContext, account_id: int, identifier_id: int
) -> None:
    """Supersede with no replacement. The row stays (append-only); it stops matching."""
    account = _account(session, account_id)
    ident = _owned(session, account_id, identifier_id)
    before = snapshot(ident)
    ident.superseded_at = _now()
    session.flush()
    audit.record(
        session,
        context,
        entity_type="account_identifier",
        entity_id=ident.id,
        action="delete",
        before=before,
        summary=f"Removed {ident.kind} {mask_value(ident.value)} from {account.name}",
    )


# --- First run -----------------------------------------------------------------------------


def load_fixture_account_identifiers(session: Session, data_dir: Path) -> int:
    """Add the fixture Accounts' synthetic placeholder identifiers, once, while there are none.

    Reads ``identifiers`` on each entry of ``accounts`` in ``<data_dir>/household.json``.
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(func.count()).select_from(AccountIdentifier)):
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("accounts", [])
    by_name = {a.name: a.id for a in session.scalars(select(Account))}
    count = 0
    for entry in entries:
        for item in entry.get("identifiers", []):
            if entry["name"] in by_name:
                add_identifier(
                    session,
                    FIXTURE_CONTEXT,
                    by_name[entry["name"]],
                    IdentifierInput(item["kind"], item["value"], item.get("institution")),
                )
                count += 1
    return count
