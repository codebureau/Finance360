"""The Tax input (ADR-0019 s3): fully resolved, versioned, serialisable. Pure.

An estimate is always for the whole Household and one financial year: for each Member their date of
birth and Label totals, the family facts, the ``TaxRules`` block and the provenance entries. The
engine sees only this value. It is frozen, made of tuples, holds no float, and is normalised on
construction (Members by id, totals by label key, a total at its default dropped) so that equal
inputs are equal values and ``from_json(to_json(x)) == x`` exactly.

Canonical JSON: money as ``{"ccy", "minor"}``, days as a whole number, a flag as a boolean, dates as
ISO text, keys sorted and no spaces. A float anywhere in a payload is rejected.

Amounts are nominal dollars of the financial year (law is nominal); a foreign-currency amount is
entered already converted (ADR-0019 s3).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from finance360.capabilities.tax import labels, rules
from finance360.capabilities.tax.rules import TaxRules
from finance360.core.money import BASE_CURRENCY, Money, MoneyError, opaque

TAX_INPUT_VERSION = 1
MAX_MEMBERS = 2
MAX_DEPENDANTS = 30

Total = Money | int | bool


class InputError(ValueError):
    """The Tax input (or a payload for one) is invalid."""


def days_in_fy(fy: int) -> int:
    """Days in the financial year ending 30 June ``fy`` (366 when it contains 29 February)."""
    return (date(fy, 6, 30) - date(fy - 1, 6, 30)).days


def default_for(kind: str) -> Total:
    if kind == labels.MONEY:
        return Money.zero(BASE_CURRENCY)
    return False if kind == labels.FLAG else 0


@dataclass(frozen=True)
class MemberInput:
    member_id: int
    born_on: date
    #: ``(label key, total)`` sorted by key, with a total at its default (zero, 0, False) left out.
    totals: tuple[tuple[str, Total], ...] = ()

    def total(self, key: str) -> Total:
        """The Member's total for a label (its default when none was entered)."""
        for held, value in self.totals:
            if held == key:
                return value
        label = labels.get(key)
        if label is None:
            raise InputError(f"Unknown Return label {key!r}.")
        return default_for(label.kind)

    def money(self, key: str) -> Money:
        value = self.total(key)
        if not isinstance(value, Money):
            raise InputError(f"{key!r} is not an amount.")
        return value

    def days(self, key: str) -> int:
        value = self.total(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise InputError(f"{key!r} is not a number of days.")
        return value

    def flag(self, key: str) -> bool:
        return self.total(key) is True


@dataclass(frozen=True)
class Provenance:
    """Where something in the input came from (for example the Rule pack version)."""

    key: str
    detail: str


@dataclass(frozen=True)
class TaxInput:
    input_version: int
    financial_year: int
    members: tuple[MemberInput, ...]
    #: Dependent children, for the Medicare levy reduction and the surcharge thresholds.
    dependants: int
    rules: TaxRules
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        if self.input_version != TAX_INPUT_VERSION:
            raise InputError(f"Unsupported input_version {self.input_version!r}.")
        if not _is_int(self.financial_year) or self.rules.financial_year != self.financial_year:
            raise InputError("The rules must be those of the financial year.")
        if not _is_int(self.dependants) or not 0 <= self.dependants <= MAX_DEPENDANTS:
            raise InputError(f"dependants must be a whole number from 0 to {MAX_DEPENDANTS}.")
        ids = [m.member_id for m in self.members]
        if not 1 <= len(ids) <= MAX_MEMBERS or len(set(ids)) != len(ids):
            raise InputError(f"An estimate covers one or {MAX_MEMBERS} different Members.")
        members: list[MemberInput] = []
        for member in sorted(self.members, key=lambda m: m.member_id):
            members.append(MemberInput(member.member_id, member.born_on, self._clean(member)))
        object.__setattr__(self, "members", tuple(members))

    def _clean(self, member: MemberInput) -> tuple[tuple[str, Total], ...]:
        seen: set[str] = set()
        kept: list[tuple[str, Total]] = []
        for key, value in member.totals:
            label = labels.get(key)
            if label is None:
                raise InputError(f"Unknown Return label {key!r}.")
            if key in seen:
                raise InputError(f"{key!r} is entered twice for one Member.")
            seen.add(key)
            self._check_total(label, value)
            if value != default_for(label.kind):
                kept.append((key, value))
        return tuple(sorted(kept, key=lambda item: item[0]))

    def _check_total(self, label: labels.Label, value: object) -> None:
        raw = opaque(value)
        if label.kind == labels.MONEY:
            if not isinstance(raw, Money) or raw.currency != BASE_CURRENCY:
                raise InputError(f"{label.key!r} must be Money in {BASE_CURRENCY}.")
            if raw.amount_minor < 0:
                raise InputError(f"{label.key!r} must be 0 or more.")
        elif label.kind == labels.DAYS:
            if not _is_int(raw) or not 0 <= cast(int, raw) <= days_in_fy(self.financial_year):
                raise InputError(
                    f"{label.key!r} must be a whole number of days from 0 to "
                    f"{days_in_fy(self.financial_year)}."
                )
        elif not isinstance(raw, bool):
            raise InputError(f"{label.key!r} must be true or false.")

    @property
    def has_spouse(self) -> bool:
        """Two Members are assessed as a couple (the family tests need both)."""
        return len(self.members) == MAX_MEMBERS


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# --- canonical JSON ------------------------------------------------------------------------


def money_out(money: Money) -> dict[str, Any]:
    return {"ccy": money.currency, "minor": money.amount_minor}


def _total_out(value: Total) -> object:
    return money_out(value) if isinstance(value, Money) else value


def to_payload(value: TaxInput) -> dict[str, Any]:
    """The input as plain JSON-ready data (no floats), in canonical order."""
    return {
        "dependants": value.dependants,
        "financial_year": value.financial_year,
        "input_version": value.input_version,
        "members": [
            {
                "born_on": m.born_on.isoformat(),
                "member_id": m.member_id,
                "totals": {key: _total_out(total) for key, total in m.totals},
            }
            for m in value.members
        ],
        "provenance": [{"detail": p.detail, "key": p.key} for p in value.provenance],
        "rules": rules.to_payload(value.rules),
    }


def to_json(value: TaxInput) -> str:
    """Canonical JSON text: sorted keys, no spaces. Equal inputs give identical text."""
    return json.dumps(to_payload(value), sort_keys=True, separators=(",", ":"))


def as_int(raw: object, what: str) -> int:
    if not _is_int(raw):
        raise InputError(f"{what} must be a whole number, not {type(raw).__name__}.")
    return cast(int, raw)


def as_text(raw: object, what: str) -> str:
    if not isinstance(raw, str):
        raise InputError(f"{what} must be text, not {type(raw).__name__}.")
    return raw


def as_obj(raw: object, what: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise InputError(f"{what} must be an object.")
    return cast("dict[str, Any]", raw)


def as_list(raw: object, what: str) -> list[Any]:
    if not isinstance(raw, list):
        raise InputError(f"{what} must be a list.")
    return cast("list[Any]", raw)


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


def _total_in(key: str, raw: object) -> Total:
    label = labels.get(key)
    if label is None:
        raise InputError(f"Unknown Return label {key!r}.")
    if label.kind == labels.MONEY:
        return money_in(raw, key)
    if label.kind == labels.DAYS:
        return as_int(raw, key)
    if not isinstance(raw, bool):
        raise InputError(f"{key!r} must be true or false.")
    return raw


def _v1(payload: Mapping[str, Any]) -> TaxInput:
    try:
        return TaxInput(
            input_version=1,
            financial_year=as_int(payload.get("financial_year"), "financial_year"),
            dependants=as_int(payload.get("dependants"), "dependants"),
            members=tuple(
                MemberInput(
                    as_int(m.get("member_id"), "member_id"),
                    as_date(m.get("born_on"), "born_on"),
                    tuple(
                        (key, _total_in(key, raw))
                        for key, raw in as_obj(m.get("totals"), "totals").items()
                    ),
                )
                for m in (as_obj(x, "a member") for x in as_list(payload.get("members"), "members"))
            ),
            rules=rules.from_payload(payload.get("rules")),
            provenance=tuple(
                Provenance(as_text(p.get("key"), "key"), as_text(p.get("detail"), "detail"))
                for p in (
                    as_obj(x, "a provenance entry")
                    for x in as_list(payload.get("provenance"), "provenance")
                )
            ),
        )
    except (TypeError, MoneyError, rules.RulesError) as error:
        raise InputError(str(error)) from None


#: One pure upcaster per past version, oldest first, each returning the next version's payload.
#: None exist yet (version 1 is current).
_UPCASTERS: dict[int, Callable[[Mapping[str, Any]], Mapping[str, Any]]] = {}


def reject_floats(value: object) -> None:
    """A float anywhere in the payload, wanted or not, is an error."""
    if isinstance(value, float):
        raise InputError(f"A float is not allowed in a Tax input: {value!r}")
    if isinstance(value, dict):
        for item in cast("dict[str, object]", value).values():
            reject_floats(item)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            reject_floats(item)


def from_payload(payload: object) -> TaxInput:
    """Rebuild an input from ``to_payload`` data, upcasting an older version. Floats raise."""
    reject_floats(payload)
    body = as_obj(payload, "payload")
    version = as_int(body.get("input_version"), "input_version")
    while version < TAX_INPUT_VERSION:
        upcast = _UPCASTERS.get(version)
        if upcast is None:
            raise InputError(f"No upcaster from input_version {version}.")
        body = upcast(body)
        version += 1
    if version != TAX_INPUT_VERSION:
        raise InputError(f"Unsupported input_version {version}.")
    return _v1(body)


def no_float(text: str) -> Any:
    raise InputError(f"A float is not allowed in a Tax input: {text}")


def loads(text: str) -> object:
    """Parse JSON text. A number with a fraction or exponent, or NaN, is rejected."""
    try:
        return json.loads(text, parse_float=no_float, parse_constant=no_float)
    except json.JSONDecodeError as error:
        raise InputError(f"Not valid JSON: {error}") from None


def from_json(text: str) -> TaxInput:
    return from_payload(loads(text))
