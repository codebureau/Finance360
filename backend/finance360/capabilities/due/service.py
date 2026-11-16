"""What's due: evaluate the rules, hide Snoozes, snooze and unsnooze (ADR-0020).

Prompts are never stored: every request evaluates the registered Due rules against the data, so a
prompt cannot disagree with it. Only a Snooze is stored, and it is audited. A Snooze expires by
comparison with today (no job, no clean-up). One rule that raises is reported under
``failed_rules`` and never blanks the list.

Mutating functions take the caller's ``Session`` and an ``AuditContext``, flush and never commit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.core import audit
from finance360.core import due as core_due
from finance360.core.audit import AuditContext
from finance360.core.due import DEFAULT_LEVEL, IMPORTANT, DueItem, EffortLevel
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.localtime import DEFAULT_TIME_ZONE, local_today
from finance360.core.models.due_snooze import DueSnooze
from finance360.core.models.household import HOUSEHOLD_ID, Household

log = logging.getLogger(__name__)

#: The Snooze choices: a week, a month, or "skip this one" (this period only).
WEEK = "week"
MONTH = "month"
SKIP = "skip"
SNOOZE_CHOICES = (WEEK, MONTH, SKIP)
SNOOZE_DAYS = {WEEK: 7, MONTH: 31}
SUBJECT_KEY_MAX = 200


@dataclass(frozen=True)
class SnoozedItem:
    item: DueItem
    snooze_id: int
    #: ``None``: skipped for this period.
    until_on: date | None


@dataclass(frozen=True)
class FailedRule:
    rule_key: str
    #: The Guide name of the rule, for "Could not check <name>".
    name: str


@dataclass(frozen=True)
class WhatsDue:
    level: EffortLevel
    today: date
    items: tuple[DueItem, ...]
    snoozed: tuple[SnoozedItem, ...]
    failed_rules: tuple[FailedRule, ...]


def explicit_interest(suits: tuple[str, ...]) -> bool:
    """The seam for a later, explicit "areas I use" choice (ADR-0020 section 2). It would only add
    prompts, so for now it adds none."""
    return False


def _household(session: Session) -> Household | None:
    return session.get(Household, HOUSEHOLD_ID)


def current_level(session: Session) -> EffortLevel:
    household = _household(session)
    if household is None:
        return DEFAULT_LEVEL
    level = household.effort_level
    return level if level in core_due.LEVELS else DEFAULT_LEVEL  # type: ignore[return-value]


def due_today(session: Session) -> date:
    """Today in the Household's time zone."""
    household = _household(session)
    return local_today(household.time_zone if household else DEFAULT_TIME_ZONE)


def _hidden(snooze: DueSnooze, today: date) -> bool:
    """A Snooze hides its item while ``until_on`` is null or after today."""
    return snooze.until_on is None or snooze.until_on > today


def _order(item: DueItem) -> tuple[bool, date, str, str]:
    """Important first, then the soonest, then a stable tie-break."""
    return (item.importance != IMPORTANT, item.due_on, item.title, item.subject_key)


def whats_due(
    session: Session, *, today: date | None = None, level: EffortLevel | None = None
) -> WhatsDue:
    """The items to show, the Snoozed ones, and any rule that could not be checked.

    ``today`` and ``level`` default to the Household's; tests inject them. Light shows only
    ``important`` rules (the whole filter, ADR-0020 section 3): the cadence decides what is asked at
    all, importance decides what Light shows.
    """
    today = today if today is not None else due_today(session)
    level = level if level is not None else current_level(session)
    snoozes = {(s.rule_key, s.subject_key): s for s in session.scalars(select(DueSnooze))}
    shown: list[DueItem] = []
    snoozed: list[SnoozedItem] = []
    failed: list[FailedRule] = []
    for rule in core_due.rules():
        if level == "light" and rule.importance != IMPORTANT:
            continue
        try:
            if not (rule.interested(session) or explicit_interest(rule.suits)):
                continue
            items = rule.check(session, today, level)
        except Exception:
            log.exception("Due rule %s could not be checked", rule.key)
            failed.append(FailedRule(rule.key, rule.guide_item))
            continue
        for item in items:
            snooze = snoozes.get((item.rule_key, item.subject_key))
            if snooze is not None and _hidden(snooze, today):
                snoozed.append(SnoozedItem(item, snooze.id, snooze.until_on))
            else:
                shown.append(item)
    shown.sort(key=_order)
    snoozed.sort(key=lambda s: _order(s.item))
    return WhatsDue(level, today, tuple(shown), tuple(snoozed), tuple(failed))


def important_count(session: Session) -> int:
    """How many important items are showing (for the sidebar count)."""
    return sum(1 for item in whats_due(session).items if item.importance == IMPORTANT)


# --- Snoozes -------------------------------------------------------------------------------


def _snapshot(snooze: DueSnooze) -> dict[str, object]:
    return {
        "rule_key": snooze.rule_key,
        "subject_key": snooze.subject_key,
        "until_on": snooze.until_on.isoformat() if snooze.until_on else None,
    }


def get_snooze(session: Session, snooze_id: int) -> DueSnooze:
    snooze = session.get(DueSnooze, snooze_id)
    if snooze is None:
        raise NotFoundError(f"Snooze {snooze_id} does not exist.")
    return snooze


def _until(choice: str, today: date) -> date | None:
    return today + timedelta(days=SNOOZE_DAYS[choice]) if choice in SNOOZE_DAYS else None


def snooze(
    session: Session,
    context: AuditContext,
    rule_key: str,
    subject_key: str,
    choice: str,
    *,
    today: date | None = None,
) -> DueSnooze:
    """Snooze one item for a week or a month, or skip this period. Snoozing again updates it."""
    errors: list[FieldError] = []
    if core_due.get(rule_key) is None:
        errors.append(FieldError("rule_key", "That is not a known check."))
    subject = subject_key.strip()
    if not subject or len(subject) > SUBJECT_KEY_MAX:
        errors.append(FieldError("subject_key", "Name the item to snooze."))
    if choice not in SNOOZE_CHOICES:
        errors.append(FieldError("snooze", "Choose a week, a month or skip this one."))
    if errors:
        raise ValidationError(errors)
    until_on = _until(choice, today if today is not None else due_today(session))
    existing = session.scalars(
        select(DueSnooze).where(DueSnooze.rule_key == rule_key, DueSnooze.subject_key == subject)
    ).first()
    if existing is None:
        row = DueSnooze(
            rule_key=rule_key, subject_key=subject, until_on=until_on, created_at=_now()
        )
        session.add(row)
        session.flush()
        audit.record(
            session,
            context,
            entity_type="due_snooze",
            entity_id=row.id,
            action="create",
            after=_snapshot(row),
            summary=f"Snoozed {rule_key} ({subject})",
        )
        return row
    before = _snapshot(existing)
    existing.until_on = until_on
    session.flush()
    audit.record(
        session,
        context,
        entity_type="due_snooze",
        entity_id=existing.id,
        action="update",
        before=before,
        after=_snapshot(existing),
        summary=f"Snoozed {rule_key} ({subject}) again",
    )
    return existing


def unsnooze(session: Session, context: AuditContext, snooze_id: int) -> None:
    row = get_snooze(session, snooze_id)
    before = _snapshot(row)
    session.delete(row)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="due_snooze",
        entity_id=snooze_id,
        action="delete",
        before=before,
        summary=f"Unsnoozed {row.rule_key} ({row.subject_key})",
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as the audit trail stores it
