"""The overrides of a Tax what-if (ADR-0019 section 6, following ADR-0016). Pure.

A Tax what-if is a base plus **overrides**, never inheriting from another. There are three
operations, four shapes, and every value is **absolute, never a delta**:

* ``set``: a Member's Label total for one label becomes the amount (the "slider");
* ``exclude_label``: a Member's total for one label is left out;
* ``exclude_record``: one Tax record is left out (its Contributions disappear from its totals);
* ``add_record``: a hypothetical Tax record of any type is added (a laptop purchase is a
  depreciating asset, so that type's own function computes the effect). Its fields are those of a
  Tax record as the register takes them; this module holds them opaquely and the service checks them
  with the register's own validation.

This module parses and serialises the list, checks it against the label registry and applies the
label-level overrides to computed Label totals. It reads no database and no Rule pack. Canonical
JSON: money as ``{"ccy", "minor"}``, days a whole number, a flag a boolean; keys sorted, no spaces,
no float anywhere.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from finance360.capabilities.tax import input as tinput
from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import InputError, Total, days_in_fy
from finance360.capabilities.tax.record_types import LabelTotal
from finance360.core.money import BASE_CURRENCY, Money, MoneyError

SET = "set"
EXCLUDE_LABEL = "exclude_label"
EXCLUDE_RECORD = "exclude_record"
ADD_RECORD = "add_record"
OPS = (SET, EXCLUDE_LABEL, EXCLUDE_RECORD, ADD_RECORD)

MAX_OVERRIDES = 50


class WhatIfError(ValueError):
    """The overrides (or a payload for them) are invalid. ``index`` is the override's position."""

    def __init__(self, message: str, index: int | None = None) -> None:
        super().__init__(message)
        self.index = index


@dataclass(frozen=True)
class SetTotal:
    member_id: int
    label: str
    value: Total

    op = SET


@dataclass(frozen=True)
class ExcludeLabel:
    member_id: int
    label: str

    op = EXCLUDE_LABEL


@dataclass(frozen=True)
class ExcludeRecord:
    record_id: int

    op = EXCLUDE_RECORD


@dataclass(frozen=True)
class AddRecord:
    #: The record as the register takes it (``RecordInput`` fields as JSON-ready data).
    record: Mapping[str, Any]

    op = ADD_RECORD


Override = SetTotal | ExcludeLabel | ExcludeRecord | AddRecord


# --- canonical JSON ------------------------------------------------------------------------


def _value_out(value: Total) -> object:
    return tinput.money_out(value) if isinstance(value, Money) else value


def override_payload(item: Override) -> dict[str, Any]:
    if isinstance(item, SetTotal):
        return {
            "label": item.label,
            "member_id": item.member_id,
            "op": SET,
            "value": _value_out(item.value),
        }
    if isinstance(item, ExcludeLabel):
        return {"label": item.label, "member_id": item.member_id, "op": EXCLUDE_LABEL}
    if isinstance(item, ExcludeRecord):
        return {"op": EXCLUDE_RECORD, "record_id": item.record_id}
    return {"op": ADD_RECORD, "record": dict(item.record)}


def to_payload(overrides: Sequence[Override]) -> list[dict[str, Any]]:
    return [override_payload(o) for o in overrides]


