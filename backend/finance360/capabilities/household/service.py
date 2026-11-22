"""Household and Member services (ADR-0009 section 7).

Mutating functions take the caller's ``Session`` and an explicit ``AuditContext`` and write the
audit record in that same session, so the change and its record commit or roll back together.
They flush but never commit: the caller (the API request, the startup bootstrap) owns the
transaction.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.household import facts
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.due import LEVELS

# The shared error types moved to ``core.errors``; re-exported so existing imports keep working.
from finance360.core.errors import FieldError as FieldError
from finance360.core.errors import NotFoundError as NotFoundError
from finance360.core.errors import ValidationError as ValidationError
from finance360.core.localtime import (
    DEFAULT_TIME_ZONE,
    InvalidTimeZoneError,
    local_today,
    parse_time_zone,
)
from finance360.core.models.account import Account
from finance360.core.models.catalogue import Category
from finance360.core.models.household import COUPLE_STATUSES, HOUSEHOLD_ID, Household, Member

DEFAULT_HOUSEHOLD_NAME = "My household"
NAME_MAX = 200
#: Earliest date of birth accepted: a typo guard, not a rule about people.
EARLIEST_DATE_OF_BIRTH = date(1900, 1, 1)

#: A guard returns a reason (a short phrase such as "it owns an Account") if the Member must not
#: be deleted, else None. ``position`` registers one for Ownership; ``household`` never imports it.
MemberDeleteGuard = Callable[[Session, int], str | None]
_member_delete_guards: list[MemberDeleteGuard] = []


def register_member_delete_guard(guard: MemberDeleteGuard) -> None:
    """Register a check run before a Member is hard-deleted (ADR-0009 section 2)."""
    if guard not in _member_delete_guards:  # idempotent: every app start registers
        _member_delete_guards.append(guard)


def unregister_member_delete_guard(guard: MemberDeleteGuard) -> None:
    _member_delete_guards.remove(guard)


class MemberInUseError(Exception):
    """The Member cannot be deleted because something still refers to it."""


def validate_name(name: str, *, label: str = "Name") -> tuple[str, list[FieldError]]:
    clean = name.strip()
    if not clean:
        return clean, [FieldError("name", f"{label} is required.")]
    if len(clean) > NAME_MAX:
        return clean, [FieldError("name", f"{label} must be at most {NAME_MAX} characters.")]
    return clean, []


def validate_date_of_birth(value: date | None, today: date) -> list[FieldError]:
    if value is None:
        return [FieldError("date_of_birth", "Date of birth is required.")]
    if value >= today:
        return [FieldError("date_of_birth", "Date of birth must be in the past.")]
    if value < EARLIEST_DATE_OF_BIRTH:
        return [
            FieldError(
                "date_of_birth", f"Date of birth must be on or after {EARLIEST_DATE_OF_BIRTH}."
            )
        ]
    return []


def _member_snapshot(member: Member) -> dict[str, Any]:
    return {"name": member.name, "date_of_birth": member.date_of_birth.isoformat()}


# --- Household -----------------------------------------------------------------------------


def find_household(session: Session) -> Household | None:
    return session.get(Household, HOUSEHOLD_ID)


def get_household(session: Session) -> Household:
    household = find_household(session)
    if household is None:
        raise NotFoundError("The Household has not been created yet.")
    return household


def ensure_household(
    session: Session,
    name: str = DEFAULT_HOUSEHOLD_NAME,
    time_zone: str = DEFAULT_TIME_ZONE,
    effort_level: str | None = None,
) -> Household:
    """Create the single Household on first run. Not audited (system bootstrap, ADR-0009 s5).
    ``effort_level`` ``None`` leaves the column's default (Light)."""
    household = find_household(session)
    if household is None:
        household = Household(id=HOUSEHOLD_ID, name=name, time_zone=time_zone)
        if effort_level is not None:
            household.effort_level = effort_level
        session.add(household)
        session.flush()
    return household


def rename_household(session: Session, context: AuditContext, name: str) -> Household:
    clean, errors = validate_name(name, label="Household name")
    if errors:
        raise ValidationError(errors)
    household = get_household(session)
    if household.name != clean:
        before = {"name": household.name}
        household.name = clean
        session.flush()
        audit.record(
            session,
            context,
            entity_type="household",
            entity_id=household.id,
            action="update",
            before=before,
            after={"name": clean},
            summary=f"Renamed the Household from '{before['name']}' to '{clean}'",
        )
    return household


def validate_time_zone(value: str) -> tuple[str, list[FieldError]]:
    clean = value.strip()
    try:
        parse_time_zone(clean)
    except InvalidTimeZoneError:
        return clean, [
            FieldError("time_zone", "Enter an IANA time zone name, e.g. Australia/Melbourne.")
        ]
    return clean, []


