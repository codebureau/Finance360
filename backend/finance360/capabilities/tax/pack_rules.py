"""Build the ``TaxRules`` of one financial year from a Rule pack (ADR-0019 s1, s4). Pure.

This is the one place in the Tax Capability that knows the pack's ``tax.`` keys. It is a function
of a ``RulePack`` value (the service looks the pack up in the registry and passes it), so it does no
I/O. It is kept apart from ``rules`` so the engine and the input, which must never read a pack
(import-linter), do not reach ``core.rulepack`` even indirectly.

**Exact years only.** ``build_rules``, which an estimate uses, raises ``TaxRulesError`` for a year
with no figures, or with some of them missing, naming the year and what is missing. Nothing is
carried forward from another year (ADR-0019 s4). ``build_carried_rules`` is the projection's own
read: each figure is carried forward by its policy (ADR-0018 s3: ``cpi`` repeats,
``frozen`` falls in real terms by the run's inflation) and the result says how far it was carried.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from finance360.capabilities.tax.div293 import Div293Rules
from finance360.capabilities.tax.record_types import (
    car_kilometres,
    depreciating_asset,
    home_office_hours,
)
from finance360.capabilities.tax.record_types.base import RecordRules
from finance360.capabilities.tax.rules import Band, TaxRules
from finance360.core.money import Money, format_amount
from finance360.core.rate import Rate
from finance360.core.rulepack import (
    FROZEN,
    TAX_PREFIX,
    Param,
    RulePack,
    Value,
    carried_value,
    fy_label,
)

ORIGIN_SHIPPED = "shipped"
ORIGIN_CAPTURED = "captured"

#: How to fix a year with no figures; shown with the error.
HOW_TO_CAPTURE = "Capture them on Rule figures (FY {label})."

#: ``TaxRules`` attribute -> pack key, for every figure an estimate must have.
REQUIRED: dict[str, str] = {
    "resident_brackets": "tax.resident_brackets",
    "lito_max": "tax.lito.max",
    "lito_taper1_from": "tax.lito.taper1_from",
    "lito_taper1_rate": "tax.lito.taper1_rate",
    "lito_taper2_from": "tax.lito.taper2_from",
    "lito_taper2_rate": "tax.lito.taper2_rate",
    "medicare_levy_rate": "tax.medicare.levy_rate",
    "medicare_single_lower": "tax.medicare.low_income.single_lower",
    "medicare_family_lower": "tax.medicare.low_income.family_lower",
    "medicare_family_per_child": "tax.medicare.low_income.family_per_child",
    "medicare_shade_in_rate": "tax.medicare.low_income.shade_in_rate",
    "medicare_family_reduction_rate": "tax.medicare.low_income.family_reduction_rate",
    "mls_single_tier1": "tax.mls.single_tier1",
    "mls_single_tier2": "tax.mls.single_tier2",
    "mls_single_tier3": "tax.mls.single_tier3",
    "mls_family_tier1": "tax.mls.family_tier1",
    "mls_family_tier2": "tax.mls.family_tier2",
    "mls_family_tier3": "tax.mls.family_tier3",
    "mls_rate_tier1": "tax.mls.rate_tier1",
    "mls_rate_tier2": "tax.mls.rate_tier2",
    "mls_rate_tier3": "tax.mls.rate_tier3",
    "mls_family_per_child_after_first": "tax.mls.family_per_child_after_first",
    "help_t1": "tax.help.t1",
    "help_r1": "tax.help.r1",
    "help_t2": "tax.help.t2",
    "help_r2": "tax.help.r2",
    "help_t3": "tax.help.t3",
    "help_top_rate": "tax.help.top_rate",
    "sbito_rate": "tax.sbito.rate",
    "sbito_cap": "tax.sbito.cap",
    "cgt_discount_rate": "tax.cgt.discount_rate",
}
#: Held only by a year that has one.
OPTIONAL_KEY = "tax.standard_work_deduction"
#: Whole-number figures the Record types read through ``RecordRules`` (a money figure as cents).
#: Each is needed only by a record that uses it, so an absent one never blocks an estimate that has
#: no such record.
RECORD_KEYS: tuple[str, ...] = (
    depreciating_asset.DV_PERCENT_KEY,
    home_office_hours.RATE_KEY,
    car_kilometres.RATE_KEY,
    car_kilometres.LIMIT_KEY,
)


class TaxRulesError(ValueError):
    """The pack cannot supply the rules for the year asked (the message says why and how to fix)."""


def tax_years(pack: RulePack) -> tuple[int, ...]:
    """Every financial year in which the pack holds at least one ``tax.`` figure."""
    return tuple(sorted(fy for fy, values in pack.years.items() if any(_is_tax(k) for k in values)))


def _is_tax(key: str) -> bool:
    return key.startswith(TAX_PREFIX)


def missing_keys(pack: RulePack, fy: int) -> tuple[str, ...]:
    """The required ``tax.`` keys the pack lacks for ``fy`` (all of them if the year is absent)."""
    held: Mapping[str, Value] = pack.years.get(fy, {})
    return tuple(key for key in REQUIRED.values() if key not in held)


def record_rules(pack: RulePack | None) -> RecordRules:
    """The figures the Record types read, for every year the pack holds them (none for no pack)."""
    if pack is None:
        return RecordRules()
    held = (
        (key, fy, value)
        for fy, values in sorted(pack.years.items())
        for key in RECORD_KEYS
        if (value := _whole_number(values.get(key))) is not None
    )
    return RecordRules(tuple(held))


def _whole_number(value: Value | None) -> int | None:
    """A whole-number figure as is, a money figure as its minor units (cents), else ``None``."""
    if isinstance(value, Money):
        return value.amount_minor
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


@dataclass(frozen=True)
class FigureUsed:
    """An official figure a record used, with where it came from."""

    key: str
    name: str
    financial_year: int
    #: Display text, such as ``"0.70 an hour"`` or ``"200%"``.
    value: str
    #: The citation title.
    source: str
    url: str | None
    url_confirmed: bool
    retrieved: date | None
    provisional_note: str | None
    rule_pack_version: str
    #: ``shipped`` (the release holds it) or ``captured`` (a person captured it in the app,
    #: ADR-0022).
    origin: str = ORIGIN_SHIPPED
    #: For a captured figure: the capture row, which always resolves to its quote and page.
    capture_id: int | None = None
    quote: str | None = None
    captured_by: str | None = None
    captured_at: datetime | None = None
    #: The shipped figure a capture replaced, shown when it differs.
    shipped_value: str | None = None
    #: For a captured figure: who supplied it (``typed``, ``ai`` or ``matched``; ADR-0023).
    method: str | None = None


@dataclass(frozen=True)
class FigureWaiting:
    """A figure a record needs and the pack does not hold for the year."""

    key: str
    name: str
    financial_year: int


_PER = {
    "hour": "an hour",
    "year": "a year",
    "fortnight": "a fortnight",
    "week": "a week",
    "km": "a km",
}


def value_text(param: Param, value: Value) -> str:
    """A figure as shown: money with its unit, a rate as a percent, a number with its suffix."""
    if isinstance(value, Money):
        per = _PER.get(param.unit or "", f"per {param.unit}" if param.unit else "")
        return f"{format_amount(value)} {per}".strip()
    if isinstance(value, Rate):
        return f"{value.to_percent()}%"
    if isinstance(value, tuple):
        return f"{len(value)} bands"
    return f"{value}{param.value_suffix}"


def figures_used(pack: RulePack | None, reads: Sequence[tuple[str, int]]) -> tuple[FigureUsed, ...]:
    """The figures among ``reads`` (``(key, financial year)``) that the pack holds, traced.
    A figure the pack lacks is not listed here: ``figures_waiting`` names it."""
    if pack is None:
        return ()
    found: list[FigureUsed] = []
    for key, fy in reads:
        param = pack.describe(key)
        value = pack.years.get(fy, {}).get(key)
        if param is None or value is None:
            continue
        captured = pack.capture_of(fy, key)
        if captured is None:
            found.append(
                FigureUsed(
                    key,
                    param.label or key,
                    fy,
                    value_text(param, value),
                    param.source,
                    param.page_url,
                    param.url_confirmed,
                    pack.retrieved_on(fy, key),
                    pack.provisional_note(fy, key),
                    pack.version_label,
                )
            )
            continue
        # A captured figure: the page is the one the person read (so it is confirmed), and the
        # shipped figure it replaced is shown when it differs.
        found.append(
            FigureUsed(
                key,
                param.label or key,
                fy,
                value_text(param, value),
                param.source,
                captured.page_url or param.page_url,
                captured.page_url is not None or param.url_confirmed,
                pack.retrieved_on(fy, key),
                pack.provisional_note(fy, key),
                pack.version_label,
                ORIGIN_CAPTURED,
                captured.id,
                captured.quote,
                captured.captured_by,
                captured.captured_at,
                value_text(param, captured.shipped_value)
                if captured.shipped_value is not None
                else None,
                captured.method,
            )
        )
    return tuple(found)


def figures_waiting(
    pack: RulePack | None, reads: Sequence[tuple[str, int]], missing: Sequence[str]
) -> tuple[FigureWaiting, ...]:
    """The ``missing`` keys among ``reads``, named, for the year each is read in."""
    waiting: list[FigureWaiting] = []
    for key, fy in reads:
        if key in missing:
            param = pack.describe(key) if pack is not None else None
            waiting.append(FigureWaiting(key, (param.label if param else "") or key, fy))
    return tuple(waiting)


def captured_in_use(pack: RulePack, fy: int) -> tuple[tuple[str, int], ...]:
    """The ``tax.`` figures of ``fy`` that were captured in the app, as ``(key, capture id)``."""
    return tuple(sorted((c.key, c.id) for c in pack.captured if c.fy == fy and _is_tax(c.key)))


def provisional_notes(pack: RulePack, fy: int) -> tuple[tuple[str, str], ...]:
    """The ``tax.`` figures of ``fy`` that are provisional, with their notes."""
    return tuple(sorted((k, n) for k, n in pack.provisional_keys(fy).items() if _is_tax(k)))


def build_rules(pack: RulePack, fy: int) -> TaxRules:
    """The rules for exactly ``fy``, or ``TaxRulesError`` naming the year and what is missing."""
    missing = missing_keys(pack, fy)
    label = fy_label(fy)
    if len(missing) == len(REQUIRED):
        raise TaxRulesError(
            f"The Rule pack ({pack.version_label}) holds no tax figures for FY {label}, and an "
            f"estimate never uses another year's. {HOW_TO_CAPTURE.format(label=label)}"
        )
    if missing:
        raise TaxRulesError(
            f"The Rule pack ({pack.version_label}) is missing tax figures for FY {label}: "
            f"{', '.join(missing)}. {HOW_TO_CAPTURE.format(label=label)}"
        )
    held = pack.years[fy]
    values: dict[str, object] = {}
    for attribute, key in REQUIRED.items():
        value = held[key]
        if isinstance(value, tuple):
            value = tuple(Band(b.threshold, b.rate) for b in value)
        values[attribute] = value
    return TaxRules(
        financial_year=fy,
        standard_work_deduction=_money_or_none(held.get(OPTIONAL_KEY)),
        provisional=provisional_notes(pack, fy),
        **values,  # type: ignore[arg-type]
    )


def _money_or_none(value: Value | None) -> Money | None:
    if value is None:
        return None
    if not isinstance(value, Money):
        raise TaxRulesError(f"{OPTIONAL_KEY} is not an amount.")
    return value


@dataclass(frozen=True)
class CarriedRules:
    """The rules a projection year uses, and how far they were carried from published figures."""

    rules: TaxRules
    financial_year: int
    #: The oldest FY a figure was taken from (the year itself when all are published for it).
    source_fy: int
    #: How many frozen figures were eroded by inflation (carried from an earlier year).
    frozen_eroded: int

    @property
    def carried(self) -> bool:
        return self.source_fy < self.financial_year


def build_carried_rules(pack: RulePack, fy: int, inflation: Rate) -> CarriedRules:
    """The rules for ``fy`` with each figure carried from the latest year that publishes it.

    ``TaxRulesError`` when a required figure has no value in or before ``fy`` at all, naming it.
    """
    values: dict[str, object] = {}
    missing: list[str] = []
    sources: list[int] = []
    eroded = 0
    notes: dict[str, str] = {}
    for attribute, key in REQUIRED.items():
        found = carried_value(pack, key, fy, inflation)
        if found is None:
            missing.append(key)
            continue
        value = found.value
        if isinstance(value, tuple):
            value = tuple(Band(b.threshold, b.rate) for b in value)
        values[attribute] = value
        sources.append(found.source_fy)
        if found.policy == FROZEN and found.behind > 0:
            eroded += 1
        note = pack.provisional_note(found.source_fy, key)
        if note is not None:
            notes[key] = note
    if missing:
        raise TaxRulesError(
            f"The Rule pack ({pack.version_label}) holds no tax figures to carry into FY "
            f"{fy_label(fy)}: {', '.join(missing)}. {HOW_TO_CAPTURE.format(label=fy_label(fy))}"
        )
    rules = TaxRules(
        financial_year=fy,
        standard_work_deduction=None,
        provisional=tuple(sorted(notes.items())),
        **values,  # type: ignore[arg-type]
    )
    return CarriedRules(rules, fy, min(sources), eroded)


DIV293_THRESHOLD = "tax.div293.threshold"
DIV293_RATE = "tax.div293.rate"
CONCESSIONAL_CAP = "tax.super.concessional_cap"


def carried_div293_rules(pack: RulePack, fy: int, inflation: Rate) -> Div293Rules | None:
    """The Division 293 threshold, rate and concessional cap for ``fy``, each carried like the
    others, or ``None`` when the pack holds any of the three in no year up to ``fy``."""
    held = [
        carried_value(pack, key, fy, inflation)
        for key in (DIV293_THRESHOLD, DIV293_RATE, CONCESSIONAL_CAP)
    ]
    if any(found is None for found in held):
        return None
    threshold, rate, cap = (found.value for found in held if found is not None)
    if not (isinstance(threshold, Money) and isinstance(rate, Rate) and isinstance(cap, Money)):
        return None
    return Div293Rules(threshold, rate, cap)