def to_json(overrides: Sequence[Override]) -> str:
    payload = to_payload(overrides)
    tinput.reject_floats(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _positive(raw: object, what: str, index: int) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise WhatIfError(f"{what} must be a whole number from 1.", index)
    return raw


def _label(raw: object, index: int) -> labels.Label:
    label = labels.get(raw) if isinstance(raw, str) else None
    if label is None:
        raise WhatIfError(f"{raw!r} is not a Return label.", index)
    if label.direction == labels.MEMO:
        raise WhatIfError(
            f"'{label.name}' is on no return, so an estimate does not read it.", index
        )
    return label


def _value(label: labels.Label, raw: object, index: int, fy: int | None) -> Total:
    if label.kind == labels.MONEY:
        try:
            money = tinput.money_in(raw, label.key)
        except InputError as error:
            raise WhatIfError(str(error), index) from None
        if money.currency != BASE_CURRENCY or money.amount_minor < 0:
            raise WhatIfError(f"'{label.name}' must be {BASE_CURRENCY} of 0 or more.", index)
        return money
    if label.kind == labels.DAYS:
        limit = days_in_fy(fy) if fy is not None else 366
        if not isinstance(raw, int) or isinstance(raw, bool) or not 0 <= raw <= limit:
            raise WhatIfError(
                f"'{label.name}' must be a whole number of days from 0 to {limit}.", index
            )
        return raw
    if not isinstance(raw, bool):
        raise WhatIfError(f"'{label.name}' must be true or false.", index)
    return raw


def _one(raw: object, index: int, fy: int | None) -> Override:
    body = tinput.as_obj(raw, "an override")
    op = body.get("op")
    if op == SET:
        label = _label(body.get("label"), index)
        return SetTotal(
            _positive(body.get("member_id"), "member_id", index),
            label.key,
            _value(label, body.get("value"), index, fy),
        )
    if op == EXCLUDE_LABEL:
        label = _label(body.get("label"), index)
        return ExcludeLabel(_positive(body.get("member_id"), "member_id", index), label.key)
    if op == EXCLUDE_RECORD:
        return ExcludeRecord(_positive(body.get("record_id"), "record_id", index))
    if op == ADD_RECORD:
        record = body.get("record")
        if not isinstance(record, dict) or not record:
            raise WhatIfError("A hypothetical record needs its fields.", index)
        return AddRecord(cast("dict[str, Any]", record))
    raise WhatIfError(f"Choose an operation: {', '.join(OPS)}.", index)


def _key(item: Override) -> tuple[Any, ...] | None:
    """What an override acts on, so two cannot act on the same thing."""
    if isinstance(item, SetTotal | ExcludeLabel):
        return ("label", item.member_id, item.label)
    if isinstance(item, ExcludeRecord):
        return ("record", item.record_id)
    return None


def check(overrides: Sequence[Override]) -> None:
    """An override acts on each thing once: setting a total and excluding it, or excluding one
    record twice, is ambiguous and refused. Raises ``WhatIfError`` for the later one."""
    if len(overrides) > MAX_OVERRIDES:
        raise WhatIfError(f"A Tax what-if holds at most {MAX_OVERRIDES} overrides.")
    seen: set[tuple[Any, ...]] = set()
    for index, item in enumerate(overrides):
        key = _key(item)
        if key is None:
            continue
        if key in seen:
            raise WhatIfError("This is overridden twice; keep one.", index)
        seen.add(key)


def from_payload(payload: object, fy: int | None = None) -> tuple[Override, ...]:
    """Overrides from ``to_payload`` data. Floats are an error. Raises ``WhatIfError``."""
    try:
        tinput.reject_floats(payload)
        items = tinput.as_list(payload, "overrides")
        parsed = tuple(_one(raw, index, fy) for index, raw in enumerate(items))
    except (InputError, MoneyError) as error:
        raise WhatIfError(str(error)) from None
    check(parsed)
    return parsed


def from_json(text: str, fy: int | None = None) -> tuple[Override, ...]:
    try:
        return from_payload(tinput.loads(text), fy)
    except InputError as error:
        raise WhatIfError(str(error)) from None


# --- applying the label-level overrides ----------------------------------------------------


def excluded_records(overrides: Sequence[Override]) -> frozenset[int]:
    return frozenset(o.record_id for o in overrides if isinstance(o, ExcludeRecord))


def added_records(overrides: Sequence[Override]) -> tuple[AddRecord, ...]:
    return tuple(o for o in overrides if isinstance(o, AddRecord))


def apply_labels(
    totals: Mapping[tuple[int | None, str], LabelTotal], overrides: Sequence[Override]
) -> tuple[dict[tuple[int | None, str], LabelTotal], dict[tuple[int, str], Total | None]]:
    """The totals with each ``set`` and ``exclude_label`` applied, and what each one replaced.

    A ``set`` total replaces the computed one whole and has no Contributions, because it came from
    no record. Absolute: whatever the computed total was, it is now the stated one. The second
    result maps ``(member, label)`` to the total it replaced (``None`` when there was none).
    """
    out = dict(totals)
    replaced: dict[tuple[int, str], Total | None] = {}
    for item in overrides:
        if isinstance(item, SetTotal):
            key = (item.member_id, item.label)
            held = out.get(key)
            replaced[key] = held.total if held is not None else None
            out[key] = LabelTotal(item.member_id, item.label, item.value, ())
        elif isinstance(item, ExcludeLabel):
            key = (item.member_id, item.label)
            held = out.pop(key, None)
            replaced[key] = held.total if held is not None else None
    return out, replaced
