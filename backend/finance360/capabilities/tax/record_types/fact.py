"""The ``fact`` Record type: a flag or a number of days against a Return label (ADR-0019 s2).

Private cover days, non-commercial loss rules met and a HELP debt are facts. The engine still sees
only Label totals: a flag is set when any record sets it, and days add up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import InputError, as_int, as_obj, as_text, reject_floats
from finance360.capabilities.tax.record_types.base import (
    Contribution,
    Record,
    RecordRules,
    RecordType,
    check_label,
)

KEY = "fact"


@dataclass(frozen=True)
class Fact:
    label: str
    #: ``True`` or ``False`` for a flag label, a number of days for a days label.
    value: bool | int


def parse(raw: object) -> Fact:
    reject_floats(raw)
    body = as_obj(raw, "A fact")
    label = check_label(as_text(body.get("label"), "label"), KEY, (labels.FLAG, labels.DAYS))
    value = body.get("value")
    if label.kind == labels.FLAG:
        if not isinstance(value, bool):
            raise InputError(f"'{label.name}' must be true or false.")
        return Fact(label.key, value)
    days = as_int(value, "value")
    if days < 0:
        raise InputError("Days are 0 or more.")
    return Fact(label.key, days)


def to_payload(value: Fact) -> dict[str, Any]:
    return {"label": value.label, "value": value.value}


def contributions(
    record: Record, financial_year: int, rules: RecordRules
) -> tuple[Contribution, ...]:
    value: Fact = record.value
    return (Contribution(record.id, value.label, value.value),)


TYPE = RecordType(KEY, "Fact", parse, to_payload, lambda v: v.label, contributions)
