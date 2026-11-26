"""The Projection endpoint. Thin: the maths is the engine, the input is built by the service.

Request values are strings (percent strings, decimal amounts), never JSON numbers; ages are whole
numbers. Responses carry amounts as plain AUD decimal strings. Nothing is stored (B1): ``run`` only
reads the Position and calculates.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, Field, StrictInt

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.api.scenarios import SpendingPhaseIn, SpendingPhaseOut
from finance360.capabilities.household import facts as household_facts
from finance360.capabilities.projection import (
    age_pension_used,
    assumptions,
    engine,
    income,
    life_events,
    rules,
    runs,
    service,
    spending,
    tax_income,
)
from finance360.capabilities.projection.input import anniversary
from finance360.core import rulepack
from finance360.core.money import Money, format_amount
from finance360.core.rate import Rate

router = APIRouter(prefix="/api/projection", tags=["projection"])


class ProjectionReturnIn(BaseModel):
    category: str = Field(description="A projected Category key, such as 'investments'.")
    rate: str = Field(description="Nominal annual return as a percent string, e.g. '6.5'.")


class ProjectionRetirementAgeIn(BaseModel):
    member_id: int
    age: StrictInt


class ProjectionIn(BaseModel):
    as_at: date | None = Field(
        default=None, description="Position date. Omit for today in the Household time zone."
    )
    inflation: str = Field(description="Annual inflation as a percent string, e.g. '2.5'.")
    returns: list[ProjectionReturnIn]
    retirement_ages: list[ProjectionRetirementAgeIn]
    spending_phases: list[SpendingPhaseIn] = Field(
        description="The spending plan. Each phase runs until the next starts; empty is no "
        "spending."
    )
    annual_saving: str | None = Field(
        default=None, description="Today's dollars, per year, until the first retirement."
    )
    horizon_age: StrictInt = Field(description="The youngest Member's age at the horizon.")
    real_wage_growth: str = Field(
        default="0",
        description="Real wage growth per year as a percent string, for every Member with a "
        "salary stream. 0 means pay keeps pace with inflation.",
    )


class ProjectionCategoryOut(BaseModel):
    key: str
    name: str


class ProjectionMemberOut(BaseModel):
    id: int
    name: str
    age_now: int
    retirement_year: int = Field(description="Years from the as-at date; 0 if already retired.")


class ProjectionFlaggedAccountOut(BaseModel):
    account_id: int
    name: str
    category_key: str
    status: str = Field(description="no_valuation or no_rate: left out, as in the Position.")


class ProjectionCategoryYearOut(BaseModel):
    category_key: str
    opening: str
    growth: str
    credited: str
    debited: str = Field(description="Line debits paid from the Category's lines (ADR-0028).")
    withdrawn: str
    closing: str


class ProjectionDebitOut(BaseModel):
    member_id: int
    category_key: str
    reason: str = Field(description="'div293', 'insurance_premium' or 'fund_fee'.")
    ref: str = Field(description="What it is for, such as 'FY2025-26'.")
    requested: str
    paid: str = Field(description="Paid from the line.")
    unpaid: str = Field(
        description="What the line could not pay: paid personally, so it is in the year's need."
    )


class AgePensionPaidOut(BaseModel):
    member_id: int
    amount: str = Field(description="Credited to this Member's own cash line, before Drawdown.")


class AgePensionCategoryOut(BaseModel):
    category_key: str
    amount: str = Field(description="The Category's opening balance that was counted.")


class AgePensionYearOut(BaseModel):
    """The working of one year's Age Pension (ADR-0029 section 4): what the means test read, what
    each test gave, which one bound. Assessed on the OPENING balances of the year."""

    paid: list[AgePensionPaidOut]
    total: str = Field(description="The assessed household's whole pension, before it is split.")
    couple: bool
    homeowner: bool
    categories: list[AgePensionCategoryOut] = Field(
        description="Each projected Category counted, at its opening balance: Cash and "
        "Investments from year 0, Super and Overseas pensions from their owner's Age Pension age."
    )
    property_assets: str = Field(
        description="Non-home Property: a fixed input, not projected (constant in real terms)."
    )
    vehicle_assets: str = Field(
        description="Vehicles: a fixed input, not projected (flat in nominal terms)."
    )
    assets: str = Field(description="Assessable assets: the Categories plus Property and Vehicles.")
    financial_assets: str = Field(description="The deemed part of the assets.")
    other_income: str = Field(description="Gross pay of the stored Income streams this year.")
    deemed_income: str = Field(description="Deemed income on the financial assets, a year.")
    maximum: str
    income_test: str = Field(description="The pension the income test alone would pay.")
    assets_test: str = Field(description="The pension the assets test alone would pay.")
    bound: str = Field(
        description="'income' or 'assets': the test that paid less. 'none': neither reduced the "
        "maximum."
    )


class ProjectionYearOut(BaseModel):
    t: int
    starts_on: date
    age: int = Field(description="The youngest Member's age at the start of the year.")
    categories: list[ProjectionCategoryYearOut]
    opening: str
    closing: str
    need: str = Field(description="Spending plus the unpaid parts of the year's line debits.")
    shortfall: str
    debits: list[ProjectionDebitOut] = Field(
        description="The year's line debits, empty for a run saved by engine 1."
    )
    paid_personally: str = Field(description="The sum of the unpaid parts of the year's debits.")
    age_pension: AgePensionYearOut | None = Field(
        description="The Age Pension of the year and its working. Null when no Member was "
        "eligible, none is calculated for the run, or the run was saved by engine 2 or earlier."
    )


class ProjectionSustainableOut(BaseModel):
    annual_spending: str = Field(
        description="The most that can be spent each year, in today's dollars, from the last "
        "retirement and still be met in full to the horizon. Ignores the spending entered."
    )
    capped: bool = Field(
        description="True when no limit was found below the search ceiling: it is 'at least'."
    )


class RuleParameterOut(BaseModel):
    key: str
    label: str
    source: str = Field(description="The public law or guidance the value comes from.")


class RuleValueOut(BaseModel):
    member_id: int
    key: str = Field(
        description="'preservation_age', 'unrestricted_super_age' or 'age_pension_age'."
    )
    years: int
    months: int = Field(description="0 to 11: Age Pension age has half-year steps.")
    layer: str = Field(description="'rule_pack', or 'scenario' / 'household' for an override.")


class RulesOut(BaseModel):
    pack_version: str = Field(description="For example 'au/2026-27/r1'.")
    parameters: list[RuleParameterOut]
    values: list[RuleValueOut]


class LifeEventOut(BaseModel):
    member_id: int
    kind: str = Field(
        description="'retirement', 'preservation_age', 'unrestricted_super_age' or "
        "'age_pension_age'."
    )
    on: date = Field(description="The date the Member reaches the age.")
    age_years: int
    age_months: int = Field(description="0 to 11: Age Pension age has half-year steps.")
    source: str = Field(
        description="'scenario' for retirement, or 'rule_pack' (or 'scenario' / 'household' for an "
        "override) for a Rule pack age."
    )
    year: int = Field(
        description="The projection year it takes effect: the first starting on or after."
    )


class SuperAccessOut(BaseModel):
    member_id: int
    year: int = Field(
        description="The projection year the Member's superannuation can first be drawn: the later "
        "of retirement and preservation age, at the latest at 65. It may be past the horizon."
    )
    on: date = Field(description="The start of that year.")


class SpendingPhaseUsedOut(SpendingPhaseOut):
    from_year: int = Field(description="The projection year the phase starts, resolved.")
    until_year: int | None = Field(
        description="The year the next phase starts; null: it runs to the horizon."
    )
    empty: bool = Field(
        description="True when another phase starts the same year and so wins: this one spends "
        "nothing."
    )


class SpendingUsedOut(BaseModel):
    layer: str | None = Field(
        description="'scenario' if the Scenario states its own phases, else 'household'. Null for "
        "a run that was not made from a Scenario."
    )
    phases: list[SpendingPhaseUsedOut] = Field(description="In the order they start.")


class IncomeStreamUsedOut(BaseModel):
    id: int
    name: str
    type_key: str
    member_id: int
    gross_annual: str = Field(description="As stored: today's dollars a year, before tax.")
    take_home_annual: str = Field(description="As stored: credited to the projection each year.")
    credit_category: str
    start_on: date | None
    start_event: str | None
    end_on: date | None
    end_event: str | None
    from_year: int = Field(description="The first projection year it is in force.")
    until_year: int | None = Field(
        description="The year it stops (the first year it is not in force); null: the horizon."
    )
    real_wage_growth: str = Field(description="The real wage growth applied, as a percent.")


class IncomeRateUsedOut(BaseModel):
    key: str = Field(description="'sg_rate' or 'contributions_tax_rate'.")
    from_year: int = Field(description="The projection year it applies from, until it changes.")
    financial_year: str = Field(description="The financial year of that year, such as 2026-27.")
    value: str = Field(description="A percent.")
    layer: str = Field(description="'rule_pack', or 'scenario' / 'household' for an override.")
    source: str = Field(
        description="The public source the pack cites for it, or the Assumption that overrides it."
    )


class IncomeUsedOut(BaseModel):
    streams: list[IncomeStreamUsedOut]
    rates: list[IncomeRateUsedOut] = Field(
        description="The Rule pack rates applied to employer super, where each takes effect."
    )


class TaxMemberYearOut(BaseModel):
    member_id: int
    gross: str = Field(description="Gross pay for the year, in today's dollars.")
    tax: str = Field(description="Income tax, Medicare levy and any surcharge the engine found.")
    take_home: str = Field(description="Gross pay less that tax: what the streams credit.")


class TaxYearOut(BaseModel):
    t: int
    financial_year: str = Field(description="Such as 2026-27.")
    source_financial_year: str = Field(
        description="The oldest financial year a Rule pack figure was taken from."
    )
    carried: bool = Field(description="True when figures were carried forward into this year.")
    frozen_eroded: int = Field(
        description="How many frozen thresholds fell in real terms by inflation."
    )
    members: list[TaxMemberYearOut]


class Div293PaymentOut(BaseModel):
    member_id: int
    for_financial_year: str
    amount: str
    payer: str = Field(description="'own' or 'super'.")
    paid_year: int | None = Field(
        description="The projection year it is paid in. Null: after the horizon, or not modelled."
    )
    modelled: bool = Field(
        description="True for every run made by engine 2 or later. False only in a run saved by "
        "engine 1 with the 'super' payer, which did not model it: nothing was taken."
    )
    estimated_from_year_0: bool = Field(
        description="True for the year just before the start, estimated from year-0 figures."
    )
    taxable_income: str
    low_tax_contributions: str
    div293_income: str
    threshold: str
    rate: str = Field(description="A percent.")


class TaxPayerOut(BaseModel):
    member_id: int
    payer: str
    layer: str = Field(description="'scenario', 'household' or 'default'.")


class TaxUsedOut(BaseModel):
    tax_engine_version: int
    tax_input_version: int
    rule_pack_version: str
    years: list[TaxYearOut]
    payments: list[Div293PaymentOut]
    payers: list[TaxPayerOut]
    div293_unavailable: bool = Field(
        description="True when the Rule pack lacks the Division 293 figures, so none was estimated."
    )


class AgePensionFactOut(BaseModel):
    key: str = Field(description="'couple_status', 'homeowner' or 'principal_home'.")
    value: str
    source: str = Field(description="'entered' or 'default'.")
    label: str = Field(description="'entered' or 'default, not entered'.")


class AgePensionEligibilityOut(BaseModel):
    member_id: int
    on: date = Field(description="The date the Member reaches Age Pension age.")
    year: int = Field(description="The projection year it became: paid from this year.")


class AgePensionRuleOut(BaseModel):
    key: str
    from_year: int = Field(description="The projection year this value applies from.")
    financial_year: str = Field(description="The financial year whose published figure it is.")
    value: str = Field(
        description="A decimal amount (as published: money is per fortnight where the key says "
        "so) or a percent."
    )
    origin: str = Field(description="'shipped', or 'captured' (a figure the household captured).")
    capture_id: int | None
    provisional: bool = Field(description="True when announced but not yet law.")
    provisional_note: str | None


class AgePensionAssetOut(BaseModel):
    account_id: int
    name: str
    category_key: str
    value: str
    valued_on: date = Field(description="The Valuation date the value is from.")
    role: str = Field(description="'assessed', or 'exempt_home' (valued but not counted).")


class AgePensionLeftOutOut(BaseModel):
    account_id: int
    name: str
    category_key: str
    status: str = Field(description="no_valuation or no_rate: not counted.")


class AgePensionSimplificationOut(BaseModel):
    key: str
    text: str


class AgePensionUsedOut(BaseModel):
    status: str = Field(
        description="'calculated', or why not: 'needs_home_choice', 'not_modelled' or "
        "'no_figures'. In each of those the run has no Age Pension and says why (reasons)."
    )
    reasons: list[str]
    facts: list[AgePensionFactOut]
    eligibility: list[AgePensionEligibilityOut]
    rules: list[AgePensionRuleOut]
    assets: list[AgePensionAssetOut] = Field(
        description="Each Property and Vehicle Account with its latest Valuation as at the start."
    )
    left_out: list[AgePensionLeftOutOut]
    simplifications: list[AgePensionSimplificationOut] = Field(
        description="The simplifications that apply to this run."
    )


class ProjectionOut(BaseModel):
    engine_version: int
    input_version: int
    as_at: date
    years: int
    reference_member: ProjectionMemberOut
    funds_last_to_age: int | None = Field(
        description="The youngest Member's age when spending is first not met in full, if ever."
    )
    lasts_beyond_horizon: bool
    sustainable: ProjectionSustainableOut
    categories: list[ProjectionCategoryOut] = Field(
        description="In the drawdown order used: the first funds spending first."
    )
    members: list[ProjectionMemberOut]
    flagged: list[ProjectionFlaggedAccountOut]
    rows: list[ProjectionYearOut]
    rules: RulesOut | None = Field(
        description="Each Member's ages from the Rule pack, shown with their source. They change "
        "no figure on their own: they set when superannuation opens (see life_events). Null for a "
        "run saved before Rule packs were recorded."
    )
    life_events: list[LifeEventOut] | None = Field(
        description="Each Member's Life events in date order. Null for a run saved before Life "
        "events were recorded."
    )
    super_access: list[SuperAccessOut] = Field(
        description="When each Member's superannuation opens, as the projection used it."
    )
    spending: SpendingUsedOut | None = Field(
        description="The spending phases the projection used. Null for a run saved before "
        "spending phases were recorded."
    )
    income: IncomeUsedOut | None = Field(
        description="The income streams and Rule pack rates the projection used, as stored. Null "
        "for a run saved before income streams were recorded."
    )
    tax: TaxUsedOut | None = Field(
        description="The tax estimate behind take-home pay and Division 293, with the tax engine "
        "and Rule pack versions. Null when tax is off, and for a run saved before it was built."
    )
    age_pension: AgePensionUsedOut | None = Field(
        description="What the Age Pension calculation used (Household facts, eligibility, each "
        "rule value, each Property and Vehicle), or why none was calculated. Null for a run saved "
        "by engine 2 or earlier."
    )


class ProvenanceOut(BaseModel):
    key: str
    scope_type: str
    scope_ref: str
    value: str = Field(description="A percent, a whole number of years, or a decimal amount.")
    layer: str = Field(description="'scenario' if the Scenario states it, else 'household'.")


class ScenarioRefOut(BaseModel):
    id: int
    key: str | None
    name: str


class ScenarioProjectionOut(ProjectionOut):
    scenario: ScenarioRefOut
    provenance: list[ProvenanceOut] = Field(
        description="Each Assumption value used, and which layer supplied it."
    )


def _member(m: service.MemberInfo) -> ProjectionMemberOut:
    return ProjectionMemberOut(
        id=m.member_id, name=m.name, age_now=m.age_now, retirement_year=m.retirement_year
    )


def _rules_out(used: rules.RulesUsed | None) -> RulesOut | None:
    if used is None:
        return None
    return RulesOut(
        pack_version=used.pack_version,
        parameters=[
            RuleParameterOut(key=p.key, label=p.label, source=p.source) for p in used.parameters
        ],
        values=[
            RuleValueOut(
                member_id=v.member_id,
                key=v.key,
                years=v.value.years,
                months=v.value.months,
                layer=v.layer,
            )
            for v in used.values
        ],
    )


def _life_events_out(events: tuple[life_events.LifeEvent, ...] | None) -> list[LifeEventOut] | None:
    if events is None:
        return None
    return [
        LifeEventOut(
            member_id=e.member_id,
            kind=e.kind,
            on=e.on,
            age_years=e.age.years,
            age_months=e.age.months,
            source=e.source,
            year=e.year,
        )
        for e in events
    ]


def _income_out(used: income.IncomeUsed | None) -> IncomeUsedOut | None:
    if used is None:
        return None
    return IncomeUsedOut(
        streams=[
            IncomeStreamUsedOut(
                id=u.stream.id,
                name=u.stream.name,
                type_key=u.stream.type_key,
                member_id=u.stream.member_id,
                gross_annual=format_amount(u.stream.gross_annual),
                take_home_annual=format_amount(u.stream.take_home_annual),
                credit_category=u.stream.credit_category,
                start_on=u.stream.start_on,
                start_event=u.stream.start_event,
                end_on=u.stream.end_on,
                end_event=u.stream.end_event,
                from_year=u.span.from_year,
                until_year=u.span.until_year,
                real_wage_growth=u.growth.to_percent(),
            )
            for u in used.streams
        ],
        rates=[
            IncomeRateUsedOut(
                key=r.key,
                from_year=r.from_year,
                financial_year=rulepack.fy_label(r.fy),
                value=r.value.to_percent(),
                layer=r.layer,
                source=r.source,
            )
            for r in used.rates
        ],
    )


def _tax_out(used: tax_income.TaxUsed | None) -> TaxUsedOut | None:
    if used is None:
        return None
    return TaxUsedOut(
        tax_engine_version=used.tax_engine_version,
        tax_input_version=used.tax_input_version,
        rule_pack_version=used.rule_pack_version,
        years=[
            TaxYearOut(
                t=y.t,
                financial_year=rulepack.fy_label(y.fy),
                source_financial_year=rulepack.fy_label(y.source_fy),
                carried=y.carried,
                frozen_eroded=y.frozen_eroded,
                members=[
                    TaxMemberYearOut(
                        member_id=m.member_id,
                        gross=format_amount(m.gross),
                        tax=format_amount(m.tax),
                        take_home=format_amount(m.take_home),
                    )
                    for m in y.members
                ],
            )
            for y in used.years
        ],
        payments=[
            Div293PaymentOut(
                member_id=p.member_id,
                for_financial_year=rulepack.fy_label(p.for_fy),
                amount=format_amount(p.amount),
                payer=p.payer,
                paid_year=p.paid_year,
                modelled=p.modelled,
                estimated_from_year_0=p.estimated_from_year_0,
                taxable_income=format_amount(p.taxable_income),
                low_tax_contributions=format_amount(p.low_tax_contributions),
                div293_income=format_amount(p.div293_income),
                threshold=format_amount(p.threshold),
                rate=p.rate.to_percent(),
            )
            for p in used.payments
        ],
        payers=[
            TaxPayerOut(member_id=p.member_id, payer=p.payer, layer=p.layer) for p in used.payers
        ],
        div293_unavailable=used.div293_unavailable,
    )


def _age_pension_year_out(year: engine.AgePensionYear | None) -> AgePensionYearOut | None:
    if year is None:
        return None
    return AgePensionYearOut(
        paid=[AgePensionPaidOut(member_id=m, amount=format_amount(a)) for m, a in year.paid],
        total=format_amount(year.total),
        couple=year.couple,
        homeowner=year.homeowner,
        categories=[
            AgePensionCategoryOut(category_key=k, amount=format_amount(a))
            for k, a in year.categories
        ],
        property_assets=format_amount(year.property_assets),
        vehicle_assets=format_amount(year.vehicle_assets),
        assets=format_amount(year.assets),
        financial_assets=format_amount(year.financial_assets),
        other_income=format_amount(year.other_income),
        deemed_income=format_amount(year.deemed_income),
        maximum=format_amount(year.maximum),
        income_test=format_amount(year.income_test),
        assets_test=format_amount(year.assets_test),
        bound=year.bound,
    )


def _age_pension_out(used: age_pension_used.AgePensionUsed | None) -> AgePensionUsedOut | None:
    if used is None:
        return None
    return AgePensionUsedOut(
        status=used.status,
        reasons=list(used.reasons),
        facts=[
            AgePensionFactOut(
                key=f.key, value=f.value, source=f.source, label=household_facts.LABELS[f.source]
            )
            for f in used.facts
        ],
        eligibility=[
            AgePensionEligibilityOut(member_id=e.member_id, on=e.on, year=e.year)
            for e in used.eligibility
        ],
        rules=[
            AgePensionRuleOut(
                key=r.key,
                from_year=r.from_year,
                financial_year=rulepack.fy_label(r.fy),
                value=r.value.to_percent() if isinstance(r.value, Rate) else format_amount(r.value),
                origin=r.origin,
                capture_id=r.capture_id,
                provisional=r.provisional_note is not None,
                provisional_note=r.provisional_note,
            )
            for r in used.rules
        ],
        assets=[
            AgePensionAssetOut(
                account_id=a.account_id,
                name=a.name,
                category_key=a.category_key,
                value=format_amount(a.value),
                valued_on=a.valued_on,
                role=a.role,
            )
            for a in used.assets
        ],
        left_out=[
            AgePensionLeftOutOut(
                account_id=x.account_id, name=x.name, category_key=x.category_key, status=x.status
            )
            for x in used.left_out
        ],
        simplifications=[
            AgePensionSimplificationOut(key=k, text=age_pension_used.SIMPLIFICATIONS[k])
            for k in used.simplifications
        ],
    )


def _spending_out(prepared: service.Prepared, layer: str | None) -> SpendingUsedOut | None:
    if prepared.spending is None:
        return None
    return SpendingUsedOut(
        layer=layer,
        phases=[
            SpendingPhaseUsedOut(
                anchor=s.phase.anchor,
                offset_years=s.phase.offset_years,
                annual_amount=format_amount(s.phase.annual_amount),
                from_year=s.from_year,
                until_year=s.until_year,
                empty=s.empty,
            )
            for s in spending.schedule(prepared.spending, prepared.last_retirement_year)
        ],
    )


def _out(run: service.ProjectionRun, spending_layer: str | None = None) -> ProjectionOut:
    prepared, result = run.prepared, run.result
    order = prepared.input.drawdown_order
    members = [_member(m) for m in prepared.members]
    reference = next(m for m in members if m.id == result.reference_member_id)
    ages = {m.id: m.age_now for m in members}
    rows: list[ProjectionYearOut] = []
    for row in result.years:
        totals = engine.category_totals(row, order)
        rows.append(
            ProjectionYearOut(
                t=row.t,
                starts_on=row.starts_on,
                age=ages[result.reference_member_id] + row.t,
                categories=[
                    ProjectionCategoryYearOut(
                        category_key=c.category_key,
                        opening=format_amount(c.opening),
                        growth=format_amount(c.growth),
                        credited=format_amount(c.credited),
                        debited=format_amount(c.debited),
                        withdrawn=format_amount(c.withdrawn),
                        closing=format_amount(c.closing),
                    )
                    for c in totals
                ],
                opening=format_amount(sum((c.opening for c in totals[1:]), totals[0].opening)),
                closing=format_amount(sum((c.closing for c in totals[1:]), totals[0].closing)),
                need=format_amount(row.need),
                shortfall=format_amount(row.shortfall),
                debits=[
                    ProjectionDebitOut(
                        member_id=d.member_id,
                        category_key=d.category_key,
                        reason=d.reason,
                        ref=d.ref,
                        requested=format_amount(d.requested),
                        paid=format_amount(d.paid),
                        unpaid=format_amount(d.unpaid),
                    )
                    for d in row.debits
                ],
                paid_personally=format_amount(
                    sum((d.unpaid for d in row.debits), Money.zero(row.need.currency))
                ),
                age_pension=_age_pension_year_out(row.age_pension),
            )
        )
    return ProjectionOut(
        engine_version=result.engine_version,
        input_version=result.input_version,
        as_at=prepared.as_at,
        years=prepared.input.years,
        reference_member=reference,
        funds_last_to_age=result.funds_last_to_age,
        lasts_beyond_horizon=result.lasts_beyond_horizon,
        sustainable=ProjectionSustainableOut(
            annual_spending=format_amount(run.sustainable.amount), capped=run.sustainable.capped
        ),
        categories=[ProjectionCategoryOut(key=k, name=n) for k, n in prepared.categories],
        members=members,
        flagged=[
            ProjectionFlaggedAccountOut(
                account_id=f.account_id, name=f.name, category_key=f.category_key, status=f.status
            )
            for f in prepared.flagged
        ],
        rows=rows,
        rules=_rules_out(prepared.rules),
        life_events=_life_events_out(prepared.life_events),
        super_access=[
            SuperAccessOut(
                member_id=line.member_id,
                year=line.accessible_from_year,
                on=anniversary(prepared.as_at, prepared.as_at.year + line.accessible_from_year),
            )
            for line in prepared.input.lines
            if line.category_key == service.SUPERANNUATION
        ],
        spending=_spending_out(prepared, spending_layer),
        income=_income_out(prepared.income),
        tax=_tax_out(prepared.tax),
        age_pension=_age_pension_out(prepared.age_pension),
    )


@router.post(
    "/run",
    response_model=ProjectionOut,
    operation_id="runProjection",
    responses=ERRORS,  # type: ignore[arg-type]
)
def run_projection(body: ProjectionIn, session: SessionDep) -> ProjectionOut:
    """Project one set of Assumptions from the Position, in today's dollars. Nothing is stored."""
    request = service.parse_request(
        inflation=body.inflation,
        returns=[(r.category, r.rate) for r in body.returns],
        retirement_ages=[(r.member_id, r.age) for r in body.retirement_ages],
        spending_phases=[(p.anchor, p.offset_years, p.annual_amount) for p in body.spending_phases],
        annual_saving=body.annual_saving,
        horizon_age=body.horizon_age,
        as_at=body.as_at,
        real_wage_growth=body.real_wage_growth,
    )
    return _out(service.run(session, request))


