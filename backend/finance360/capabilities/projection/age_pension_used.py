"""What an Age Pension calculation used, as a run records it (ADR-0029 section 4 provenance).

Plain values and their canonical JSON, no database. The engine never sees these: it sees
``input.AgePensionInput``. This is the trace beside it: the Household facts (entered or default),
each Member's eligibility date, every rule value (key, FY, origin, provisional flag), each Property
and Vehicle Account counted (with its value and Valuation date), the Accounts left out, and the
simplifications that apply. It also carries the "not calculated, because..." states.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from finance360.core.money import Money
from finance360.core.rate import Rate

CALCULATED = "calculated"
NEEDS_HOME_CHOICE = "needs_home_choice"
NOT_MODELLED = "not_modelled"
#: The Rule pack (with captures) holds no ``age_pension.`` figure for the first financial year.
NO_FIGURES = "no_figures"

ORIGIN_SHIPPED = "shipped"
ORIGIN_CAPTURED = "captured"

ROLE_ASSESSED = "assessed"
ROLE_EXEMPT_HOME = "exempt_home"

#: The stated simplifications, by key (ADR-0029 section 2). The working flags those that apply.
NO_DEBT_DEDUCTION = "no_debt_deduction"
RENT_NOT_COUNTED = "rent_not_counted"
RETIREMENT_SUPER_BEFORE_AGE = "retirement_super_before_age"
PROPERTY_HELD_CONSTANT = "property_held_constant"
SIMPLIFICATIONS: dict[str, str] = {
    NO_DEBT_DEDUCTION: (
        "No deduction for debt secured against an assessed Property or Vehicle: assets are "
        "overstated, so the pension is conservative."
    ),
    RENT_NOT_COUNTED: (
        "Rent from an assessed Property is not counted as income: the pension is overstated for "
        "a rented Property."
    ),
    RETIREMENT_SUPER_BEFORE_AGE: (
        "Super drawn as a retirement-phase pension before Age Pension age is assessable in "
        "practice but not counted here: the pension is overstated in those years."
    ),
    PROPERTY_HELD_CONSTANT: (
        "Property is held at its latest Valuation, constant in real terms: no growth or sale is "
        "projected."
    ),
}


@dataclass(frozen=True)
class FactUsed:
    """A Household fact as used. ``source`` is ``entered`` or ``default``."""

    key: str
    value: str
    source: str


@dataclass(frozen=True)
class EligibilityUsed:
    member_id: int
    #: The date the Member reaches Age Pension age, and the projection year index it became.
    on: date
    year: int


@dataclass(frozen=True)
class RuleFigureUsed:
    """One rule value in force from projection year ``from_year``: the pack key, the financial year
    whose published figure it is (``fy``, keyed by the year it ends in), where it came from."""

    key: str
    from_year: int
    fy: int
    value: Money | Rate
    #: ``shipped``, or ``captured`` (a figure the household captured, ADR-0022).
    origin: str
    capture_id: int | None
    #: The note when the figure is announced but not yet law; None for a firm figure.
    provisional_note: str | None


@dataclass(frozen=True)
class AssetUsed:
    """A Property or Vehicle Account at its latest Valuation as at the projection start."""

    account_id: int
    name: str
    category_key: str
    value: Money
    valued_on: date
    #: ``assessed`` or ``exempt_home`` (the principal home, valued but not counted).
    role: str


@dataclass(frozen=True)
class LeftOut:
    """A Property or Vehicle Account the Position flags (no Valuation or FX rate): not counted."""

    account_id: int
    name: str
    category_key: str
    status: str


@dataclass(frozen=True)
class AgePensionUsed:
    status: str
    #: Why nothing was calculated (empty when ``calculated``).
    reasons: tuple[str, ...] = ()
    facts: tuple[FactUsed, ...] = ()
    eligibility: tuple[EligibilityUsed, ...] = ()
    rules: tuple[RuleFigureUsed, ...] = ()
    assets: tuple[AssetUsed, ...] = ()
    left_out: tuple[LeftOut, ...] = ()
    #: Keys of ``SIMPLIFICATIONS`` that apply to this run.
    simplifications: tuple[str, ...] = ()


# --- canonical JSON ------------------------------------------------------------------------


def _money_out(money: Money) -> dict[str, Any]:
    return {"ccy": money.currency, "minor": money.amount_minor}


def to_payload(used: AgePensionUsed) -> dict[str, Any]:
    return {
        "assets": [
            {
                "account_id": a.account_id,
                "category_key": a.category_key,
                "name": a.name,
                "role": a.role,
                "valued_on": a.valued_on.isoformat(),
                "value": _money_out(a.value),
            }
            for a in used.assets
        ],
        "eligibility": [
            {"member_id": e.member_id, "on": e.on.isoformat(), "year": e.year}
            for e in used.eligibility
        ],
        "facts": [{"key": f.key, "source": f.source, "value": f.value} for f in used.facts],
        "left_out": [
            {
                "account_id": x.account_id,
                "category_key": x.category_key,
                "name": x.name,
                "status": x.status,
            }
            for x in used.left_out
        ],
        "reasons": list(used.reasons),
        "rules": [
            {
                "capture_id": r.capture_id,
                "from_year": r.from_year,
                "fy": r.fy,
                "key": r.key,
                "origin": r.origin,
                "provisional_note": r.provisional_note,
                "value": _money_out(r.value)
                if isinstance(r.value, Money)
                else r.value.to_percent(),
            }
            for r in used.rules
        ],
        "simplifications": list(used.simplifications),
        "status": used.status,
    }


def from_payload(raw: Mapping[str, Any]) -> AgePensionUsed:
    def value(item: Any) -> Money | Rate:
        if isinstance(item, dict):
            return Money(item["minor"], item["ccy"])  # pyright: ignore[reportUnknownArgumentType]
        return Rate.from_percent(item)

    return AgePensionUsed(
        raw["status"],
        tuple(raw["reasons"]),
        tuple(FactUsed(f["key"], f["value"], f["source"]) for f in raw["facts"]),
        tuple(
            EligibilityUsed(e["member_id"], date.fromisoformat(e["on"]), e["year"])
            for e in raw["eligibility"]
        ),
        tuple(
            RuleFigureUsed(
                r["key"],
                r["from_year"],
                r["fy"],
                value(r["value"]),
                r["origin"],
                r["capture_id"],
                r["provisional_note"],
            )
            for r in raw["rules"]
        ),
        tuple(
            AssetUsed(
                a["account_id"],
                a["name"],
                a["category_key"],
                Money(a["value"]["minor"], a["value"]["ccy"]),
                date.fromisoformat(a["valued_on"]),
                a["role"],
            )
            for a in raw["assets"]
        ),
        tuple(
            LeftOut(x["account_id"], x["name"], x["category_key"], x["status"])
            for x in raw["left_out"]
        ),
        tuple(raw["simplifications"]),
    )
