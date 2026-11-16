"""The pure Position calculation (ADR-0009). No I/O: the service assembles the inputs.

For each Account that counts on the as-at date ``d`` (``opened_on <= d < closed_on``; a null bound
is open-ended):

1. take the best Valuation on or before ``d`` (``core.valuation``);
2. convert it to AUD at the exact-pair FX rate on or before ``d`` (``core.fx``, ``core.money``);
3. sign it by Class: a Valuation is the amount owed for a Liability, so its Class gives the sign
   (CONTEXT.md). Assets are positive, Liabilities negative, so Net is a plain sum;
4. split it across its owners by ``share_bp`` with the largest-remainder allocation
   (``core.money.allocate``), so the parts sum exactly to the Account's AUD total.

An Account with no Valuation, or no FX rate, is **flagged and left out of every total**, never
counted as zero; each node reports how many flagged Accounts sit beneath it. A Tag filter drops
every Account without that Tag before anything is summed, so it narrows every figure alike.

Every figure carries the same breakdown (``Figures``): the Household total, the Joint total
(Accounts
whose mode is joint, whole), and per Member the part held ``sole`` plus their ``joint_share``. So
``sum(member.total) == household`` and ``sum(member.sole) + joint == household``, exactly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from finance360.core.fx import FxPoint, NoRate, rate_on_or_before
from finance360.core.models.catalogue import CLASSES
from finance360.core.money import BASE_CURRENCY, Money, allocate, convert
from finance360.core.valuation import NoValuation, ValuationPoint, best_on_or_before

AUD = Money.zero(BASE_CURRENCY)
Status = Literal["ok", "no_valuation", "no_rate"]


@dataclass(frozen=True)
class CategoryInfo:
    key: str
    name: str
    class_: str  # "asset" | "liability"


@dataclass(frozen=True)
class AccountFacts:
    """One Account as the calculation needs it: immutable, no ORM."""

    id: int
    name: str
    category_key: str
    currency: str
    ownership: str  # "sole" | "joint"
    #: (member_id, share_bp) pairs, shares summing to 10000.
    owners: tuple[tuple[int, int], ...]
    tags: tuple[str, ...]
    opened_on: date | None
    closed_on: date | None
    valuations: tuple[ValuationPoint, ...]


@dataclass(frozen=True)
class MemberFigure:
    member_id: int
    sole: Money
    joint_share: Money

    @property
    def total(self) -> Money:
        return self.sole + self.joint_share


@dataclass(frozen=True)
class Figures:
    household: Money
    joint: Money
    members: tuple[MemberFigure, ...]


@dataclass(frozen=True)
class AccountLine:
    account_id: int
    name: str
    ownership: str
    currency: str
    tags: tuple[str, ...]
    status: Status
    valuation_as_at: date | None = None
    source: str | None = None
    #: The Valuation as recorded, in the Account's currency (Liabilities: the amount owed).
    original: Money | None = None
    rate: Decimal | None = None
    rate_as_at: date | None = None
    #: Where the rate came from (``manual`` or a provider key); ``None`` for an AUD Account.
    rate_source: str | None = None
    #: Signed AUD (Liabilities negative); ``None`` when flagged.
    value: Money | None = None
    #: Signed AUD per owner, summing exactly to ``value``; empty when flagged.
    shares: tuple[tuple[int, Money], ...] = ()


@dataclass(frozen=True)
class CategoryNode:
    key: str
    name: str
    figures: Figures
    flagged: int
    accounts: tuple[AccountLine, ...]


@dataclass(frozen=True)
class ClassNode:
    class_: str
    figures: Figures
    flagged: int
    categories: tuple[CategoryNode, ...]


@dataclass(frozen=True)
class Position:
    on: date
    tag: str | None
    classes: tuple[ClassNode, ...]
    net: Figures
    flagged: int


def counts_on(account: AccountFacts, on: date) -> bool:
    """``opened_on <= on < closed_on``: ``closed_on`` is the first date it no longer counts."""
    return (account.opened_on is None or account.opened_on <= on) and (
        account.closed_on is None or on < account.closed_on
    )


def _line(account: AccountFacts, class_: str, fx: Iterable[FxPoint], on: date) -> AccountLine:
    head = (account.id, account.name, account.ownership, account.currency, account.tags)
    best = best_on_or_before(account.valuations, on)
    if isinstance(best, NoValuation):
        return AccountLine(*head, "no_valuation")
    recorded = (best.as_at, best.source, best.amount)
    rate: Decimal | None = None
    rate_as_at: date | None = None
    rate_source: str | None = None
    aud = best.amount
    if best.amount.currency != BASE_CURRENCY:
        found = rate_on_or_before(fx, best.amount.currency, BASE_CURRENCY, on)
        if isinstance(found, NoRate):
            return AccountLine(*head, "no_rate", *recorded)
        rate, rate_as_at, rate_source = found.rate, found.as_at, found.source
        aud = convert(best.amount, found.rate, BASE_CURRENCY)
    value = -aud if class_ == "liability" else aud
    parts = allocate(value, dict(account.owners))
    return AccountLine(
        *head, "ok", *recorded, rate, rate_as_at, rate_source, value, tuple(parts.items())
    )


def _figures(lines: Iterable[AccountLine], member_ids: Sequence[int]) -> Figures:
    household = joint = AUD
    sole = {m: AUD for m in member_ids}
    joint_share = {m: AUD for m in member_ids}
    for line in lines:
        if line.value is None:
            continue
        household += line.value
        if line.ownership == "joint":
            joint += line.value
        bucket = joint_share if line.ownership == "joint" else sole
        for member_id, part in line.shares:
            bucket[member_id] += part
    return Figures(
        household,
        joint,
        tuple(MemberFigure(m, sole[m], joint_share[m]) for m in member_ids),
    )


def _add(a: Figures, b: Figures) -> Figures:
    return Figures(
        a.household + b.household,
        a.joint + b.joint,
        tuple(
            MemberFigure(x.member_id, x.sole + y.sole, x.joint_share + y.joint_share)
            for x, y in zip(a.members, b.members, strict=True)
        ),
    )


def compute_position(
    *,
    on: date,
    accounts: Iterable[AccountFacts],
    categories: Sequence[CategoryInfo],
    member_ids: Sequence[int],
    fx: Sequence[FxPoint],
    tag: str | None = None,
) -> Position:
    """The Position on ``on``. Pure: the same inputs always give the same answer."""
    wanted = tag.strip().casefold() if tag and tag.strip() else None
    by_category: dict[str, list[AccountLine]] = {c.key: [] for c in categories}
    class_of = {c.key: c.class_ for c in categories}
    for account in sorted(accounts, key=lambda a: (a.name.casefold(), a.id)):
        if not counts_on(account, on):
            continue
        if wanted is not None and wanted not in {t.casefold() for t in account.tags}:
            continue
        by_category[account.category_key].append(
            _line(account, class_of[account.category_key], fx, on)
        )

    classes: list[ClassNode] = []
    for class_ in CLASSES:
        nodes: list[CategoryNode] = []
        for category in (c for c in categories if c.class_ == class_):
            lines = by_category[category.key]
            if not lines:
                continue
            nodes.append(
                CategoryNode(
                    category.key,
                    category.name,
                    _figures(lines, member_ids),
                    sum(1 for line in lines if line.value is None),
                    tuple(lines),
                )
            )
        class_lines = [line for node in nodes for line in node.accounts]
        classes.append(
            ClassNode(
                class_,
                _figures(class_lines, member_ids),
                sum(node.flagged for node in nodes),
                tuple(nodes),
            )
        )
    net = _figures([], member_ids)
    for node in classes:
        net = _add(net, node.figures)
    return Position(on, tag, tuple(classes), net, sum(node.flagged for node in classes))


def member_totals(figures: Figures) -> Mapping[int, Money]:
    """Each Member's total (held solely plus their share of joint Accounts)."""
    return {m.member_id: m.total for m in figures.members}


