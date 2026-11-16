"""Income stream services (ADR-0017 section 2).

Reads and writes of ``income_stream`` and ``income_stream_owner``. Mutating functions take the
caller's ``Session`` and an explicit ``AuditContext``, write the audit record (entity type
``income_stream``) in that session and flush, never commit. Rows are edited in place; the Audit
trail is the history. A saved projection run snapshots the resolved input, so editing or deleting a
stream later never changes a saved run.

M3 allows only the ``salary`` type and only sole ownership. Take-home pay is stated by the
household (it is required for a salary) until a tax calculation replaces it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.capabilities.projection import assumptions, income
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.account import FULL_SHARE_BP
from finance360.core.models.income_stream import IncomeStream, IncomeStreamOwner
from finance360.core.money import BASE_CURRENCY, Money, MoneyError, format_amount, parse_amount

NAME_MAX = 200
NOTES_MAX = 500
SOLE = "sole"


@dataclass(frozen=True)
class OwnerInput:
    member_id: int
    #: Basis points. Omit (or 10000) for a sole stream.
    share_bp: int | None = None


@dataclass(frozen=True)
class StreamInput:
    """What the household states. Amounts are decimal text in AUD (never a float)."""

    name: str
    type_key: str
    gross_annual: str
    take_home_annual: str | None
    owners: Sequence[OwnerInput]
    ownership: str = SOLE
    notes: str | None = None
    start_on: date | None = None
    start_event: str | None = None
    end_on: date | None = None
    end_event: str | None = None
    credit_category: str = "cash"


# --- reads ---------------------------------------------------------------------------------


def get_stream(session: Session, stream_id: int) -> IncomeStream:
    stream = session.get(IncomeStream, stream_id)
    if stream is None:
        raise NotFoundError(f"Income stream {stream_id} does not exist.")
    return stream


def list_streams(session: Session) -> list[IncomeStream]:
    return list(session.scalars(select(IncomeStream).order_by(IncomeStream.name, IncomeStream.id)))


def _owner_id(stream: IncomeStream) -> int:
    return stream.owners[0].member_id


def projection_streams(session: Session) -> list[income.Stream]:
    """Every stored stream as the plain value the projection uses (M3: all are salaries)."""
    out: list[income.Stream] = []
    for s in list_streams(session):
        take_home = s.take_home_annual
        if take_home is None:  # the service requires it for a salary; a manual edit could drop it
            raise ValueError(f"Income stream {s.id} has no take-home amount")
        out.append(
            income.Stream(
                s.id,
                s.name,
                s.type_key,
                _owner_id(s),
                s.gross_annual,
                take_home,
                s.credit_category,
                s.start_on,
                s.start_event,
                s.end_on,
                s.end_event,
            )
        )
    return out


def salary_member_ids(session: Session) -> set[int]:
    """The Members who own at least one salary stream (``annual_saving`` is not applied to them,
    and each needs a real wage growth)."""
    rows = session.execute(
        select(IncomeStreamOwner.member_id)
        .join(IncomeStream, IncomeStream.id == IncomeStreamOwner.stream_id)
        .where(IncomeStream.type_key == income.SALARY)
    )
    return {int(r[0]) for r in rows}


# --- audit snapshots -----------------------------------------------------------------------


def _money(money: Money | None) -> dict[str, Any] | None:
    return None if money is None else {"minor": money.amount_minor, "ccy": money.currency}


def _snapshot(stream: IncomeStream) -> dict[str, Any]:
    return {
        "name": stream.name,
        "type_key": stream.type_key,
        "ownership": stream.ownership,
        "owners": [{"member_id": o.member_id, "share_bp": o.share_bp} for o in stream.owners],
        "gross_annual": _money(stream.gross_annual),
        "take_home_annual": _money(stream.take_home_annual),
        "start_on": stream.start_on.isoformat() if stream.start_on else None,
        "start_event": stream.start_event,
        "end_on": stream.end_on.isoformat() if stream.end_on else None,
        "end_event": stream.end_event,
        "credit_category": stream.credit_category,
        "notes": stream.notes,
    }


# --- validation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Checked:
    name: str
    type_key: str
    gross: Money
    take_home: Money
    member_id: int
    notes: str | None
    start_on: date | None
    start_event: str | None
    end_on: date | None
    end_event: str | None
    credit_category: str


def _amount(text: str | None, field: str, errors: list[FieldError], *, required: bool) -> Money:
    zero = Money.zero(BASE_CURRENCY)
    if text is None or not text.strip():
        if required:
            errors.append(FieldError(field, "Enter an amount."))
        return zero
    try:
        return parse_amount(text, BASE_CURRENCY)
    except MoneyError as error:
        errors.append(FieldError(field, str(error)))
        return zero


def _check(session: Session, given: StreamInput) -> _Checked:
    errors: list[FieldError] = []
    name, name_errors = household.validate_name(given.name)
    errors += [FieldError(e.field, e.message) for e in name_errors]
    notes = (given.notes or "").strip() or None
    if notes is not None and len(notes) > NOTES_MAX:
        errors.append(FieldError("notes", f"Notes must be at most {NOTES_MAX} characters."))

    stream_type = income.TYPES.get(given.type_key)
    if stream_type is None:
        errors.append(
            FieldError(
                "type_key",
                f"Income stream type '{given.type_key}' is not available. "
                f"Choose one of: {', '.join(t.label for t in income.TYPES.values())}.",
            )
        )

    if given.ownership == "joint":
        errors.append(
            FieldError("ownership", "Joint income streams are not available yet. Choose a Member.")
        )
    elif given.ownership != SOLE:
        errors.append(FieldError("ownership", "Ownership must be sole."))
    member_id = 0
    if len(given.owners) != 1:
        errors.append(FieldError("owners", "A sole income stream has exactly one Member."))
    else:
        owner = given.owners[0]
        member_id = owner.member_id
        if owner.share_bp not in (None, FULL_SHARE_BP):
            errors.append(FieldError("owners", "A sole Member holds 100%."))
        elif member_id not in {m.id for m in household.list_members(session)}:
            errors.append(FieldError("owners", "This Member does not exist."))

    gross = _amount(given.gross_annual, "gross_annual", errors, required=True)
    if gross.amount_minor <= 0 and not any(e.field == "gross_annual" for e in errors):
        errors.append(FieldError("gross_annual", "Enter an amount above 0."))
    take_home = _amount(given.take_home_annual, "take_home_annual", errors, required=True)
    if take_home.amount_minor < 0:
        errors.append(FieldError("take_home_annual", "Take-home pay can't be negative."))
    elif take_home.amount_minor > gross.amount_minor and not any(
        e.field in ("gross_annual", "take_home_annual") for e in errors
    ):
        errors.append(FieldError("take_home_annual", "Take-home pay can't exceed gross pay."))

    start_event, end_event = given.start_event, given.end_event
    for pair, on, event in (
        ("start", given.start_on, start_event),
        ("end", given.end_on, end_event),
    ):
        if on is not None and event is not None:
            errors.append(
                FieldError(f"{pair}_event", f"Give a {pair} date or a Life event, not both.")
            )
        elif event is not None and event not in income.EVENT_KEYS:
            errors.append(FieldError(f"{pair}_event", f"'{event}' is not a Life event."))
    if given.end_on is None and end_event is None:
        end_event = income.DEFAULT_END_EVENT  # a salary ends at retirement unless it says otherwise
    if given.start_on and given.end_on and given.end_on <= given.start_on:
        errors.append(FieldError("end_on", "The end date must be after the start date."))

    if given.credit_category not in assumptions.PROJECTED_CATEGORIES:
        errors.append(FieldError("credit_category", "This Category is not projected."))

    if errors:
        raise ValidationError(errors)
    return _Checked(
        name,
        given.type_key,
        gross,
        take_home,
        member_id,
        notes,
        given.start_on,
        start_event,
        given.end_on,
        end_event,
        given.credit_category,
    )


def _apply(stream: IncomeStream, c: _Checked) -> None:
    stream.name = c.name
    stream.notes = c.notes
    stream.type_key = c.type_key
    stream.ownership = SOLE
    stream.gross_annual = c.gross
    stream.take_home_annual_minor = c.take_home.amount_minor
    stream.take_home_annual_ccy = c.take_home.currency
    stream.start_on, stream.start_event = c.start_on, c.start_event
    stream.end_on, stream.end_event = c.end_on, c.end_event
    stream.credit_category = c.credit_category


def _summary(stream: IncomeStream) -> str:
    return f"'{stream.name}' ({format_amount(stream.gross_annual)} a year gross)"


# --- writes --------------------------------------------------------------------------------


def create_stream(session: Session, context: AuditContext, given: StreamInput) -> IncomeStream:
    c = _check(session, given)
    stream = IncomeStream(name=c.name, type_key=c.type_key, ownership=SOLE, gross_annual=c.gross)
    _apply(stream, c)
    stream.owners = [IncomeStreamOwner(member_id=c.member_id, share_bp=FULL_SHARE_BP)]
    session.add(stream)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="income_stream",
        entity_id=stream.id,
        action="create",
        after=_snapshot(stream),
        summary=f"Added income stream {_summary(stream)}",
    )
    return stream


def update_stream(
    session: Session, context: AuditContext, stream_id: int, given: StreamInput
) -> IncomeStream:
    stream = get_stream(session, stream_id)
    c = _check(session, given)
    before = _snapshot(stream)
    _apply(stream, c)
    if _owner_id(stream) != c.member_id:
        stream.owners = [IncomeStreamOwner(member_id=c.member_id, share_bp=FULL_SHARE_BP)]
    session.flush()
    after = _snapshot(stream)
    if after != before:
        audit.record(
            session,
            context,
            entity_type="income_stream",
            entity_id=stream.id,
            action="update",
            before=before,
            after=after,
            summary=f"Edited income stream {_summary(stream)}",
        )
    return stream


def delete_stream(session: Session, context: AuditContext, stream_id: int) -> None:
    """Remove a stream. Saved runs keep the amounts they used (they snapshot the input)."""
    stream = get_stream(session, stream_id)
    before = _snapshot(stream)
    summary = f"Deleted income stream {_summary(stream)}"
    session.delete(stream)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="income_stream",
        entity_id=stream_id,
        action="delete",
        before=before,
        summary=summary,
    )


# --- Member delete guard -------------------------------------------------------------------


def member_delete_guard(session: Session, member_id: int) -> str | None:
    """Block deleting a Member who owns an income stream (ADR-0009)."""
    owned = session.scalar(
        select(func.count())
        .select_from(IncomeStreamOwner)
        .where(IncomeStreamOwner.member_id == member_id)
    )
    return "they own an income stream" if owned else None


def register() -> None:
    """Register ``projection``'s rule with ``household``. Idempotent; call at app start."""
    household.register_member_delete_guard(member_delete_guard)


