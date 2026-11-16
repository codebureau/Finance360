"""The Tax Capability's Due rule (ADR-0020 section 1): an income statement for the year just ended.

An income statement arrives in mid-July, so the prompt starts on **15 July** at every Effort
level (a yearly event, not a rhythm to scale). Only the latest ended financial year prompts, so a
first import of history gives no backlog. A Member is asked only when they already have an income
statement for an earlier year: implicit interest, so a first-year user is not prompted until one is
on record.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.tax.record_types import INCOME_STATEMENT, financial_year_span
from finance360.core.due import (
    IMPORTANT,
    Cadence,
    DueItem,
    DueRule,
    EffortLevel,
    FromDate,
)
from finance360.core.models.household import Member
from finance360.core.models.tax_record import TaxRecord
from finance360.core.rulepack import fy_label, fy_of

INCOME_STATEMENT_RULE = "tax.income_statement"

#: Mid-July, at every level.
CADENCE: dict[EffortLevel, Cadence | None] = {
    "light": FromDate(7, 15),
    "steady": FromDate(7, 15),
    "thorough": FromDate(7, 15),
}


def _statements(session: Session) -> list[TaxRecord]:
    return list(
        session.scalars(
            select(TaxRecord).where(
                TaxRecord.record_type == INCOME_STATEMENT, TaxRecord.member_id.is_not(None)
            )
        )
    )


def _interested(session: Session) -> bool:
    return bool(_statements(session))


def _check(session: Session, today: date, level: EffortLevel) -> tuple[DueItem, ...]:
    cadence = CADENCE[level]
    if not isinstance(cadence, FromDate):
        return ()
    year = fy_of(today) - 1  # the latest ended financial year, keyed by the year it ends in
    if today < cadence.on(year):
        return ()
    start, end = financial_year_span(year)
    earlier: set[int] = set()
    current: set[int] = set()
    for record in _statements(session):
        member_id = record.member_id
        if member_id is None:
            continue
        last = record.to_on or record.from_on
        if record.from_on <= end and last >= start:
            current.add(member_id)
        elif last < start:
            earlier.add(member_id)
    members = {m.id: m for m in session.scalars(select(Member).order_by(Member.id))}
    label = fy_label(year)
    return tuple(
        DueItem(
            rule_key=INCOME_STATEMENT_RULE,
            subject_key=f"member:{member_id}@fy{year}",
            importance=IMPORTANT,
            title=f"Your tax estimate for {label} has no income statement for "
            f"{members[member_id].name}",
            effect=(
                "An income statement replaces the salary and tax-withheld lines, "
                "so the estimate is more accurate."
            ),
            due_on=cadence.on(year),
            link="/tax/records",
            suits=("tax", "whole"),
        )
        for member_id in sorted(earlier - current)
        if member_id in members
    )


RULES: tuple[DueRule, ...] = (
    DueRule(
        key=INCOME_STATEMENT_RULE,
        importance=IMPORTANT,
        cadence=CADENCE,
        suits=("tax", "whole"),
        guide_item="An income statement for each employer, for each Member",
        interested=_interested,
        check=_check,
    ),
)
