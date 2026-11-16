"""Canonical JSON for a Tax result (ADR-0019 s3, s6). Pure.

The same exactness as the input: money as ``{"ccy", "minor"}``, rates as percent strings, keys
sorted, no spaces, and a float anywhere is an error on write and on read.
``from_json(to_json(x)) == x`` exactly. The result is stored in the shape the engine produced it,
whatever ``engine_version`` made it.
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any, cast

from finance360.capabilities.tax import input as tinput
from finance360.capabilities.tax.engine import (
    HouseholdResult,
    MemberResult,
    TaxResult,
    Unmodelled,
)
from finance360.capabilities.tax.input import InputError
from finance360.core.money import Money
from finance360.core.rate import Rate, RateError

_HOUSEHOLD_MONEY = (
    "taxable_income",
    "tax_on_taxable_income",
    "offsets",
    "medicare_levy",
    "medicare_levy_surcharge",
    "help_repayment",
    "total_tax",
    "credits",
    "net_outcome",
    "surcharge_income",
)


def _member_out(member: MemberResult) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in fields(member):
        value = getattr(member, item.name)
        if isinstance(value, Money):
            out[item.name] = tinput.money_out(value)
        elif isinstance(value, Rate):
            out[item.name] = value.to_percent()
        elif isinstance(value, tuple):
            out[item.name] = list(cast("tuple[str, ...]", value))
        else:
            out[item.name] = value
    return out


def to_payload(result: TaxResult) -> dict[str, Any]:
    household = result.household
    return {
        "engine_version": result.engine_version,
        "financial_year": result.financial_year,
        "household": {
            **{name: tinput.money_out(getattr(household, name)) for name in _HOUSEHOLD_MONEY},
            "surcharge_thresholds": household.surcharge_thresholds,
            "surcharge_tier": household.surcharge_tier,
        },
        "incomplete": result.incomplete,
        "input_version": result.input_version,
        "members": [_member_out(m) for m in result.members],
        "provisional": [{"key": key, "note": note} for key, note in result.provisional],
        "unmodelled": [
            {"label_key": u.label_key, "member_id": u.member_id, "value": tinput.money_out(u.value)}
            for u in result.unmodelled
        ],
    }


def to_json(result: TaxResult) -> str:
    payload = to_payload(result)
    tinput.reject_floats(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _member_in(raw: object) -> MemberResult:
    body = tinput.as_obj(raw, "a member result")
    values: dict[str, Any] = {}
    for item in fields(MemberResult):
        value = body.get(item.name)
        if item.name in ("surcharge_rate",):
            try:
                values[item.name] = Rate.from_percent(tinput.as_text(value, item.name))
            except RateError as error:
                raise InputError(str(error)) from None
        elif item.name == "notes":
            values[item.name] = tuple(
                tinput.as_text(n, "a note") for n in tinput.as_list(value, "notes")
            )
        elif item.name == "member_id":
            values[item.name] = tinput.as_int(value, item.name)
        else:
            values[item.name] = tinput.money_in(value, item.name)
    return MemberResult(**values)


def from_payload(payload: object) -> TaxResult:
    tinput.reject_floats(payload)
    body = tinput.as_obj(payload, "payload")
    house = tinput.as_obj(body.get("household"), "household")
    return TaxResult(
        engine_version=tinput.as_int(body.get("engine_version"), "engine_version"),
        input_version=tinput.as_int(body.get("input_version"), "input_version"),
        financial_year=tinput.as_int(body.get("financial_year"), "financial_year"),
        incomplete=body.get("incomplete") is True,
        provisional=tuple(
            (tinput.as_text(p.get("key"), "key"), tinput.as_text(p.get("note"), "note"))
            for p in (
                tinput.as_obj(x, "a provisional note")
                for x in tinput.as_list(body.get("provisional"), "provisional")
            )
        ),
        members=tuple(_member_in(m) for m in tinput.as_list(body.get("members"), "members")),
        household=HouseholdResult(
            **{name: tinput.money_in(house.get(name), name) for name in _HOUSEHOLD_MONEY},
            surcharge_thresholds=tinput.as_text(
                house.get("surcharge_thresholds"), "surcharge_thresholds"
            ),
            surcharge_tier=tinput.as_int(house.get("surcharge_tier"), "surcharge_tier"),
        ),
        unmodelled=tuple(
            Unmodelled(
                tinput.as_int(u.get("member_id"), "member_id"),
                tinput.as_text(u.get("label_key"), "label_key"),
                tinput.money_in(u.get("value"), "value"),
            )
            for u in (
                tinput.as_obj(x, "an unmodelled item")
                for x in tinput.as_list(body.get("unmodelled"), "unmodelled")
            )
        ),
    )


def from_json(text: str) -> TaxResult:
    return from_payload(tinput.loads(text))