# --- fixture -------------------------------------------------------------------------------

FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


def load_fixture_income_streams(session: Session, data_dir: Path) -> int:
    """Create the fixture's income streams through the service, once, while there are none.

    Reads ``income_streams`` from ``<data_dir>/household.json``: each entry is a name, ``type_key``,
    the owner's name (``owner``), ``gross_annual``, ``take_home_annual`` and optional dates or
    Life events. Members are named by name. The fixture's spending phases already include the
    working-years ``start`` phase a salary needs (ADR-0017 section 5).
    """
    path = data_dir / household.FIXTURE_FILENAME
    if not path.is_file():
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("income_streams", [])
    if not entries or session.scalar(select(func.count()).select_from(IncomeStream)):
        return 0
    ids = {m.name: m.id for m in household.list_members(session)}
    for e in entries:
        create_stream(
            session,
            FIXTURE_CONTEXT,
            StreamInput(
                name=e["name"],
                type_key=e["type_key"],
                gross_annual=e["gross_annual"],
                take_home_annual=e["take_home_annual"],
                owners=[OwnerInput(ids[e["owner"]])],
                notes=e.get("notes"),
                start_on=date.fromisoformat(e["start_on"]) if e.get("start_on") else None,
                start_event=e.get("start_event"),
                end_on=date.fromisoformat(e["end_on"]) if e.get("end_on") else None,
                end_event=e.get("end_event"),
            ),
        )
    return len(entries)