@router.get(
    "/scenarios/{scenario_id}",
    response_model=ScenarioProjectionOut,
    operation_id="runScenarioProjection",
    responses=ERRORS,  # type: ignore[arg-type]
)
def run_scenario_projection(
    scenario_id: int, session: SessionDep, as_at: date | None = None
) -> ScenarioProjectionOut:
    """Project a Scenario's resolved Assumptions from the Position. Nothing is stored."""
    scenario_run = service.run_scenario(session, scenario_id, as_at)
    base = _out(scenario_run.run, scenario_run.spending_layer)
    scenario = scenario_run.scenario
    return ScenarioProjectionOut(
        **base.model_dump(),
        scenario=ScenarioRefOut(id=scenario.id, key=scenario.key, name=scenario.name),
        provenance=[
            ProvenanceOut(
                key=p.key,
                scope_type=p.scope_type,
                scope_ref=p.scope_ref,
                value=assumptions.format_value(p.value),
                layer=p.layer,
            )
            for p in scenario_run.provenance
        ],
    )


class ComparisonErrorOut(BaseModel):
    field: str
    message: str


class ComparedAgePensionOut(BaseModel):
    status: str = Field(
        description="'calculated', or why none was: 'needs_home_choice', 'not_modelled' or "
        "'no_figures'."
    )
    first_year: int | None = Field(
        description="The first projection year (1 is the first) in which an Age Pension is paid; "
        "null when none is."
    )
    first_age: int | None = Field(
        description="The youngest Member's age at the start of that year."
    )
    first_amount: str | None = Field(
        description="What that year pays in total, a year, in today's dollars."
    )


