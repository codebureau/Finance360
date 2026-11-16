"""The projection engine (ADR-0015): one pure function over an immutable Projection input.

No I/O. It reads no clock, database, setting or Rule pack, and it imports only ``core.money``,
``core.rate`` and the Projection input. The same input always gives an equal result.

One annual step, year ``t`` (flows at the end of the year, in real terms):

1. **Growth.** Each line's opening balance becomes ``scale(balance, 10**6 + r, 10**6 + i)``: the
   exact real factor ``(1 + r) / (1 + i)``, rounded once, half to even. Growth is on the opening
   balance only.
2. **Cash flows.** Active inflows are credited to their lines. The year's *need* is the sum of the
   active outflows.
2b. **Line debits** (ADR-0028). Each debit for the year, in canonical order, is paid
   ``min(amount, balance still on the line)`` from ``grown + credited``, so a line never goes
   below zero. The part a line cannot pay is *unpaid*: it is paid personally that year, added to
   the year's need and met by drawdown like spending. It is never borrowed or carried. A debit
   ignores ``accessible_from_year`` and is not taxed here.
3. **Withdrawals.** The need (outflows plus the unpaid debit parts) is met from accessible lines
   (``t >= accessible_from_year``), Category by Category in ``drawdown_order``, on the balances
   left after the debits. Within a Category the amount is split across Members' lines in
   proportion to their balances (``allocate``, largest remainder), so no line goes below zero.
   What the accessible balances cannot cover is the year's *shortfall*; nothing is ever borrowed
   and the projection continues to the horizon.

Identities: ``closing = opening + growth + credited - debited - withdrawn``;
``requested = paid + unpaid`` per debit; ``need = spending + sum of unpaid``;
``withdrawn + shortfall = need``.

Bump ``ENGINE_VERSION`` whenever the same input could give a different result (ADR-0015 s3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from finance360.capabilities.projection.input import (
    INPUT_VERSION,
    ProjectionInput,
    age_on,
    anniversary,
)
from finance360.core.money import Money, allocate, scale
from finance360.core.rate import PPM

ENGINE_VERSION = 2


@dataclass(frozen=True)
class LineYear:
    member_id: int
    category_key: str
    opening: Money
    growth: Money
    credited: Money
    #: The sum of the debits paid from this line (ADR-0028).
    debited: Money
    withdrawn: Money
    closing: Money


@dataclass(frozen=True)
class CategoryYear:
    category_key: str
    opening: Money
    growth: Money
    credited: Money
    debited: Money
    withdrawn: Money
    closing: Money


@dataclass(frozen=True)
class DebitRow:
    """One debit as the engine paid it: ``requested = paid + unpaid``."""

    member_id: int
    category_key: str
    reason: str
    ref: str
    requested: Money
    #: Paid from the line.
    paid: Money
    #: The part the line could not pay: paid personally, so it is in the year's ``need``.
    unpaid: Money


@dataclass(frozen=True)
class YearRow:
    t: int
    #: The anniversary of ``as_at`` that opens this year.
    starts_on: date
    #: ``(member_id, age)`` at the start of the year, by member id.
    ages: tuple[tuple[int, int], ...]
    lines: tuple[LineYear, ...]
    #: The spending plus the unpaid parts of the year's debits.
    need: Money
    shortfall: Money
    #: Every debit of the year, in canonical order.
    debits: tuple[DebitRow, ...] = ()


@dataclass(frozen=True)
class ProjectionResult:
    engine_version: int
    input_version: int
    #: The youngest Member (latest ``born_on``; a tie goes to the lowest id).
    reference_member_id: int
    years: tuple[YearRow, ...]
    #: The reference Member's age at the start of the first year with a shortfall, else None.
    funds_last_to_age: int | None
    lasts_beyond_horizon: bool


def reference_member_id(projection_input: ProjectionInput) -> int:
    """The youngest Member: latest ``born_on``, a tie going to the lowest id."""
    return min(
        projection_input.members, key=lambda m: (-m.born_on.toordinal(), m.member_id)
    ).member_id


def category_totals(row: YearRow, order: tuple[str, ...]) -> tuple[CategoryYear, ...]:
    """Each Category's lines summed (a plain sum, ADR-0015 s7), in ``order``."""
    totals: list[CategoryYear] = []
    for key in order:
        lines = [line for line in row.lines if line.category_key == key]
        if not lines:
            continue
        zero = Money.zero(lines[0].opening.currency)
        totals.append(
            CategoryYear(
                key,
                sum((x.opening for x in lines), zero),
                sum((x.growth for x in lines), zero),
                sum((x.credited for x in lines), zero),
                sum((x.debited for x in lines), zero),
                sum((x.withdrawn for x in lines), zero),
                sum((x.closing for x in lines), zero),
            )
        )
    return tuple(totals)


