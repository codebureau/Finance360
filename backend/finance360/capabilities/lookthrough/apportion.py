"""The pure look-through roll-up (ADR-0030 section 8). No I/O.

Each analysed Account's look-through lines are rolled up to its Category, to the Household and,
apportioned by ownership share, to its Members. Accounts with no look-through are carried as **Not
analysed** (with their Position values), so every level adds up to the Position exactly.

**A joint Account is apportioned so both margins hold exactly.** Per line, the Members' amounts sum
to the Account's; per Member, the amounts sum to the Position's Member share of that Account:

1. a negative line (Unallocated, when the parts exceed the Account's value) is allocated across the
   Members by share first, so nothing else is negative;
2. each Member in turn then takes their remaining share across the remaining line amounts, by
   largest remainder (``core.money.allocate``); the last Member takes exactly what is left.

Money is integer minor units throughout; percentages are display only, rounded once from Money.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from finance360.capabilities.lookthrough.lines import LineKind, percent_text
from finance360.core.money import Money, allocate

#: A line's identity in a roll-up: its kind, and its label key for a label line.
Identity = tuple[LineKind, str | None]

#: The order the explicit lines follow the labels.
_EXPLICIT: tuple[LineKind, ...] = ("no_profile", "not_classified", "unallocated")
_EXPLICIT_NAMES = {
    "no_profile": "No profile",
    "not_classified": "Not classified",
    "unallocated": "Unallocated",
}


def apportion(
    amounts: Sequence[Money], shares: Sequence[tuple[int, Money]]
) -> dict[int, tuple[Money, ...]]:
    """Split each of ``amounts`` across the owners so both margins hold exactly.

    ``shares`` is each owner's ``(id, share)`` of the Account's value, summing to the amounts' total
    and not negative. The result gives each owner one amount per input amount: a column (one input
    amount) sums to it, a row (one owner) sums to that owner's share. Only a negative input amount
    may give a negative part.
    """
    if not amounts:
        return {owner: () for owner, _ in shares}
    currency = amounts[0].currency
    zero = Money.zero(currency)
    owners = sorted(shares)
    total = sum(a.amount_minor for a in amounts)
    if total != sum(s.amount_minor for _, s in owners):
        raise ValueError("The owners' shares must sum to the amounts' total.")
    rows: dict[int, list[Money]] = {owner: [zero] * len(amounts) for owner, _ in owners}
    left = [a.amount_minor for a in amounts]
    remaining = {owner: s.amount_minor for owner, s in owners}
    # A negative line first, by share: what each owner has left to place stays non-negative.
    weights = {owner: s.amount_minor for owner, s in owners if s.amount_minor > 0}
    for j, amount in enumerate(amounts):
        if amount.amount_minor >= 0 or not weights:
            continue
        for owner, part in allocate(amount, weights).items():
            rows[owner][j] = part
            remaining[owner] -= part.amount_minor
        left[j] = 0
    # Then each owner in turn across what is left; the last takes it all.
    for position, (owner, _) in enumerate(owners):
        if position == len(owners) - 1:
            for j, minor in enumerate(left):
                rows[owner][j] += Money(minor, currency)
                left[j] = 0
            break
        take = remaining[owner]
        pool = {j: minor for j, minor in enumerate(left) if minor > 0}
        if take <= 0 or not pool:
            continue
        for j, part in allocate(Money(take, currency), pool).items():
            rows[owner][j] += part
            left[j] -= part.amount_minor
    return {owner: tuple(row) for owner, row in rows.items()}


@dataclass(frozen=True)
class LineIn:
    """One line of an Account's look-through, as ``lines.compose`` gives it."""

    kind: LineKind
    #: The label key of a ``label`` line.
    key: str | None
    amount: Money


@dataclass(frozen=True)
class AccountIn:
    """One Account counted in the Position, with its look-through lines when it has them."""

    account_id: int
    name: str
    category_key: str
    ownership: str
    #: The Position value, signed (Liabilities negative); ``None`` when the Account has none.
    value: Money | None
    #: Each owner's share of ``value``, summing to it exactly.
    shares: tuple[tuple[int, Money], ...]
    #: The look-through lines, summing to ``value``; ``None`` when the Account is Not analysed.
    lines: tuple[LineIn, ...] | None
    #: Why an Account is Not analysed.
    reason: str | None = None
    #: The Unallocated line is beyond the reconcile tolerance.
    flagged: bool = False


@dataclass(frozen=True)
class RolledLine:
    kind: LineKind
    key: str | None
    name: str
    amount: Money
    #: Percent of the entity's analysed value, two decimal places; display only.
    percent: str | None


@dataclass(frozen=True)
class Rolled:
    """One entity's analysed lines, what is Not analysed in it, and the two added up."""

    lines: tuple[RolledLine, ...]
    analysed: Money
    not_analysed: Money

    @property
    def total(self) -> Money:
        return self.analysed + self.not_analysed


@dataclass(frozen=True)
class RolledMember:
    member_id: int
    name: str
    rolled: Rolled


