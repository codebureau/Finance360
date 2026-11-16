"""Price services (ADR-0013 section 2).

Prices are append-only, like Valuations and FX rates: a correction inserts a new row and supersedes
the old one; a removal supersedes with no replacement. Every mutation writes its audit record in
the caller's session (the caller owns the transaction). The owner enters ``manual`` Prices (with a
required note) and may correct or remove only those: a provider's Price is overridden by adding a
manual one for the same date, which wins the tie. Provider fetching arrives in a later slice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from finance360.capabilities.reference.instruments import get_instrument
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.instrument import Instrument
from finance360.core.models.price import Price
from finance360.core.money import MoneyError
from finance360.core.price import (
    IMPORT_SOURCE,
    SOURCE_PRECEDENCE,
    NoPrice,
    PricePoint,
    format_price,
    parse_price,
    price_on_or_before,
)

NOTE_MAX = 2000
MANUAL = "manual"

#: Same file as ``household.FIXTURE_FILENAME``; ``reference`` depends on ``core`` only.
FIXTURE_FILENAME = "household.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


@dataclass(frozen=True)
class PriceInput:
    as_at: date
    #: A plain decimal string, e.g. ``"34.5678"``, in the Instrument's currency. Never a float.
    value: str
    provenance_note: str | None = None
    #: Optional; if given it must equal the Instrument's currency.
    currency: str | None = None
    #: The API only ever enters ``manual``; the fixture loader (and later providers) name theirs.
    source: str = MANUAL


# --- reads ---------------------------------------------------------------------------------


def get_price(session: Session, instrument_id: int, price_id: int) -> Price:
    price = session.get(Price, price_id)
    if price is None or price.instrument_id != instrument_id:
        raise NotFoundError(f"Price {price_id} does not exist for Instrument {instrument_id}.")
    return price


def current_prices(session: Session, instrument_id: int) -> list[Price]:
    """Current (non-superseded) Prices of an Instrument, newest first."""
    query = (
        select(Price)
        .where(Price.instrument_id == instrument_id, Price.superseded_at.is_(None))
        .order_by(Price.as_at.desc(), Price.id.desc())
    )
    return list(session.scalars(query))


def list_prices(session: Session, instrument_id: int) -> list[Price]:
    """The whole history, superseded rows included: by date, current before superseded."""
    get_instrument(session, instrument_id)
    query = (
        select(Price)
        .where(Price.instrument_id == instrument_id)
        .order_by(
            Price.as_at.desc(),
            Price.superseded_at.is_not(None),
            Price.recorded_at.desc(),
            Price.id.desc(),
        )
    )
    return list(session.scalars(query))


def point(price: Price) -> PricePoint:
    return PricePoint(price.id, price.instrument_id, price.as_at, price.value, price.source)


def price_for(session: Session, instrument_id: int, on: date) -> PricePoint | NoPrice:
    """The current Price on or before ``on`` (``manual`` wins a tie), or an explicit ``NoPrice``."""
    get_instrument(session, instrument_id)
    return price_on_or_before(map(point, current_prices(session, instrument_id)), instrument_id, on)


# --- audit snapshots -----------------------------------------------------------------------


def _snapshot(price: Price) -> dict[str, Any]:
    return {
        "id": price.id,
        "instrument_id": price.instrument_id,
        "as_at": price.as_at.isoformat(),
        "value": format_price(price.value),  # a decimal string, never a float
        "ccy": price.ccy,
        "source": price.source,
        "provenance_note": price.provenance_note,
    }


def _describe(instrument: Instrument, price: Price) -> str:
    return (
        f"{price.source} Price of {instrument.name} at {price.as_at}: "
        f"{format_price(price.value)} {price.ccy}"
    )


# --- validation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Checked:
    as_at: date
    value: Decimal
    note: str
    source: str


def _check(instrument: Instrument, data: PriceInput) -> _Checked:
    errors: list[FieldError] = []
    if data.currency is not None and data.currency.strip().upper() != instrument.currency:
        errors.append(
            FieldError(
                "currency",
                f"{instrument.name} is priced in {instrument.currency}; so is every Price.",
            )
        )
    source = data.source.strip().lower()
    if source not in SOURCE_PRECEDENCE:
        errors.append(FieldError("source", f"Choose one of: {', '.join(SOURCE_PRECEDENCE)}."))
    note = (data.provenance_note or "").strip()
    if not note:
        errors.append(FieldError("provenance_note", "Say where this Price came from."))
    elif len(note) > NOTE_MAX:
        errors.append(
            FieldError("provenance_note", f"The note must be at most {NOTE_MAX} characters.")
        )
    value = Decimal(1)
    try:
        value = parse_price(data.value)
    except MoneyError:
        errors.append(
            FieldError(
                "value",
                f"Enter a price above zero with at most 6 decimal places, e.g. 34.5678 "
                f"({instrument.currency} per unit).",
            )
        )
    if errors:
        raise ValidationError(errors)
    return _Checked(data.as_at, value, note, source)


def _ensure_no_current(
    session: Session, instrument_id: int, checked: _Checked, *, except_id: int | None = None
) -> None:
    clash = session.scalar(
        select(Price.id).where(
            Price.instrument_id == instrument_id,
            Price.as_at == checked.as_at,
            Price.source == checked.source,
            Price.superseded_at.is_(None),
            Price.id != (except_id or 0),
        )
    )
    if clash is not None:
        raise ValidationError(
            [
                FieldError(
                    "as_at",
                    f"There is already a {checked.source} Price for {checked.as_at}. "
                    "Correct that one instead.",
                )
            ]
        )


def _ensure_manual(price: Price, verb: str) -> None:
    if price.superseded_at is not None:
        raise ValidationError([FieldError("id", f"Only a current Price can be {verb}.")])
    if price.source != MANUAL:
        raise ValidationError(
            [
                FieldError(
                    "id",
                    f"Only a manual Price can be {verb} here. To override a {price.source} "
                    "Price, add a manual one for the same date.",
                )
            ]
        )


# --- mutations -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as the audit trail stores it


def _insert(session: Session, instrument: Instrument, checked: _Checked) -> Price:
    price = Price(
        instrument_id=instrument.id,
        as_at=checked.as_at,
        value=checked.value,
        ccy=instrument.currency,
        source=checked.source,
        provenance_note=checked.note,
        recorded_at=_now(),
    )
    session.add(price)
    session.flush()
    return price


def _supersede(session: Session, old: Price, replacement_id: int | None) -> None:
    old.superseded_at = _now()
    old.superseded_by_id = replacement_id
    session.flush()


def add_price(
    session: Session, context: AuditContext, instrument_id: int, data: PriceInput
) -> Price:
    instrument = get_instrument(session, instrument_id)
    checked = _check(instrument, data)
    _ensure_no_current(session, instrument.id, checked)
    price = _insert(session, instrument, checked)
    audit.record(
        session,
        context,
        entity_type="price",
        entity_id=price.id,
        action="create",
        after=_snapshot(price),
        summary=f"Added {_describe(instrument, price)}",
    )
    return price


def correct_price(
    session: Session,
    context: AuditContext,
    instrument_id: int,
    price_id: int,
    data: PriceInput,
) -> Price:
    """Replace a current manual Price: insert the corrected row, supersede the old one (kept)."""
    instrument = get_instrument(session, instrument_id)
    old = get_price(session, instrument_id, price_id)
    _ensure_manual(old, "corrected")
    checked = _check(
        instrument, PriceInput(data.as_at, data.value, data.provenance_note, data.currency)
    )
    if (checked.as_at, checked.value, checked.note) == (
        old.as_at,
        old.value,
        old.provenance_note,
    ):
        return old
    _ensure_no_current(session, instrument.id, checked, except_id=old.id)
    before = _snapshot(old)
    was = format_price(old.value)
    _supersede(session, old, None)  # free the unique slot first; link once the new row exists
    new = _insert(session, instrument, checked)
    old.superseded_by_id = new.id
    session.flush()
    audit.record(
        session,
        context,
        entity_type="price",
        entity_id=new.id,
        action="supersede",
        before=before,
        after=_snapshot(new),
        summary=f"Corrected {_describe(instrument, new)} (was {was})",
    )
    return new


def remove_price(
    session: Session, context: AuditContext, instrument_id: int, price_id: int
) -> Price:
    """Supersede a current manual Price with no replacement. The row stays, marked superseded."""
    instrument = get_instrument(session, instrument_id)
    old = get_price(session, instrument_id, price_id)
    _ensure_manual(old, "removed")
    before = _snapshot(old)
    _supersede(session, old, None)
    audit.record(
        session,
        context,
        entity_type="price",
        entity_id=old.id,
        action="supersede",
        before=before,
        summary=f"Removed {_describe(instrument, old)}",
    )
    return old


# --- Import prices -------------------------------------------------------------------------


def record_import_price(
    session: Session, instrument: Instrument, as_at: date, value: Decimal, note: str
) -> str:
    """Record an Import's unit price as an ``import`` Price, in the caller's transaction.

    Returns ``"added"``, ``"revised"`` (a different value for a date that already had a current
    ``import`` Price: the old row is superseded, kept and linked) or ``"unchanged"``. The write is
    ``INSERT ... ON CONFLICT DO NOTHING`` against the partial unique index, so it is idempotent.
    Audit is the caller's: the commit's one event carries the counts.
    """
    old = session.scalar(
        select(Price).where(
            Price.instrument_id == instrument.id,
            Price.as_at == as_at,
            Price.source == IMPORT_SOURCE,
            Price.superseded_at.is_(None),
        )
    )
    if old is not None and old.value == value:
        return "unchanged"
    now = _now()
    if old is not None:
        old.superseded_at = now
        session.flush()
    result = session.execute(
        insert(Price)
        .values(
            instrument_id=instrument.id,
            as_at=as_at,
            value=value,
            ccy=instrument.currency,
            source=IMPORT_SOURCE,
            provenance_note=note,
            recorded_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["instrument_id", "as_at", "source"],
            index_where=Price.superseded_at.is_(None),
        )
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        return "unchanged"
    if old is None:
        return "added"
    old.superseded_by_id = session.scalar(
        select(Price.id).where(
            Price.instrument_id == instrument.id,
            Price.as_at == as_at,
            Price.source == IMPORT_SOURCE,
            Price.superseded_at.is_(None),
        )
    )
    session.flush()
    return "revised"


# --- First run -----------------------------------------------------------------------------


def load_fixture_prices(session: Session, data_dir: Path) -> int:
    """Create the fixture's Prices through the service, once, while there are none.

    Reads ``prices`` from each Instrument entry in ``<data_dir>/household.json`` (matched by
    name): rows of ``as_at``, ``value``, ``source`` and ``note``.
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(func.count()).select_from(Price)):
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("instruments", [])
    ids = {i.name: i.id for i in session.scalars(select(Instrument))}
    created = 0
    for entry in entries:
        if entry["name"] not in ids:
            continue
        for item in entry.get("prices", []):
            add_price(
                session,
                FIXTURE_CONTEXT,
                ids[entry["name"]],
                PriceInput(
                    as_at=date.fromisoformat(item["as_at"]),
                    value=item["value"],
                    provenance_note=item["note"],
                    source=item.get("source", MANUAL),
                ),
            )
            created += 1
    return created
