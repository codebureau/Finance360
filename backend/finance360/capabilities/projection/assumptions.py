"""Assumption keys and the pure resolver (ADR-0016 sections 1 and 2).

An Assumption is one stated value; a Scenario states only what differs from the Household default.
``resolve`` takes the two layers as plain rows and returns the values one projection needs, plus
where each came from. It imports no database, model or service code (the import-linter contract
forbids it), so it is tested without a database. The engine never sees a Scenario.

Precedence, most specific first: the Scenario's own value at the most specific scope, the Scenario's
value at a wider scope, the Household default at the most specific scope, the Household default at a
wider scope. Values are absolute, never deltas. A key still unset after all layers is an error that
names the key: there is no silent zero.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from finance360.capabilities.projection import spending
from finance360.core import rulepack
from finance360.core.money import BASE_CURRENCY, Money, MoneyError, format_amount, parse_amount
from finance360.core.rate import Rate, RateError
from finance360.core.rulepack import Age

#: The Categories projected, in the fixed default drawdown order (ADR-0015 s8).
PROJECTED_CATEGORIES = ("cash", "investments", "superannuation", "overseas_pensions")
MAX_AGE = 120

HOUSEHOLD = "household"
CATEGORY = "category"
MEMBER = "member"
#: ``account`` is reserved (ADR-0016 s1) and rejected until the input carries Account lines.
SCOPE_TYPES = (HOUSEHOLD, CATEGORY, MEMBER)

LAYER_SCENARIO = "scenario"
LAYER_HOUSEHOLD = "household"

Value = Rate | int | Money | Age | str
Scope = tuple[str, str]  # (scope_type, scope_ref)


@dataclass(frozen=True)
class AssumptionKey:
    """What a key holds (``rate``, ``int`` or ``money``) and the scope types it may be stored at."""

    kind: str
    scopes: tuple[str, ...]
    label: str
    #: For a ``choice`` key: the words it may hold. Held as the word's place in this list (a whole
    #: number), so no new column is needed.
    choices: tuple[str, ...] = ()


#: Tax in the projection (ADR-0019 section 1): ``off`` (the default) uses each stream's
#: stated take-home pay; ``on`` estimates it with the tax engine.
TAX_IN_PROJECTION = "tax_in_projection"
TAX_OFF = "off"
TAX_ON = "on"
#: Who pays a Member's Division 293 (ADR-0019 section 10). ``own`` is an Outflow a year later;
#: ``super`` is a line debit on the Member's own superannuation line a year later (ADR-0028).
DIV293_PAYER = "div293_payer"
PAYER_OWN = "own"
PAYER_SUPER = "super"
#: A Member with no stated payer pays from super (ADR-0028 s3; ``own`` before ADR-0028).
DEFAULT_PAYER = PAYER_SUPER

#: The M3 keys are the ``ProjectionRequest`` fields. A new key is code, not a migration. Spending is
#: not a key: it is a list of spending phases (``spending``), layered as a whole (ADR-0017 s6).
KEYS: dict[str, AssumptionKey] = {
    "inflation": AssumptionKey("rate", (HOUSEHOLD,), "Inflation per year"),
    "return_rate": AssumptionKey("rate", (CATEGORY,), "Return per year"),
    "retirement_age": AssumptionKey("int", (MEMBER,), "Retirement age"),
    "annual_saving": AssumptionKey("money", (HOUSEHOLD,), "Annual saving"),
    "horizon_age": AssumptionKey("int", (HOUSEHOLD,), "Horizon age"),
    # Real: 0% means pay keeps pace with inflation. A Member's own value beats the Household's.
    "real_wage_growth": AssumptionKey("rate", (HOUSEHOLD, MEMBER), "Real wage growth per year"),
    # Optional, with a default when unstated, so an existing Scenario does not move.
    TAX_IN_PROJECTION: AssumptionKey(
        "choice", (HOUSEHOLD,), "Tax in the projection", (TAX_OFF, TAX_ON)
    ),
    DIV293_PAYER: AssumptionKey(
        "choice", (HOUSEHOLD, MEMBER), "Division 293 paid by", (PAYER_OWN, PAYER_SUPER)
    ),
}


#: The registered pack's jurisdiction; the household uses one pack (ADR-0018 section 1).
JURISDICTION = "au"
#: Kind of a Rule pack age; stored as a whole number of months.
AGE_KIND = "age"


def spec_of(key: str) -> AssumptionKey | None:
    """The spec for ``key``: an M3 key, or ``rule.<pack key>`` admitted from the registered pack's
    schema (ADR-0018 section 5), which a Scenario or the Household may state at household scope."""
    spec = KEYS.get(key)
    if spec is not None or not key.startswith(rulepack.OVERRIDE_PREFIX):
        return spec
    pack = rulepack.get(JURISDICTION)
    name = key.removeprefix(rulepack.OVERRIDE_PREFIX)
    if pack is None:
        return None
    for age in pack.ages:
        if age.key == name:
            return AssumptionKey(AGE_KIND, (HOUSEHOLD,), age.label)
    for param in pack.params:
        if param.key == name and not rulepack.is_exact_year_key(name):
            return AssumptionKey(param.kind, (HOUSEHOLD,), param.key)
    return None


class AssumptionValueError(ValueError):
    """Text that is not a valid value for its key."""


def parse_value(key: str, text: str) -> Value:
    """Parse the screen's text (percent string, whole number, decimal amount) for ``key``."""
    spec = spec_of(key)
    if spec is None:
        raise AssumptionValueError(f"Unknown Assumption '{key}'.")
    kind = spec.kind
    if kind == "choice":
        word = text.strip().lower()
        if word not in spec.choices:
            raise AssumptionValueError(f"Choose one of: {', '.join(spec.choices)}.")
        return word
    try:
        if kind == AGE_KIND:
            return Age.parse(text)
        if kind == "rate":
            return Rate.from_percent(text)
        if kind == "money":
            money = parse_amount(text, BASE_CURRENCY)
            if money.amount_minor < 0:
                raise AssumptionValueError("Enter 0 or more.")
            return money
    except (RateError, MoneyError, rulepack.RulePackError) as error:
        raise AssumptionValueError(str(error)) from None
    clean = text.strip()
    if not (clean.isascii() and clean.isdigit()):
        raise AssumptionValueError("Enter a whole number of years.")
    age = int(clean)
    if not 0 <= age <= MAX_AGE:
        raise AssumptionValueError(f"Enter an age from 0 to {MAX_AGE}.")
    return age


