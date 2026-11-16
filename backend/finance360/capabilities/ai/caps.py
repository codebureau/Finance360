"""Spend and question caps, calculated from the log (ADR-0021 section 9).

No separate counter exists to drift: the month's spend is the sum of ``cost_usd_micros`` over the
calendar month in the Household's time zone, and the day's questions are the Ask rows of the local
day (a call refused by a cap is not itself counted).

The two caps cover different things. The **monthly spend cap counts every task**, so Rule figure
capture is bounded by it too. The **daily limit counts Ask questions only**: it is "questions a
day", and reading a page for a figure (a few calls a year) neither uses it up nor is stopped by it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.ai.errors import DAILY_LIMIT, MONTHLY_CAP
from finance360.core.ai_settings import AiSettings
from finance360.core.localtime import DEFAULT_TIME_ZONE, parse_time_zone
from finance360.core.models.ai_log import AiLog
from finance360.core.models.household import HOUSEHOLD_ID, Household


@dataclass(frozen=True)
class CapStatus:
    #: ``monthly_cap`` or ``daily_limit`` when a call must not be made; None when it may.
    reason: str | None = None
    #: The local date the cap lifts.
    resets_on: date | None = None


def _zone(session: Session) -> ZoneInfo:
    household = session.get(Household, HOUSEHOLD_ID)
    return parse_time_zone(household.time_zone if household else DEFAULT_TIME_ZONE)


#: Tasks the daily limit counts and can stop. Every task counts toward the monthly spend.
DAILY_LIMITED_TASKS = frozenset({"ask"})


def _utc_naive(day: date, zone: ZoneInfo) -> datetime:
    """Local midnight of ``day`` as the naive UTC the log stores."""
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC).replace(tzinfo=None)


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def month_spend_micros(session: Session, now: datetime) -> int:
    """Micro-dollars spent so far this month (Household time zone). ``now`` is naive UTC."""
    zone = _zone(session)
    today = now.replace(tzinfo=UTC).astimezone(zone).date()
    start, end = _utc_naive(today.replace(day=1), zone), _utc_naive(_next_month(today), zone)
    total = session.scalar(
        select(func.coalesce(func.sum(AiLog.cost_usd_micros), 0)).where(
            AiLog.occurred_at >= start, AiLog.occurred_at < end
        )
    )
    return int(total or 0)


def questions_today(session: Session, now: datetime) -> int:
    zone = _zone(session)
    today = now.replace(tzinfo=UTC).astimezone(zone).date()
    start, end = _utc_naive(today, zone), _utc_naive(today + timedelta(days=1), zone)
    return int(
        session.scalar(
            select(func.count()).where(
                AiLog.occurred_at >= start,
                AiLog.occurred_at < end,
                AiLog.task.in_(DAILY_LIMITED_TASKS),
                AiLog.outcome != "refused_cap",
            )
        )
        or 0
    )


def check(session: Session, settings: AiSettings, now: datetime, task: str = "ask") -> CapStatus:
    """Whether a ``task`` call may be made now. The monthly cap is checked first; the daily limit
    applies only to a task it counts (Ask)."""
    zone = _zone(session)
    today = now.replace(tzinfo=UTC).astimezone(zone).date()
    if month_spend_micros(session, now) >= settings.monthly_cap_usd_micros:
        return CapStatus(MONTHLY_CAP, _next_month(today))
    if task in DAILY_LIMITED_TASKS and questions_today(session, now) >= settings.daily_questions:
        return CapStatus(DAILY_LIMIT, today + timedelta(days=1))
    return CapStatus()
