"""The audit trail's read side (ADR-0009 section 5), beside the writer in core.audit.

Filter and page ``audit_event`` and ``auth_event``, and turn snapshots into readable changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session

from finance360.core.localtime import parse_time_zone
from finance360.core.models.audit import AuditEvent
from finance360.core.models.auth import AuthEvent, Login
from finance360.core.models.household import Household
from finance360.core.money import Money, MoneyError, format_amount

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

#: The only ``auth_event.detail`` keys ever returned. ``detail`` is meant to hold non-sensitive
#: facts only; this allow-list makes that a property of the read side too, so a secret that
#: was ever put there by mistake cannot reach a client.
AUTH_DETAIL_KEYS = ("client", "user_agent", "via", "trusted", "seconds")

AUTH_KIND_LABELS = {
    "setup": "Owner set up",
    "login": "Signed in",
    "login_failed": "Sign-in failed",
    "lockout": "Locked out",
    "logout": "Signed out",
    "password_changed": "Password changed",
    "password_reset": "Password reset",
    "totp_begin": "Authenticator enrolment started",
    "totp_confirmed": "Authenticator enabled",
    "totp_disabled": "Authenticator disabled",
}


@dataclass(frozen=True)
class AuditQuery:
    entity_type: str | None = None
    entity_id: int | None = None
    #: An Account's trail: its own records plus its Valuations, identifiers and Super statements.
    account_id: int | None = None
    #: Inclusive local dates in the Household's time zone.
    date_from: date | None = None
    date_to: date | None = None
    limit: int = DEFAULT_LIMIT
    offset: int = 0


@dataclass(frozen=True)
class Change:
    field: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class AuditEntry:
    id: int
    occurred_at: datetime
    action: str
    entity_type: str
    entity_id: int
    actor: str
    actor_ref: str
    source: str
    summary: str
    changes: list[Change]


@dataclass(frozen=True)
class AuthEntry:
    id: int
    occurred_at: datetime
    kind: str
    label: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    total: int


# --- filtering -----------------------------------------------------------------------------


def _utc_bounds(
    session: Session, date_from: date | None, date_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """Local calendar days -> a half-open range of naive UTC, as stored."""
    household = session.get(Household, 1)
    zone = parse_time_zone(household.time_zone if household else "UTC")

    def utc(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC).replace(tzinfo=None)

    start = utc(date_from) if date_from else None
    end = utc(date_to + timedelta(days=1)) if date_to else None
    return start, end


def _total(session: Session, query: Select[Any]) -> int:
    return session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0


def list_audit(session: Session, q: AuditQuery) -> Page[AuditEntry]:
    where: list[ColumnElement[bool]] = []
    if q.entity_type:
        where.append(AuditEvent.entity_type == q.entity_type)
    if q.entity_id is not None:
        where.append(AuditEvent.entity_id == q.entity_id)
    if q.account_id is not None:
        in_snapshot = or_(
            func.json_extract(AuditEvent.after, "$.account_id") == q.account_id,
            func.json_extract(AuditEvent.before, "$.account_id") == q.account_id,
        )
        where.append(
            or_(
                and_(AuditEvent.entity_type == "account", AuditEvent.entity_id == q.account_id),
                and_(
                    AuditEvent.entity_type.in_(
                        (
                            "valuation",
                            "account_identifier",
                            "super_statement",
                            "investment_option_link",
                        )
                    ),
                    in_snapshot,
                ),
            )
        )
    start, end = _utc_bounds(session, q.date_from, q.date_to)
    if start:
        where.append(AuditEvent.occurred_at >= start)
    if end:
        where.append(AuditEvent.occurred_at < end)
    query = select(AuditEvent).where(*where)
    rows = session.scalars(
        query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(q.limit)
        .offset(q.offset)
    )
    labels = _ActorLabels(session)
    return Page([_entry(e, labels.of(e)) for e in rows], _total(session, query))


def list_auth_events(
    session: Session,
    *,
    kind: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Page[AuthEntry]:
    where: list[ColumnElement[bool]] = []
    if kind:
        where.append(AuthEvent.kind == kind)
    start, end = _utc_bounds(session, date_from, date_to)
    if start:
        where.append(AuthEvent.occurred_at >= start)
    if end:
        where.append(AuthEvent.occurred_at < end)
    query = select(AuthEvent).where(*where)
    rows = session.scalars(
        query.order_by(AuthEvent.occurred_at.desc(), AuthEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [
        AuthEntry(
            id=e.id,
            occurred_at=e.occurred_at.replace(tzinfo=UTC),
            kind=e.kind,
            label=AUTH_KIND_LABELS.get(e.kind, e.kind),
            detail={k: e.detail[k] for k in AUTH_DETAIL_KEYS if k in (e.detail or {})},
        )
        for e in rows
    ]
    return Page(items, _total(session, query))


# --- presentation --------------------------------------------------------------------------


class _ActorLabels:
    """``login:<id>`` -> a readable label, looking each Login up at most once."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._roles: dict[int, str | None] = {}

    def of(self, event: AuditEvent) -> str:
        kind, ref = event.actor_kind, event.actor_ref
        if kind == "owner":
            if ref.startswith("login:") and ref[6:].isdigit():
                login_id = int(ref[6:])
                if login_id not in self._roles:
                    login = self._session.get(Login, login_id)
                    self._roles[login_id] = login.role if login else None
                role = self._roles[login_id]
                return role.capitalize() if role else f"Login {login_id}"
            return "Owner"
        return f"{kind.capitalize()} ({ref})" if ref else kind.capitalize()


def _entry(event: AuditEvent, actor: str) -> AuditEntry:
    return AuditEntry(
        id=event.id,
        occurred_at=event.occurred_at.replace(tzinfo=UTC),
        action=event.action,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        actor=actor,
        actor_ref=event.actor_ref,
        source=event.source,
        summary=event.summary,
        changes=diff(event.before, event.after),
    )


def diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[Change]:
    """The fields that differ between two snapshots, in readable form (unchanged ones omitted)."""
    old, new = before or {}, after or {}
    keys = [*new, *(k for k in old if k not in new)]
    return [
        Change(_label(k), _show(old.get(k)), _show(new.get(k)))
        for k in keys
        if k != "id" and old.get(k) != new.get(k)
    ]


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize().replace("Fx", "FX")


def _show(value: Any) -> str | None:
    """Exact text for a snapshot value. Money is ``{minor, ccy}``; it is never a float here."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        value = cast("dict[str, Any]", value)
        if value.keys() == {"minor", "ccy"}:
            try:
                money = Money(value["minor"], value["ccy"])
            except MoneyError:
                return f"{value['ccy']} {value['minor']} (minor units)"
            return f"{money.currency} {format_amount(money)}"
        if value.keys() == {"member_id", "share_bp"}:
            bp = value["share_bp"]
            return f"Member {value['member_id']}: {bp // 100}.{bp % 100:02d}%"
        return ", ".join(f"{k}: {_show(v)}" for k, v in value.items())
    if isinstance(value, list):
        items = cast("list[Any]", value)
        return ", ".join(s for s in (_show(v) for v in items) if s is not None) or "none"
    return str(value)
