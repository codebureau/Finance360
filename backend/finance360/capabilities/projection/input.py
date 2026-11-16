"""The Projection input (ADR-0015 section 2): fully resolved, versioned, serialisable. Pure.

The engine sees only this value. Assumptions have already been resolved into concrete numbers per
line, so the engine never knows where a figure came from. The input is frozen, made of tuples, and
holds no float. It is normalised on construction (members, lines, inflows and outflows in a
canonical order) so that equal inputs are equal values and ``from_json(to_json(x)) == x`` exactly.

Serialisation is canonical JSON: money as ``{"minor", "ccy"}``, rates as percent strings, dates as
ISO text, keys sorted. A float anywhere in a payload is rejected.

Leap days. A date on 29 February falls on 28 February in a year that has none. This one rule
serves both the anniversary of ``as_at`` (where each year starts) and a birthday (``age_on``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from finance360.core.money import Money, MoneyError, exponent, opaque
from finance360.core.rate import Rate, RateError

INPUT_VERSION = 2
MIN_YEARS = 1
MAX_YEARS = 120
#: Why an amount leaves a line (ADR-0028 s1). A tag for display and traceability only: no code
#: branches on it, so adding a reason needs no new field and no version bump.
DEBIT_REASONS = ("div293", "insurance_premium", "fund_fee")
REF_MAX = 40


class InputError(ValueError):
    """The Projection input (or a payload for one) is invalid."""


# --- dates and ages -----------------------------------------------------------------------


def anniversary(day: date, year: int) -> date:
    """``day`` moved to ``year``; 29 February becomes 28 February in a year without one."""
    try:
        return day.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def age_on(born_on: date, on: date) -> int:
    """Whole years from ``born_on`` to ``on`` (a birthday counts from the day it falls)."""
    return on.year - born_on.year - (1 if on < anniversary(born_on, on.year) else 0)


# --- the input ----------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberRef:
    member_id: int
    born_on: date


@dataclass(frozen=True)
class Line:
    """A balance held by one Member in one Category (never per Account)."""

    member_id: int
    category_key: str
    start: Money
    return_rate: Rate
    #: The first year (0-based) the balance may be drawn on.
    accessible_from_year: int


@dataclass(frozen=True)
class Inflow:
    """An annual amount credited to a line over ``[from_year, until_year)`` (null: to the end)."""

    member_id: int
    category_key: str
    amount: Money
    from_year: int
    until_year: int | None


@dataclass(frozen=True)
class Outflow:
    """The household's annual spending need over ``[from_year, until_year)``."""

    amount: Money
    from_year: int
    until_year: int | None


@dataclass(frozen=True)
class LineDebit:
    """A regular, non-discretionary amount taken from one Member's line in one projection year
    (ADR-0028). Paid from the line's balance, never below zero; the part it cannot pay is paid
    personally that year (added to the year's need)."""

    member_id: int
    category_key: str
    #: The projection year ``t``, ``0 <= year < years``.
    year: int
    #: Above 0, in the input currency, in today's dollars like every other amount.
    amount: Money
    #: A key from ``DEBIT_REASONS``.
    reason: str
    #: A short human label of what it is for, 1 to ``REF_MAX`` characters. Text, never parsed.
    ref: str


