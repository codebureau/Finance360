"""Valuation services (ADR-0009 section 3).

Valuations are append-only. A correction inserts a new row and supersedes the old one; a removal
supersedes with no replacement; nothing is updated or deleted otherwise. Every mutation writes its
audit record in the caller's session (the caller owns the transaction), like the other services.

This module deliberately does not import ``accounts``: ``accounts`` calls into it for the closing
rule, so the dependency runs one way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.account import Account
from finance360.core.models.valuation import Valuation
from finance360.core.money import Money, MoneyError, format_amount, parse_amount
from finance360.core.valuation import (
    ENTERED_SOURCES,
    NoValuation,
    ValuationPoint,
    best_on_or_before,
)

NOTE_MAX = 2000
CLOSING_NOTE = "Account closed"
#: Matches the Member date-of-birth guard.
EARLIEST_AS_AT = date(1900, 1, 1)

FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


@dataclass(frozen=True)
class ValuationInput:
    #: ``None`` means today in the Household's time zone.
    as_at: date | None
    #: A plain decimal string in the Account's currency, e.g. ``"1234.56"``. Never a float.
    amount: str
    source: str
    provenance_note: str | None = None
    #: Optional; if given it must equal the Account's currency.
    currency: str | None = None


# --- reads ---------------------------------------------------------------------------------


def get_valuation(session: Session, valuation_id: int) -> Valuation:
    valuation = session.get(Valuation, valuation_id)
    if valuation is None:
        raise NotFoundError(f"Valuation {valuation_id} does not exist.")
    return valuation


def _account(session: Session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise NotFoundError(f"Account {account_id} does not exist.")
    return account


def current_valuations(session: Session, account_id: int) -> list[Valuation]:
    """The Account's current (non-superseded) Valuations, newest first."""
    query = (
        select(Valuation)
        .where(Valuation.account_id == account_id, Valuation.superseded_at.is_(None))
        .order_by(Valuation.as_at.desc(), Valuation.id.desc())
    )
    return list(session.scalars(query))


def list_valuations(session: Session, account_id: int) -> list[Valuation]:
    """The Account's whole history, superseded rows included: by date, current before superseded."""
    _account(session, account_id)
    query = (
        select(Valuation)
        .where(Valuation.account_id == account_id)
        .order_by(
            Valuation.as_at.desc(),
            Valuation.superseded_at.is_not(None),
            Valuation.recorded_at.desc(),
            Valuation.id.desc(),
        )
    )
    return list(session.scalars(query))


def has_current_after(session: Session, account_id: int, on: date) -> bool:
    """Whether the Account has a current Valuation dated after ``on``."""
    return (
        session.scalar(
            select(Valuation.id)
            .where(
                Valuation.account_id == account_id,
                Valuation.superseded_at.is_(None),
                Valuation.as_at > on,
            )
            .limit(1)
        )
        is not None
    )


def point(valuation: Valuation) -> ValuationPoint:
    return ValuationPoint(valuation.id, valuation.as_at, valuation.source, valuation.amount)


def best_valuation(session: Session, account_id: int, on: date) -> ValuationPoint | NoValuation:
    """The best current Valuation on or before ``on``, or an explicit ``NoValuation``."""
    return best_on_or_before(map(point, current_valuations(session, account_id)), on)


def has_valuations(session: Session, account_id: int) -> bool:
    count = session.scalar(
        select(func.count()).select_from(Valuation).where(Valuation.account_id == account_id)
    )
    return bool(count)


# --- audit snapshots -----------------------------------------------------------------------


def _snapshot(valuation: Valuation) -> dict[str, Any]:
    return {
        "id": valuation.id,
        "account_id": valuation.account_id,
        "as_at": valuation.as_at.isoformat(),
        "amount": {"minor": valuation.amount.amount_minor, "ccy": valuation.amount.currency},
        "source": valuation.source,
        "provenance_note": valuation.provenance_note,
    }


