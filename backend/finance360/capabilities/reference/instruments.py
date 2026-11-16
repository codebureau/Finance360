"""Instrument and identifier-crosswalk services (ADR-0013 section 2).

Instruments are created and edited in place and never deleted; identifiers are added and removed.
Every mutation writes its audit record in the caller's session (the caller owns the transaction).
An identifier maps to exactly one Instrument: uniqueness is on (scheme, normalised value), checked
here for a readable message and enforced by the database for the race.

Identifier changes are audited as an ``update`` of their Instrument, so one Instrument has one
trail (``entity_type='instrument'``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.instrument import (
    IDENTIFIER_MAX,
    KINDS,
    SCHEMES,
    is_isin_shaped,
    normalise_identifier,
    tidy_identifier,
)
from finance360.core.models.instrument import Instrument, InstrumentIdentifier
from finance360.core.models.price import Price
from finance360.core.money import CURRENCY_EXPONENTS

NAME_MAX = 200
NOTES_MAX = 2000

#: Same file as ``household.FIXTURE_FILENAME``; ``reference`` depends on ``core`` only.
FIXTURE_FILENAME = "household.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


@dataclass(frozen=True)
class IdentifierInput:
    scheme: str
    value: str


@dataclass(frozen=True)
class InstrumentInput:
    name: str
    kind: str
    currency: str
    notes: str | None = None
    identifiers: tuple[IdentifierInput, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Ambiguous:
    """Several Instruments match a lookup that named no scheme. Say which scheme you mean."""

    value: str
    schemes: tuple[str, ...]


# --- reads ---------------------------------------------------------------------------------


def get_instrument(session: Session, instrument_id: int) -> Instrument:
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise NotFoundError(f"Instrument {instrument_id} does not exist.")
    return instrument


def list_instruments(session: Session) -> list[Instrument]:
    return list(
        session.scalars(select(Instrument).order_by(func.lower(Instrument.name), Instrument.id))
    )


def resolve(
    session: Session, value: str, scheme: str | None = None
) -> Instrument | Ambiguous | None:
    """The one Instrument an identifier names, ``None`` if none does.

    With no ``scheme`` every scheme is searched; if that finds more than one Instrument the answer
    is ``Ambiguous`` (never a guess). Matching is case- and whitespace-insensitive.
    """
    norm = normalise_identifier(value)
    query = select(InstrumentIdentifier).where(InstrumentIdentifier.value_norm == norm)
    if scheme is not None:
        query = query.where(InstrumentIdentifier.scheme == scheme)
    rows = list(session.scalars(query))
    ids = {r.instrument_id for r in rows}
    if not ids:
        return None
    if len(ids) > 1:
        return Ambiguous(tidy_identifier(value), tuple(sorted({r.scheme for r in rows})))
    return get_instrument(session, ids.pop())


# --- audit snapshots -----------------------------------------------------------------------


def _snapshot(instrument: Instrument) -> dict[str, Any]:
    return {
        "id": instrument.id,
        "name": instrument.name,
        "kind": instrument.kind,
        "currency": instrument.currency,
        "notes": instrument.notes,
        "identifiers": [{"scheme": i.scheme, "value": i.value} for i in instrument.identifiers],
    }


# --- validation ----------------------------------------------------------------------------


def _check_identifier(data: IdentifierInput, prefix: str = "") -> tuple[str, str, list[FieldError]]:
    errors: list[FieldError] = []
    scheme = data.scheme.strip().lower()
    value = tidy_identifier(data.value)
    if scheme not in SCHEMES:
        errors.append(FieldError(f"{prefix}scheme", f"Choose one of: {', '.join(SCHEMES)}."))
    if not value:
        errors.append(FieldError(f"{prefix}value", "Enter the identifier."))
    elif len(value) > IDENTIFIER_MAX:
        errors.append(FieldError(f"{prefix}value", f"At most {IDENTIFIER_MAX} characters."))
    elif scheme == "isin" and not is_isin_shaped(value):
        errors.append(
            FieldError(
                f"{prefix}value",
                "An ISIN is 12 characters: two letters, nine letters or digits, a digit.",
            )
        )
    return scheme, value, errors


def _check_fields(
    name: str, kind: str, currency: str, notes: str | None
) -> tuple[str, str, str, str | None]:
    errors: list[FieldError] = []
    name = " ".join(name.split())
    if not name:
        errors.append(FieldError("name", "Enter a name."))
    elif len(name) > NAME_MAX:
        errors.append(FieldError("name", f"The name must be at most {NAME_MAX} characters."))
    kind = kind.strip().lower()
    if kind not in KINDS:
        errors.append(FieldError("kind", f"Choose one of: {', '.join(KINDS)}."))
    currency = currency.strip().upper()
    if currency not in CURRENCY_EXPONENTS:
        errors.append(FieldError("currency", "Choose a currency code we know, e.g. AUD."))
    clean_notes = (notes or "").strip() or None
    if clean_notes is not None and len(clean_notes) > NOTES_MAX:
        errors.append(FieldError("notes", f"The notes must be at most {NOTES_MAX} characters."))
    if errors:
        raise ValidationError(errors)
    return name, kind, currency, clean_notes


def _taken(
    session: Session, scheme: str, value: str, *, except_instrument: int | None
) -> FieldError | None:
    owner = session.scalar(
        select(InstrumentIdentifier).where(
            InstrumentIdentifier.scheme == scheme,
            InstrumentIdentifier.value_norm == normalise_identifier(value),
        )
    )
    if owner is None or owner.instrument_id == except_instrument:
        return None
    other = get_instrument(session, owner.instrument_id)
    return FieldError(
        "value",
        f"The {scheme} {owner.value!r} already identifies {other.name}. "
        "Each identifier names one Instrument.",
    )


def _conflict(scheme: str, value: str) -> ValidationError:
    return ValidationError(
        [FieldError("value", f"The {scheme} {value!r} already identifies another Instrument.")]
    )


# --- mutations -----------------------------------------------------------------------------


def _attach(session: Session, instrument: Instrument, scheme: str, value: str) -> None:
    """Insert one identifier, turning the database's uniqueness guard into a field error."""
    try:
        with session.begin_nested():
            session.add(
                InstrumentIdentifier(
                    instrument_id=instrument.id,
                    scheme=scheme,
                    value=value,
                    value_norm=normalise_identifier(value),
                )
            )
    except IntegrityError:
        raise _conflict(scheme, value) from None
    session.refresh(instrument)