HistoryStep = Literal["month", "quarter"]


def history_dates(start: date, end: date, step: HistoryStep = "month") -> list[date]:
    """Month-ends (or every third) from ``start`` to ``end``, then ``end`` itself if it isn't one.

    Calendar month-ends, so a point is the Position at the close of that month. An ``end`` before
    ``start`` gives no points.
    """
    months = 1 if step == "month" else 3
    dates: list[date] = []
    year, month = start.year, start.month
    while True:
        # The last day of (year, month): the day before the first of the next month.
        nxt = date(year + month // 12, month % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        if last > end:
            break
        if last >= start:
            dates.append(last)
        year, month = year + (month - 1 + months) // 12, (month - 1 + months) % 12 + 1
    if end >= start and (not dates or dates[-1] != end):
        dates.append(end)
    return dates


def compute_history(
    *,
    dates: Iterable[date],
    accounts: Sequence[AccountFacts],
    categories: Sequence[CategoryInfo],
    member_ids: Sequence[int],
    fx: Sequence[FxPoint],
    tag: str | None = None,
) -> list[Position]:
    """The Position on each date: ``compute_position`` per date over one set of inputs.

    There is no second code path; a point is exactly the single-date Position.
    """
    return [
        compute_position(
            on=on, accounts=accounts, categories=categories, member_ids=member_ids, fx=fx, tag=tag
        )
        for on in dates
    ]