def _describe(account: Account, valuation: Valuation) -> str:
    amount = f"{valuation.amount.currency} {format_amount(valuation.amount)}"
    return f"{valuation.source} Valuation of '{account.name}' at {valuation.as_at}: {amount}"


# --- validation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Checked:
    as_at: date
    amount: Money
    source: str
    note: str | None


def _check(
    session: Session, account: Account, data: ValuationInput, today: date | None
) -> _Checked:
    errors: list[FieldError] = []
    today = today or household.household_today(session)

    as_at = data.as_at or today
    if as_at < EARLIEST_AS_AT:
        errors.append(FieldError("as_at", f"The date must be on or after {EARLIEST_AS_AT}."))
    elif as_at > today:
        errors.append(FieldError("as_at", f"The date can't be after today ({today})."))
    elif account.closed_on and as_at > account.closed_on:
        errors.append(
            FieldError(
                "as_at", f"The Account closed on {account.closed_on}; use a date up to then."
            )
        )

    if data.source not in ENTERED_SOURCES:
        errors.append(FieldError("source", "Choose manual, statement or estimate."))

    note = (data.provenance_note or "").strip() or None
    if note is None:
        errors.append(FieldError("provenance_note", "Say where this figure came from."))
    elif len(note) > NOTE_MAX:
        errors.append(
            FieldError("provenance_note", f"The note must be at most {NOTE_MAX} characters.")
        )

    amount = Money.zero(account.currency)
    if data.currency is not None and data.currency.strip().upper() != account.currency:
        errors.append(
            FieldError("currency", f"This Account is in {account.currency}; so is every Valuation.")
        )
    try:
        amount = parse_amount(data.amount, account.currency)
    except MoneyError:
        errors.append(
            FieldError(
                "amount",
                f"Enter an amount in {account.currency}, e.g. 1234.56 (no more "
                "decimal places than the currency has).",
            )
        )
    if errors:
        raise ValidationError(errors)
    return _Checked(as_at, amount, data.source, note)


def _ensure_no_current(
    session: Session, account_id: int, as_at: date, source: str, *, except_id: int | None = None
) -> None:
    clash = session.scalar(
        select(Valuation.id).where(
            Valuation.account_id == account_id,
            Valuation.as_at == as_at,
            Valuation.source == source,
            Valuation.superseded_at.is_(None),
            Valuation.id != (except_id or 0),
        )
    )
    if clash is not None:
        raise ValidationError(
            [
                FieldError(
                    "as_at",
                    f"There is already a {source} Valuation for {as_at}. Correct that one instead.",
                )
            ]
        )


# --- mutations -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as the audit trail stores it


def _insert(
    session: Session,
    account: Account,
    as_at: date,
    amount: Money,
    source: str,
    note: str | None,
) -> Valuation:
    valuation = Valuation(
        account_id=account.id,
        as_at=as_at,
        amount=amount,
        source=source,
        provenance_note=note,
        recorded_at=_now(),
    )
    session.add(valuation)
    session.flush()
    return valuation


def _supersede(session: Session, old: Valuation, replacement_id: int | None) -> None:
    old.superseded_at = _now()
    old.superseded_by_id = replacement_id
    session.flush()


def add_valuation(
    session: Session,
    context: AuditContext,
    account_id: int,
    data: ValuationInput,
    *,
    today: date | None = None,
) -> Valuation:
    account = _account(session, account_id)
    checked = _check(session, account, data, today)
    _ensure_no_current(session, account.id, checked.as_at, checked.source)
    valuation = _insert(
        session, account, checked.as_at, checked.amount, checked.source, checked.note
    )
    audit.record(
        session,
        context,
        entity_type="valuation",
        entity_id=valuation.id,
        action="create",
        after=_snapshot(valuation),
        summary=f"Added {_describe(account, valuation)}",
    )
    return valuation


