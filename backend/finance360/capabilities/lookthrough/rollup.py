"""The Household look-through roll-up service (ADR-0030 section 8). Read-only.

For one date and Dimension, every Account counted in the Position on that date is either analysed
(its ``service.account_look_through`` lines) or Not analysed (cash, property, vehicles, loans...,
and any Account whose look-through cannot be built, with the reason). ``apportion.roll_up`` then
adds them up by Account, Category, Member (a joint Account apportioned by ownership share) and the
Household. The Household total is the Position's net figure on that date, exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from finance360.capabilities.household.service import household_today, list_members
from finance360.capabilities.lookthrough import service
from finance360.capabilities.lookthrough.apportion import AccountIn, LineIn, Rollup, roll_up
from finance360.capabilities.position import derived
from finance360.capabilities.position import service as position_service
from finance360.capabilities.reference import exposure_profiles
from finance360.core.derived import DEFAULT_STALE_DAYS
from finance360.core.errors import FieldError, ValidationError
from finance360.core.exposure import ASSET_CLASS
from finance360.core.money import Money
from finance360.core.transaction import STREAMS


@dataclass(frozen=True)
class ExposureRollup:
    on: date
    dimension_key: str
    dimension_name: str
    rollup: Rollup
    #: The Position's net figure on ``on``: the Household total equals it exactly.
    position_net: Money


def default_date(session: Session, stale_days: int = DEFAULT_STALE_DAYS) -> date:
    """Today, or the latest date a holdings-based Account's Coverage reaches when that is earlier
    (the newest date a derived value can exist for), so an analysed Account is not stale only
    because its imports lag today. With none, today."""
    today = household_today(session)
    reached = [
        derived.default_as_at(inputs.coverage, today)
        for _, inputs in derived.derivers(session, stale_days).values()
        if all(stream in inputs.coverage for stream in STREAMS)
    ]
    return max(reached, default=today)


def exposure_rollup(
    session: Session,
    on: date | None = None,
    dimension: str = ASSET_CLASS,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> ExposureRollup:
    """The Position on ``on`` (default: ``default_date``) split by ``dimension`` at every level."""
    vocab = {d.key: d for d in exposure_profiles.vocabulary(session)}
    if dimension not in vocab:
        raise ValidationError([FieldError("dimension", f"Unknown Dimension {dimension!r}.")])
    view = vocab[dimension]
    on = on or default_date(session, stale_days)
    position = position_service.position_on(session, on)

    facts: list[AccountIn] = []
    categories: list[tuple[str, str]] = []
    for class_ in position.classes:
        for category in class_.categories:
            categories.append((category.key, category.name))
            for line in category.accounts:
                head = (line.account_id, line.name, category.key, line.ownership)
                if line.value is None:
                    facts.append(
                        AccountIn(
                            *head,
                            None,
                            (),
                            None,
                            "The Account has no value in the Position, so it is left out.",
                        )
                    )
                    continue
                found = service.account_look_through(
                    session, line.account_id, on, dimension, stale_days, position
                )
                composition = found.composition
                # None: this kind of Account has no look-through at all (cash, property, loans...),
                # which the screen says once per Category rather than once per Account.
                reason = found.reason if found.applies else None
                lines = (
                    tuple(LineIn(x.kind, x.key, x.amount) for x in composition.lines)
                    if composition is not None
                    else None
                )
                facts.append(
                    AccountIn(
                        *head,
                        line.value,
                        line.shares,
                        lines,
                        reason,
                        composition.flagged if composition is not None else False,
                    )
                )

    rollup = roll_up(
        facts,
        labels=[(label.key, label.name) for label in view.labels],
        members=[(m.id, m.name) for m in list_members(session)],
        categories=categories,
        currency=position.net.household.currency,
    )
    return ExposureRollup(on, view.key, view.name, rollup, position.net.household)
