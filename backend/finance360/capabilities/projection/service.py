"""Build a Projection input from the Position and the request, then run the engine (ADR-0015 s8).

The only module in this Capability that touches the Position service. Nothing here writes: a
Scenario is resolved to a ``ProjectionRequest`` (``scenarios.resolve_for``) before the engine runs.

The simplifications below are the owner's, accepted for the first slice and stated on the screen:

1. Property, vehicles and liabilities are not projected.
2. Superannuation is accessible from the later of the owning Member's retirement and preservation
   age, and at the latest at 65 (ADR-0017 s4); overseas pensions at the Member's retirement.
3. Between the first and the last retirement there is neither saving nor spending, unless a
   spending phase starts at the projection start (ADR-0017 s6). A salary in force from the start
   requires such a phase (ADR-0017 s5).
4. Saving is split equally across Members into their Investments lines, except that a Member with
   a salary stream saves what the salary leaves after spending (``annual_saving`` is not applied
   to them, ADR-0017 s5). Take-home pay and employer super (SG) follow ADR-0017 s4 and s5, with no
   caps, maximum contribution base, salary sacrifice or fund fees. Take-home pay is the stated
   amount unless the ``tax_in_projection`` Assumption is ``on``: then the tax engine estimates it,
   and Division 293 is paid a year after the year it is for (ADR-0019 s1 and s10), by the
   Member's own super as a line debit by default or by the household (ADR-0028).
5. The headline age is the youngest Member's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.capabilities.position import service as position
from finance360.capabilities.projection import (
    assumptions,
    engine,
    income,
    income_streams,
    life_events,
    rules,
    scenarios,
    spending,
    sustainable,
    tax_income,
)
from finance360.capabilities.projection.input import (
    INPUT_VERSION,
    MAX_YEARS,
    Inflow,
    InputError,
    Line,
    MemberRef,
    Outflow,
    ProjectionInput,
    age_on,
)
from finance360.core import rulepack, rulepack_store
from finance360.core.errors import FieldError, ValidationError
from finance360.core.models.scenario import Scenario
from finance360.core.money import BASE_CURRENCY, Money, MoneyError, allocate, parse_amount
from finance360.core.rate import Rate, RateError

#: The Categories projected, in the fixed default drawdown order (ADR-0015 s8). A Scenario's own
#: Drawdown policy (ADR-0016 s3) resolves into the same ``drawdown_order`` field; null means this.
DRAWDOWN_ORDER = assumptions.PROJECTED_CATEGORIES
CATEGORY_NAMES = {
    "cash": "Cash",
    "investments": "Investments",
    "superannuation": "Superannuation",
    "overseas_pensions": "Overseas pensions",
}
#: Drawable from year 0. Superannuation opens by Life events (``life_events.super_access_year``),
#: the others at the owning Member's retirement year.
ALWAYS_ACCESSIBLE = frozenset({"cash", "investments"})
SUPERANNUATION = "superannuation"
SAVING_CATEGORY = "investments"
SG_RATE = "sg_rate"
CONTRIBUTIONS_TAX_RATE = "contributions_tax_rate"
MAX_AGE = assumptions.MAX_AGE


@dataclass(frozen=True)
class ProjectionRequest:
    """The Assumptions for one run, already parsed from text. ``as_at`` None means today."""

    inflation: Rate
    returns: dict[str, Rate]
    retirement_ages: dict[int, int]
    #: The spending plan as one list: at least one phase (an empty list is no spending).
    spending_phases: tuple[spending.Phase, ...]
    annual_saving: Money | None
    horizon_age: int
    as_at: date | None = None
    #: The Scenario's whole Drawdown policy, already validated; None is the default order.
    drawdown_order: tuple[str, ...] | None = None
    #: Pack values stated as ``rule.<key>`` Assumptions, keyed by the pack key (ADR-0018 s5).
    rule_overrides: Mapping[str, assumptions.Source] = field(
        default_factory=dict[str, assumptions.Source]
    )
    #: Real wage growth for each Member with a salary stream (ADR-0017 s5), from the resolver.
    real_wage_growth: Mapping[int, Rate] = field(default_factory=dict[int, Rate])
    #: Used for a salaried Member with no entry above (the ad hoc ``run`` endpoint): 0%.
    default_wage_growth: Rate = field(default_factory=Rate.zero)
    #: True: take-home pay is estimated by the tax engine instead of the stated amount.
    tax_in_projection: bool = False
    #: Each Member's stated Division 293 payer; a Member absent pays ``own`` (the default).
    div293_payers: Mapping[int, assumptions.Source] = field(
        default_factory=dict[int, assumptions.Source]
    )


@dataclass(frozen=True)
class FlaggedAccount:
    account_id: int
    name: str
    category_key: str
    status: str


@dataclass(frozen=True)
class MemberInfo:
    member_id: int
    name: str
    age_now: int
    retirement_year: int


@dataclass(frozen=True)
class Prepared:
    """A resolved Projection input with the context the screen needs beside the result."""

    as_at: date
    input: ProjectionInput
    categories: tuple[tuple[str, str], ...]  # (key, name) in drawdown order
    members: tuple[MemberInfo, ...]
    flagged: tuple[FlaggedAccount, ...]
    first_retirement_year: int
    last_retirement_year: int
    #: The Rule pack ages reported per Member. None for a run saved before Rule packs (B10).
    rules: rules.RulesUsed | None = None
    #: Each Member's Life events in date order, with the year index each became (ADR-0017 s3).
    #: None for a run saved before B6. They are not in ``input``; the access years they produced
    #: are (``Line.accessible_from_year``).
    life_events: tuple[life_events.LifeEvent, ...] | None = None
    #: The spending phases used. None for a run saved before spending phases (B8).
    spending: tuple[spending.Phase, ...] | None = None
    #: The income streams and Rule pack rates used. None for a run saved before B7.
    income: income.IncomeUsed | None = None
    #: The tax estimate behind take-home pay and Division 293. None when tax is off, and for a run
    #: saved before tax joined the projection.
    tax: tax_income.TaxUsed | None = None
    #: The line debits in ``input`` that tax produced, with their sources (ADR-0028 s5). None for a
    #: run saved by engine 1, which had none.
    debits: tuple[tax_income.DebitUsed, ...] | None = None

    @property
    def fixed_outflows(self) -> tuple[Outflow, ...]:
        """Outflows that are not spending (Division 293 paid by the household), held as they are
        by the sustainable-spending search."""
        return self.tax.outflows() if self.tax is not None else ()


@dataclass(frozen=True)
class ProjectionRun:
    prepared: Prepared
    result: engine.ProjectionResult
    #: The most the household can spend each year, from the last retirement, and still last.
    sustainable: sustainable.SustainableSpending


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError([FieldError(field, message)])


def parse_request(
    *,
    inflation: str,
    returns: Sequence[tuple[str, str]],
    retirement_ages: Sequence[tuple[int, int]],
    spending_phases: Sequence[tuple[str, int, str]],
    annual_saving: str | None,
    horizon_age: int,
    as_at: date | None = None,
    real_wage_growth: str = "0",
) -> ProjectionRequest:
    """Parse the screen's text (percent strings, decimal amounts) into a ``ProjectionRequest``.

    ``spending_phases`` is ``(anchor, offset_years, amount text)`` per phase.

    Every problem is collected, each against its own field, and raised together as one
    ``ValidationError`` (the API's 422 body).
    """
    errors: list[FieldError] = []

    def rate(text: str, field: str) -> Rate:
        try:
            return Rate.from_percent(text)
        except RateError as error:
            errors.append(FieldError(field, str(error)))
            return Rate.zero()

    def amount(text: str, field: str) -> Money:
        try:
            return parse_amount(text, BASE_CURRENCY)
        except MoneyError as error:
            errors.append(FieldError(field, str(error)))
            return Money.zero(BASE_CURRENCY)

    rates: dict[str, Rate] = {}
    for key, text in returns:
        if key not in DRAWDOWN_ORDER:
            errors.append(FieldError(f"returns.{key}", "This Category is not projected."))
        elif key in rates:
            errors.append(FieldError(f"returns.{key}", "Enter this Category once."))
        else:
            rates[key] = rate(text, f"returns.{key}")
    ages: dict[int, int] = {}
    for member_id, age in retirement_ages:
        if member_id in ages:
            errors.append(FieldError(f"retirement_ages.{member_id}", "Enter this Member once."))
        ages[member_id] = age
    phases, phase_errors = spending.parse(spending_phases)
    errors.extend(phase_errors)
    request = ProjectionRequest(
        inflation=rate(inflation, "inflation"),
        returns=rates,
        retirement_ages=ages,
        spending_phases=tuple(phases),
        annual_saving=amount(annual_saving, "annual_saving") if annual_saving else None,
        horizon_age=horizon_age,
        as_at=as_at,
        default_wage_growth=rate(real_wage_growth, "real_wage_growth"),
    )
    if errors:
        raise ValidationError(errors)
    return request


def _check_request(request: ProjectionRequest, member_ids: list[int]) -> None:
    errors: list[FieldError] = []
    for key in DRAWDOWN_ORDER:
        if key not in request.returns:
            errors.append(FieldError(f"returns.{key}", "Enter a return for this Category."))
    for member_id in member_ids:
        age = request.retirement_ages.get(member_id)
        if age is None:
            errors.append(FieldError(f"retirement_ages.{member_id}", "Enter a retirement age."))
        elif not 0 <= age <= MAX_AGE:
            errors.append(
                FieldError(f"retirement_ages.{member_id}", f"Enter an age from 0 to {MAX_AGE}.")
            )
    if any(p.annual_amount.amount_minor < 0 for p in request.spending_phases):
        errors.append(FieldError(spending.FIELD, "Spending must be 0 or more."))
    if request.annual_saving is not None and request.annual_saving.amount_minor < 0:
        errors.append(FieldError("annual_saving", "Saving must be 0 or more."))
    if errors:
        raise ValidationError(errors)


def _accessible_from(category_key: str, retirement_year: int, super_year: int) -> int:
    if category_key in ALWAYS_ACCESSIBLE:
        return 0
    return super_year if category_key == SUPERANNUATION else retirement_year


def _require_working_years_spending(
    names: Mapping[int, str],
    streams: Sequence[income.Stream],
    spans: Sequence[income.Span],
    request: ProjectionRequest,
    last_retirement_year: int,
) -> None:
    """A salary in force in year 0 needs a spending phase that starts in year 0 (ADR-0017 s5).

    Without one the whole take-home pay would be banked and every headline inflated, so it is a
    missing-value error naming working-years spending, never a silent zero.
    """
    paid_now = [st for st, span in zip(streams, spans, strict=True) if span.in_force(0)]
    if not paid_now:
        return
    starts_now = any(
        spending.start_year(p, last_retirement_year) == 0 and p.annual_amount.amount_minor > 0
        for p in request.spending_phases
    )
    if starts_now:
        return
    paid = sorted({names[st.member_id] for st in paid_now})
    earns = "earns a salary" if len(paid) == 1 else "earn salaries"
    raise _fail(
        spending.FIELD,
        f"Working-years spending has no value. {' and '.join(paid)} {earns} from the projection "
        "start, so spending needs a phase that starts at the projection start. Add one, or the "
        "salary would all be saved.",
    )


def _rate_years(
    pack: rulepack.RulePack, as_at: date, years: int, request: ProjectionRequest
) -> tuple[income.RatesFor, tuple[income.RateUsed, ...]]:
    """The SG rate and contributions tax rate for each projection year, and where they changed."""
    keys = (SG_RATE, CONTRIBUTIONS_TAX_RATE)
    overrides = {
        k: o.value
        for k, o in request.rule_overrides.items()
        if k in keys and isinstance(o.value, Rate)
    }
    first_fy = rulepack.fy_of(as_at)
    try:
        spans = rulepack.materialise(pack, first_fy, years, request.inflation, overrides)
    except rulepack.RulePackError as error:
        raise _fail("body", f"Superannuation can't be projected from this date: {error}") from None
    by_year: dict[str, list[Rate]] = {k: [] for k in keys}
    used: list[income.RateUsed] = []
    last: dict[str, Rate] = {}
    for i, span in enumerate(spans):
        end = spans[i + 1].first_t if i + 1 < len(spans) else years
        for key in keys:
            value = span.values[key]
            if not isinstance(value, Rate):
                raise TypeError(f"Rule pack value {key!r} is not a rate")
            by_year[key].extend([value] * (end - span.first_t))
            if last.get(key) != value:
                last[key] = value
                override = request.rule_overrides.get(key)
                stated = override is not None and key in overrides
                layer = override.layer if override is not None and stated else rules.LAYER_PACK
                source = (
                    f"Stated as the Assumption {rulepack.OVERRIDE_PREFIX}{key}"
                    if stated
                    else pack.param(key).source
                )
                used.append(
                    income.RateUsed(
                        key, span.first_t, first_fy + span.first_t, value, layer, source
                    )
                )
    return income.RatesFor(by_year[SG_RATE], by_year[CONTRIBUTIONS_TAX_RATE]), tuple(used)


def _income_flows(
    request: ProjectionRequest,
    pack: rulepack.RulePack,
    as_at: date,
    years: int,
    streams: Sequence[income.Stream],
    spans: Sequence[income.Span],
    members: Sequence[tuple[int, date]],
) -> tuple[list[Inflow], income.IncomeUsed, tax_income.TaxUsed | None]:
    """Take-home pay and employer super as one ``Inflow`` per year in force (ADR-0017 s4 and s5)."""
    flows: list[Inflow] = []
    used: list[income.StreamUsed] = []
    pays_super = any(income.TYPES[st.type_key].employer_super for st in streams)
    rates: income.RatesFor | None = None
    rates_used: tuple[income.RateUsed, ...] = ()
    if pays_super and any(span.from_year < years for span in spans):
        rates, rates_used = _rate_years(pack, as_at, years, request)
    growth_of = {
        st.member_id: request.real_wage_growth.get(st.member_id, request.default_wage_growth)
        for st in streams
    }
    estimated: tax_income.Estimated | None = None
    try:
        if request.tax_in_projection:
            estimated = tax_income.estimate(
                pack=pack,
                first_fy=rulepack.fy_of(as_at),
                years=years,
                inflation=request.inflation,
                members=members,
                streams=streams,
                spans=spans,
                growth=growth_of,
                rates=rates,
                payers={m: _payer_of(request, m) for m, _ in members},
            )
        for st, span in zip(streams, spans, strict=True):
            growth = growth_of[st.member_id]
            pay = estimated.take_home.get(st.id) if estimated is not None else None
            flows.extend(income.flows(st, span, growth, years, rates, pay))
            used.append(income.StreamUsed(st, span, growth))
    except (MoneyError, income.IncomeError, tax_income.TaxIncomeError) as error:
        if isinstance(error, income.IncomeError | tax_income.TaxIncomeError):
            raise _fail("body", str(error)) from None
        raise _fail("body", "These figures are too large to project.") from None
    return (
        flows,
        income.IncomeUsed(tuple(used), rates_used),
        estimated.used if estimated is not None else None,
    )


def _payer_of(request: ProjectionRequest, member_id: int) -> tax_income.PayerUsed:
    """The Member's stated Division 293 payer, or ``super``: the default (ADR-0028 s3)."""
    stated = request.div293_payers.get(member_id)
    if stated is not None and isinstance(stated.value, str):
        return tax_income.PayerUsed(member_id, stated.value, stated.layer)
    return tax_income.PayerUsed(member_id, assumptions.DEFAULT_PAYER, "default")


def build_input(session: Session, request: ProjectionRequest) -> Prepared:
    """Resolve the request and the Position into a Projection input (and the run's context)."""
    as_at = request.as_at or household.household_today(session)
    members = household.list_members(session)
    if not members:
        raise _fail("body", "Add a Member to the Household first.")
    _check_request(request, [m.id for m in members])
    for m in members:
        if m.date_of_birth > as_at:
            raise _fail("as_at", f"{m.name} is born after this date.")

    ages = {m.id: age_on(m.date_of_birth, as_at) for m in members}
    youngest = max(members, key=lambda m: (m.date_of_birth.toordinal(), -m.id))
    years = request.horizon_age - ages[youngest.id]
    if not 1 <= years <= MAX_YEARS:
        raise _fail(
            "horizon_age",
            f"Enter an age above {ages[youngest.id]} (the youngest Member, {youngest.name}) "
            f"and at most {ages[youngest.id] + MAX_YEARS}.",
        )
    pack = rulepack_store.effective_pack(session, assumptions.JURISDICTION)
    if pack is None:
        raise RuntimeError(f"No Rule pack is registered for {assumptions.JURISDICTION!r}")
    member_dobs = [(m.id, m.date_of_birth) for m in members]
    rules_used = rules.member_ages(pack, member_dobs, request.rule_overrides)
    events = life_events.resolve(member_dobs, request.retirement_ages, rules_used, as_at)
    retirement_year = {e.member_id: e.year for e in events if e.kind == life_events.RETIREMENT}
    super_year = life_events.member_super_access_years(events)
    first_retired = min(retirement_year.values())
    all_retired = max(retirement_year.values())

    pos = position.position_on(session, as_at)
    totals: dict[tuple[int, str], Money] = {}
    names = dict(CATEGORY_NAMES)
    flagged: list[FlaggedAccount] = []
    for class_node in pos.classes:
        for category in class_node.categories:
            if category.key not in DRAWDOWN_ORDER:
                continue
            names[category.key] = category.name
            for figure in category.figures.members:
                totals[(figure.member_id, category.key)] = figure.total
            flagged.extend(
                FlaggedAccount(a.account_id, a.name, category.key, a.status)
                for a in category.accounts
                if a.value is None
            )

    order = request.drawdown_order or DRAWDOWN_ORDER
    zero = Money.zero(BASE_CURRENCY)
    lines = tuple(
        Line(
            member.id,
            key,
            totals.get((member.id, key), zero),
            request.returns[key],
            _accessible_from(key, retirement_year[member.id], super_year[member.id]),
        )
        for member in members
        for key in DRAWDOWN_ORDER
    )

    streams = income_streams.projection_streams(session)
    owner_years = life_events.member_years(events)
    spans = [income.span_of(st, owner_years[st.member_id], as_at) for st in streams]
    _require_working_years_spending(
        {m.id: m.name for m in members}, streams, spans, request, all_retired
    )
    salaried = {st.member_id for st in streams if st.type_key == income.SALARY}

    inflows: list[Inflow] = []
    saving = request.annual_saving
    if saving is not None and saving.amount_minor > 0 and first_retired > 0:
        shares = allocate(saving, {m.id: 1 for m in members})
        inflows = [
            Inflow(member_id, SAVING_CATEGORY, share, 0, first_retired)
            for member_id, share in shares.items()
            if share.amount_minor > 0 and member_id not in salaried
        ]
    outflows = list(spending.outflows(request.spending_phases, all_retired))
    stream_inflows, income_used, tax_used = _income_flows(
        request, pack, as_at, years, streams, spans, member_dobs
    )
    inflows.extend(stream_inflows)
    debits_used: tuple[tax_income.DebitUsed, ...] = ()
    if tax_used is not None:
        outflows.extend(tax_used.outflows())
        debits_used = tax_used.debits()

    try:
        projection_input = ProjectionInput(
            input_version=INPUT_VERSION,
            as_at=as_at,
            currency=BASE_CURRENCY,
            members=tuple(MemberRef(m.id, m.date_of_birth) for m in members),
            years=years,
            inflation=request.inflation,
            lines=lines,
            inflows=tuple(inflows),
            outflows=tuple(outflows),
            drawdown_order=order,
            debits=tuple(d.debit for d in debits_used),
        )
    except InputError as error:
        raise _fail("body", str(error)) from None

    return Prepared(
        as_at,
        projection_input,
        tuple((key, names[key]) for key in order),
        tuple(MemberInfo(m.id, m.name, ages[m.id], retirement_year[m.id]) for m in members),
        tuple(flagged),
        first_retired,
        all_retired,
        rules_used,
        events,
        request.spending_phases,
        income_used,
        tax_used,
        debits_used,
    )


def run(session: Session, request: ProjectionRequest) -> ProjectionRun:
    """Project one Scenario's Assumptions from the Position. Read-only; nothing is stored."""
    prepared = build_input(session, request)
    try:
        result = engine.project(prepared.input)
        # Independent of the amounts entered: the phases from the last retirement are scaled.
        most = sustainable.sustainable_spending(
            prepared.input,
            request.spending_phases,
            prepared.last_retirement_year,
            prepared.fixed_outflows,
        )
    except MoneyError:
        raise _fail("body", "These figures are too large to project.") from None
    return ProjectionRun(prepared, result, most)


@dataclass(frozen=True)
class ScenarioRun:
    scenario: Scenario
    run: ProjectionRun
    #: Each value used, and whether the Scenario or the Household default supplied it.
    provenance: tuple[assumptions.Source, ...]
    #: Which layer supplied the spending phases: ``scenario`` or ``household``.
    spending_layer: str


def run_scenario(session: Session, scenario_id: int, as_at: date | None = None) -> ScenarioRun:
    """Resolve a Scenario over the Household defaults, then project it. Read-only."""
    scenario, resolved = scenarios.resolve_for(session, scenario_id)
    request = ProjectionRequest(
        inflation=resolved.inflation,
        returns=resolved.returns,
        retirement_ages=resolved.retirement_ages,
        spending_phases=resolved.spending_phases,
        annual_saving=resolved.annual_saving,
        horizon_age=resolved.horizon_age,
        as_at=as_at,
        drawdown_order=scenarios.drawdown_order_of(scenario),
        rule_overrides=resolved.rule_overrides,
        real_wage_growth=resolved.real_wage_growth,
        tax_in_projection=resolved.tax_in_projection,
        div293_payers=resolved.div293_payers,
    )
    return ScenarioRun(
        scenario, run(session, request), resolved.provenance, resolved.spending_layer
    )


@dataclass(frozen=True)
class ScenarioOutcome:
    """One Scenario's place in a comparison: its run, or why it could not be projected."""

    scenario: Scenario
    run: ScenarioRun | None
    errors: tuple[FieldError, ...]


@dataclass(frozen=True)
class Comparison:
    outcomes: tuple[ScenarioOutcome, ...]
    #: The (key, scope_ref) of every Assumption whose resolved value is not the same in every
    #: Scenario that resolved, in the order the first such Scenario lists them.
    differing: tuple[tuple[str, str], ...]
    #: True when the Scenarios that resolved do not all use the same spending phases.
    spending_differs: bool


def compare_scenarios(session: Session, as_at: date | None = None) -> Comparison:
    """Resolve and project every Scenario with the same single-Scenario path. Read-only.

    A Scenario that cannot be resolved (or projected) carries its errors in its own outcome and
    does not stop the others.
    """
    outcomes: list[ScenarioOutcome] = []
    for scenario in scenarios.list_scenarios(session):
        try:
            outcomes.append(
                ScenarioOutcome(scenario, run_scenario(session, scenario.id, as_at), ())
            )
        except ValidationError as error:
            outcomes.append(ScenarioOutcome(scenario, None, tuple(error.errors)))
    resolved = [o.run.provenance for o in outcomes if o.run is not None]
    differing: list[tuple[str, str]] = []
    if len(resolved) > 1:
        values = [{(p.key, p.scope_ref): p.value for p in prov} for prov in resolved]
        for p in resolved[0]:
            slot = (p.key, p.scope_ref)
            if any(v.get(slot) != values[0].get(slot) for v in values[1:]):
                differing.append(slot)
    plans = {spending.ordered(o.run.run.prepared.spending or ()) for o in outcomes if o.run}
    return Comparison(tuple(outcomes), tuple(differing), len(plans) > 1)
