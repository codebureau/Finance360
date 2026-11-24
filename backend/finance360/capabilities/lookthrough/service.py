"""The Account look-through service (ADR-0030 section 6). Read-only.

Assembles an Account's parts from a small ordered registry of parts sources:

- ``holdings``: an Account whose Behaviour derives a value (each Holding at its latest Price x
  quantity through FX, plus cash);
- ``super_statement``: a ``super_fund_account`` Account, whose parts are the Investment option
  values on its latest current Super statement on or before the date, resolved through the option
  mapping (``option_links``). Option values are never scaled to the Account's value.

It loads the Instruments' current Exposure profiles and the Dimension's labels, and hands
immutable values to the pure ``lines.compose``. The lines sum exactly to the Account's value in the
Position. An Account no source applies to is reported as not applying; one whose source has
nothing to build from says why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.household.service import household_today
from finance360.capabilities.lookthrough.lines import Composition, Part, compose
from finance360.capabilities.lookthrough.option_links import links_for_account
from finance360.capabilities.position import accounts, derived
from finance360.capabilities.position import service as position_service
from finance360.capabilities.position.calculation import AccountLine, Position
from finance360.capabilities.position.super_statements import SUPER_ACCOUNT_TYPE
from finance360.capabilities.reference import exposure_profiles
from finance360.core.derived import DEFAULT_STALE_DAYS, NoDerivedValue
from finance360.core.errors import FieldError, ValidationError
from finance360.core.exposure import ASSET_CLASS, normalise_option_name
from finance360.core.models.super_statement import SuperStatement
from finance360.core.money import BASE_CURRENCY, Money, convert


@dataclass(frozen=True)
class LookThrough:
    #: False when the Account's value is not built from Holdings (no panel is shown).
    applies: bool
    on: date
    status: Literal["ok", "not_analysed"]
    #: Why the Account is Not analysed.
    reason: str | None
    dimension_key: str
    dimension_name: str
    composition: Composition | None
    #: Where the parts come from: ``holdings`` or ``super_statement`` (``None`` when neither
    #: applies).
    source: Literal["holdings", "super_statement"] | None = None
    #: The Super statement the parts come from (``super_statement`` source, when there is one).
    statement_id: int | None = None
    statement_date: date | None = None


def _account_line(position: Position, account_id: int) -> AccountLine | None:
    for class_ in position.classes:
        for category in class_.categories:
            for line in category.accounts:
                if line.account_id == account_id:
                    return line
    return None


def _latest_statement(session: Session, account_id: int, on: date) -> SuperStatement | None:
    """The Account's latest current Super statement whose period ends on or before ``on``."""
    return session.scalars(
        select(SuperStatement)
        .where(
            SuperStatement.account_id == account_id,
            SuperStatement.superseded_at.is_(None),
            SuperStatement.period_end <= on,
        )
        .order_by(SuperStatement.period_end.desc(), SuperStatement.id.desc())
        .limit(1)
    ).first()


def _in_base(value: Money, line: AccountLine) -> Money | None:
    """``value`` (in the Account's currency) in the Base currency, at the Position's rate."""
    if value.currency == BASE_CURRENCY:
        return value
    if line.rate is None:  # cannot happen: a counted non-Base Account has its rate
        return None
    return convert(value, line.rate, BASE_CURRENCY)


def account_look_through(
    session: Session,
    account_id: int,
    on: date | None = None,
    dimension: str = ASSET_CLASS,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> LookThrough:
    """The Account's value on ``on`` (default: the latest date its Coverage reaches, at most today)
    split by ``dimension``."""
    account = accounts.get_account(session, account_id)  # NotFoundError for an unknown Account
    vocab = {d.key: d for d in exposure_profiles.vocabulary(session)}
    if dimension not in vocab:
        raise ValidationError([FieldError("dimension", f"Unknown Dimension {dimension!r}.")])
    view = vocab[dimension]
    today = household_today(session)
    found = derived.derivers(session, stale_days, only=account_id)
    # Default date: the latest one the Account's Streams' Coverage reaches (at most today), as
    # the Derived value panel does, so the two always show the same date.
    on = on or (
        derived.default_as_at(found[account_id][1].coverage, today)
        if account_id in found
        else today
    )
    is_super = account_id not in found and account.account_type.key == SUPER_ACCOUNT_TYPE
    source: Literal["holdings", "super_statement"] | None = (
        "holdings" if account_id in found else "super_statement" if is_super else None
    )
    statement: SuperStatement | None = None

    def out(
        reason: str | None, composition: Composition | None = None, *, applies: bool = True
    ) -> LookThrough:
        status: Literal["ok", "not_analysed"] = "ok" if composition else "not_analysed"
        return LookThrough(
            applies,
            on,
            status,
            reason,
            view.key,
            view.name,
            composition,
            source,
            statement.id if statement else None,
            statement.period_end if statement else None,
        )

    if source is None:
        return out("This Account's value is not built from Holdings.", applies=False)

    parts: list[Part]
    parts_total: Money
    if source == "holdings":
        hook, inputs = found[account_id]
        result = hook(inputs, on)
        if isinstance(result, NoDerivedValue):
            return out(f"No value can be built from Holdings on {on.isoformat()}. {result.reason}")
        parts_total = result.total
        parts = [
            Part(
                d.instrument_id,
                inputs.instrument_names.get(d.instrument_id, f"Instrument {d.instrument_id}")
                if d.instrument_id is not None
                else f"Cash ({d.currency})",
                d.value,
                d.currency if d.instrument_id is None else None,
            )
            for d in result.lines
        ]
    else:
        statement = _latest_statement(session, account_id, on)
        if statement is None:
            return out(
                f"There is no Super statement on or before {on.isoformat()}. Provide a Super "
                "statement to see what this Account's Investment options hold."
            )
        links = links_for_account(session, account_id)
        # An option stated as nothing (or less) adds no part; whatever it leaves is Unallocated.
        parts = []
        for option in statement.options:
            if option.value_minor <= 0:
                continue
            link = links.get(normalise_option_name(option.name))
            parts.append(
                Part(
                    link.instrument_id if link else None,
                    option.name,
                    Money(option.value_minor, statement.currency),
                    option=option.name,
                )
            )
        parts_total = Money(sum(p.amount.amount_minor for p in parts), statement.currency)

    line = _account_line(position_service.position_on(session, on), account_id)
    if line is None:
        return out(f"The Account does not count in the Position on {on.isoformat()}.")
    if line.value is None:
        return out(f"The Account has no value in the Position on {on.isoformat()}.")

    # The parts are in the Account's currency; the Position value is in the Base currency.
    parts_value = _in_base(parts_total, line)
    if parts_value is None:
        return out("No exchange rate is available for this Account.")
    ids = [p.instrument_id for p in parts if p.instrument_id is not None]
    # A fund-of-funds resolves through its underlying funds: load theirs too, and every name.
    profiles = exposure_profiles.profile_facts_closure(session, ids)
    named = {*ids, *profiles, *(r for ps in profiles.values() for p in ps for r, _ in p.references)}
    names_of = exposure_profiles.instrument_names(session, named)
    change_since = (
        statement.period_end
        if statement is not None
        and line.valuation_as_at is not None
        and line.valuation_as_at > statement.period_end
        else None
    )
    composition = compose(
        position_value=line.value,
        parts_value=parts_value,
        parts=parts,
        dimension=dimension,
        labels=[(label.key, label.name) for label in view.labels],
        on=on,
        profiles=profiles,
        names_of=names_of,
        change_since=change_since,
    )
    return out(None, composition)