def project(projection_input: ProjectionInput) -> ProjectionResult:
    """Run the projection. Pure and deterministic."""
    i = projection_input
    zero = Money.zero(i.currency)
    inflation_ppm = i.inflation.ppm
    balances = {(line.member_id, line.category_key): line.start for line in i.lines}
    starting_ages = {m.member_id: age_on(m.born_on, i.as_at) for m in i.members}
    reference = reference_member_id(i)

    rows: list[YearRow] = []
    first_shortfall: int | None = None
    for t in range(i.years):
        grown = {
            (line.member_id, line.category_key): scale(
                balances[(line.member_id, line.category_key)],
                PPM + line.return_rate.ppm,
                PPM + inflation_ppm,
            )
            for line in i.lines
        }
        credited = {key: zero for key in grown}
        for inflow in i.inflows:
            until = i.years if inflow.until_year is None else inflow.until_year
            if inflow.from_year <= t < until:
                key = (inflow.member_id, inflow.category_key)
                credited[key] = credited[key] + inflow.amount

        need = zero
        for outflow in i.outflows:
            until = i.years if outflow.until_year is None else outflow.until_year
            if outflow.from_year <= t < until:
                need = need + outflow.amount

        available = {key: grown[key] + credited[key] for key in grown}
        debited = {key: zero for key in grown}
        paid_rows: list[DebitRow] = []
        for debit in i.debits:  # canonical order (ADR-0028 s1)
            if debit.year != t:
                continue
            key = (debit.member_id, debit.category_key)
            paid = Money(min(debit.amount.amount_minor, available[key].amount_minor), i.currency)
            unpaid = debit.amount - paid
            available[key] = available[key] - paid
            debited[key] = debited[key] + paid
            need = need + unpaid
            paid_rows.append(
                DebitRow(
                    debit.member_id,
                    debit.category_key,
                    debit.reason,
                    debit.ref,
                    debit.amount,
                    paid,
                    unpaid,
                )
            )
        withdrawn = {key: zero for key in grown}
        remaining = need
        for category in i.drawdown_order:
            if remaining.amount_minor == 0:
                break
            drawable = [
                line
                for line in i.lines
                if line.category_key == category and t >= line.accessible_from_year
            ]
            weights = {
                line.member_id: available[(line.member_id, category)].amount_minor
                for line in drawable
            }
            pool = sum(weights.values())
            if pool == 0:
                continue
            take = min(remaining.amount_minor, pool)
            for member_id, part in allocate(Money(take, i.currency), weights).items():
                withdrawn[(member_id, category)] = part
            remaining = remaining - Money(take, i.currency)

        lines = tuple(
            LineYear(
                line.member_id,
                line.category_key,
                balances[(line.member_id, line.category_key)],
                grown[(line.member_id, line.category_key)]
                - balances[(line.member_id, line.category_key)],
                credited[(line.member_id, line.category_key)],
                debited[(line.member_id, line.category_key)],
                withdrawn[(line.member_id, line.category_key)],
                available[(line.member_id, line.category_key)]
                - withdrawn[(line.member_id, line.category_key)],
            )
            for line in i.lines
        )
        balances = {(x.member_id, x.category_key): x.closing for x in lines}
        rows.append(
            YearRow(
                t,
                anniversary(i.as_at, i.as_at.year + t),
                tuple((member_id, age + t) for member_id, age in sorted(starting_ages.items())),
                lines,
                need,
                remaining,
                tuple(paid_rows),
            )
        )
        if first_shortfall is None and remaining.amount_minor > 0:
            first_shortfall = t

    return ProjectionResult(
        ENGINE_VERSION,
        INPUT_VERSION,
        reference,
        tuple(rows),
        None if first_shortfall is None else starting_ages[reference] + first_shortfall,
        first_shortfall is None,
    )