class ComparedBalanceOut(BaseModel):
    year: int = Field(description="The calendar year the projection year ends in.")
    closing: str = Field(description="The total closing balance of that year, exact.")


class ComparedScenarioOut(BaseModel):
    scenario: ScenarioRefOut
    resolved: bool = Field(description="False: see errors; no figures are given.")
    errors: list[ComparisonErrorOut] = Field(
        description="Why the Scenario could not be projected, such as a key with no value."
    )
    horizon_age: int | None = Field(description="The youngest Member's age at the horizon.")
    drawdown_order: list[ProjectionCategoryOut] = Field(
        description="The order Categories fund spending in; empty when not resolved."
    )
    funds_last_to_age: int | None
    lasts_beyond_horizon: bool | None
    sustainable: ProjectionSustainableOut | None
    spending: SpendingUsedOut | None = Field(
        description="The spending phases used; null when not resolved."
    )
    provenance: list[ProvenanceOut]
    age_pension: ComparedAgePensionOut | None = Field(
        description="The Age Pension in brief; null when not resolved."
    )
    balances: list[ComparedBalanceOut] = Field(
        description="The total closing balance at the end of each projection year, the same "
        "rows the Scenario's own projection shows; empty when not resolved."
    )


class DifferingAssumptionOut(BaseModel):
    key: str
    scope_ref: str


