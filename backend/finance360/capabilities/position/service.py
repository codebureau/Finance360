"""Assemble the inputs for the pure Position calculation. Read-only.

This is the only place that touches the database for a Position: it loads Accounts (closed ones
too: whether they count is the calculation's call), their current Valuations, current FX rates,
the Members and the catalogue, then hands immutable values to ``calculation.compute_position``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.capabilities.position import accounts, derived, valuations
from finance360.capabilities.position.calculation import (
    AccountFacts,
    CategoryInfo,
    HistoryStep,
    Position,
    compute_history,
    compute_position,
    history_dates,
)
from finance360.capabilities.reference import fx_rates
from finance360.core.errors import FieldError, ValidationError
from finance360.core.fx import FxPoint
from finance360.core.models.catalogue import Category
from finance360.core.models.valuation import Valuation
from finance360.core.valuation import ValuationPoint


@dataclass(frozen=True)
class Inputs:
    """Everything the calculation needs, loaded once."""

    accounts: tuple[AccountFacts, ...]
    categories: tuple[CategoryInfo, ...]
    member_ids: tuple[int, ...]
    fx: tuple[FxPoint, ...]
    #: Accounts valued by a Behaviour's derive hook (empty when none is holdings based).
    derivers: derived.Derivers


def load_inputs(session: Session) -> Inputs:
    by_account: dict[int, list[ValuationPoint]] = {}
    current = select(Valuation).where(Valuation.superseded_at.is_(None))
    for valuation in session.scalars(current):
        by_account.setdefault(valuation.account_id, []).append(valuations.point(valuation))
    facts = tuple(
        AccountFacts(
            id=a.id,
            name=a.name,
            category_key=a.category.key,
            currency=a.currency,
            ownership=a.ownership,
            owners=tuple((o.member_id, o.share_bp) for o in a.owners),
            tags=tuple(t.name for t in a.tags),
            opened_on=a.opened_on,
            closed_on=a.closed_on,
            valuations=tuple(by_account.get(a.id, ())),
        )
        for a in accounts.list_accounts(session, include_closed=True)
    )
    return Inputs(
        facts,
        tuple(
            CategoryInfo(c.key, c.name, c.class_)
            for c in session.scalars(select(Category).order_by(Category.id))
        ),
        tuple(m.id for m in household.list_members(session)),
        tuple(fx_rates.point(r) for r in fx_rates.current_fx_rates(session)),
        derived.derivers(session),
    )


def position_on(session: Session, on: date | None = None, tag: str | None = None) -> Position:
    """The Position on ``on`` (default: today in the Household zone), optionally for one Tag."""
    on = on or household.household_today(session)
    i = load_inputs(session)
    return compute_position(
        on=on,
        accounts=derived.with_derived(i.accounts, i.derivers, on),
        categories=i.categories,
        member_ids=i.member_ids,
        fx=i.fx,
        tag=tag,
    )


def position_history(
    session: Session,
    start: date | None = None,
    end: date | None = None,
    step: HistoryStep = "month",
    tag: str | None = None,
    max_points: int | None = None,
) -> list[Position]:
    """The Position at month-ends (or quarter-ends) from ``start`` to ``end``, then ``end``.

    ``end`` defaults to today in the Household zone and ``start`` to the earliest Valuation. The
    inputs load once; each date is evaluated in memory by the same pure calculation.
    """
    end = end or household.household_today(session)
    if start is not None and start > end:
        raise ValidationError([FieldError("from", "The start must not be after the end.")])
    i = load_inputs(session)
    if start is None:
        firsts = [v.as_at for a in i.accounts for v in a.valuations]
        start = min(firsts) if firsts else end
    dates = history_dates(start, end, step)
    if max_points is not None and len(dates) > max_points:
        raise ValidationError([FieldError("from", "Too many points: narrow the range.")])
    # A derived point belongs to one date, so each date gets its own inputs; the calculation is
    # still the single-date ``compute_position`` (via ``compute_history``) with nothing special.
    return [
        compute_history(
            dates=[on],
            accounts=derived.with_derived(i.accounts, i.derivers, on),
            categories=i.categories,
            member_ids=i.member_ids,
            fx=i.fx,
            tag=tag,
        )[0]
        for on in dates
    ]
