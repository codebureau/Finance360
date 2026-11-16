"""The ``line`` Record type: one money amount against one Return label (ADR-0019 section 2).

An optional **work-use share** (a ``Rate``, parts per million) counts only that part of the amount,
for a cost used partly for work. The Contribution is the amount scaled by the share, rounded once
with ``core.money.scale``. With no share the whole amount counts.

``line`` also serves the entered totals of capital gains, rental and sole trader, and the
depreciation, home office and car kilometres totals, until a richer type replaces it for those.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import (
    InputError,
    as_int,
    as_obj,
    as_text,
    money_in,
    reject_floats,
)
from finance360.capabilities.tax.record_types.base import (
    Contribution,
    Record,
    RecordRules,
    RecordType,
    check_label,
)
from finance360.core.money import BASE_CURRENCY, Money, scale
from finance360.core.rate import PPM, Rate, RateError

KEY = "line"


@dataclass(frozen=True)
class Line:
    label: str
    amount: Money
    #: The part of the amount that counts, or ``None`` for all of it.
    work_use: Rate | None = None


def parse(raw: object) -> Line:
    reject_floats(raw)
    body = as_obj(raw, "A line")
    label = as_text(body.get("label"), "label")
    check_label(label, KEY, (labels.MONEY,))
    amount = money_in(body.get("amount"), "amount")
    if amount.currency != BASE_CURRENCY:
        raise InputError(f"A line is in {BASE_CURRENCY}; enter the amount already converted.")
    if amount.amount_minor < 0:
        raise InputError("A line amount is 0 or more.")
    share = body.get("work_use_ppm")
    work_use: Rate | None = None
    if share is not None:
        try:
            work_use = Rate(as_int(share, "work_use_ppm"))
        except RateError as error:
            raise InputError(str(error)) from None
        if work_use.ppm < 0:
            raise InputError("A work-use share is between 0% and 100%.")
    return Line(label, amount, work_use)


def to_payload(value: Line) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "label": value.label,
        "amount": {"minor": value.amount.amount_minor, "ccy": value.amount.currency},
    }
    if value.work_use is not None:
        payload["work_use_ppm"] = value.work_use.ppm
    return payload


def counted(value: Line) -> Money:
    """The part of the amount that counts: the amount times the work-use share, rounded once."""
    if value.work_use is None:
        return value.amount
    return scale(value.amount, value.work_use.ppm, PPM)


def contributions(
    record: Record, financial_year: int, rules: RecordRules
) -> tuple[Contribution, ...]:
    value: Line = record.value
    return (Contribution(record.id, value.label, counted(value)),)


TYPE = RecordType(KEY, "Line", parse, to_payload, lambda v: v.label, contributions)