class ComparisonOut(BaseModel):
    scenarios: list[ComparedScenarioOut]
    differing: list[DifferingAssumptionOut] = Field(
        description="Assumptions whose value is not the same in every Scenario that resolved."
    )
    drawdown_differs: bool = Field(
        description="True when the Scenarios that resolved do not all use the same drawdown order."
    )
    spending_differs: bool = Field(
        description="True when the Scenarios that resolved do not all use the same spending phases."
    )


def _provenance(items: tuple[assumptions.Source, ...]) -> list[ProvenanceOut]:
    return [
        ProvenanceOut(
            key=p.key,
            scope_type=p.scope_type,
            scope_ref=p.scope_ref,
            value=assumptions.format_value(p.value),
            layer=p.layer,
        )
        for p in items
    ]


def _compared_age_pension(p: ProjectionOut) -> ComparedAgePensionOut | None:
    """The first year the Age Pension is actually paid (above nil), for the comparison."""
    if p.age_pension is None:
        return None
    for row in p.rows:
        w = row.age_pension
        paid = sum((Decimal(x.amount) for x in w.paid), Decimal(0)) if w else Decimal(0)
        if w is not None and paid > 0:
            return ComparedAgePensionOut(
                status=p.age_pension.status,
                first_year=row.t + 1,
                first_age=row.age,
                first_amount=f"{paid:.2f}",
            )
    return ComparedAgePensionOut(
        status=p.age_pension.status, first_year=None, first_age=None, first_amount=None
    )