def format_value(value: Value) -> str:
    """The value as the text ``parse_value`` accepts (a percent, a whole number, an amount)."""
    if isinstance(value, Rate):
        return value.to_percent()
    if isinstance(value, Money):
        return format_amount(value)
    return str(value)  # an int, or an Age as "67" / "66y6m"


@dataclass(frozen=True)
class Row:
    """One stored Assumption, whichever layer it is in."""

    key: str
    scope_type: str
    scope_ref: str
    value: Value


@dataclass(frozen=True)
class Source:
    """A value used, and the layer that supplied it."""

    key: str
    scope_type: str
    scope_ref: str
    value: Value
    layer: str


@dataclass(frozen=True)
class Missing:
    key: str
    scope_type: str
    scope_ref: str


class UnresolvedError(Exception):
    """One or more keys have no value in any layer. ``missing`` names each."""

    def __init__(self, missing: Sequence[Missing]) -> None:
        super().__init__(", ".join(m.key for m in missing))
        self.missing = tuple(missing)


@dataclass(frozen=True)
class Resolved:
    inflation: Rate
    returns: dict[str, Rate]
    retirement_ages: dict[int, int]
    #: The spending phases used, as one list, and the layer that supplied them.
    spending_phases: tuple[spending.Phase, ...]
    spending_layer: str
    annual_saving: Money
    horizon_age: int
    provenance: tuple[Source, ...]
    #: Real wage growth for each Member that has a salary stream (``wage_member_ids``).
    real_wage_growth: dict[int, Rate] = field(default_factory=dict[int, Rate])
    #: Pack values a Scenario or the Household states (``rule.<key>``), keyed by the pack key.
    rule_overrides: dict[str, Source] = field(default_factory=dict[str, Source])
    #: True when tax is switched on (``tax_in_projection``); unstated means off.
    tax_in_projection: bool = False
    #: The Division 293 payer stated for each Member (``div293_payer``), with the layer that stated
    #: it; a Member with none is absent here and the projection treats them as ``DEFAULT_PAYER``.
    div293_payers: dict[int, Source] = field(default_factory=dict[int, Source])