def set_time_zone(session: Session, context: AuditContext, time_zone: str) -> Household:
    clean, errors = validate_time_zone(time_zone)
    if errors:
        raise ValidationError(errors)
    household = get_household(session)
    if household.time_zone != clean:
        before = {"time_zone": household.time_zone}
        household.time_zone = clean
        session.flush()
        audit.record(
            session,
            context,
            entity_type="household",
            entity_id=household.id,
            action="update",
            before=before,
            after={"time_zone": clean},
            summary=f"Changed the Household time zone from {before['time_zone']} to {clean}",
        )
    return household


def validate_effort_level(value: str) -> tuple[str, list[FieldError]]:
    clean = value.strip().lower()
    if clean not in LEVELS:
        return clean, [FieldError("effort_level", "Choose Light, Steady or Thorough.")]
    return clean, []


def set_effort_level(session: Session, context: AuditContext, effort_level: str) -> Household:
    """Set how much What's due asks (ADR-0020 section 3). An ordinary, audited Household update."""
    clean, errors = validate_effort_level(effort_level)
    if errors:
        raise ValidationError(errors)
    household = get_household(session)
    if household.effort_level != clean:
        before = {"effort_level": household.effort_level}
        household.effort_level = clean
        session.flush()
        audit.record(
            session,
            context,
            entity_type="household",
            entity_id=household.id,
            action="update",
            before=before,
            after={"effort_level": clean},
            summary=f"Changed the Effort level from {before['effort_level']} to {clean}",
        )
    return household


def household_today(session: Session) -> date:
    """Today's date in the Household's time zone: the "today" for every date check and default."""
    household = find_household(session)
    return local_today(household.time_zone if household else DEFAULT_TIME_ZONE)


# --- Household facts (ADR-0029 section 1) ---------------------------------------------------

PROPERTY_CATEGORY_KEY = "property"
#: Sentinel for "leave this fact unchanged" (``None`` means "clear it back to not entered").
UNCHANGED: Any = object()


def open_property_accounts(session: Session, today: date | None = None) -> list[Account]:
    """The open Property Accounts, by name: the choices for the principal home."""
    on = today or household_today(session)
    return list(
        session.scalars(
            select(Account)
            .join(Category, Account.category_id == Category.id)
            .where(Category.key == PROPERTY_CATEGORY_KEY)
            .where(Account.closed_on.is_(None) | (Account.closed_on > on))
            .order_by(Account.name, Account.id)
        )
    )


def effective_facts(session: Session, today: date | None = None) -> facts.EffectiveFacts:
    """The Household's facts as a rule may use them: defaults resolved, each labelled, plus the
    state ("ok", "needs a choice of home" or "not modelled"). The one entry point for #69."""
    household = get_household(session)
    members = len(list_members(session))
    open_ids = frozenset(a.id for a in open_property_accounts(session, today))
    stored = facts.StoredFacts(
        household.couple_status, household.homeowner, household.principal_home_account_id
    )
    return facts.resolve_facts(stored, members, open_ids)


def _fact_text(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def set_household_facts(
    session: Session,
    context: AuditContext,
    *,
    couple_status: str | None = UNCHANGED,
    homeowner: bool | None = UNCHANGED,
    principal_home_account_id: int | None = UNCHANGED,
    today: date | None = None,
) -> Household:
    """Set, clear or reset (``None``) each fact given; one audit entry per fact that changes.

    A couple status that contradicts the Member count is accepted: it is reported as not modelled
    (ADR-0029), not refused. A chosen home must be an open Property Account.
    """
    errors: list[FieldError] = []
    if couple_status is not UNCHANGED and couple_status not in (None, *COUPLE_STATUSES):
        errors.append(FieldError("couple_status", "Choose single or couple."))
    home_name: str | None = None
    if principal_home_account_id is not UNCHANGED and principal_home_account_id is not None:
        homes = {a.id: a.name for a in open_property_accounts(session, today)}
        if principal_home_account_id not in homes:
            errors.append(
                FieldError("principal_home_account_id", "Choose one of the open Property Accounts.")
            )
        else:
            home_name = homes[principal_home_account_id]
    if errors:
        raise ValidationError(errors)

    household = get_household(session)
    changes: list[tuple[str, object, str]] = []
    if couple_status is not UNCHANGED:
        changes.append(("couple_status", couple_status, "couple status"))
    if homeowner is not UNCHANGED:
        changes.append(("homeowner", homeowner, "homeowner"))
    if principal_home_account_id is not UNCHANGED:
        changes.append(("principal_home_account_id", principal_home_account_id, "principal home"))
    for column, value, label in changes:
        old = getattr(household, column)
        if old == value:
            continue
        setattr(household, column, value)
        session.flush()
        shown = home_name if column == "principal_home_account_id" and value else value
        audit.record(
            session,
            context,
            entity_type="household",
            entity_id=household.id,
            action="update",
            before={column: old},
            after={column: value},
            summary=(
                f"Reset the Household {label} to not entered"
                if value is None
                else f"Set the Household {label} to {_fact_text(shown)}"
            ),
        )
    return household


# --- Members -------------------------------------------------------------------------------


def list_members(session: Session) -> list[Member]:
    return list(session.scalars(select(Member).order_by(Member.date_of_birth, Member.id)))


def get_member(session: Session, member_id: int) -> Member:
    member = session.get(Member, member_id)
    if member is None:
        raise NotFoundError(f"Member {member_id} does not exist.")
    return member


def _validated(
    session: Session, name: str, date_of_birth: date | None, today: date | None
) -> tuple[str, date]:
    clean, errors = validate_name(name)
    errors += validate_date_of_birth(date_of_birth, today or household_today(session))
    if errors or date_of_birth is None:
        raise ValidationError(errors)
    return clean, date_of_birth


def add_member(
    session: Session,
    context: AuditContext,
    name: str,
    date_of_birth: date | None,
    *,
    today: date | None = None,
) -> Member:
    clean, dob = _validated(session, name, date_of_birth, today)
    household = get_household(session)
    member = Member(household_id=household.id, name=clean, date_of_birth=dob)
    session.add(member)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="member",
        entity_id=member.id,
        action="create",
        after=_member_snapshot(member),
        summary=f"Added Member '{member.name}'",
    )
    return member