def correct_valuation(
    session: Session,
    context: AuditContext,
    valuation_id: int,
    data: ValuationInput,
    *,
    today: date | None = None,
) -> Valuation:
    """Replace a current Valuation: insert the corrected row, supersede the old one (kept)."""
    old = get_valuation(session, valuation_id)
    if old.superseded_at is not None:
        raise ValidationError([FieldError("id", "Only a current Valuation can be corrected.")])
    account = _account(session, old.account_id)
    checked = _check(session, account, data, today)
    unchanged = (
        checked.as_at == old.as_at
        and checked.amount == old.amount
        and checked.source == old.source
        and checked.note == old.provenance_note
    )
    if unchanged:
        return old
    _ensure_no_current(session, account.id, checked.as_at, checked.source, except_id=old.id)
    before = _snapshot(old)
    _supersede(session, old, None)  # free the unique slot first; link once the new row exists
    new = _insert(session, account, checked.as_at, checked.amount, checked.source, checked.note)
    old.superseded_by_id = new.id
    session.flush()
    audit.record(
        session,
        context,
        entity_type="valuation",
        entity_id=new.id,
        action="supersede",
        before=before,
        after=_snapshot(new),
        summary=f"Corrected {_describe(account, new)} (was {format_amount(old.amount)})",
    )
    return new


def remove_valuation(session: Session, context: AuditContext, valuation_id: int) -> Valuation:
    """Supersede a current Valuation with no replacement. The row stays, marked superseded."""
    old = get_valuation(session, valuation_id)
    if old.superseded_at is not None:
        raise ValidationError([FieldError("id", "Only a current Valuation can be removed.")])
    account = _account(session, old.account_id)
    before = _snapshot(old)
    _supersede(session, old, None)
    audit.record(
        session,
        context,
        entity_type="valuation",
        entity_id=old.id,
        action="supersede",
        before=before,
        summary=f"Removed {_describe(account, old)}",
    )
    return old


def write_closing_valuation(
    session: Session, context: AuditContext, account: Account, closed_on: date
) -> Valuation:
    """The final zero Valuation on ``closed_on`` (source manual, note "Account closed").

    A manual Valuation already current on that date is superseded by it, so the unique slot is
    never contested. Bypasses the "not after today" check: a close date may be set ahead.
    """
    zero = Money.zero(account.currency)
    existing = session.scalar(
        select(Valuation).where(
            Valuation.account_id == account.id,
            Valuation.as_at == closed_on,
            Valuation.source == "manual",
            Valuation.superseded_at.is_(None),
        )
    )
    before = _snapshot(existing) if existing else None
    if existing:
        _supersede(session, existing, None)
    valuation = _insert(session, account, closed_on, zero, "manual", CLOSING_NOTE)
    if existing:
        existing.superseded_by_id = valuation.id
        session.flush()
    audit.record(
        session,
        context,
        entity_type="valuation",
        entity_id=valuation.id,
        action="supersede" if existing else "create",
        before=before,
        after=_snapshot(valuation),
        summary=f"Closing {_describe(account, valuation)}",
    )
    return valuation


# --- First run -----------------------------------------------------------------------------


def load_fixture_valuations(session: Session, data_dir: Path) -> int:
    """Create the fixture's Valuations through the service, once, while there are none.

    Reads ``valuations`` from each Account entry in ``<data_dir>/household.json`` (matched by
    Account name). Fixture dates are in the past, so the usual date checks apply unchanged.
    """
    path = data_dir / household.FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(func.count()).select_from(Valuation)):
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("accounts", [])
    ids = {a.name: a.id for a in session.scalars(select(Account))}
    created = 0
    for entry in entries:
        for item in entry.get("valuations", []):
            if entry["name"] not in ids:
                continue
            add_valuation(
                session,
                FIXTURE_CONTEXT,
                ids[entry["name"]],
                ValuationInput(
                    as_at=date.fromisoformat(item["as_at"]),
                    amount=item["amount"],
                    source=item["source"],
                    provenance_note=item["note"],
                ),
            )
            created += 1
    return created
