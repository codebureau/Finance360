"""The Account look-through service (ADR-0030 section 6). Read-only.

Assembles an Account's parts from its derived value (the ``holdings`` parts source: each Holding at
its latest Price x quantity through FX, plus cash), loads the Instruments' current Exposure
profiles and the Dimension's labels, and hands immutable values to the pure
``lines.compose``. The lines sum exactly to the Account's value in the Position. An Account whose
Behaviour does not derive a value has no parts source yet (super options come with #86) and is
reported as not applying; one with no derived value says why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from finance360.capabilities.household.service import household_today
from finance360.capabilities.lookthrough.lines import Composition, Part, compose
from finance360.capabilities.position import accounts, derived
from finance360.capabilities.position import service as position_service
from finance360.capabilities.position.calculation import AccountLine, Position
from finance360.capabilities.reference import exposure_profiles
from finance360.core.derived import DEFAULT_STALE_DAYS, NoDerivedValue
from finance360.core.errors import FieldError, ValidationError
from finance360.core.exposure import ASSET_CLASS
from finance360.core.money import BASE_CURRENCY, convert


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


def _account_line(position: Position, account_id: int) -> AccountLine | None:
    for class_ in position.classes:
        for category in class_.categories:
            for line in category.accounts:
                if line.account_id == account_id:
                    return line
    return None


def account_look_through(
    session: Session,
    account_id: int,
    on: date | None = None,
    dimension: str = ASSET_CLASS,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> LookThrough:
    """The Account's value on ``on`` (default: the latest date its Coverage reaches, at most today)
    split by ``dimension``."""
    accounts.get_account(session, account_id)  # NotFoundError for an unknown Account
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

    def out(
        reason: str | None, composition: Composition | None = None, *, applies: bool = True
    ) -> LookThrough:
        status: Literal["ok", "not_analysed"] = "ok" if composition else "not_analysed"
        return LookThrough(applies, on, status, reason, view.key, view.name, composition)

    if account_id not in found:
        return out("This Account's value is not built from Holdings.", applies=False)
    hook, inputs = found[account_id]
    result = hook(inputs, on)
    if isinstance(result, NoDerivedValue):
        return out(f"No value can be built from Holdings on {on.isoformat()}. {result.reason}")

    line = _account_line(position_service.position_on(session, on), account_id)
    if line is None:
        return out(f"The Account does not count in the Position on {on.isoformat()}.")
    if line.value is None:
        return out(f"The Account has no value in the Position on {on.isoformat()}.")

    # The parts are in the Account's currency; the Position value is in the Base currency.
    parts_value = result.total
    if parts_value.currency != BASE_CURRENCY:
        if line.rate is None:  # cannot happen: a counted non-Base Account has its rate
            return out("No exchange rate is available for this Account.")
        parts_value = convert(parts_value, line.rate, BASE_CURRENCY)
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
    ids = [p.instrument_id for p in parts if p.instrument_id is not None]
    composition = compose(
        position_value=line.value,
        parts_value=parts_value,
        parts=parts,
        dimension=dimension,
        labels=[(label.key, label.name) for label in view.labels],
        on=on,
        profiles=exposure_profiles.current_profile_facts(session, ids),
    )
    return out(None, composition)
