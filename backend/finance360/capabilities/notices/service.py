"""Notices service (ADR-0033): one wrapper per producer, plus the reads and dismissal.

A wrapper captures a ``Picture``, makes the existing write, flushes, captures again, ``diff``s and
records the Notice in the same session, so the request's single commit makes the write and its
Notice atomic. Capturing and recording run in a savepoint: if they raise, it is logged and the
write proceeds with no Notice (the same stance as Could not check).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from finance360.capabilities.position import service as position
from finance360.capabilities.position import valuations
from finance360.capabilities.questions import service as questions
from finance360.core import notices as core
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError
from finance360.core.models.account import Account
from finance360.core.models.notice import Notice
from finance360.core.models.valuation import Valuation
from finance360.core.money import Money
from finance360.core.notices import Picture, QuestionState

log = logging.getLogger(__name__)

LIST_LIMIT_MAX = 50


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _capture(session: Session) -> Picture:
    net = position.position_on(session).net.household
    views = questions.evaluate(session).questions
    return Picture(
        net,
        tuple(
            QuestionState(v.question.key, v.question.wording, v.question.screen, v.state)
            for v in views
        ),
    )


def _capture_safely(session: Session) -> Picture | None:
    try:
        with session.begin_nested():
            return _capture(session)
    except Exception:
        log.exception("Could not capture the picture before a write; no Notice will be made")
        return None


def _record(
    session: Session, before: Picture | None, kind: str, account_id: int, now: datetime
) -> Notice | None:
    """Capture the after picture, diff and insert the Notice; never raises."""
    if before is None:
        return None
    try:
        with session.begin_nested():
            effect = core.diff(before, _capture(session))
            if effect is None:
                return None
            account = session.get(Account, account_id)
            if account is None:
                return None
            delta = effect.delta
            expire(session, now)
            notice = Notice(
                created_at=now,
                kind=kind,
                headline=core.headline(kind, account.name),
                effect=effect.text,
                delta_minor=delta.amount_minor if delta else None,
                delta_currency=delta.currency if delta else None,
                link_path=effect.link_path,
            )
            session.add(notice)
            session.flush()
            return notice
    except Exception:
        log.exception("Could not record a Notice; the write is kept")
        return None


def expire(session: Session, now: datetime | None = None) -> None:
    """Delete Notices older than the expiry."""
    cutoff = (now or _now()) - timedelta(days=core.NOTICE_EXPIRY_DAYS)
    session.execute(delete(Notice).where(Notice.created_at <= cutoff))


def add_valuation(
    session: Session,
    context: AuditContext,
    account_id: int,
    data: valuations.ValuationInput,
    *,
    today: date | None = None,
) -> tuple[Valuation, Notice | None]:
    before = _capture_safely(session)
    valuation = valuations.add_valuation(session, context, account_id, data, today=today)
    session.flush()
    return valuation, _record(session, before, core.VALUATION_ADDED, account_id, _now())


def correct_valuation(
    session: Session,
    context: AuditContext,
    valuation_id: int,
    data: valuations.ValuationInput,
    *,
    today: date | None = None,
) -> tuple[Valuation, Notice | None]:
    before = _capture_safely(session)
    result = valuations.correct_valuation(session, context, valuation_id, data, today=today)
    if result.id == valuation_id:  # nothing changed: the existing row came back
        return result, None
    session.flush()
    notice = _record(session, before, core.VALUATION_CORRECTED, result.account_id, _now())
    return result, notice


def list_notices(
    session: Session, *, include_dismissed: bool = False, limit: int = core.HOME_LIMIT
) -> list[Notice]:
    """Newest first. Expired Notices are never returned, whether or not they are deleted yet."""
    cutoff = _now() - timedelta(days=core.NOTICE_EXPIRY_DAYS)
    stmt = select(Notice).where(Notice.created_at > cutoff)
    if not include_dismissed:
        stmt = stmt.where(Notice.dismissed_at.is_(None))
    stmt = stmt.order_by(Notice.created_at.desc(), Notice.id.desc()).limit(
        max(1, min(limit, LIST_LIMIT_MAX))
    )
    return list(session.scalars(stmt))


def dismiss(session: Session, notice_id: int) -> None:
    """Dismiss a Notice; dismissing one already dismissed changes nothing."""
    notice = session.get(Notice, notice_id)
    if notice is None:
        raise NotFoundError(f"Notice {notice_id} does not exist.")
    if notice.dismissed_at is None:
        notice.dismissed_at = _now()


def delta_of(notice: Notice) -> Money | None:
    if notice.delta_minor is None or notice.delta_currency is None:
        return None
    return Money(notice.delta_minor, notice.delta_currency)