@dataclass(frozen=True)
class RolledCategory:
    key: str
    name: str
    rolled: Rolled


@dataclass(frozen=True)
class RolledAccount:
    account_id: int
    name: str
    category_key: str
    ownership: str
    rolled: Rolled
    flagged: bool


@dataclass(frozen=True)
class NotAnalysed:
    account_id: int
    name: str
    category_key: str
    ownership: str
    #: ``None`` when the Account has no value in the Position (it is left out of every total).
    value: Money | None
    #: Why this Account is Not analysed; ``None`` when its kind has no look-through at all.
    reason: str | None


@dataclass(frozen=True)
class Rollup:
    household: Rolled
    members: tuple[RolledMember, ...]
    categories: tuple[RolledCategory, ...]
    accounts: tuple[RolledAccount, ...]
    not_analysed: tuple[NotAnalysed, ...]


def _collapse(lines: Sequence[LineIn], zero: Money) -> dict[Identity, Money]:
    """An Account's lines by identity: several No profile lines are one."""
    out: dict[Identity, Money] = {}
    for line in lines:
        identity = (line.kind, line.key if line.kind == "label" else None)
        out[identity] = out.get(identity, zero) + line.amount
    return out


def _rolled(
    amounts: Mapping[Identity, Money],
    not_analysed: Money,
    labels: Sequence[tuple[str, str]],
    zero: Money,
) -> Rolled:
    """The lines in vocabulary order, then No profile, Not classified and Unallocated. Zero lines
    are dropped (the line is shown only where it has value)."""
    names = dict(labels)
    ordered: list[Identity] = [("label", key) for key, _ in labels]
    ordered.extend((kind, None) for kind in _EXPLICIT)
    analysed = sum(amounts.values(), zero)
    lines = tuple(
        RolledLine(
            kind,
            key,
            names[key] if kind == "label" and key is not None else _EXPLICIT_NAMES[kind],
            amounts[(kind, key)],
            percent_text(amounts[(kind, key)], analysed),
        )
        for kind, key in ordered
        if (kind, key) in amounts and amounts[(kind, key)].amount_minor
    )
    return Rolled(lines, analysed, not_analysed)


def roll_up(
    accounts: Sequence[AccountIn],
    *,
    labels: Sequence[tuple[str, str]],
    members: Sequence[tuple[int, str]],
    categories: Sequence[tuple[str, str]],
    currency: str,
) -> Rollup:
    """The Household, Members, Categories and Accounts of one Dimension.

    ``labels`` is the Dimension's ``(key, name)`` in seed order, ``members`` and ``categories`` the
    entities to report, in order. Accounts with no value are listed Not analysed and counted in no
    total, as in the Position.
    """
    zero = Money.zero(currency)
    household: dict[Identity, Money] = {}
    by_member: dict[int, dict[Identity, Money]] = {m: {} for m, _ in members}
    by_category: dict[str, dict[Identity, Money]] = {k: {} for k, _ in categories}
    member_not = {m: zero for m, _ in members}
    category_not = {k: zero for k, _ in categories}
    household_not = zero
    rolled_accounts: list[RolledAccount] = []
    not_analysed: list[NotAnalysed] = []

    def add(into: dict[Identity, Money], identity: Identity, amount: Money) -> None:
        into[identity] = into.get(identity, zero) + amount

    for account in accounts:
        if account.value is None:
            not_analysed.append(
                NotAnalysed(
                    account.account_id,
                    account.name,
                    account.category_key,
                    account.ownership,
                    None,
                    account.reason or "The Account has no value in the Position.",
                )
            )
            continue
        if account.lines is None:
            not_analysed.append(
                NotAnalysed(
                    account.account_id,
                    account.name,
                    account.category_key,
                    account.ownership,
                    account.value,
                    account.reason,
                )
            )
            household_not += account.value
            category_not[account.category_key] += account.value
            for member_id, share in account.shares:
                member_not[member_id] += share
            continue
        collapsed = _collapse(account.lines, zero)
        identities = list(collapsed)
        rolled_accounts.append(
            RolledAccount(
                account.account_id,
                account.name,
                account.category_key,
                account.ownership,
                _rolled(collapsed, zero, labels, zero),
                account.flagged,
            )
        )
        split = apportion([collapsed[i] for i in identities], account.shares)
        for identity, amount in collapsed.items():
            add(household, identity, amount)
            add(by_category[account.category_key], identity, amount)
        for member_id, parts in split.items():
            for identity, part in zip(identities, parts, strict=True):
                add(by_member[member_id], identity, part)

    return Rollup(
        _rolled(household, household_not, labels, zero),
        tuple(
            RolledMember(m, name, _rolled(by_member[m], member_not[m], labels, zero))
            for m, name in members
        ),
        tuple(
            RolledCategory(k, name, _rolled(by_category[k], category_not[k], labels, zero))
            for k, name in categories
        ),
        tuple(rolled_accounts),
        tuple(not_analysed),
    )
