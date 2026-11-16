"""Position's Due rules (ADR-0020 section 1): when a manually valued Account's figure is stale.

An Account is **manually valued** when its value comes only from entered Valuations: no derive hook
(a Behaviour such as holdings-based values it from Imports and Prices, so it is never asked for a
Valuation). The cadences sit here, beside the code that knows the domain, and the Guide line for
each rule (``frontend/src/guide/04-what-to-provide.md``) is tied to them by a test.

A rule reads only; ``today`` and the Effort level are passed in. An Account with no Valuation is due
from its opened date; a closed Account never prompts. The period in the ``subject_key`` is the date
of the figure Position is using, so skipping one item does not silence the next, newer figure.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.position import derived, valuations
from finance360.core.due import (
    IMPORTANT,
    OPTIONAL,
    Cadence,
    DueItem,
    DueRule,
    EffortLevel,
    Every,
    Importance,
)
from finance360.core.models.account import Account
from finance360.core.models.catalogue import Category

MANUAL_VALUATION = "position.manual_valuation"
SUPER_VALUATION = "position.super_valuation"
SLOW_VALUATION = "position.slow_valuation"

#: Each Effort level to its cadence (``None``: not asked at that level).
MANUAL_CADENCE: dict[EffortLevel, Cadence | None] = {
    "light": Every(92),
    "steady": Every(31),
    "thorough": Every(31),
}
SUPER_CADENCE: dict[EffortLevel, Cadence | None] = {
    "light": Every(183),
    "steady": Every(31),
    "thorough": Every(31),
}
SLOW_CADENCE: dict[EffortLevel, Cadence | None] = {
    "light": None,
    "steady": Every(366),
    "thorough": Every(366),
}


def _on(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


def _manually_valued(session: Session, category_keys: Sequence[str]) -> list[Account]:
    """The Accounts of these Categories whose value comes only from entered Valuations."""
    hooks = derived.registered_hooks()
    found = session.scalars(
        select(Account)
        .join(Category, Account.category_id == Category.id)
        .where(Category.key.in_(category_keys))
        .order_by(Account.name, Account.id)
    )
    return [a for a in found if a.account_type.behaviour_key not in hooks]


def _interested(category_keys: Sequence[str]) -> Callable[[Session], bool]:
    def interested(session: Session) -> bool:
        return bool(_manually_valued(session, category_keys))

    return interested


def _check(
    session: Session,
    today: date,
    level: EffortLevel,
    *,
    rule_key: str,
    importance: Importance,
    cadence_by_level: dict[EffortLevel, Cadence | None],
    category_keys: Sequence[str],
    suits: tuple[str, ...],
    using: str,
    none_yet: str,
    effect: str,
) -> tuple[DueItem, ...]:
    cadence = cadence_by_level[level]
    if not isinstance(cadence, Every):
        return ()
    items: list[DueItem] = []
    for account in _manually_valued(session, category_keys):
        if account.closed_on is not None and account.closed_on <= today:
            continue  # closed Accounts never prompt
        if account.opened_on is not None and account.opened_on > today:
            continue  # not yet open
        current = valuations.current_valuations(session, account.id)
        link = f"/accounts/{account.id}"
        if not current:
            items.append(
                DueItem(
                    rule_key=rule_key,
                    subject_key=f"account:{account.id}@none",
                    importance=importance,
                    title=none_yet.format(name=account.name),
                    effect=effect,
                    due_on=account.opened_on or today,  # due from the opened date
                    link=link,
                    suits=suits,
                )
            )
            continue
        last = current[0].as_at
        due_on = last + timedelta(days=cadence.days)
        if due_on <= today:
            items.append(
                DueItem(
                    rule_key=rule_key,
                    subject_key=f"account:{account.id}@{last.isoformat()}",
                    importance=importance,
                    title=using.format(name=account.name, as_at=_on(last)),
                    effect=effect,
                    due_on=due_on,
                    link=link,
                    suits=suits,
                )
            )
    return tuple(items)


def _manual_check(session: Session, today: date, level: EffortLevel) -> tuple[DueItem, ...]:
    return _check(
        session,
        today,
        level,
        rule_key=MANUAL_VALUATION,
        importance=IMPORTANT,
        cadence_by_level=MANUAL_CADENCE,
        category_keys=("cash", "loans_credit"),
        suits=("investments", "whole"),
        using="Your Position uses a balance for {name} from {as_at}",
        none_yet="Your Position has no balance for {name} yet",
        effect="A current balance makes your net position more accurate.",
    )


def _super_check(session: Session, today: date, level: EffortLevel) -> tuple[DueItem, ...]:
    return _check(
        session,
        today,
        level,
        rule_key=SUPER_VALUATION,
        importance=IMPORTANT,
        cadence_by_level=SUPER_CADENCE,
        category_keys=("superannuation",),
        suits=("investments", "whole"),
        using="Your Position uses a super balance for {name} from {as_at}",
        none_yet="Your Position has no super balance for {name} yet",
        effect="A current super balance makes your net position and projection more accurate.",
    )


def _slow_check(session: Session, today: date, level: EffortLevel) -> tuple[DueItem, ...]:
    return _check(
        session,
        today,
        level,
        rule_key=SLOW_VALUATION,
        importance=OPTIONAL,
        cadence_by_level=SLOW_CADENCE,
        category_keys=("property", "vehicles"),
        suits=("investments", "whole"),
        using="Your Position uses an estimate for {name} from {as_at}",
        none_yet="Your Position has no estimate for {name} yet",
        effect="A fresh estimate keeps your net position close to the truth.",
    )


RULES: tuple[DueRule, ...] = (
    DueRule(
        key=MANUAL_VALUATION,
        importance=IMPORTANT,
        cadence=MANUAL_CADENCE,
        suits=("investments", "whole"),
        guide_item="Valuations for cash and loan Accounts",
        interested=_interested(("cash", "loans_credit")),
        check=_manual_check,
    ),
    DueRule(
        key=SUPER_VALUATION,
        importance=IMPORTANT,
        cadence=SUPER_CADENCE,
        suits=("investments", "whole"),
        guide_item="Valuations for super Accounts",
        interested=_interested(("superannuation",)),
        check=_super_check,
    ),
    DueRule(
        key=SLOW_VALUATION,
        importance=OPTIONAL,
        cadence=SLOW_CADENCE,
        suits=("investments", "whole"),
        guide_item="Valuations for property and vehicle Accounts",
        interested=_interested(("property", "vehicles")),
        check=_slow_check,
    ),
)
