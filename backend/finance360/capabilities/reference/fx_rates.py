"""FxRate services (ADR-0009 section 4).

FX rates are append-only, like Valuations: a correction inserts a new row and supersedes the old
one; a removal supersedes with no replacement. Every mutation writes its audit record in the
caller's session (the caller owns the transaction). M1 stores foreign -> AUD rates only, entered
manually; inverse pairs and triangulation are not derived.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.fx import FxPoint, NoRate, format_rate, parse_rate, rate_on_or_before
from finance360.core.models.fx_rate import FxRate
from finance360.core.money import (
    BASE_CURRENCY,
    CURRENCY_EXPONENTS,
    Money,
    MoneyError,
    convert,
)

NOTE_MAX = 2000
#: The source of every rate the owner enters; a provider's key (``rba``) marks a fetched one.
MANUAL = "manual"

#: The sample-household fixture file (same name as ``household.FIXTURE_FILENAME``; ``reference``
#: depends on ``core`` only, so it can't import that constant).
FIXTURE_FILENAME = "household.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


@dataclass(frozen=True)
class FxRateInput:
    from_ccy: str
    as_at: date
    #: A plain decimal string, e.g. ``"2.0143"``: AUD per 1 unit of ``from_ccy``. Never a float.
    rate: str
    provenance_note: str | None = None
    #: M1 stores foreign -> AUD only; anything else is refused.
    to_ccy: str = BASE_CURRENCY


@dataclass(frozen=True)
class Converted:
    """An amount converted at the rate found, which carries its ``as_at`` so staleness can show."""

    original: Money
    converted: Money
    rate: Decimal
    rate_as_at: date
    rate_source: str = MANUAL


# --- reads ---------------------------------------------------------------------------------


def get_fx_rate(session: Session, fx_rate_id: int) -> FxRate:
    fx_rate = session.get(FxRate, fx_rate_id)
    if fx_rate is None:
        raise NotFoundError(f"FX rate {fx_rate_id} does not exist.")
    return fx_rate


def current_fx_rates(session: Session) -> list[FxRate]:
    """Current (non-superseded) rates, newest first."""
    query = (
        select(FxRate)
        .where(FxRate.superseded_at.is_(None))
        .order_by(FxRate.as_at.desc(), FxRate.id.desc())
    )
    return list(session.scalars(query))


def list_fx_rates(session: Session) -> list[FxRate]:
    """The whole history, superseded rows included: by date, current before superseded."""
    query = select(FxRate).order_by(
        FxRate.as_at.desc(),
        FxRate.from_ccy,
        FxRate.superseded_at.is_not(None),
        FxRate.recorded_at.desc(),
        FxRate.id.desc(),
    )
    return list(session.scalars(query))


def point(fx_rate: FxRate) -> FxPoint:
    return FxPoint(
        fx_rate.id, fx_rate.from_ccy, fx_rate.to_ccy, fx_rate.as_at, fx_rate.rate, fx_rate.source
    )


def rate_for(session: Session, from_ccy: str, to_ccy: str, on: date) -> FxPoint | NoRate:
    """The current rate for the exact pair, latest ``as_at <= on``, or an explicit ``NoRate``."""
    return rate_on_or_before(map(point, current_fx_rates(session)), from_ccy, to_ccy, on)


def convert_on(session: Session, money: Money, to_ccy: str, on: date) -> Converted | NoRate:
    """Convert ``money`` at the rate in force on ``on``, or return an explicit ``NoRate``.

    Never assumes 1.0: a missing rate (including the inverse of a stored pair) is a ``NoRate``.
    """
    if money.currency == to_ccy:
        raise ValidationError([FieldError("currency", f"This is already in {to_ccy}.")])
    found = rate_for(session, money.currency, to_ccy, on)
    if isinstance(found, NoRate):
        return found
    return Converted(
        money, convert(money, found.rate, to_ccy), found.rate, found.as_at, found.source
    )


# --- audit snapshots -----------------------------------------------------------------------


def _snapshot(fx_rate: FxRate) -> dict[str, Any]:
    return {
        "id": fx_rate.id,
        "from_ccy": fx_rate.from_ccy,
        "to_ccy": fx_rate.to_ccy,
        "as_at": fx_rate.as_at.isoformat(),
        "rate": format_rate(fx_rate.rate),  # a decimal string, never a float
        "source": fx_rate.source,
        "provenance_note": fx_rate.provenance_note,
    }


def _describe(fx_rate: FxRate) -> str:
    return (
        f"{fx_rate.source} FX rate {fx_rate.from_ccy}->{fx_rate.to_ccy} at {fx_rate.as_at}: "
        f"{format_rate(fx_rate.rate)}"
    )


# --- validation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Checked:
    from_ccy: str
    to_ccy: str
    as_at: date
    rate: Decimal
    note: str


def _check(data: FxRateInput) -> _Checked:
    errors: list[FieldError] = []

    from_ccy = data.from_ccy.strip().upper()
    if from_ccy not in CURRENCY_EXPONENTS:
        errors.append(FieldError("from_ccy", "Choose a currency code we know, e.g. GBP."))
    elif from_ccy == BASE_CURRENCY:
        errors.append(
            FieldError(
                "from_ccy", f"Rates are entered for foreign currencies, not {BASE_CURRENCY}."
            )
        )

    to_ccy = data.to_ccy.strip().upper()
    if to_ccy != BASE_CURRENCY:
        errors.append(FieldError("to_ccy", f"Rates are stored as foreign to {BASE_CURRENCY} only."))

    note = (data.provenance_note or "").strip()
    if not note:
        errors.append(FieldError("provenance_note", "Say where this rate came from."))
    elif len(note) > NOTE_MAX:
        errors.append(
            FieldError("provenance_note", f"The note must be at most {NOTE_MAX} characters.")
        )

    rate = Decimal(1)
    try:
        rate = parse_rate(data.rate)
    except MoneyError:
        errors.append(
            FieldError(
                "rate",
                f"Enter a rate above zero with at most 10 decimal places, e.g. 2.0143 "
                f"({BASE_CURRENCY} per 1 {from_ccy or 'unit'}).",
            )
        )
    if errors:
        raise ValidationError(errors)
    return _Checked(from_ccy, to_ccy, data.as_at, rate, note)


def _ensure_no_current(
    session: Session, checked: _Checked, *, except_id: int | None = None
) -> None:
    clash = session.scalar(
        select(FxRate.id).where(
            FxRate.from_ccy == checked.from_ccy,
            FxRate.to_ccy == checked.to_ccy,
            FxRate.as_at == checked.as_at,
            FxRate.source == MANUAL,
            FxRate.superseded_at.is_(None),
            FxRate.id != (except_id or 0),
        )
    )
    if clash is not None:
        raise ValidationError(
            [
                FieldError(
                    "as_at",
                    f"There is already a {checked.from_ccy} rate for {checked.as_at}. "
                    "Correct that one instead.",
                )
            ]
        )


def _ensure_manual(rate: FxRate, verb: str) -> None:
    """The owner edits only rates they entered. A fetched rate is overridden by adding a manual
    one for the same date, which wins the tie (ADR-0013 section 6), not by editing a provider's."""
    if rate.superseded_at is not None:
        raise ValidationError([FieldError("id", f"Only a current FX rate can be {verb}.")])
    if rate.source != MANUAL:
        raise ValidationError(
            [
                FieldError(
                    "id",
                    f"Only a manual FX rate can be {verb} here. To override a {rate.source} "
                    "rate, add a manual one for the same date.",
                )
            ]
        )