def lookup(
    key: str, scopes: Sequence[Scope], defaults: Sequence[Row], overrides: Sequence[Row]
) -> Source | None:
    """The winning value for ``key`` at ``scopes`` (most specific first), or None if unset."""
    for layer, rows in ((LAYER_SCENARIO, overrides), (LAYER_HOUSEHOLD, defaults)):
        by_scope = {(r.scope_type, r.scope_ref): r for r in rows if r.key == key}
        for scope in scopes:
            row = by_scope.get(scope)
            if row is not None:
                return Source(key, row.scope_type, row.scope_ref, row.value, layer)
    return None


def resolve(
    defaults: Sequence[Row],
    overrides: Sequence[Row],
    *,
    member_ids: Collection[int],
    categories: Collection[str] = PROJECTED_CATEGORIES,
    rule_keys: Collection[str] = (),
    wage_member_ids: Collection[int] = (),
    default_phases: Sequence[spending.Phase] = (),
    override_phases: Sequence[spending.Phase] = (),
) -> Resolved:
    """Layer the Scenario's ``overrides`` over the Household ``defaults``; see the module docs.

    Spending phases layer as a whole list: the Scenario's own list if it has one, else the default.
    """
    household: Scope = (HOUSEHOLD, "")
    missing: list[Missing] = []
    used: list[Source] = []

    def get(key: str, line_scope: Scope = household) -> Value | None:
        scopes = [line_scope] if line_scope == household else [line_scope, household]
        source = lookup(key, scopes, defaults, overrides)
        if source is None:
            missing.append(Missing(key, *line_scope))
            return None
        if source not in used:  # members that share the Household's value list it once
            used.append(source)
        return source.value

    inflation = get("inflation")
    phases = spending.pick(default_phases, override_phases)
    if phases is None:
        missing.append(Missing(spending.FIELD, HOUSEHOLD, ""))
    saving = get("annual_saving")
    horizon = get("horizon_age")
    returns = {c: get("return_rate", (CATEGORY, c)) for c in categories}
    ages = {m: get("retirement_age", (MEMBER, str(m))) for m in sorted(member_ids)}
    # Only a Member with a salary stream needs a wage growth, so a household with none is unchanged.
    wages = {m: get("real_wage_growth", (MEMBER, str(m))) for m in sorted(wage_member_ids)}
    if missing:
        raise UnresolvedError(missing)
    # Each key's kind is fixed by KEYS, so these only narrow the types for the checker.
    if not (isinstance(inflation, Rate) and isinstance(saving, Money) and isinstance(horizon, int)):
        raise TypeError("An Assumption holds a value of the wrong kind for its key")
    rule_overrides: dict[str, Source] = {}
    for rule_key in rule_keys:
        stated = lookup(rulepack.OVERRIDE_PREFIX + rule_key, [household], defaults, overrides)
        if stated is not None:
            rule_overrides[rule_key] = stated
    if phases is None:  # unreachable: an unset list was reported above
        raise TypeError("No spending phases")
    # Optional keys: unstated is a default (off, super), never a missing value, so an existing
    # Scenario resolves exactly as before. A stated value is listed with the others.
    tax_stated = lookup(TAX_IN_PROJECTION, [household], defaults, overrides)
    payers: dict[int, Source] = {}
    for m in sorted(member_ids):
        stated = lookup(DIV293_PAYER, [(MEMBER, str(m)), household], defaults, overrides)
        if stated is not None and isinstance(stated.value, str):
            payers[m] = stated
            if stated not in used:
                used.append(stated)
    if tax_stated is not None and tax_stated not in used:
        used.append(tax_stated)
    return Resolved(
        inflation,
        {c: r for c, r in returns.items() if isinstance(r, Rate)},
        {m: a for m, a in ages.items() if isinstance(a, int)},
        phases.phases,
        phases.layer,
        saving,
        horizon,
        tuple(used),
        {m: r for m, r in wages.items() if isinstance(r, Rate)},
        rule_overrides,
        tax_stated is not None and tax_stated.value == TAX_ON,
        payers,
    )
