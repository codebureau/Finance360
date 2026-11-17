"""The rules an estimate is calculated with: a typed block for one financial year (ADR-0019 s1, s4).

``TaxRules`` is built by the service from the Rule pack and passed to the engine, so this module
and the engine never read the pack. It holds integers, ``Money`` (AUD) and ``Rate`` only, and its
canonical JSON is part of ``TaxInput`` so an estimate is explained without the pack. The names of
the attributes follow the pack keys (``tax.lito.max`` is ``lito_max``); ``pack_rules`` maps them.

``provisional`` lists every figure of the year that is announced but not yet law, with its note
(copied from the pack, ADR-0019 s4).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, cast

from finance360.core.money import BASE_CURRENCY, Money, MoneyError, opaque
from finance360.core.rate import Rate, RateError


class RulesError(ValueError):
    """The rules block (or a payload for one) is invalid."""


@dataclass(frozen=True)
class Band:
    """From ``over`` upward, up to the next band's ``over``, ``rate`` applies."""

    over: Money
    rate: Rate


#: How each rules attribute is held in JSON.
MONEY = "money"
RATE = "rate"
BANDS = "bands"
MONEY_OR_NONE = "money?"


@dataclass(frozen=True)
class TaxRules:
    financial_year: int
    resident_brackets: tuple[Band, ...]
    lito_max: Money
    lito_taper1_from: Money
    lito_taper1_rate: Rate
    lito_taper2_from: Money
    lito_taper2_rate: Rate
    medicare_levy_rate: Rate
    medicare_single_lower: Money
    medicare_family_lower: Money
    medicare_family_per_child: Money
    medicare_shade_in_rate: Rate
    medicare_family_reduction_rate: Rate
    mls_single_tier1: Money
    mls_single_tier2: Money
    mls_single_tier3: Money
    mls_family_tier1: Money
    mls_family_tier2: Money
    mls_family_tier3: Money
    mls_rate_tier1: Rate
    mls_rate_tier2: Rate
    mls_rate_tier3: Rate
    mls_family_per_child_after_first: Money
    help_t1: Money
    help_r1: Rate
    help_t2: Money
    help_r2: Rate
    help_t3: Money
    help_top_rate: Rate
    sbito_rate: Rate
    sbito_cap: Money
    cgt_discount_rate: Rate
    #: The instant work-related expenses deduction; ``None`` for a year without one.
    standard_work_deduction: Money | None = None
    #: The most foreign income tax offset the estimate claims without working out the full limit
    #: (the simple cap); ``None`` for a year whose pack does not hold it.
    foreign_offset_cap: Money | None = None
    #: ``(pack key, note)`` for each figure of the year that is not yet law, in key order.
    provisional: tuple[tuple[str, str], ...] = field(default=())

    def __post_init__(self) -> None:
        if not isinstance(opaque(self.financial_year), int) or self.financial_year < 1:
            raise RulesError("financial_year must be a whole number.")
        bands = self.resident_brackets
        if not bands or bands[0].over != Money(0, BASE_CURRENCY):
            raise RulesError("The resident brackets start at 0.")
        for earlier, later in zip(bands, bands[1:], strict=False):
            if later.over <= earlier.over:
                raise RulesError("The resident brackets ascend strictly.")
        for item in fields(self):
            value = getattr(self, item.name)
            kind = KINDS.get(item.name)
            if kind == MONEY and not _is_money(value):
                raise RulesError(f"{item.name} must be AUD Money.")
            if kind == MONEY_OR_NONE and value is not None and not _is_money(value):
                raise RulesError(f"{item.name} must be AUD Money or none.")
            if kind == RATE and not isinstance(opaque(value), Rate):
                raise RulesError(f"{item.name} must be a Rate.")
        object.__setattr__(self, "provisional", tuple(sorted(self.provisional)))

    @property
    def has_provisional(self) -> bool:
        return bool(self.provisional)


def _is_money(value: object) -> bool:
    return isinstance(value, Money) and value.currency == BASE_CURRENCY