def update_member(
    session: Session,
    context: AuditContext,
    member_id: int,
    name: str,
    date_of_birth: date | None,
    *,
    today: date | None = None,
) -> Member:
    clean, dob = _validated(session, name, date_of_birth, today)
    member = get_member(session, member_id)
    before = _member_snapshot(member)
    member.name, member.date_of_birth = clean, dob
    after = _member_snapshot(member)
    if after != before:
        session.flush()
        audit.record(
            session,
            context,
            entity_type="member",
            entity_id=member.id,
            action="update",
            before=before,
            after=after,
            summary=f"Edited Member '{clean}'",
        )
    return member


def delete_member(session: Session, context: AuditContext, member_id: int) -> None:
    member = get_member(session, member_id)
    for guard in _member_delete_guards:
        reason = guard(session, member_id)
        if reason:
            raise MemberInUseError(f"Cannot delete {member.name}: {reason}.")
    before = _member_snapshot(member)
    session.delete(member)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="member",
        entity_id=member_id,
        action="delete",
        before=before,
        summary=f"Deleted Member '{before['name']}'",
    )


# --- First run -----------------------------------------------------------------------------

FIXTURE_FILENAME = "household.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


def bootstrap(session: Session, data_dir: Path) -> Household:
    """Create the Household on first run, from ``<data_dir>/household.json`` if it exists.

    The review slot copies the synthetic household there (``fixtures/sample_household``). The file
    is read only while no Household exists, so it never overwrites real edits. The Household row
    itself is not audited; Members from the fixture are, with source ``fixture``.
    """
    existing = find_household(session)
    if existing is not None:
        return existing
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file():
        return ensure_household(session)
    data = json.loads(path.read_text(encoding="utf-8"))
    household = ensure_household(
        session,
        data.get("household", {}).get("name", DEFAULT_HOUSEHOLD_NAME),
        data.get("household", {}).get("time_zone", DEFAULT_TIME_ZONE),
        data.get("household", {}).get("effort_level"),
    )
    for entry in data.get("members", []):
        add_member(
            session,
            FIXTURE_CONTEXT,
            entry["name"],
            date.fromisoformat(entry["date_of_birth"]),
        )
    return household


def load_fixture_facts(session: Session, data_dir: Path) -> None:
    """Set the fixture's Household facts, through the service. Call it only on the run that created
    the fixture's Accounts (it names the principal home by Account name), so a later start never
    undoes the owner's own edit. Reads ``household.facts`` from ``<data_dir>/household.json``."""
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file():
        return
    entry = json.loads(path.read_text(encoding="utf-8")).get("household", {}).get("facts", {})
    home = entry.get("principal_home")
    home_id = None
    if home is not None:
        home_id = next((a.id for a in open_property_accounts(session) if a.name == home), None)
        if home_id is None:
            raise ValidationError([FieldError("principal_home", f"No open Property '{home}'.")])
    set_household_facts(
        session,
        FIXTURE_CONTEXT,
        couple_status=entry.get("couple_status", UNCHANGED),
        homeowner=entry.get("homeowner", UNCHANGED),
        principal_home_account_id=home_id if home is not None else UNCHANGED,
    )