@router.get(
    "/compare",
    response_model=ComparisonOut,
    operation_id="compareScenarios",
)
def compare_scenarios(session: SessionDep, as_at: date | None = None) -> ComparisonOut:
    """Project every Scenario side by side. One that cannot resolve reports its own errors."""
    comparison = service.compare_scenarios(session, as_at)
    out: list[ComparedScenarioOut] = []
    for o in comparison.outcomes:
        ref = ScenarioRefOut(id=o.scenario.id, key=o.scenario.key, name=o.scenario.name)
        if o.run is None:
            out.append(
                ComparedScenarioOut(
                    scenario=ref,
                    resolved=False,
                    errors=[ComparisonErrorOut(field=e.field, message=e.message) for e in o.errors],
                    horizon_age=None,
                    drawdown_order=[],
                    funds_last_to_age=None,
                    lasts_beyond_horizon=None,
                    sustainable=None,
                    spending=None,
                    provenance=[],
                    age_pension=None,
                    balances=[],
                )
            )
            continue
        base = _out(o.run.run, o.run.spending_layer)
        out.append(
            ComparedScenarioOut(
                scenario=ref,
                resolved=True,
                errors=[],
                horizon_age=base.reference_member.age_now + base.years,
                drawdown_order=base.categories,
                funds_last_to_age=base.funds_last_to_age,
                lasts_beyond_horizon=base.lasts_beyond_horizon,
                sustainable=base.sustainable,
                spending=base.spending,
                provenance=_provenance(o.run.provenance),
                age_pension=_compared_age_pension(base),
                balances=[
                    ComparedBalanceOut(year=r.starts_on.year + 1, closing=r.closing)
                    for r in base.rows
                ],
            )
        )
    orders = {tuple(c.key for c in s.drawdown_order) for s in out if s.resolved}
    return ComparisonOut(
        scenarios=out,
        differing=[DifferingAssumptionOut(key=k, scope_ref=r) for k, r in comparison.differing],
        drawdown_differs=len(orders) > 1,
        spending_differs=comparison.spending_differs,
    )