# --- mutations -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as the audit trail stores it


def _insert(session: Session, checked: _Checked) -> FxRate:
    fx_rate = FxRate(
        from_ccy=checked.from_ccy,
        to_ccy=checked.to_ccy,
        as_at=checked.as_at,
        rate=checked.rate,
        source=MANUAL,
        provenance_note=checked.note,
        recorded_at=_now(),
    )
    session.add(fx_rate)
    session.flush()
    return fx_rate


def _supersede(session: Session, old: FxRate, replacement_id: int | None) -> None:
    old.superseded_at = _now()
    old.superseded_by_id = replacement_id
    session.flush()


def add_fx_rate(session: Session, context: AuditContext, data: FxRateInput) -> FxRate:
    checked = _check(data)
    _ensure_no_current(session, checked)
    fx_rate = _insert(session, checked)
    audit.record(
        session,
        context,
        entity_type="fx_rate",
        entity_id=fx_rate.id,
        action="create",
        after=_snapshot(fx_rate),
        summary=f"Added {_describe(fx_rate)}",
    )
    return fx_rate


def correct_fx_rate(
    session: Session, context: AuditContext, fx_rate_id: int, data: FxRateInput
) -> FxRate:
    """Replace a current rate: insert the corrected row, supersede the old one (kept)."""
    old = get_fx_rate(session, fx_rate_id)
    _ensure_manual(old, "corrected")
    checked = _check(data)
    unchanged = (checked.from_ccy, checked.to_ccy, checked.as_at, checked.rate, checked.note) == (
        old.from_ccy,
        old.to_ccy,
        old.as_at,
        old.rate,
        old.provenance_note,
    )
    if unchanged:
        return old
    _ensure_no_current(session, checked, except_id=old.id)
    before = _snapshot(old)
    _supersede(session, old, None)  # free the unique slot first; link once the new row exists
    new = _insert(session, checked)
    old.superseded_by_id = new.id
    session.flush()
    audit.record(
        session,
        context,
        entity_type="fx_rate",
        entity_id=new.id,
        action="supersede",
        before=before,
        after=_snapshot(new),
        summary=f"Corrected {_describe(new)} (was {format_rate(old.rate)})",
    )
    return new


def remove_fx_rate(session: Session, context: AuditContext, fx_rate_id: int) -> FxRate:
    """Supersede a current rate with no replacement. The row stays, marked superseded."""
    old = get_fx_rate(session, fx_rate_id)
    _ensure_manual(old, "removed")
    before = _snapshot(old)
    _supersede(session, old, None)
    audit.record(
        session,
        context,
        entity_type="fx_rate",
        entity_id=old.id,
        action="supersede",
        before=before,
        summary=f"Removed {_describe(old)}",
    )
    return old


# --- First run -----------------------------------------------------------------------------


def load_fixture_fx_rates(session: Session, data_dir: Path) -> int:
    """Create the fixture's FX rates through the service, once, while there are none.

    Reads ``fx_rates`` from ``<data_dir>/household.json``.
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(func.count()).select_from(FxRate)):
        return 0
    items = json.loads(path.read_text(encoding="utf-8")).get("fx_rates", [])
    for item in items:
        add_fx_rate(
            session,
            FIXTURE_CONTEXT,
            FxRateInput(
                from_ccy=item["from_ccy"],
                as_at=date.fromisoformat(item["as_at"]),
                rate=item["rate"],
                provenance_note=item["note"],
            ),
        )
    return len(items)
