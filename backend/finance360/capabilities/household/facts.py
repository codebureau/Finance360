"""Effective Household facts (ADR-0029 section 1): a pure resolver, no database.

``resolve_facts`` turns the stored (nullable) facts, the Member count and the open Property
Accounts into the facts a rule may use, each labelled "entered" or "default, not entered", plus the
state the Age Pension reads: ``ok``, ``needs_home_choice`` or ``not_modelled`` (with reasons). It
never guesses between several Property Accounts and never raises for a Household that does not fit.
"""

from __future__ import annotations

from dataclasses import dataclass

ENTERED = "entered"
DEFAULT = "default"
LABELS = {ENTERED: "entered", DEFAULT: "default, not entered"}

STATE_OK = "ok"
STATE_NEEDS_HOME_CHOICE = "needs_home_choice"
STATE_NOT_MODELLED = "not_modelled"

#: A Household of more than two Members is not a pension couple or a single person.
MAX_MEMBERS = 2


@dataclass(frozen=True)
class StoredFacts:
    """The three columns as stored; ``None`` is "not entered"."""

    couple_status: str | None = None
    homeowner: bool | None = None
    principal_home_account_id: int | None = None


@dataclass(frozen=True)
class Fact[T]:
    """One effective value and where it came from (``entered`` or ``default``). ``label`` is the
    words shown beside it. ``value`` is ``None`` when nothing can be resolved or applies."""

    value: T | None
    source: str

    @property
    def label(self) -> str:
        return LABELS[self.source]


@dataclass(frozen=True)
class EffectiveFacts:
    couple_status: Fact[str]
    homeowner: Fact[bool]
    #: The Property Account the means test exempts. ``None`` for a non-homeowner, for no Property
    #: Account, or when a choice is needed.
    principal_home_account_id: Fact[int]
    state: str
    reasons: tuple[str, ...]


def resolve_facts(
    stored: StoredFacts, member_count: int, open_property_account_ids: frozenset[int]
) -> EffectiveFacts:
    """Resolve the effective facts. ``open_property_account_ids`` are the open Property Accounts."""
    reasons: list[str] = []

    # Couple status: default from the Member count; an explicit value must agree with it.
    couple: Fact[str]
    if stored.couple_status is not None:
        couple = Fact(stored.couple_status, ENTERED)
    elif member_count == MAX_MEMBERS:
        couple = Fact("couple", DEFAULT)
    elif member_count == 1:
        couple = Fact("single", DEFAULT)
    else:
        couple = Fact[str](None, DEFAULT)
    if member_count == 0:
        reasons.append("The Household has no Members.")
    elif member_count > MAX_MEMBERS:
        reasons.append(
            f"The Household has {member_count} Members; only one Member, or a couple of two, "
            "is modelled."
        )
    elif couple.value == "couple" and member_count != MAX_MEMBERS:
        reasons.append("Couple status is couple but the Household does not have two Members.")
    elif couple.value == "single" and member_count != 1:
        reasons.append("Couple status is single but the Household does not have one Member.")

    homeowner: Fact[bool] = (
        Fact(stored.homeowner, ENTERED) if stored.homeowner is not None else Fact(True, DEFAULT)
    )

    # Principal home: only for a homeowner; a stored choice that is not an open Property Account
    # is treated as unset; one open Property Account is the home by default; several need a choice.
    needs_choice = False
    home: Fact[int]
    if homeowner.value is not True:
        home = Fact[int](None, DEFAULT)
    elif stored.principal_home_account_id in open_property_account_ids:
        home = Fact(stored.principal_home_account_id, ENTERED)
    elif len(open_property_account_ids) == 1:
        home = Fact(next(iter(open_property_account_ids)), DEFAULT)
    else:
        home = Fact[int](None, DEFAULT)
        needs_choice = len(open_property_account_ids) > 1

    if reasons:
        state = STATE_NOT_MODELLED
    elif needs_choice:
        state = STATE_NEEDS_HOME_CHOICE
        reasons.append("Choose which Property Account is the principal home.")
    else:
        state = STATE_OK
    return EffectiveFacts(couple, homeowner, home, state, tuple(reasons))