class SaveRunIn(BaseModel):
    scenario_id: int
    label: str | None = Field(default=None, description="Optional, to tell runs apart.")
    as_at: date | None = Field(
        default=None, description="Position date. Omit for today in the Household time zone."
    )


class RunOut(BaseModel):
    id: int
    created_at: datetime
    label: str | None
    scenario_id: int
    scenario_name: str = Field(description="The Scenario's name when the run was saved.")
    engine_version: int = Field(description="The engine version that computed the run.")
    current_engine: bool = Field(
        description="False: the run can be re-run with the current engine."
    )
    funds_last_to_age: int | None
    lasts_beyond_horizon: bool
    rule_pack_version: str | None = Field(
        description="The Rule pack the run recorded, such as 'au/2026-27/r1'; null if saved "
        "before Rule packs were recorded."
    )


class SavedRunOut(ScenarioProjectionOut):
    run: RunOut


def _run_out(header: runs.RunHeader) -> RunOut:
    return RunOut(
        id=header.id,
        created_at=header.created_at.replace(tzinfo=UTC),
        label=header.label,
        scenario_id=header.scenario_id,
        scenario_name=header.scenario_name,
        engine_version=header.engine_version,
        current_engine=header.current_engine,
        funds_last_to_age=header.funds_last_to_age,
        lasts_beyond_horizon=header.lasts_beyond_horizon,
        rule_pack_version=header.rule_pack_version,
    )


