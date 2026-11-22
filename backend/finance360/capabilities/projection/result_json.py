"""Canonical JSON for a Projection result and the sustainable-spending output (ADR-0016 s4).

The same exactness as the input (``input.to_json``): money as ``{"ccy", "minor"}`` with integer
minor units, dates as ISO text, keys sorted, no spaces, and a float anywhere is an error, on write
and on read. ``from_json(to_json(x)) == x`` exactly. Pure: no database, no engine call.

The result is stored in the shape the engine produced it, whatever ``engine_version`` made it, so a
run saved by an older engine still loads and is shown as that engine's work.
"""

from __future__ import annotations

import json
from typing import Any

from finance360.capabilities.projection import input as pinput
from finance360.capabilities.projection.engine import (
    AgePensionYear,
    DebitRow,
    LineYear,
    ProjectionResult,
    YearRow,
)
from finance360.capabilities.projection.input import InputError
from finance360.capabilities.projection.sustainable import SustainableSpending
from finance360.core.money import Money

_MONEY_FIELDS = ("opening", "growth", "credited", "withdrawn", "closing")


def dumps(payload: object) -> str:
    """Canonical JSON text of a JSON-ready payload. A float anywhere raises ``InputError``."""
    pinput.reject_floats(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def loads(text: str) -> object:
    """Parse canonical JSON text. A number with a fraction or exponent, or NaN, is rejected."""
    try:
        return json.loads(text, parse_float=pinput.no_float, parse_constant=pinput.no_float)
    except json.JSONDecodeError as error:
        raise InputError(f"Not valid JSON: {error}") from None


def _line_payload(line: LineYear) -> dict[str, Any]:
    return {
        "category_key": line.category_key,
        "member_id": line.member_id,
        "opening": pinput.money_out(line.opening),
        "growth": pinput.money_out(line.growth),
        "credited": pinput.money_out(line.credited),
        "debited": pinput.money_out(line.debited),
        "withdrawn": pinput.money_out(line.withdrawn),
        "closing": pinput.money_out(line.closing),
    }


def _debit_payload(debit: DebitRow) -> dict[str, Any]:
    return {
        "category_key": debit.category_key,
        "member_id": debit.member_id,
        "paid": pinput.money_out(debit.paid),
        "reason": debit.reason,
        "ref": debit.ref,
        "requested": pinput.money_out(debit.requested),
        "unpaid": pinput.money_out(debit.unpaid),
    }


def _age_pension_payload(year: AgePensionYear | None) -> dict[str, Any] | None:
    if year is None:
        return None
    return {
        "assets": pinput.money_out(year.assets),
        "assets_test": pinput.money_out(year.assets_test),
        "bound": year.bound,
        "categories": [[key, pinput.money_out(amount)] for key, amount in year.categories],
        "couple": year.couple,
        "deemed_income": pinput.money_out(year.deemed_income),
        "financial_assets": pinput.money_out(year.financial_assets),
        "homeowner": year.homeowner,
        "income_test": pinput.money_out(year.income_test),
        "maximum": pinput.money_out(year.maximum),
        "other_income": pinput.money_out(year.other_income),
        "paid": [[member_id, pinput.money_out(amount)] for member_id, amount in year.paid],
        "property_assets": pinput.money_out(year.property_assets),
        "total": pinput.money_out(year.total),
        "vehicle_assets": pinput.money_out(year.vehicle_assets),
    }


def result_payload(result: ProjectionResult) -> dict[str, Any]:
    return {
        "engine_version": result.engine_version,
        "funds_last_to_age": result.funds_last_to_age,
        "input_version": result.input_version,
        "lasts_beyond_horizon": result.lasts_beyond_horizon,
        "reference_member_id": result.reference_member_id,
        "years": [
            {
                "age_pension": _age_pension_payload(row.age_pension),
                "ages": [[member_id, age] for member_id, age in row.ages],
                "debits": [_debit_payload(d) for d in row.debits],
                "lines": [_line_payload(line) for line in row.lines],
                "need": pinput.money_out(row.need),
                "shortfall": pinput.money_out(row.shortfall),
                "starts_on": row.starts_on.isoformat(),
                "t": row.t,
            }
            for row in result.years
        ],
    }


def result_to_json(result: ProjectionResult) -> str:
    return dumps(result_payload(result))


def _money(body: Any, field: str) -> Money:
    return pinput.money_in(body.get(field), field)


def _line_in(raw: object, currency: str) -> LineYear:
    line = pinput.as_obj(raw, "a line")
    return LineYear(
        pinput.as_int(line.get("member_id"), "member_id"),
        pinput.as_text(line.get("category_key"), "category_key"),
        _money(line, "opening"),
        _money(line, "growth"),
        _money(line, "credited"),
        # A run saved by engine 1 has no ``debited``: it debited nothing (ADR-0028 s4).
        _money(line, "debited") if "debited" in line else Money.zero(currency),
        _money(line, "withdrawn"),
        _money(line, "closing"),
    )


def _debit_in(raw: object) -> DebitRow:
    debit = pinput.as_obj(raw, "a debit")
    return DebitRow(
        pinput.as_int(debit.get("member_id"), "member_id"),
        pinput.as_text(debit.get("category_key"), "category_key"),
        pinput.as_text(debit.get("reason"), "reason"),
        pinput.as_text(debit.get("ref"), "ref"),
        _money(debit, "requested"),
        _money(debit, "paid"),
        _money(debit, "unpaid"),
    )


def _pairs(raw: object, what: str) -> list[tuple[object, object]]:
    pairs: list[tuple[object, object]] = []
    for item in pinput.as_list(raw, what):
        pair = pinput.as_list(item, what)
        if len(pair) != 2:
            raise InputError(f"{what} must be [key, amount] pairs.")
        pairs.append((pair[0], pair[1]))
    return pairs


def _age_pension_in(raw: object) -> AgePensionYear | None:
    if raw is None:  # also a run saved by engine 2 or earlier: none was calculated
        return None
    body = pinput.as_obj(raw, "age_pension")
    return AgePensionYear(
        tuple(
            (pinput.as_int(key, "member_id"), pinput.money_in(amount, "paid"))
            for key, amount in _pairs(body.get("paid"), "paid")
        ),
        _bool(body, "couple"),
        _bool(body, "homeowner"),
        tuple(
            (pinput.as_text(key, "category_key"), pinput.money_in(amount, "category"))
            for key, amount in _pairs(body.get("categories"), "categories")
        ),
        _money(body, "property_assets"),
        _money(body, "vehicle_assets"),
        _money(body, "assets"),
        _money(body, "financial_assets"),
        _money(body, "other_income"),
        _money(body, "deemed_income"),
        _money(body, "maximum"),
        _money(body, "income_test"),
        _money(body, "assets_test"),
        pinput.as_text(body.get("bound"), "bound"),
        _money(body, "total"),
    )


def _bool(body: Any, field: str) -> bool:
    value = body.get(field)
    if not isinstance(value, bool):
        raise InputError(f"{field} must be true or false.")
    return value


def _ages_in(raw: object) -> tuple[tuple[int, int], ...]:
    ages: list[tuple[int, int]] = []
    for item in pinput.as_list(raw, "ages"):
        pair = pinput.as_list(item, "an age")
        if len(pair) != 2:
            raise InputError("an age must be a [member_id, age] pair.")
        ages.append((pinput.as_int(pair[0], "member_id"), pinput.as_int(pair[1], "age")))
    return tuple(ages)


def _row_in(raw: object) -> YearRow:
    row = pinput.as_obj(raw, "a year")
    currency = _money(row, "need").currency
    return YearRow(
        pinput.as_int(row.get("t"), "t"),
        pinput.as_date(row.get("starts_on"), "starts_on"),
        _ages_in(row.get("ages")),
        tuple(_line_in(x, currency) for x in pinput.as_list(row.get("lines"), "lines")),
        _money(row, "need"),
        _money(row, "shortfall"),
        # Missing in a run saved by engine 1: no debits.
        tuple(_debit_in(x) for x in pinput.as_list(row.get("debits", []), "debits")),
        # Missing in a run saved by engine 2 or earlier: no Age Pension was calculated.
        _age_pension_in(row.get("age_pension")),
    )


def result_from_json(text: str) -> ProjectionResult:
    body = pinput.as_obj(loads(text), "result")
    lasts = body.get("lasts_beyond_horizon")
    if not isinstance(lasts, bool):
        raise InputError("lasts_beyond_horizon must be true or false.")
    funds = body.get("funds_last_to_age")
    return ProjectionResult(
        pinput.as_int(body.get("engine_version"), "engine_version"),
        pinput.as_int(body.get("input_version"), "input_version"),
        pinput.as_int(body.get("reference_member_id"), "reference_member_id"),
        tuple(_row_in(x) for x in pinput.as_list(body.get("years"), "years")),
        None if funds is None else pinput.as_int(funds, "funds_last_to_age"),
        lasts,
    )


def sustainable_to_json(value: SustainableSpending) -> str:
    return dumps({"amount": pinput.money_out(value.amount), "capped": value.capped})


def sustainable_from_json(text: str) -> SustainableSpending:
    body = pinput.as_obj(loads(text), "sustainable")
    capped = body.get("capped")
    if not isinstance(capped, bool):
        raise InputError("capped must be true or false.")
    return SustainableSpending(_money(body, "amount"), capped)
