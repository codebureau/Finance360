"""Prefilled base values from Income streams (ADR-0019 sections 6 and 10). Pure.

Records are facts. An Income stream is a forecast. A forecast enters an estimate as a prefilled,
overridable, source-marked base value and is **never written as a Record**. This module turns plain
stream facts into prefill lines and applies the one rule that stops a year-to-date Record total and
a full-year forecast both counting.

**The no-double-count rule.** For one Member and one Return label in one financial year, if the
Tax register has any Contribution to that label (after a what-if's excluded and hypothetical
records), the Records are the whole answer and the Income streams' prefill for that Member and label
is *not counted*. It is still listed, with that reason. Otherwise the streams' prefill is the base
total. There is no blending, no pro-rating of a forecast against a year-to-date total, and no
partial count: either a Member's label is recorded or it is forecast.

Each stream becomes one line per owner: the stream's stated annual amount (as entered, not indexed
to the year), pro-rated by the days the stream is in force in the year when it has a start or end
date, then split between its owners in proportion to their shares with ``core.money.allocate``, so
the parts sum exactly. A start or end on a Life event (retirement) has no date here, because Life
events come from a Scenario that Tax cannot read (it sits above Tax), so that side is not applied:
the stream counts from the start of the year, or to its end, and its text says so. A Tax what-if can
state the total where the event falls inside the year.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from finance360.capabilities.tax.input import days_in_fy
from finance360.core import income_types
from finance360.core.money import BASE_CURRENCY, Money, allocate, scale

COUNTED = "counted"
RECORDS_WIN = "records_win"
NO_LABEL = "no_label"
STATUSES = (COUNTED, RECORDS_WIN, NO_LABEL)

#: The Return label each stream tax kind prefills. Other kinds have no label to prefill yet.
LABEL_FOR_KIND: Mapping[str, str] = {income_types.EMPLOYMENT: "salary"}


@dataclass(frozen=True)
class StreamFacts:
    """An Income stream as the prefill needs it: plain values, no row."""

    stream_id: int
    name: str
    type_key: str
    gross_annual: Money
    #: ``(member_id, share_bp)`` for each owner.
    shares: tuple[tuple[int, int], ...]
    start_on: date | None
    end_on: date | None
    #: True when the stream starts or ends on a Life event (no date here: that side is not applied).
    event_bounded: bool


@dataclass(frozen=True)
class PrefillLine:
    member_id: int
    #: The Return label, or empty when the stream's kind has none.
    label: str
    stream_id: int
    stream_name: str
    amount: Money
    status: str
    #: How the amount was reached, or why it is not counted. Plain words, shown as is.
    text: str


def _days_in_force(facts: StreamFacts, financial_year: int) -> int:
    start = max(facts.start_on or date.min, date(financial_year - 1, 7, 1))
    end = min(facts.end_on or date.max, date(financial_year, 6, 30))
    return max(0, (end - start).days + 1)


def forecast(
    streams: Iterable[StreamFacts], financial_year: int, member_ids: Iterable[int]
) -> tuple[PrefillLine, ...]:
    """One line per stream owner in the estimate, before the no-double-count rule is applied.

    A stream not in force in the year yields nothing. Lines are in stream then Member order.
    """
    members = set(member_ids)
    year_days = days_in_fy(financial_year)
    zero = Money.zero(BASE_CURRENCY)
    out: list[PrefillLine] = []
    for facts in streams:
        owners = {m: bp for m, bp in facts.shares if m in members}
        if not owners:
            continue
        kind = income_types.TYPES.get(facts.type_key)
        label = LABEL_FOR_KIND.get(kind.tax_kind, "") if kind else ""
        days = _days_in_force(facts, financial_year)
        if days == 0:
            continue
        if not label:
            status, why = NO_LABEL, "Not counted: this kind of income has no Return label yet."
        else:
            status, why = COUNTED, ""
        amount = facts.gross_annual
        how = f"stated annual amount {_money(amount)}"
        if status == COUNTED and days != year_days:
            amount = scale(amount, days, year_days)
            how = f"{_money(facts.gross_annual)} a year for {days} of {year_days} days"
        event = (
            "; its start or end on a Life event is not applied here, so state the total in a "
            "Tax what-if if that falls in this year"
            if facts.event_bounded
            else ""
        )
        shares = allocate(amount, {m: bp for m, bp in facts.shares})
        total_bp = sum(bp for _, bp in facts.shares)
        for member_id in sorted(owners):
            share = shares[member_id]
            joint = (
                f", {owners[member_id] * 100 // total_bp}% share" if len(facts.shares) > 1 else ""
            )
            text = why or f"Income stream '{facts.name}': {how}{joint}{event}"
            out.append(
                PrefillLine(
                    member_id,
                    label,
                    facts.stream_id,
                    facts.name,
                    share if status == COUNTED else zero,
                    status,
                    text,
                )
            )
    return tuple(out)


def _money(value: Money) -> str:
    major, minor = divmod(abs(value.amount_minor), 100)
    return f"${major:,}.{minor:02d}"


def decide(
    lines: Iterable[PrefillLine], recorded: Iterable[tuple[int | None, str]]
) -> tuple[PrefillLine, ...]:
    """Apply the no-double-count rule: a line whose ``(Member, label)`` has Records is not counted.

    ``recorded`` holds each ``(member_id, label)`` that has at least one Contribution.
    """
    held = set(recorded)
    out: list[PrefillLine] = []
    for line in lines:
        if line.status == COUNTED and (line.member_id, line.label) in held:
            zero = Money.zero(BASE_CURRENCY)
            out.append(
                PrefillLine(
                    line.member_id,
                    line.label,
                    line.stream_id,
                    line.stream_name,
                    zero,
                    RECORDS_WIN,
                    "Not counted: the Tax register already has records for this income and "
                    f"year, and they are used instead ({line.text}).",
                )
            )
        else:
            out.append(line)
    return tuple(out)


def totals(lines: Iterable[PrefillLine]) -> dict[tuple[int, str], Money]:
    """The prefilled base total for each ``(Member, label)``: the sum of its counted lines."""
    out: dict[tuple[int, str], Money] = {}
    for line in lines:
        if line.status != COUNTED:
            continue
        key = (line.member_id, line.label)
        out[key] = out.get(key, Money.zero(BASE_CURRENCY)) + line.amount
    return out