@router.post(
    "/runs",
    status_code=201,
    response_model=RunOut,
    operation_id="saveProjectionRun",
    responses=ERRORS,  # type: ignore[arg-type]
)
def save_run(body: SaveRunIn, session: SessionDep, ctx: ContextDep) -> RunOut:
    """Project a Scenario now and save the whole result as an immutable Projection run."""
    return _run_out(runs.save_run(session, ctx, body.scenario_id, body.label, body.as_at))


@router.get("/runs", response_model=list[RunOut], operation_id="listProjectionRuns")
def list_runs(session: SessionDep) -> list[RunOut]:
    """Saved runs, newest first. Reads the headline columns only, never the stored results."""
    return [_run_out(h) for h in runs.list_runs(session)]


@router.get(
    "/runs/{run_id}",
    response_model=SavedRunOut,
    operation_id="getProjectionRun",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_run(run_id: int, session: SessionDep) -> SavedRunOut:
    """A saved run exactly as computed, rendered from its stored snapshot alone."""
    saved = runs.get_run(session, run_id)
    base = _out(saved.run, saved.spending_layer)
    return SavedRunOut(
        **base.model_dump(),
        scenario=ScenarioRefOut(
            id=saved.header.scenario_id, key=None, name=saved.header.scenario_name
        ),
        provenance=[
            ProvenanceOut(
                key=p.key,
                scope_type=p.scope_type,
                scope_ref=p.scope_ref,
                value=assumptions.format_value(p.value),
                layer=p.layer,
            )
            for p in saved.provenance
        ],
        run=_run_out(saved.header),
    )


@router.post(
    "/runs/{run_id}/rerun",
    status_code=201,
    response_model=RunOut,
    operation_id="rerunProjectionRun",
    responses=ERRORS,  # type: ignore[arg-type]
)
def rerun_run(run_id: int, session: SessionDep, ctx: ContextDep) -> RunOut:
    """Project a run's stored input with the current engine and save a new run. The old stays."""
    return _run_out(runs.rerun_with_current_engine(session, ctx, run_id))
