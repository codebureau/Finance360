"""The Tax endpoints. Thin: the maths is the engine, the input is built by the service.

Request amounts are decimal strings (never JSON numbers); days are whole numbers and flags are
booleans. Responses carry amounts as plain AUD decimal strings. Tax records are stored and audited
(ADR-0019 section 2); a Label total is computed from them on demand. ``estimate`` stores nothing:
it reads the Members' Label totals from the register and the Rule pack, and calculates.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, NamedTuple

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.household import service as household
from finance360.capabilities.tax import div293, engine, labels, pack_rules, reconcile, service
from finance360.capabilities.tax.input import Total
from finance360.capabilities.tax.record_types import home_office_hours as hours_type
from finance360.capabilities.tax.record_types.base import Operand
from finance360.capabilities.tax.record_types.income_statement import IncomeStatement
from finance360.core.money import Money, format_amount
from finance360.core.rulepack import fy_label

router = APIRouter(prefix="/api/tax", tags=["tax"])


class TaxLabelOut(BaseModel):
    key: str
    return_item: str = Field(description="The tax return item, such as '1' or 'D5'; may be empty.")
    name: str
    description: str
    direction: str = Field(
        description="'income', 'deduction', 'offset', 'credit', 'fact' or 'memo'."
    )
    kind: str = Field(description="'money', 'days' or 'flag'.")
    modelled: bool = Field(description="False: a figure here marks the estimate incomplete.")
    section: str
    record_types: list[str] = Field(description="The Record types that may feed it.")


class TaxSectionOut(BaseModel):
    key: str
    name: str


class TaxProvisionalOut(BaseModel):
    key: str = Field(description="The Rule pack key, such as 'tax.help.t1'.")
    note: str


class TaxYearOut(BaseModel):
    financial_year: int = Field(description="The year it ends in: 2027 is FY 2026-27.")
    label: str
    complete: bool = Field(description="False when the pack lacks some figures an estimate needs.")
    missing: list[str]
    provisional: list[TaxProvisionalOut]


class TaxSetupOut(BaseModel):
    rule_pack_version: str | None
    years: list[TaxYearOut] = Field(description="Years the Rule pack holds tax figures for.")
    sections: list[TaxSectionOut]
    labels: list[TaxLabelOut]


class TaxEstimateIn(BaseModel):
    financial_year: StrictInt = Field(description="The year it ends in: 2027 is FY 2026-27.")
    dependants: StrictInt = Field(default=0, description="Dependent children.")
    member_ids: list[StrictInt] = Field(
        description="One or two Household Members. Their totals come from the Tax records."
    )


class TaxStatementIn(BaseModel):
    """An income statement's figures: decimal amounts in AUD, as text, and three short text facts.

    The three required figures are gross_payments (the statement's total gross amount),
    tax_withheld and employer_super. The rest are optional, net_pay included: it is not on the
    ATO statement, and when it is left out the Income reconciliation works take-home out.
    """

    gross_payments: str | None = Field(default=None, description="Total gross amount. Required.")
    tax_withheld: str | None = Field(
        default=None, description="Tax withheld (includes the Medicare levy). Required."
    )
    employer_super: str | None = Field(default=None, description="Employer super. Required.")
    net_pay: str | None = Field(
        default=None,
        description="Net pay received. Optional: absent means 'not stated', not zero.",
    )
    reportable_fringe_benefits: str | None = Field(
        default=None, description="The grossed-up reportable fringe benefits total."
    )
    reportable_fringe_benefits_taxable: str | None = Field(
        default=None,
        description="The taxable employer status part of the reportable fringe benefits. "
        "Information only; not more than the total.",
    )
    reportable_employer_super: str | None = None
    novated_lease_post_tax: str | None = Field(
        default=None, description="Novated lease costs paid from net pay."
    )
    other_deductions: str | None = Field(default=None, description="Other deductions from pay.")
    super_insurance_premiums: str | None = Field(
        default=None, description="Insurance premiums paid from the super balance."
    )
    gross_amount: str | None = Field(
        default=None, description="Gross amount of salary and wages. Information only."
    )
    bonuses_commissions: str | None = Field(
        default=None, description="Bonuses and commissions. Information only."
    )
    paid_leave_amount: str | None = Field(
        default=None, description="Paid leave amount. Information only."
    )
    salary_sacrifice_amount: str | None = Field(
        default=None, description="Salary sacrifice amount. Information only."
    )
    lump_sum_a: str | None = Field(default=None, description="Lump sum payment A (Item 3).")
    lump_sum_b: str | None = Field(default=None, description="Lump sum payment B (Item 3).")
    lump_sum_d: str | None = Field(
        default=None, description="Lump sum payment D. Tax-free; for the record only."
    )
    lump_sum_e: str | None = Field(
        default=None, description="Lump sum payment E, in arrears (Item 24)."
    )
    lump_sum_w: str | None = Field(default=None, description="Lump sum payment W.")
    allowances: str | None = Field(default=None, description="Allowances (Item 2).")
    deductions_total: str | None = Field(
        default=None, description="Deductions total on the statement. Information only."
    )
    employer: str | None = Field(
        default=None, description="The employer's name, at most 80 characters."
    )
    leave_payment_type: str | None = Field(default=None, description="Leave payment type.")
    salary_sacrifice_type: str | None = Field(default=None, description="Salary sacrifice type.")


class TaxStatementOut(BaseModel):
    gross_payments: str
    tax_withheld: str
    employer_super: str
    net_pay: str | None = Field(description="Null when not stated.")
    reportable_fringe_benefits: str
    reportable_fringe_benefits_taxable: str
    reportable_employer_super: str
    novated_lease_post_tax: str
    other_deductions: str
    super_insurance_premiums: str
    gross_amount: str
    bonuses_commissions: str
    paid_leave_amount: str
    salary_sacrifice_amount: str
    lump_sum_a: str
    lump_sum_b: str
    lump_sum_d: str
    lump_sum_e: str
    lump_sum_w: str
    allowances: str
    deductions_total: str
    employer: str
    leave_payment_type: str
    salary_sacrifice_type: str


class TaxStatementChangeOut(BaseModel):
    figure: str = Field(description="The statement field, such as 'bonuses_commissions'.")
    current: str
    previous: str
    change: str = Field(description="This year minus last year: negative where it fell.")


class TaxPreviousYearOut(BaseModel):
    record_id: int = Field(description="Last year's statement.")
    description: str
    financial_year: int
    financial_year_label: str
    changes: list[TaxStatementChangeOut]


class TaxMonthIn(BaseModel):
    month: str = Field(description="The month, as 'YYYY-MM'.")
    working_days: str = Field(default="", description="Working days in the month, such as '21'.")
    leave_days: str = Field(default="", description="Leave or holiday days, such as '2.5'.")
    office_days: str = Field(default="", description="Days worked away from home.")
    hours: str = Field(
        default="",
        description="Casual arrangement only: the actual hours worked from home that month, "
        "such as '18.5' (at most two decimal places).",
    )


class TaxTripIn(BaseModel):
    description: str = Field(default="", description="Where to or from, and the purpose.")
    km: str = Field(
        default="",
        description="Kilometres of one trip: more than 0, one decimal place at most ('42.5').",
    )
    times: str = Field(
        default="1", description="How many times the trip was made: a whole number, 1 or more."
    )
    on: str = Field(
        default="",
        description="Optional: the date, as 'YYYY-MM-DD', within the record's dates.",
    )


class TaxRecordIn(BaseModel):
    member_id: StrictInt | None = Field(default=None, description="The Member the record is for.")
    record_type: str = Field(
        default="line",
        description="'line', 'fact', 'income_statement', 'depreciating_asset', "
        "'home_office_hours' or 'car_kilometres'.",
    )
    from_on: date
    to_on: date | None = Field(
        default=None,
        description="Omit for a single day; for a depreciating asset, omit while it is still used. "
        "Home office hours and car kilometres need both dates, in one financial year.",
    )
    description: str = ""
    notes: str | None = None
    label: str = Field(default="", description="The Return label key.")
    amount: str | None = Field(
        default=None,
        description="A line: a decimal amount in AUD. A depreciating asset: its cost.",
    )
    work_use_percent: str | None = Field(
        default=None,
        description="A line or depreciating asset: the percent used for work, such as '60'.",
    )
    effective_life_years: str | None = Field(
        default=None,
        description="A depreciating asset: its effective life in years, such as '4' or '2.5'.",
    )
    claim_in_full: StrictBool | None = Field(
        default=None,
        description="A depreciating asset: deduct the work-use share of the cost in full in the "
        "year it started. Check the asset is eligible; nothing here tests it.",
    )
    arrangement: str | None = Field(
        default=None,
        description="Home office hours: 'regular' (days at home, with hours per day) or "
        "'casual' (the actual hours worked from home each month).",
    )
    hours_per_day: str | None = Field(
        default=None,
        description="Home office hours, regular: the hours worked per home day, such as '7.6' "
        "(at most two decimal places).",
    )
    home_weekdays: list[str] | None = Field(
        default=None,
        description="Home office hours, regular: the weekdays usually worked at home, a "
        "non-empty subset of 'mon', 'tue', 'wed', 'thu', 'fri'. Give this or "
        "office_days_per_month, not both.",
    )
    office_days_per_month: str | None = Field(
        default=None,
        description="Home office hours, regular: the office days a month, such as '2' "
        "(at most two decimal places). Give this or home_weekdays, not both.",
    )
    months: list[TaxMonthIn] | None = Field(
        default=None,
        description="Home office hours: one entry per month of the worksheet.",
    )
    trips: list[TaxTripIn] | None = Field(
        default=None,
        description="Car kilometres: the trips (at least one, at most 500). A Member has one car "
        "kilometres record for each financial year.",
    )
    value: StrictBool | StrictInt | None = Field(
        default=None, description="A fact: true or false for a flag, a number of days."
    )
    statement: TaxStatementIn | None = Field(
        default=None, description="An income statement: its figures."
    )


class TaxAssetYearOut(BaseModel):
    financial_year: int
    financial_year_label: str
    days: int = Field(description="Days held in the year; 0 when claimed in full.")
    opening: str = Field(description="The adjustable value at the start of the year.")
    decline: str = Field(description="The fall in adjustable value, before the work-use share.")
    deduction: str = Field(description="What counts toward the Return label that year.")
    closing: str = Field(description="The adjustable value at the end of the year.")


class TaxAssetOut(BaseModel):
    """A depreciating asset's worksheet, computed now and never stored."""

    effective_life_years: str
    claim_in_full: bool
    multiplier_percent: int | None = Field(
        description="The Rule pack diminishing value percent used; null when it is not captured."
    )
    missing: list[str] = Field(
        description="Rule pack keys the schedule needs and the pack lacks; the schedule is then "
        "empty and the asset adds nothing to any total."
    )
    schedule: list[TaxAssetYearOut]


class TaxMonthOut(BaseModel):
    month: str
    month_label: str
    working_days: str | None = Field(description="Regular only; null for a casual month.")
    leave_days: str | None = Field(description="Regular only; null for a casual month.")
    office_days: str | None = Field(description="Regular only; null for a casual month.")
    home_days: str | None = Field(
        description="Regular: working days less leave and office days; null for a casual month."
    )
    hours: str = Field(
        description="Regular: home days times hours per day, exact. Casual: the hours entered."
    )


class TaxHomeOfficeOut(BaseModel):
    """A home office worksheet's working, computed now and never stored."""

    arrangement: str = Field(description="'regular' or 'casual'.")
    hours_per_day: str | None = Field(description="Regular only; null for a casual record.")
    home_weekdays: list[str] | None = Field(
        description="Regular: the weekdays usually worked at home, in week order; null for a "
        "casual record, a record kept as office days a month, or one saved before the "
        "arrangement existed."
    )
    office_days_per_month: str | None = Field(
        description="Regular: the office days a month, when the pattern is kept that way."
    )
    months: list[TaxMonthOut]
    total_hours: str = Field(description="The sum of the months' hours, exact.")
    rate_per_hour: str | None = Field(
        description="The Rule pack fixed rate per hour for the year; null when not captured."
    )
    deduction: str | None = Field(
        description="Total hours times the rate, rounded once; null while the rate is missing."
    )
    missing: list[str] = Field(
        description="Rule pack keys the deduction needs and the pack lacks; the record then "
        "adds nothing to any total."
    )


class TaxTripOut(BaseModel):
    description: str
    km: str = Field(description="Kilometres of one trip, exact, one decimal place.")
    times: int
    on: date | None
    total_km: str = Field(description="Kilometres times the times, exact.")


class TaxCarOut(BaseModel):
    """A car kilometres record's working, computed now and never stored."""

    trips: list[TaxTripOut]
    total_km: str = Field(description="The sum of the trips' kilometres times times, exact.")
    limit_km: int | None = Field(
        description="The Rule pack limit per car for the year; null when not captured."
    )
    counted_km: str | None = Field(
        description="The total limited to the limit; null while the limit is missing."
    )
    capped: bool = Field(description="Whether the total is over the limit.")
    rate_per_km: str | None = Field(
        description="The Rule pack rate per kilometre for the year; null when not captured."
    )
    deduction: str | None = Field(
        description="Kilometres counted times the rate, rounded once; null while a figure is "
        "missing."
    )
    missing: list[str] = Field(
        description="Rule pack keys the deduction needs and the pack lacks; the record then "
        "adds nothing to any total."
    )


class TaxFigureUsedOut(BaseModel):
    """An official figure a record used: what it is, and where it came from."""

    key: str = Field(description="The Rule pack key, such as 'tax.home_office.rate_per_hour'.")
    name: str
    financial_year: int = Field(description="The year it ends in: 2026 is FY 2025-26.")
    financial_year_label: str
    value: str = Field(description="As shown, such as '0.70 an hour' or '200%'.")
    source: str = Field(description="The citation title.")
    url: str | None = Field(description="The official page the figure was read from.")
    url_confirmed: bool = Field(
        description="False until a person has opened the address and found it correct."
    )
    retrieved: date | None = Field(description="The day the figure was captured.")
    provisional: bool = Field(description="Announced but not yet law.")
    provisional_note: str | None
    rule_pack_version: str = Field(
        description="Such as 'au/2026-27/r5', or 'au/2026-27/r5+2:a3f9c1' with captured figures."
    )
    origin: Literal["shipped", "captured"] = Field(
        description="Whether the release holds the figure or a person captured it in the app."
    )
    capture_id: int | None = Field(
        description="For a captured figure: its capture, which always resolves to the quote."
    )
    quote: str | None = Field(description="The sentence a captured figure was read from.")
    captured_by: str | None = Field(description="Who captured it, such as 'owner'.")
    captured_at: datetime | None = Field(description="UTC.")
    shipped_value: str | None = Field(
        description="The figure the release holds, when a capture replaced a different one."
    )
    method: Literal["typed", "ai", "matched", "carried"] | None = Field(
        description="For a captured figure: typed by hand, read by AI, matched by words, or "
        "carried from an earlier year."
    )


class TaxOperandOut(BaseModel):
    value: str = Field(description="An exact decimal string.")
    unit: str | None = Field(description="Shown after the value, such as 'hours' or '%'.")
    label: str | None = Field(description="A short caption saying what the number is.")
    operator: str | None = Field(
        description="How it joins the operand before it: '×', '÷' or '+'; null for the first."
    )


class TaxStepOut(BaseModel):
    title: str
    operands: list[TaxOperandOut] = Field(description="Combined left to right into the result.")
    result: TaxOperandOut
    note: str | None


class TaxFigureWaitingOut(BaseModel):
    key: str
    name: str
    financial_year: int
    financial_year_label: str


class TaxCalculationOut(BaseModel):
    """A record's working, replayed from exact values. Computed now and never stored."""

    steps: list[TaxStepOut]
    waiting_for: list[TaxFigureWaitingOut] = Field(
        description="Figures the calculation needs and the Rule pack does not hold for the year."
    )


class TaxRecordOut(BaseModel):
    id: int
    member_id: int | None
    member_name: str | None
    record_type: str
    from_on: date
    to_on: date | None
    description: str
    notes: str | None
    label_key: str
    label_name: str
    return_item: str
    kind: str = Field(description="The label's value kind: 'money', 'days' or 'flag'.")
    amount: str | None = Field(
        description="A line: the amount entered. A depreciating asset: its cost."
    )
    work_use_percent: str | None = Field(description="The share used for work, if any.")
    counted: str | None = Field(
        description="What counts toward the label total, after the work-use share. A depreciating "
        "asset counts a different amount each year: it is given only when a financial_year is "
        "asked for."
    )
    value: bool | int | None = Field(description="A fact: the flag or the number of days.")
    statement: TaxStatementOut | None = Field(
        description="An income statement: its figures. The label is then the gross pay's."
    )
    previous_year: TaxPreviousYearOut | None = Field(
        default=None,
        description="An income statement opened on its own: the previous financial year's "
        "statement for the same Member and employer, with the change in each figure. Null where "
        "there is none, and in the register list.",
    )
    asset: TaxAssetOut | None = Field(description="A depreciating asset: its schedule.")
    home_office: TaxHomeOfficeOut | None = Field(
        description="Home office hours: the hours, the rate and what is missing."
    )
    car: TaxCarOut | None = Field(
        description="Car kilometres: the trips, the totals, the rate and limit, what is missing."
    )
    figures_used: list[TaxFigureUsedOut] = Field(
        description="The official figures the record used; empty when it uses none."
    )
    calculation: TaxCalculationOut | None = Field(
        description="The record's working, step by step; null for a record with no calculation."
    )


class TaxContributionOut(BaseModel):
    record_id: int
    amount: str | None
    days: int | None
    flag: bool | None


class TaxLabelTotalOut(BaseModel):
    member_id: int
    member_name: str
    label_key: str
    label_name: str
    return_item: str
    kind: str
    amount: str | None
    days: int | None
    flag: bool | None
    contributions: list[TaxContributionOut] = Field(
        description="The records behind the total, each with its record_id."
    )


class TaxUncountedOut(BaseModel):
    record_id: int
    member_id: int | None
    description: str
    label_name: str
    missing: list[str] = Field(description="Rule pack keys the record needs and the pack lacks.")
    note: str


class TaxLabelTotalsOut(BaseModel):
    financial_year: int
    financial_year_label: str
    totals: list[TaxLabelTotalOut]
    uncounted: list[TaxUncountedOut] = Field(
        description="Records left out of the totals because a Rule pack figure is missing. A "
        "total is incomplete while any is listed."
    )


class TaxMemberOut(BaseModel):
    member_id: int
    name: str
    total_income: str
    total_deductions: str
    taxable_income: str
    tax_on_taxable_income: str
    low_income_offset: str
    small_business_offset: str
    offsets: str = Field(description="Offsets applied; they cannot take the tax below zero.")
    medicare_levy: str
    medicare_levy_surcharge: str
    surcharge_rate: str = Field(description="A percent string.")
    help_repayment: str
    total_tax: str
    credits: str = Field(description="Tax withheld, franking credits and PAYG instalments.")
    net_outcome: str = Field(description="Credits less total tax: negative is an amount payable.")
    notes: list[str]


class TaxHouseholdOut(BaseModel):
    taxable_income: str
    tax_on_taxable_income: str
    offsets: str
    medicare_levy: str
    medicare_levy_surcharge: str
    help_repayment: str
    total_tax: str
    credits: str
    net_outcome: str
    surcharge_income: str
    surcharge_thresholds: str = Field(description="'single' or 'family'.")
    surcharge_tier: int


class TaxUnmodelledOut(BaseModel):
    member_id: int
    name: str
    label_key: str
    return_item: str
    label_name: str
    amount: str


class TaxEstimateOut(BaseModel):
    financial_year: int
    financial_year_label: str
    engine_version: int
    rule_pack_version: str
    incomplete: bool = Field(
        description="True when a figure was entered for a Return label the engine does not model."
    )
    provisional: list[TaxProvisionalOut] = Field(
        description="Rule figures of the year that are announced but not yet law."
    )
    members: list[TaxMemberOut]
    household: TaxHouseholdOut
    unmodelled: list[TaxUnmodelledOut]
    division_293: TaxDiv293Out


class TaxDiv293InputOut(BaseModel):
    label: str
    amount: str
    source: str = Field(description="Where the figure came from, named in words.")


class TaxDiv293MemberOut(BaseModel):
    member_id: int
    name: str
    liability: str = Field(description="Division 293 tax for the year; zero when none is due.")
    applies: bool
    div293_income: str
    inputs: list[TaxDiv293InputOut] = Field(description="Each input with its value and source.")
    steps: list[TaxStepOut] = Field(description="The working, step by step, to the liability.")
    contributions_source: str = Field(
        description="Where the concessional contributions came from: 'statement' (a Super "
        "statement), 'income_stream' or 'none'."
    )
    received: str = Field(description="Concessional contributions received by the fund.")
    cap: str
    cap_status: str = Field(description="'ok', 'near' (within 10% of the cap) or 'over'.")
    cap_warning: str | None = Field(description="Set when within 10% of the cap or over it.")
    released: str | None = Field(
        description="Division 293 released from super in the year, from the Super statement; "
        "null when no statement states it."
    )
    released_source: str | None
    notes: list[str]


class TaxDiv293Out(BaseModel):
    members: list[TaxDiv293MemberOut] = Field(
        description="Empty while the Rule pack lacks a Division 293 figure for the year."
    )
    figures_used: list[TaxFigureUsedOut]
    waiting_for: list[TaxFigureWaitingOut]


def amount_out(money: Money) -> str:
    return format_amount(money)


@router.get("/setup", response_model=TaxSetupOut, operation_id="getTaxSetup")
def get_setup(session: SessionDep) -> TaxSetupOut:
    info = service.setup(session)
    return TaxSetupOut(
        rule_pack_version=info.pack_version,
        years=[
            TaxYearOut(
                financial_year=y.financial_year,
                label=y.label,
                complete=y.complete,
                missing=list(y.missing),
                provisional=[TaxProvisionalOut(key=k, note=n) for k, n in y.provisional],
            )
            for y in info.years
        ],
        sections=[TaxSectionOut(key=k, name=n) for k, n in labels.SECTIONS],
        labels=[
            TaxLabelOut(
                key=lb.key,
                return_item=lb.return_item,
                name=lb.name,
                description=lb.description,
                direction=lb.direction,
                kind=lb.kind,
                modelled=lb.modelled,
                section=lb.section,
                record_types=list(lb.record_types),
            )
            for lb in labels.all_labels()
        ],
    )


@router.post(
    "/estimate",
    response_model=TaxEstimateOut,
    operation_id="calculateTaxEstimate",
    responses=ERRORS,  # type: ignore[arg-type]
)
def calculate_estimate(body: TaxEstimateIn, session: SessionDep) -> TaxEstimateOut:
    """Calculate from the Members' Tax records; nothing is stored."""
    run = service.estimate(
        session,
        financial_year=body.financial_year,
        dependants=body.dependants,
        member_ids=body.member_ids,
    )
    result = run.result
    names = run.member_names
    return TaxEstimateOut(
        financial_year=result.financial_year,
        financial_year_label=fy_label(result.financial_year),
        engine_version=result.engine_version,
        rule_pack_version=run.rule_pack_version,
        incomplete=result.incomplete,
        provisional=[TaxProvisionalOut(key=k, note=n) for k, n in result.provisional],
        members=[member_out(m, names[m.member_id]) for m in result.members],
        household=TaxHouseholdOut(
            taxable_income=amount_out(result.household.taxable_income),
            tax_on_taxable_income=amount_out(result.household.tax_on_taxable_income),
            offsets=amount_out(result.household.offsets),
            medicare_levy=amount_out(result.household.medicare_levy),
            medicare_levy_surcharge=amount_out(result.household.medicare_levy_surcharge),
            help_repayment=amount_out(result.household.help_repayment),
            total_tax=amount_out(result.household.total_tax),
            credits=amount_out(result.household.credits),
            net_outcome=amount_out(result.household.net_outcome),
            surcharge_income=amount_out(result.household.surcharge_income),
            surcharge_thresholds=result.household.surcharge_thresholds,
            surcharge_tier=result.household.surcharge_tier,
        ),
        unmodelled=[unmodelled_out(u, names[u.member_id]) for u in result.unmodelled],
        division_293=_div293_out(run.division_293),
    )


def record_input(body: TaxRecordIn) -> service.RecordInput:
    return service.RecordInput(
        member_id=body.member_id,
        record_type=body.record_type,
        from_on=body.from_on,
        to_on=body.to_on,
        description=body.description,
        notes=body.notes,
        label=body.label,
        amount=body.amount,
        work_use_percent=body.work_use_percent,
        effective_life_years=body.effective_life_years,
        claim_in_full=body.claim_in_full,
        arrangement=body.arrangement,
        hours_per_day=body.hours_per_day,
        home_weekdays=body.home_weekdays,
        office_days_per_month=body.office_days_per_month,
        months=[m.model_dump() for m in body.months] if body.months is not None else None,
        trips=[t.model_dump() for t in body.trips] if body.trips is not None else None,
        value=body.value,
        statement=(
            {k: v for k, v in body.statement.model_dump().items() if v is not None}
            if body.statement is not None
            else None
        ),
    )


def _statement_out(value: IncomeStatement) -> TaxStatementOut:
    figures: dict[str, Any] = {
        k: v if isinstance(v, str) or v is None else amount_out(v) for k, v in vars(value).items()
    }
    return TaxStatementOut(**figures)


def _previous_year_out(view: service.RecordView) -> TaxPreviousYearOut | None:
    previous = view.previous_year
    if previous is None:
        return None
    return TaxPreviousYearOut(
        record_id=previous.record_id,
        description=previous.description,
        financial_year=previous.financial_year,
        financial_year_label=fy_label(previous.financial_year),
        changes=[
            TaxStatementChangeOut(
                figure=c.field,
                current=amount_out(c.current),
                previous=amount_out(c.previous),
                change=amount_out(c.change),
            )
            for c in previous.changes
        ],
    )


def _asset_out(view: service.RecordView) -> TaxAssetOut | None:
    if view.asset is None:
        return None
    return TaxAssetOut(
        effective_life_years=view.asset.effective_life,
        claim_in_full=view.asset.asset.claim_in_full,
        multiplier_percent=view.asset.multiplier_percent,
        missing=list(view.asset.missing),
        schedule=[
            TaxAssetYearOut(
                financial_year=r.financial_year,
                financial_year_label=fy_label(r.financial_year),
                days=r.days,
                opening=amount_out(r.opening),
                decline=amount_out(r.decline),
                deduction=amount_out(r.deduction),
                closing=amount_out(r.closing),
            )
            for r in view.asset.rows
        ],
    )


def _home_office_out(view: service.RecordView) -> TaxHomeOfficeOut | None:
    sheet = view.home_office
    if sheet is None:
        return None
    return TaxHomeOfficeOut(
        arrangement=sheet.arrangement,
        hours_per_day=sheet.hours_per_day,
        home_weekdays=list(sheet.home_weekdays) if sheet.home_weekdays is not None else None,
        office_days_per_month=sheet.office_days_per_month,
        months=[
            TaxMonthOut(
                month=r.row.month,
                month_label=hours_type.month_name(r.row.month),
                working_days=None
                if r.home_days is None
                else hours_type.hundredths_text(r.row.working),
                leave_days=None if r.home_days is None else hours_type.hundredths_text(r.row.leave),
                office_days=None
                if r.home_days is None
                else hours_type.hundredths_text(r.row.office),
                home_days=r.home_days,
                hours=r.hours,
            )
            for r in sheet.rows
        ],
        total_hours=sheet.total_hours,
        rate_per_hour=amount_out(sheet.rate) if sheet.rate is not None else None,
        deduction=amount_out(sheet.deduction) if sheet.deduction is not None else None,
        missing=list(sheet.missing),
    )


def _car_out(view: service.RecordView) -> TaxCarOut | None:
    car = view.car
    if car is None:
        return None
    return TaxCarOut(
        trips=[
            TaxTripOut(
                description=t.trip.description,
                km=t.km,
                times=t.trip.times,
                on=t.trip.on,
                total_km=t.total_km,
            )
            for t in car.trips
        ],
        total_km=car.total_km,
        limit_km=car.limit_km,
        counted_km=car.counted_km,
        capped=car.capped,
        rate_per_km=amount_out(car.rate) if car.rate is not None else None,
        deduction=amount_out(car.deduction) if car.deduction is not None else None,
        missing=list(car.missing),
    )


def _operand_out(o: Operand) -> TaxOperandOut:
    return TaxOperandOut(value=o.value, unit=o.unit, label=o.label, operator=o.operator)


def _calculation_out(view: service.RecordView) -> TaxCalculationOut | None:
    if not view.steps and not view.waiting_for:
        return None
    return TaxCalculationOut(
        steps=[
            TaxStepOut(
                title=step.title,
                operands=[_operand_out(o) for o in step.operands],
                result=_operand_out(step.result),
                note=step.note,
            )
            for step in view.steps
        ],
        waiting_for=[
            TaxFigureWaitingOut(
                key=w.key,
                name=w.name,
                financial_year=w.financial_year,
                financial_year_label=fy_label(w.financial_year),
            )
            for w in view.waiting_for
        ],
    )


def _record_out(view: service.RecordView) -> TaxRecordOut:
    row = view.row
    counted = view.counted
    return TaxRecordOut(
        id=row.id,
        member_id=row.member_id,
        member_name=view.member_name,
        record_type=row.record_type,
        from_on=row.from_on,
        to_on=row.to_on,
        description=row.description,
        notes=row.notes,
        label_key=view.label.key,
        label_name=view.label.name,
        return_item=view.label.return_item,
        kind=view.label.kind,
        amount=amount_out(view.entered) if view.entered is not None else None,
        work_use_percent=view.work_use.to_percent() if view.work_use is not None else None,
        counted=amount_out(counted) if isinstance(counted, Money) else None,
        value=None if isinstance(counted, Money) else counted,
        statement=_statement_out(view.statement) if view.statement is not None else None,
        previous_year=_previous_year_out(view),
        asset=_asset_out(view),
        home_office=_home_office_out(view),
        car=_car_out(view),
        figures_used=[_figure_out(f) for f in view.figures_used],
        calculation=_calculation_out(view),
    )


@router.get("/records", response_model=list[TaxRecordOut], operation_id="listTaxRecords")
def list_records(
    session: SessionDep,
    member_id: Annotated[int | None, Query(description="Only this Member's records.")] = None,
    financial_year: Annotated[
        int | None,
        Query(
            ge=1990,
            le=2200,
            description="Only records whose dates overlap this year (2027 is FY 2026-27).",
        ),
    ] = None,
    label: Annotated[str | None, Query(description="Only this Return label's records.")] = None,
    q: Annotated[
        str | None,
        Query(description="Words in the description, notes, Member or label (all must match)."),
    ] = None,
) -> list[TaxRecordOut]:
    views = service.list_records(
        session, member_id=member_id, financial_year=financial_year, label=label, query=q
    )
    return [_record_out(v) for v in views]


@router.get(
    "/records/{record_id}",
    response_model=TaxRecordOut,
    operation_id="getTaxRecord",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_record(record_id: int, session: SessionDep) -> TaxRecordOut:
    return _record_out(service.get_record(session, record_id))


@router.post(
    "/records",
    status_code=201,
    response_model=TaxRecordOut,
    operation_id="createTaxRecord",
    responses=ERRORS,  # type: ignore[arg-type]
)
def create_record(body: TaxRecordIn, session: SessionDep, ctx: ContextDep) -> TaxRecordOut:
    return _record_out(service.create_record(session, ctx, record_input(body)))


@router.put(
    "/records/{record_id}",
    response_model=TaxRecordOut,
    operation_id="updateTaxRecord",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_record(
    record_id: int, body: TaxRecordIn, session: SessionDep, ctx: ContextDep
) -> TaxRecordOut:
    return _record_out(service.update_record(session, ctx, record_id, record_input(body)))


@router.delete(
    "/records/{record_id}",
    status_code=204,
    operation_id="deleteTaxRecord",
    responses=ERRORS,  # type: ignore[arg-type]
)
def delete_record(record_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    service.delete_record(session, ctx, record_id)
    return Response(status_code=204)


class ValueOut(NamedTuple):
    amount: str | None
    days: int | None
    flag: bool | None


def value_out(value: Total) -> ValueOut:
    if isinstance(value, Money):
        return ValueOut(amount_out(value), None, None)
    if isinstance(value, bool):
        return ValueOut(None, None, value)
    return ValueOut(None, value, None)


@router.get(
    "/label-totals",
    response_model=TaxLabelTotalsOut,
    operation_id="getTaxLabelTotals",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_label_totals(
    session: SessionDep,
    financial_year: Annotated[
        int, Query(ge=1990, le=2200, description="The year it ends in: 2027 is FY 2026-27.")
    ],
    member_id: Annotated[int | None, Query(description="Only this Member's totals.")] = None,
) -> TaxLabelTotalsOut:
    """Each Member's Label totals from the records, computed now, with the records behind each."""
    names = {m.id: m.name for m in household.list_members(session)}
    out: list[TaxLabelTotalOut] = []
    for total in service.label_totals(session, financial_year, member_id=member_id):
        label = labels.LABELS[total.label]
        if total.member_id is None:  # the service requires a Member for now
            continue
        value = value_out(total.total)
        contributions = [(c.record_id, value_out(c.value)) for c in total.contributions]
        out.append(
            TaxLabelTotalOut(
                member_id=total.member_id,
                member_name=names.get(total.member_id, ""),
                label_key=label.key,
                label_name=label.name,
                return_item=label.return_item,
                kind=label.kind,
                amount=value.amount,
                days=value.days,
                flag=value.flag,
                contributions=[
                    TaxContributionOut(record_id=rid, amount=v.amount, days=v.days, flag=v.flag)
                    for rid, v in contributions
                ],
            )
        )
    uncounted = [
        TaxUncountedOut(
            record_id=u.record_id,
            member_id=u.member_id,
            description=u.description,
            label_name=u.label.name,
            missing=list(u.keys),
            note=f"Left out of {u.label.name}: the Rule pack has no {', '.join(u.keys)} for "
            f"FY {fy_label(u.figure_year)}.",
        )
        for u in service.uncounted_records(session, financial_year, member_id=member_id)
    ]
    return TaxLabelTotalsOut(
        financial_year=financial_year,
        financial_year_label=fy_label(financial_year),
        totals=out,
        uncounted=uncounted,
    )


# --- Income reconciliation (ADR-0019 section 9) --------------------------------------------


class IncomeSliceOut(BaseModel):
    key: str = Field(description="'tax', 'super', 'lease', 'other', 'take_home' or 'unreconciled'.")
    name: str
    amount: str
    share_percent: str | None = Field(
        description="Percent of the drawn whole; the drawn shares sum to exactly 100."
    )


class IncomeReconciliationOut(BaseModel):
    member_id: int | None = Field(description="Null for the Household.")
    name: str
    gross_pay: str
    employer_super: str
    package: str = Field(description="Gross pay plus employer super: the whole, 100%.")
    slices: list[IncomeSliceOut] = Field(
        description="What is drawn: the five slices, then Unreconciled when it is positive."
    )
    unreconciled: str = Field(
        description="Package minus the five slices, signed. Always given. A negative one is not "
        "a slice: the shares then divide the slices alone."
    )
    reportable_fringe_benefits: str = Field(description="Beside the chart, not in it.")
    super_insurance_premiums: str = Field(description="A note, not a slice.")
    take_home_worked_out: str = Field(
        description="The part of Take-home worked out because the statement states no net pay: "
        "all of it, some of it or none (0.00)."
    )


class IncomeReconciliationsOut(BaseModel):
    financial_year: int
    financial_year_label: str
    members: list[IncomeReconciliationOut]
    household: IncomeReconciliationOut | None


def _reconciliation_out(name: str, r: reconcile.IncomeReconciliation) -> IncomeReconciliationOut:
    return IncomeReconciliationOut(
        member_id=r.member_id,
        name=name,
        gross_pay=amount_out(r.gross_pay),
        employer_super=amount_out(r.employer_super),
        package=amount_out(r.package),
        slices=[
            IncomeSliceOut(
                key=s.key,
                name=s.name,
                amount=amount_out(s.amount),
                share_percent=reconcile.percent_text(s.share_ppm)
                if s.share_ppm is not None
                else None,
            )
            for s in r.slices
        ],
        unreconciled=amount_out(r.unreconciled),
        reportable_fringe_benefits=amount_out(r.reportable_fringe_benefits),
        super_insurance_premiums=amount_out(r.super_insurance_premiums),
        take_home_worked_out=amount_out(r.take_home_worked_out),
    )


@router.get(
    "/income-reconciliation",
    response_model=IncomeReconciliationsOut,
    operation_id="getIncomeReconciliation",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_income_reconciliation(
    session: SessionDep,
    financial_year: Annotated[
        int, Query(ge=1990, le=2200, description="The year it ends in: 2026 is FY 2025-26.")
    ],
) -> IncomeReconciliationsOut:
    """Where each Member's total package goes, and the Household's, from income statements."""
    result = service.income_reconciliation(session, financial_year)
    return IncomeReconciliationsOut(
        financial_year=financial_year,
        financial_year_label=fy_label(financial_year),
        members=[_reconciliation_out(name, r) for name, r in result.members],
        household=_reconciliation_out("Household", result.household)
        if result.household is not None
        else None,
    )


def _figure_out(f: pack_rules.FigureUsed) -> TaxFigureUsedOut:
    return TaxFigureUsedOut(
        key=f.key,
        name=f.name,
        financial_year=f.financial_year,
        financial_year_label=fy_label(f.financial_year),
        value=f.value,
        source=f.source,
        url=f.url,
        url_confirmed=f.url_confirmed,
        retrieved=f.retrieved,
        provisional=f.provisional_note is not None,
        provisional_note=f.provisional_note,
        rule_pack_version=f.rule_pack_version,
        origin="captured" if f.origin == "captured" else "shipped",
        capture_id=f.capture_id,
        quote=f.quote,
        captured_by=f.captured_by,
        captured_at=f.captured_at,
        shipped_value=f.shipped_value,
        method=f.method,  # type: ignore[arg-type]
    )


def _div293_out(d: service.Division293) -> TaxDiv293Out:
    return TaxDiv293Out(
        members=[
            TaxDiv293MemberOut(
                member_id=m.member_id,
                name=m.name,
                liability=amount_out(m.result.liability),
                applies=m.result.applies,
                div293_income=amount_out(m.result.div293_income),
                inputs=[
                    TaxDiv293InputOut(label=i.label, amount=amount_out(i.amount), source=i.source)
                    for i in m.inputs
                ],
                steps=[
                    TaxStepOut(
                        title=step.title,
                        operands=[_operand_out(o) for o in step.operands],
                        result=_operand_out(step.result),
                        note=step.note,
                    )
                    for step in m.result.steps
                ],
                contributions_source=m.contributions_source,
                received=amount_out(m.received),
                cap=amount_out(m.cap),
                cap_status=m.result.cap_status,
                cap_warning=_cap_warning(m),
                released=amount_out(m.released) if m.released is not None else None,
                released_source=m.released_source,
                notes=list(m.notes),
            )
            for m in d.members
        ],
        figures_used=[_figure_out(f) for f in d.figures],
        waiting_for=[
            TaxFigureWaitingOut(
                key=w.key,
                name=w.name,
                financial_year=w.financial_year,
                financial_year_label=fy_label(w.financial_year),
            )
            for w in d.waiting
        ],
    )


def _cap_warning(m: service.Div293Member) -> str | None:
    received, cap = amount_out(m.received), amount_out(m.cap)
    if m.result.cap_status == div293.OVER:
        return (
            f"Concessional contributions received ({received}) are over the cap ({cap}) by "
            f"{amount_out(m.result.excess_contributions)}. The excess is taken out of the low tax "
            "contributions here; excess contributions tax is not worked out."
        )
    if m.result.cap_status == div293.NEAR:
        return (
            f"Concessional contributions received ({received}) are within 10% of the cap ({cap})."
        )
    return None


def member_out(m: engine.MemberResult, name: str) -> TaxMemberOut:
    return TaxMemberOut(
        member_id=m.member_id,
        name=name,
        total_income=amount_out(m.total_income),
        total_deductions=amount_out(m.total_deductions),
        taxable_income=amount_out(m.taxable_income),
        tax_on_taxable_income=amount_out(m.tax_on_taxable_income),
        low_income_offset=amount_out(m.low_income_offset),
        small_business_offset=amount_out(m.small_business_offset),
        offsets=amount_out(m.offsets),
        medicare_levy=amount_out(m.medicare_levy),
        medicare_levy_surcharge=amount_out(m.medicare_levy_surcharge),
        surcharge_rate=m.surcharge_rate.to_percent(),
        help_repayment=amount_out(m.help_repayment),
        total_tax=amount_out(m.total_tax),
        credits=amount_out(m.credits),
        net_outcome=amount_out(m.net_outcome),
        notes=list(m.notes),
    )


def unmodelled_out(u: engine.Unmodelled, name: str) -> TaxUnmodelledOut:
    label = labels.LABELS[u.label_key]
    return TaxUnmodelledOut(
        member_id=u.member_id,
        name=name,
        label_key=u.label_key,
        return_item=label.return_item,
        label_name=label.name,
        amount=amount_out(u.value),
    )