def debit_order(debit: LineDebit) -> tuple[int, int, str, str, str, int]:
    """The canonical order of debits (ADR-0028 s1): year, Member, Category, reason, ref, amount."""
    return (
        debit.year,
        debit.member_id,
        debit.category_key,
        debit.reason,
        debit.ref,
        debit.amount.amount_minor,
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class ProjectionInput:
    input_version: int
    as_at: date
    currency: str
    members: tuple[MemberRef, ...]
    years: int
    inflation: Rate
    lines: tuple[Line, ...]
    inflows: tuple[Inflow, ...]
    outflows: tuple[Outflow, ...]
    drawdown_order: tuple[str, ...]
    debits: tuple[LineDebit, ...] = ()

    def __post_init__(self) -> None:
        if self.input_version != INPUT_VERSION:
            raise InputError(f"Unsupported input_version {self.input_version!r}.")
        try:
            exponent(self.currency)
        except MoneyError as error:
            raise InputError(str(error)) from None
        if not _is_int(self.years) or not MIN_YEARS <= self.years <= MAX_YEARS:
            raise InputError(f"years must be a whole number from {MIN_YEARS} to {MAX_YEARS}.")
        if not isinstance(opaque(self.inflation), Rate):
            raise InputError("inflation must be a Rate.")

        member_ids = [m.member_id for m in self.members]
        if not member_ids or len(set(member_ids)) != len(member_ids):
            raise InputError("members must be non-empty and unique.")
        order = list(self.drawdown_order)
        if not order or len(set(order)) != len(order):
            raise InputError("drawdown_order must be non-empty and unique.")

        keys = [(line.member_id, line.category_key) for line in self.lines]
        if len(set(keys)) != len(keys):
            raise InputError("lines must be unique on (member_id, category_key).")
        for line in self.lines:
            if line.member_id not in member_ids or line.category_key not in order:
                raise InputError(
                    "every line needs a known Member and a Category in drawdown_order."
                )
            if not isinstance(opaque(line.return_rate), Rate):
                raise InputError("return_rate must be a Rate.")
            self._money(line.start, "a line start", allow_zero=True)
            if not _is_int(line.accessible_from_year) or line.accessible_from_year < 0:
                raise InputError("accessible_from_year must be a whole number, 0 or more.")
        for inflow in self.inflows:
            if (inflow.member_id, inflow.category_key) not in keys:
                raise InputError("every inflow needs a line to credit.")
            self._money(inflow.amount, "an inflow amount")
            self._range(inflow.from_year, inflow.until_year)
        for outflow in self.outflows:
            self._money(outflow.amount, "an outflow amount")
            self._range(outflow.from_year, outflow.until_year)

        for debit in self.debits:
            if (debit.member_id, debit.category_key) not in keys:
                raise InputError("every debit needs a line to debit.")
            self._money(debit.amount, "a debit amount")
            if not _is_int(debit.year) or not 0 <= debit.year < self.years:
                raise InputError("a debit's year must be a whole number within the projection.")
            if debit.reason not in DEBIT_REASONS:
                raise InputError(f"a debit's reason must be one of {', '.join(DEBIT_REASONS)}.")
            if not isinstance(debit.ref, str) or not 1 <= len(debit.ref) <= REF_MAX:  # pyright: ignore[reportUnnecessaryIsInstance]
                raise InputError(f"a debit's ref must be 1 to {REF_MAX} characters.")

        rank = {key: i for i, key in enumerate(order)}
        object.__setattr__(self, "debits", tuple(sorted(self.debits, key=debit_order)))
        object.__setattr__(self, "members", tuple(sorted(self.members, key=lambda m: m.member_id)))
        object.__setattr__(
            self,
            "lines",
            tuple(sorted(self.lines, key=lambda x: (rank[x.category_key], x.member_id))),
        )
        object.__setattr__(
            self,
            "inflows",
            tuple(
                sorted(
                    self.inflows,
                    key=lambda x: (
                        rank[x.category_key],
                        x.member_id,
                        x.from_year,
                        x.until_year is None,
                        x.until_year or 0,
                        x.amount.amount_minor,
                    ),
                )
            ),
        )
        object.__setattr__(
            self,
            "outflows",
            tuple(
                sorted(
                    self.outflows,
                    key=lambda x: (
                        x.from_year,
                        x.until_year is None,
                        x.until_year or 0,
                        x.amount.amount_minor,
                    ),
                )
            ),
        )

    def _money(self, money: Money, what: str, *, allow_zero: bool = False) -> None:
        if not isinstance(opaque(money), Money) or money.currency != self.currency:
            raise InputError(f"{what} must be Money in {self.currency}.")
        if money.amount_minor < 0 or (money.amount_minor == 0 and not allow_zero):
            raise InputError(f"{what} must be {'0 or more' if allow_zero else 'above 0'}.")

    @staticmethod
    def _range(from_year: int, until_year: int | None) -> None:
        if not _is_int(from_year) or from_year < 0:
            raise InputError("from_year must be a whole number, 0 or more.")
        if until_year is not None and (not _is_int(until_year) or until_year < from_year):
            raise InputError("until_year must be null or at least from_year.")


# --- canonical JSON -----------------------------------------------------------------------


def money_out(money: Money) -> dict[str, Any]:
    return {"ccy": money.currency, "minor": money.amount_minor}


def to_payload(value: ProjectionInput) -> dict[str, Any]:
    """The input as plain JSON-ready data (no floats), in canonical order."""
    return {
        "as_at": value.as_at.isoformat(),
        "currency": value.currency,
        "debits": [
            {
                "amount": money_out(d.amount),
                "category_key": d.category_key,
                "member_id": d.member_id,
                "reason": d.reason,
                "ref": d.ref,
                "year": d.year,
            }
            for d in value.debits
        ],
        "drawdown_order": list(value.drawdown_order),
        "inflation": value.inflation.to_percent(),
        "inflows": [
            {
                "amount": money_out(i.amount),
                "category_key": i.category_key,
                "from_year": i.from_year,
                "member_id": i.member_id,
                "until_year": i.until_year,
            }
            for i in value.inflows
        ],
        "input_version": value.input_version,
        "lines": [
            {
                "accessible_from_year": x.accessible_from_year,
                "category_key": x.category_key,
                "member_id": x.member_id,
                "return_rate": x.return_rate.to_percent(),
                "start": money_out(x.start),
            }
            for x in value.lines
        ],
        "members": [
            {"born_on": m.born_on.isoformat(), "member_id": m.member_id} for m in value.members
        ],
        "outflows": [
            {
                "amount": money_out(o.amount),
                "from_year": o.from_year,
                "until_year": o.until_year,
            }
            for o in value.outflows
        ],
        "years": value.years,
    }


def to_json(value: ProjectionInput) -> str:
    """Canonical JSON text: sorted keys, no spaces. Equal inputs give identical text."""
    return json.dumps(to_payload(value), sort_keys=True, separators=(",", ":"))


def as_int(raw: object, what: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise InputError(f"{what} must be a whole number, not {type(raw).__name__}.")
    return raw


def as_text(raw: object, what: str) -> str:
    if not isinstance(raw, str):
        raise InputError(f"{what} must be text, not {type(raw).__name__}.")
    return raw


def as_obj(raw: object, what: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise InputError(f"{what} must be an object.")
    return raw  # pyright: ignore[reportUnknownVariableType]


def as_list(raw: object, what: str) -> list[Any]:
    if not isinstance(raw, list):
        raise InputError(f"{what} must be a list.")
    return raw  # pyright: ignore[reportUnknownVariableType]


def as_date(raw: object, what: str) -> date:
    try:
        return date.fromisoformat(as_text(raw, what))
    except ValueError:
        raise InputError(f"{what} must be an ISO date.") from None


def money_in(raw: object, what: str) -> Money:
    body = as_obj(raw, what)
    try:
        return Money(as_int(body.get("minor"), what), as_text(body.get("ccy"), what))
    except MoneyError as error:
        raise InputError(str(error)) from None


def _rate_in(raw: object, what: str) -> Rate:
    try:
        return Rate.from_percent(as_text(raw, what))
    except RateError as error:
        raise InputError(str(error)) from None


def _until(raw: object, what: str) -> int | None:
    return None if raw is None else as_int(raw, what)


def _v2(payload: Mapping[str, Any]) -> ProjectionInput:
    try:
        return ProjectionInput(
            input_version=2,
            as_at=as_date(payload.get("as_at"), "as_at"),
            currency=as_text(payload.get("currency"), "currency"),
            members=tuple(
                MemberRef(
                    as_int(m.get("member_id"), "member_id"), as_date(m.get("born_on"), "born_on")
                )
                for m in (as_obj(x, "a member") for x in as_list(payload.get("members"), "members"))
            ),
            years=as_int(payload.get("years"), "years"),
            inflation=_rate_in(payload.get("inflation"), "inflation"),
            lines=tuple(
                Line(
                    as_int(x.get("member_id"), "member_id"),
                    as_text(x.get("category_key"), "category_key"),
                    money_in(x.get("start"), "start"),
                    _rate_in(x.get("return_rate"), "return_rate"),
                    as_int(x.get("accessible_from_year"), "accessible_from_year"),
                )
                for x in (as_obj(y, "a line") for y in as_list(payload.get("lines"), "lines"))
            ),
            inflows=tuple(
                Inflow(
                    as_int(x.get("member_id"), "member_id"),
                    as_text(x.get("category_key"), "category_key"),
                    money_in(x.get("amount"), "amount"),
                    as_int(x.get("from_year"), "from_year"),
                    _until(x.get("until_year"), "until_year"),
                )
                for x in (
                    as_obj(y, "an inflow") for y in as_list(payload.get("inflows"), "inflows")
                )
            ),
            outflows=tuple(
                Outflow(
                    money_in(x.get("amount"), "amount"),
                    as_int(x.get("from_year"), "from_year"),
                    _until(x.get("until_year"), "until_year"),
                )
                for x in (
                    as_obj(y, "an outflow") for y in as_list(payload.get("outflows"), "outflows")
                )
            ),
            drawdown_order=tuple(
                as_text(k, "a drawdown category")
                for k in as_list(payload.get("drawdown_order"), "drawdown_order")
            ),
            debits=tuple(
                LineDebit(
                    as_int(x.get("member_id"), "member_id"),
                    as_text(x.get("category_key"), "category_key"),
                    as_int(x.get("year"), "year"),
                    money_in(x.get("amount"), "amount"),
                    as_text(x.get("reason"), "reason"),
                    as_text(x.get("ref"), "ref"),
                )
                for x in (as_obj(y, "a debit") for y in as_list(payload.get("debits"), "debits"))
            ),
        )
    except (TypeError, MoneyError, RateError) as error:
        raise InputError(str(error)) from None


def _upcast_v1_to_v2(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Version 2 added ``debits`` (ADR-0028); a version 1 snapshot has none."""
    return {**payload, "input_version": 2, "debits": []}


#: One pure upcaster per past version, oldest first, each returning the next version's payload, so
#: a saved run's snapshot always loads.
_UPCASTERS: dict[int, Callable[[Mapping[str, Any]], Mapping[str, Any]]] = {
    1: _upcast_v1_to_v2,
}


def reject_floats(value: object) -> None:
    """A float anywhere in the payload, wanted or not, is an error."""
    if isinstance(value, float):
        raise InputError(f"A float is not allowed in a Projection input: {value!r}")
    if isinstance(value, dict):
        for item in cast("dict[str, object]", value).values():
            reject_floats(item)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            reject_floats(item)


def from_payload(payload: object) -> ProjectionInput:
    """Rebuild an input from ``to_payload`` data, upcasting an older version. Floats raise."""
    reject_floats(payload)
    body = as_obj(payload, "payload")
    version = as_int(body.get("input_version"), "input_version")
    while version < INPUT_VERSION:
        upcast = _UPCASTERS.get(version)
        if upcast is None:
            raise InputError(f"No upcaster from input_version {version}.")
        body = upcast(body)
        version += 1
    if version != INPUT_VERSION:
        raise InputError(f"Unsupported input_version {version}.")
    return _v2(body)


def no_float(text: str) -> Any:
    raise InputError(f"A float is not allowed in a Projection input: {text}")


def from_json(text: str) -> ProjectionInput:
    """Parse canonical JSON text. A number with a fraction or exponent, or NaN, is rejected."""
    try:
        payload = json.loads(text, parse_float=no_float, parse_constant=no_float)
    except json.JSONDecodeError as error:
        raise InputError(f"Not valid JSON: {error}") from None
    return from_payload(payload)