def create_instrument(session: Session, context: AuditContext, data: InstrumentInput) -> Instrument:
    name, kind, currency, notes = _check_fields(data.name, data.kind, data.currency, data.notes)
    errors: list[FieldError] = []
    checked: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for i, ident in enumerate(data.identifiers):
        scheme, value, problems = _check_identifier(ident, f"identifiers[{i}].")
        errors += problems
        if problems:
            continue
        key = (scheme, normalise_identifier(value))
        if key in seen:
            errors.append(FieldError(f"identifiers[{i}].value", "This identifier is listed twice."))
            continue
        seen.add(key)
        clash = _taken(session, scheme, value, except_instrument=None)
        if clash:
            errors.append(FieldError(f"identifiers[{i}].value", clash.message))
        checked.append((scheme, value))
    if errors:
        raise ValidationError(errors)
    instrument = Instrument(name=name, kind=kind, currency=currency, notes=notes)
    session.add(instrument)
    session.flush()
    for scheme, value in checked:
        _attach(session, instrument, scheme, value)
    session.refresh(instrument)
    audit.record(
        session,
        context,
        entity_type="instrument",
        entity_id=instrument.id,
        action="create",
        after=_snapshot(instrument),
        summary=f"Added {instrument.kind} {instrument.name} ({instrument.currency})",
    )
    return instrument


def update_instrument(
    session: Session, context: AuditContext, instrument_id: int, data: InstrumentInput
) -> Instrument:
    """Edit name, kind, currency and notes. Identifiers change through add/remove."""
    instrument = get_instrument(session, instrument_id)
    name, kind, currency, notes = _check_fields(data.name, data.kind, data.currency, data.notes)
    if (name, kind, currency, notes) == (
        instrument.name,
        instrument.kind,
        instrument.currency,
        instrument.notes,
    ):
        return instrument
    if currency != instrument.currency and session.scalar(
        select(func.count()).select_from(Price).where(Price.instrument_id == instrument.id)
    ):
        raise ValidationError(
            [
                FieldError(
                    "currency",
                    f"This Instrument already has Prices in {instrument.currency}, "
                    "so its currency cannot change.",
                )
            ]
        )
    before = _snapshot(instrument)
    instrument.name, instrument.kind, instrument.currency, instrument.notes = (
        name,
        kind,
        currency,
        notes,
    )
    session.flush()
    audit.record(
        session,
        context,
        entity_type="instrument",
        entity_id=instrument.id,
        action="update",
        before=before,
        after=_snapshot(instrument),
        summary=f"Edited {instrument.kind} {instrument.name}",
    )
    return instrument


def add_identifier(
    session: Session, context: AuditContext, instrument_id: int, data: IdentifierInput
) -> Instrument:
    instrument = get_instrument(session, instrument_id)
    scheme, value, errors = _check_identifier(data)
    if errors:
        raise ValidationError(errors)
    clash = _taken(session, scheme, value, except_instrument=None)
    if clash:
        raise ValidationError([clash])
    before = _snapshot(instrument)
    _attach(session, instrument, scheme, value)
    audit.record(
        session,
        context,
        entity_type="instrument",
        entity_id=instrument.id,
        action="update",
        before=before,
        after=_snapshot(instrument),
        summary=f"Added {scheme} {value!r} to {instrument.name}",
    )
    return instrument


def remove_identifier(
    session: Session, context: AuditContext, instrument_id: int, identifier_id: int
) -> Instrument:
    instrument = get_instrument(session, instrument_id)
    ident = session.get(InstrumentIdentifier, identifier_id)
    if ident is None or ident.instrument_id != instrument.id:
        raise NotFoundError(
            f"Identifier {identifier_id} does not belong to Instrument {instrument_id}."
        )
    before = _snapshot(instrument)
    session.delete(ident)
    session.flush()
    session.refresh(instrument)
    audit.record(
        session,
        context,
        entity_type="instrument",
        entity_id=instrument.id,
        action="update",
        before=before,
        after=_snapshot(instrument),
        summary=f"Removed {ident.scheme} {ident.value!r} from {instrument.name}",
    )
    return instrument


# --- First run -----------------------------------------------------------------------------


def load_fixture_instruments(session: Session, data_dir: Path) -> int:
    """Create the fixture's Instruments through the service, once, while there are none.

    Reads ``instruments`` from ``<data_dir>/household.json``.
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(func.count()).select_from(Instrument)):
        return 0
    items = json.loads(path.read_text(encoding="utf-8")).get("instruments", [])
    for item in items:
        create_instrument(
            session,
            FIXTURE_CONTEXT,
            InstrumentInput(
                name=item["name"],
                kind=item["kind"],
                currency=item["currency"],
                notes=item.get("notes"),
                identifiers=tuple(
                    IdentifierInput(i["scheme"], i["value"]) for i in item.get("identifiers", [])
                ),
            ),
        )
    return len(items)