#: Attribute name -> how it is held. Every attribute except the year and ``provisional`` is here.
KINDS: dict[str, str] = {
    "resident_brackets": BANDS,
    "lito_max": MONEY,
    "lito_taper1_from": MONEY,
    "lito_taper1_rate": RATE,
    "lito_taper2_from": MONEY,
    "lito_taper2_rate": RATE,
    "medicare_levy_rate": RATE,
    "medicare_single_lower": MONEY,
    "medicare_family_lower": MONEY,
    "medicare_family_per_child": MONEY,
    "medicare_shade_in_rate": RATE,
    "medicare_family_reduction_rate": RATE,
    "mls_single_tier1": MONEY,
    "mls_single_tier2": MONEY,
    "mls_single_tier3": MONEY,
    "mls_family_tier1": MONEY,
    "mls_family_tier2": MONEY,
    "mls_family_tier3": MONEY,
    "mls_rate_tier1": RATE,
    "mls_rate_tier2": RATE,
    "mls_rate_tier3": RATE,
    "mls_family_per_child_after_first": MONEY,
    "help_t1": MONEY,
    "help_r1": RATE,
    "help_t2": MONEY,
    "help_r2": RATE,
    "help_t3": MONEY,
    "help_top_rate": RATE,
    "sbito_rate": RATE,
    "sbito_cap": MONEY,
    "cgt_discount_rate": RATE,
    "standard_work_deduction": MONEY_OR_NONE,
    "foreign_offset_cap": MONEY_OR_NONE,
}

#: Attributes added after the first stored inputs: a payload without one reads as ``None``.
ADDED_LATER: frozenset[str] = frozenset({"foreign_offset_cap"})

# --- canonical JSON ------------------------------------------------------------------------


def _money_out(money: Money) -> dict[str, Any]:
    return {"ccy": money.currency, "minor": money.amount_minor}


def _encode(kind: str, value: object) -> object:
    if kind == MONEY_OR_NONE and value is None:
        return None
    if kind in (MONEY, MONEY_OR_NONE):
        return _money_out(cast(Money, value))
    if kind == RATE:
        return cast(Rate, value).to_percent()
    return [
        {"over": _money_out(b.over), "rate": b.rate.to_percent()}
        for b in cast("tuple[Band, ...]", value)
    ]


def to_payload(rules: TaxRules) -> dict[str, Any]:
    """The rules as plain JSON-ready data (no floats), in canonical order."""
    return {
        "financial_year": rules.financial_year,
        "provisional": [{"key": key, "note": note} for key, note in rules.provisional],
        "values": {
            name: _encode(kind, getattr(rules, name)) for name, kind in sorted(KINDS.items())
        },
    }


def _obj(raw: object, what: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RulesError(f"{what} must be an object.")
    return cast("dict[str, Any]", raw)


def _money_in(raw: object, what: str) -> Money:
    body = _obj(raw, what)
    minor, ccy = body.get("minor"), body.get("ccy")
    if not isinstance(minor, int) or isinstance(minor, bool) or not isinstance(ccy, str):
        raise RulesError(f"{what} must be money.")
    try:
        return Money(minor, ccy)
    except MoneyError as error:
        raise RulesError(str(error)) from None


def _rate_in(raw: object, what: str) -> Rate:
    if not isinstance(raw, str):
        raise RulesError(f"{what} must be a percent string.")
    try:
        return Rate.from_percent(raw)
    except RateError as error:
        raise RulesError(str(error)) from None


def _decode(kind: str, raw: object, what: str) -> object:
    if kind == MONEY_OR_NONE and raw is None:
        return None
    if kind in (MONEY, MONEY_OR_NONE):
        return _money_in(raw, what)
    if kind == RATE:
        return _rate_in(raw, what)
    if not isinstance(raw, list):
        raise RulesError(f"{what} must be a list.")
    return tuple(
        Band(_money_in(_obj(b, what).get("over"), what), _rate_in(_obj(b, what).get("rate"), what))
        for b in cast("list[Any]", raw)
    )


def from_payload(payload: object) -> TaxRules:
    body = _obj(payload, "rules")
    year = body.get("financial_year")
    if not isinstance(year, int) or isinstance(year, bool):
        raise RulesError("financial_year must be a whole number.")
    values = _obj(body.get("values"), "values")
    if not set(KINDS) - ADDED_LATER <= set(values) <= set(KINDS):
        raise RulesError("The rules values do not match the rules block.")
    notes = body.get("provisional")
    if not isinstance(notes, list):
        raise RulesError("provisional must be a list.")
    decoded = {
        name: _decode(kind, values.get(name), name)
        for name, kind in KINDS.items()
        if name in values
    }
    provisional = tuple(
        (
            str(_obj(n, "a provisional note").get("key")),
            str(_obj(n, "a provisional note").get("note")),
        )
        for n in cast("list[Any]", notes)
    )
    return TaxRules(financial_year=year, provisional=provisional, **decoded)  # type: ignore[arg-type]
