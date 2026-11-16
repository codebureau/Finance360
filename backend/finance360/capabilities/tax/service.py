"""The Tax service: the Tax record register, and an estimate built from it (ADR-0019 s2, s3, s5).

The only module in this Capability that touches the database, the Rule pack registry or the
Household. **Tax records** are entered per Member, edited in place and audited (entity type
``tax_record``); a **Label total** is computed from them on demand and never stored. An estimate
reads the Members' Label totals for the financial year from the register and the year's figures
from the Rule pack, then calculates; nothing is saved by it. The engine itself reads neither
(ADR-0019 s1).

An estimate is for exact financial years only. A year the pack has no (or only some) tax figures
for fails naming the year and how to capture them; it never borrows another year's. The register
needs no Rule pack at all.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.capabilities.library import service as library
from finance360.capabilities.position import super_statements
from finance360.capabilities.tax import (
    div293,
    engine,
    labels,
    link_kind,
    pack_rules,
    reconcile,
    record_types,
    whatif,
)
from finance360.capabilities.tax import input as tinput
from finance360.capabilities.tax import prefill as prefill_module
from finance360.capabilities.tax.input import (
    MAX_DEPENDANTS,
    TAX_INPUT_VERSION,
    MemberInput,
    Provenance,
    TaxInput,
    Total,
)
from finance360.capabilities.tax.record_types import car_kilometres as car_type
from finance360.capabilities.tax.record_types import depreciating_asset as asset_type
from finance360.capabilities.tax.record_types import home_office_hours as hours_type
from finance360.capabilities.tax.record_types import income_statement as statement_type
from finance360.capabilities.tax.record_types import line as line_type
from finance360.capabilities.tax.record_types.base import Contribution, Record, RecordRules, Step
from finance360.core import audit, rulepack, rulepack_store
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.income_types import TYPES as INCOME_TYPES
from finance360.core.models.account import Account
from finance360.core.models.income_stream import IncomeStream, IncomeStreamOwner
from finance360.core.models.library import DocumentLink
from finance360.core.models.tax_record import DESCRIPTION_MAX, NOTES_MAX, TaxRecord
from finance360.core.money import (
    BASE_CURRENCY,
    Money,
    MoneyError,
    ato_scale,
    format_amount,
    parse_amount,
)
from finance360.core.rate import PPM, Rate, RateError

#: The household uses the AU pack as a constant (ADR-0018 s1).
JURISDICTION = "au"
#: A reasonable window for a financial year typed into the request.
EARLIEST_FY = 1990
LATEST_FY = 2200
#: Dates a record may carry: far enough to be a typing slip when outside.
EARLIEST_DATE = date(1990, 7, 1)
LATEST_DATE = date(2200, 6, 30)


@dataclass(frozen=True)
class YearSetup:
    financial_year: int
    label: str
    #: False when the pack holds only some of the figures an estimate needs.
    complete: bool
    missing: tuple[str, ...]
    provisional: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Setup:
    #: The registered pack's version label, or ``None`` when there is no pack for the jurisdiction.
    pack_version: str | None
    #: Every financial year with at least one tax figure, oldest first.
    years: tuple[YearSetup, ...]


@dataclass(frozen=True)
class Div293Line:
    """One input to a Member's Division 293 working: its value and where it came from."""

    label: str
    amount: Money
    source: str


@dataclass(frozen=True)
class Div293Member:
    member_id: int
    name: str
    result: div293.Div293Result
    #: The inputs, each with its value and source, in the order the working uses them.
    inputs: tuple[Div293Line, ...]
    cap: Money
    #: Concessional contributions received by the fund, before the cap.
    received: Money
    #: ``statement``, ``income_stream`` or ``none``: where the contributions came from.
    contributions_source: str
    #: Division 293 released from super, from the Super statement; ``None`` without one stating it.
    released: Money | None
    released_source: str | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Division293:
    members: tuple[Div293Member, ...]
    #: The Rule pack figures the measure read, with where each came from.
    figures: tuple[pack_rules.FigureUsed, ...]
    #: The figures the pack lacks for the year. While any, there is no Division 293 working.
    waiting: tuple[pack_rules.FigureWaiting, ...]


@dataclass(frozen=True)
class Estimate:
    input: TaxInput
    result: engine.TaxResult
    member_names: dict[int, str]
    rule_pack_version: str
    division_293: Division293


def _pack(session: Session) -> rulepack.RulePack | None:
    """The Rule pack as this instance sees it: the shipped one with its captured figures over it
    (ADR-0022). Nothing in this Capability reads the registered pack directly."""
    return rulepack_store.effective_pack(session, JURISDICTION)


def setup(session: Session) -> Setup:
    """What the screen needs before anything is entered: the years the pack can estimate."""
    pack = _pack(session)
    if pack is None:
        return Setup(None, ())
    years = tuple(
        YearSetup(
            fy,
            rulepack.fy_label(fy),
            not (missing := pack_rules.missing_keys(pack, fy)),
            missing,
            pack_rules.provisional_notes(pack, fy),
        )
        for fy in pack_rules.tax_years(pack)
    )
    return Setup(pack.version_label, years)


# --- the register: reads --------------------------------------------------------------------


@dataclass(frozen=True)
class AssetView:
    """A depreciating asset's worksheet: its schedule, computed now and never stored."""

    asset: asset_type.DepreciatingAsset
    #: The effective life as entered, in years (``"4"`` or ``"2.5"``).
    effective_life: str
    #: One row per financial year from the start: the effective life's years and the year after
    #: (a part-year start), or to the end date's year if sooner. Empty when a figure is missing.
    rows: tuple[asset_type.YearRow, ...]
    #: The Rule pack keys the schedule needs and the pack lacks (so the schedule is empty).
    missing: tuple[str, ...]
    #: The diminishing value percent the schedule used (``None`` if not captured).
    multiplier_percent: int | None = None


@dataclass(frozen=True)
class HomeOfficeRowView:
    """One month of the worksheet with its working: home days and hours."""

    row: hours_type.MonthRow
    #: Working days less leave and office days, as decimal text (``None`` for a casual month).
    home_days: str | None
    #: Home days x hours per day, exact (two decimal places, more only when needed).
    hours: str


@dataclass(frozen=True)
class HomeOfficeView:
    """A home office worksheet's working: the rows, the total hours, the Rule pack rate and the
    deduction. Computed now and never stored."""

    arrangement: str
    #: Hours per day as decimal text (``None`` for a casual record).
    hours_per_day: str | None
    #: A regular record's pattern: the weekdays at home, or the office days a month (decimal text).
    home_weekdays: tuple[str, ...] | None
    office_days_per_month: str | None
    rows: tuple[HomeOfficeRowView, ...]
    #: The sum of the rows' hours, exact.
    total_hours: str
    #: The fixed rate per hour of the year the record sits in (``None`` if not captured).
    rate: Money | None
    #: Total hours x rate, rounded once (``None`` while the rate is missing).
    deduction: Money | None
    #: The Rule pack keys the deduction needs and the pack lacks (then nothing is counted).
    missing: tuple[str, ...]


@dataclass(frozen=True)
class CarTripView:
    """One trip of the schedule with its working: kilometres x times."""

    trip: car_type.Trip
    #: One trip's kilometres, exact (one decimal place).
    km: str
    #: Kilometres x times, exact (one decimal place).
    total_km: str


@dataclass(frozen=True)
class CarView:
    """A car kilometres record's working: the trips, the totals, the Rule pack rate and limit and
    the deduction. Computed now and never stored."""

    trips: tuple[CarTripView, ...]
    #: The sum of the trips' kilometres x times, exact.
    total_km: str
    #: The Rule pack limit per car for the year, in whole kilometres (``None`` if not captured).
    limit_km: int | None
    #: The kilometres that count: the total, limited to the limit (``None`` while it is missing).
    counted_km: str | None
    #: Whether the total is over the limit, so some kilometres do not count.
    capped: bool
    #: The Rule pack rate per kilometre for the year (``None`` if not captured).
    rate: Money | None
    #: Kilometres counted x rate, rounded once (``None`` while a figure is missing).
    deduction: Money | None
    #: The Rule pack keys the deduction needs and the pack lacks (then nothing is counted).
    missing: tuple[str, ...]


@dataclass(frozen=True)
class PreviousYear:
    """The previous financial year's statement for the same Member and employer, with the change
    in each money figure (this year minus last year)."""

    record_id: int
    description: str
    financial_year: int
    changes: tuple[statement_type.Change, ...]


@dataclass(frozen=True)
class RecordView:
    """A stored record with its parsed value, so the screen can say what it contributes."""

    row: TaxRecord
    member_name: str | None
    record: Record
    #: The label the record is shown against (the first it feeds; a statement feeds several).
    label: labels.Label
    #: What it adds in the year asked about. For a depreciating asset with no year asked, none.
    contributions: tuple[Contribution, ...]
    #: The work-use share, for a line or a depreciating asset that has one.
    work_use: Rate | None
    #: The amount as entered, for a line (the gross payments, for an income statement; the cost,
    #: for a depreciating asset).
    entered: Money | None
    #: The parsed figures, for an income statement.
    statement: statement_type.IncomeStatement | None = None
    #: The worksheet, for a depreciating asset.
    asset: AssetView | None = None
    #: The working, for a home office schedule.
    home_office: HomeOfficeView | None = None
    #: The official figures the record used, with where each came from.
    figures_used: tuple[pack_rules.FigureUsed, ...] = ()
    #: The figures it needs and the pack lacks for the year.
    waiting_for: tuple[pack_rules.FigureWaiting, ...] = ()
    #: The record's working as exact steps (empty for a type with no calculation).
    steps: tuple[Step, ...] = ()
    #: The trips and totals, for a car kilometres record.
    car: CarView | None = None
    #: For an income statement opened on its own page: last year's statement, if any (see
    #: ``previous_year``).
    previous_year: PreviousYear | None = None

    @property
    def counted(self) -> Total | None:
        return self.contributions[0].value if self.contributions else None


def _payload(row: TaxRecord) -> Any:
    try:
        return tinput.loads(row.payload_json)
    except (
        tinput.InputError
    ) as error:  # a stored payload is ours: a float means it was edited by hand
        raise ValueError(f"Tax record {row.id} has an invalid payload: {error}") from None


def _record(row: TaxRecord) -> Record:
    record_type = record_types.TYPES.get(row.record_type)
    if record_type is None:
        raise ValueError(f"Tax record {row.id} has the unknown type {row.record_type!r}.")
    return Record(
        row.id,
        row.member_id,
        row.record_type,
        row.from_on,
        row.to_on,
        record_type.parse(_payload(row)),
    )


def _members(session: Session) -> dict[int, str]:
    return {m.id: m.name for m in household.list_members(session)}


def _view_of(session: Session, row: TaxRecord) -> RecordView:
    """One record as the register shows it, with the Rule pack figures it reads."""
    pack = _pack(session)
    return _view(row, _members(session), pack_rules.record_rules(pack), pack)


def _asset_view(record: Record, rules: RecordRules) -> AssetView:
    asset: asset_type.DepreciatingAsset = record.value
    missing = asset_type.missing_figures(record, rules)
    percent = asset_type.multiplier_for(record, rules)
    rows: tuple[asset_type.YearRow, ...] = ()
    if not missing:
        rows = asset_type.full_schedule(record, percent or 0)
    return AssetView(asset, asset_type.life_text(asset.effective_life), rows, missing, percent)


def _home_office_view(record: Record, rules: RecordRules) -> HomeOfficeView:
    sheet: hours_type.HomeOfficeHours = record.value
    rate = hours_type.rate_for(record, rules)
    casual = sheet.arrangement == hours_type.CASUAL
    rows = tuple(
        HomeOfficeRowView(
            r,
            None if casual else hours_type.hundredths_text(r.home),
            hours_type.hours_text(hours_type.row_hours(r, sheet.hours_per_day)),
        )
        for r in sheet.months
    )
    return HomeOfficeView(
        sheet.arrangement,
        hours_type.hundredths_text(sheet.hours_per_day)
        if sheet.hours_per_day is not None
        else None,
        sheet.home_weekdays,
        hours_type.hundredths_text(sheet.office_days_per_month)
        if sheet.office_days_per_month is not None
        else None,
        rows,
        hours_type.hours_text(hours_type.total_hours(sheet)),
        rate,
        hours_type.deduction(sheet, rate) if rate is not None else None,
        hours_type.missing_figures(record, rules),
    )


def _car_view(record: Record, rules: RecordRules) -> CarView:
    value: car_type.CarKilometres = record.value
    limit = car_type.limit_for(record, rules)
    rate = car_type.rate_for(record, rules)
    total = car_type.total_tenths(value)
    return CarView(
        tuple(
            CarTripView(t, car_type.km_text(t.tenths), car_type.km_text(t.total))
            for t in value.trips
        ),
        car_type.km_text(total),
        limit,
        car_type.km_text(car_type.counted_tenths(value, limit)) if limit is not None else None,
        limit is not None and total > limit * car_type.TENTHS,
        rate,
        car_type.deduction(value, rate, limit) if rate is not None and limit is not None else None,
        car_type.missing_figures(record, rules),
    )


def _view(
    row: TaxRecord,
    names: dict[int, str],
    rules: RecordRules,
    pack: rulepack.RulePack | None,
    financial_year: int | None = None,
) -> RecordView:
    """The record with what it adds to ``financial_year``. A depreciating asset adds a different
    amount each year, so with no year asked it shows none (its worksheet has them all)."""
    record = _record(row)
    asset = record.value if isinstance(record.value, asset_type.DepreciatingAsset) else None
    contributions = (
        ()
        if asset is not None and financial_year is None
        else record_types.contributions_of(record, financial_year, rules)
    )
    label = labels.LABELS[record_types.label_of(record)]
    work_use = (
        record.value.work_use
        if isinstance(record.value, line_type.Line | asset_type.DepreciatingAsset)
        else None
    )
    entered = record.value.amount if isinstance(record.value, line_type.Line) else None
    statement = record.value if isinstance(record.value, statement_type.IncomeStatement) else None
    if statement is not None:
        entered = statement.gross_payments
    if asset is not None:
        entered = asset.cost
    member_name = names.get(row.member_id) if row.member_id is not None else None
    reads = record_types.figures_read(record)
    return RecordView(
        row,
        member_name,
        record,
        label,
        contributions,
        work_use,
        entered,
        statement,
        _asset_view(record, rules) if asset is not None else None,
        _home_office_view(record, rules)
        if isinstance(record.value, hours_type.HomeOfficeHours)
        else None,
        pack_rules.figures_used(pack, reads),
        pack_rules.figures_waiting(pack, reads, record_types.missing_figures(record, rules)),
        record_types.calculation_of(record, rules),
        _car_view(record, rules) if isinstance(record.value, car_type.CarKilometres) else None,
    )


def get_record(session: Session, record_id: int) -> RecordView:
    row = session.get(TaxRecord, record_id)
    if row is None:
        raise NotFoundError(f"Tax record {record_id} does not exist.")
    view = _view_of(session, row)
    if view.statement is None:
        return view
    return replace(view, previous_year=previous_year(session, row, view.statement))


def previous_year(
    session: Session, row: TaxRecord, current: statement_type.IncomeStatement
) -> PreviousYear | None:
    """The statement of the financial year before ``row``'s, for the same Member and employer.

    An employer is identified by the statement's ``employer`` text, or by its description when it
    has none (``statement_type.match_key``). Of several matches the one that starts latest wins
    (then the newest record). Nothing is returned where there is no such statement.
    """
    if row.member_id is None:
        return None
    year = statement_type.financial_year_of_span(row.from_on, row.to_on)
    key = statement_type.match_key(current.employer, row.description)
    candidates = session.scalars(
        select(TaxRecord)
        .where(
            TaxRecord.member_id == row.member_id,
            TaxRecord.record_type == record_types.INCOME_STATEMENT,
            TaxRecord.id != row.id,
        )
        .order_by(TaxRecord.from_on.desc(), TaxRecord.id.desc())
    )
    for other in candidates:
        if statement_type.financial_year_of_span(other.from_on, other.to_on) != year - 1:
            continue
        value = _record(other).value
        if statement_type.match_key(value.employer, other.description) == key:
            return PreviousYear(
                other.id,
                other.description,
                year - 1,
                statement_type.compare(current, value),
            )
    return None


def _matches(view: RecordView, query: str) -> bool:
    haystack = " ".join(
        part
        for part in (
            view.row.description,
            view.row.notes,
            view.member_name,
            view.label.key,
            view.label.name,
            view.label.return_item,
        )
        if part
    )
    return all(word in haystack.casefold() for word in query.casefold().split())


def list_records(
    session: Session,
    *,
    member_id: int | None = None,
    financial_year: int | None = None,
    label: str | None = None,
    query: str | None = None,
) -> list[RecordView]:
    """The register, newest first, optionally narrowed to a Member, a financial year (a record
    whose span overlaps it), a Return label and words found in its description, notes, Member or
    label. Narrowing is by AND."""
    statement = select(TaxRecord)
    if member_id is not None:
        statement = statement.where(TaxRecord.member_id == member_id)
    rows = session.scalars(statement.order_by(TaxRecord.from_on.desc(), TaxRecord.id.desc()))
    names = _members(session)
    pack = _pack(session)
    rules = pack_rules.record_rules(pack)
    views: list[RecordView] = []
    for row in rows:
        view = _view(row, names, rules, pack, financial_year)
        if financial_year is not None and not record_types.overlaps(view.record, financial_year):
            continue
        if label and view.label.key != label:
            continue
        if query and query.strip() and not _matches(view, query):
            continue
        views.append(view)
    return views


def label_totals(
    session: Session, financial_year: int, *, member_id: int | None = None
) -> list[record_types.LabelTotal]:
    """The Label totals of the register for a financial year, by Member then in label order."""
    statement = select(TaxRecord)
    if member_id is not None:
        statement = statement.where(TaxRecord.member_id == member_id)
    records = [_record(row) for row in session.scalars(statement.order_by(TaxRecord.id))]
    totals = record_types.label_totals(
        records, financial_year, pack_rules.record_rules(_pack(session))
    )
    order = {label.key: i for i, label in enumerate(labels.all_labels())}
    return sorted(
        totals.values(),
        key=lambda t: (t.member_id if t.member_id is not None else 0, order[t.label]),
    )


@dataclass(frozen=True)
class Uncounted:
    """A record that overlaps the year but is left out of its Label total, because a Rule pack
    figure it needs is missing."""

    record_id: int
    member_id: int | None
    description: str
    label: labels.Label
    #: The Rule pack keys it needs and the pack lacks.
    keys: tuple[str, ...]
    #: The financial year the figure is wanted for (the year the asset started in).
    figure_year: int


def _uncounted_message(left_out: Sequence[Uncounted]) -> str:
    """Why an estimate will not calculate: records whose Rule pack figure is missing."""
    keys = sorted({key for u in left_out for key in u.keys})
    years = sorted({u.figure_year for u in left_out})
    names = "; ".join(f"'{u.description}'" for u in left_out)
    return (
        f"{names} {'needs' if len(left_out) == 1 else 'need'} the Rule pack figure "
        f"{', '.join(keys)} for FY {', '.join(rulepack.fy_label(y) for y in years)}, and an "
        "estimate would otherwise leave the deduction out. Capture it on Rule figures "
        f"(FY {rulepack.fy_label(years[0])})."
    )


def _uncounted_of(
    records: Sequence[Record],
    descriptions: dict[int, str],
    financial_year: int,
    rules: RecordRules,
) -> list[Uncounted]:
    return [
        Uncounted(
            record.id,
            record.member_id,
            descriptions[record.id],
            labels.LABELS[record_types.label_of(record)],
            keys,
            asset_type.financial_year_of(record.from_on),
        )
        for record, keys in record_types.unavailable(records, financial_year, rules)
    ]


def uncounted_records(
    session: Session, financial_year: int, *, member_id: int | None = None
) -> list[Uncounted]:
    """The records that cannot be counted yet, so a total is never read as complete when it is
    not. Empty when every figure a record needs is in the Rule pack."""
    statement = select(TaxRecord)
    if member_id is not None:
        statement = statement.where(TaxRecord.member_id == member_id)
    rows = {row.id: row for row in session.scalars(statement.order_by(TaxRecord.id))}
    return _uncounted_of(
        [_record(row) for row in rows.values()],
        {rid: row.description for rid, row in rows.items()},
        financial_year,
        pack_rules.record_rules(_pack(session)),
    )


# --- the Income reconciliation (ADR-0019 section 9) ------------------------------------------


@dataclass(frozen=True)
class Reconciliation:
    financial_year: int
    #: One per Member who has an income statement in the year, in the Household's Member order.
    members: tuple[tuple[str, reconcile.IncomeReconciliation], ...]
    #: The Household: its Members' figures summed. ``None`` when no Member has a statement.
    household: reconcile.IncomeReconciliation | None


def income_reconciliation(session: Session, financial_year: int) -> Reconciliation:
    """Where the total package goes, from the year's income statements only (so a salary entered
    as a plain line is not reconciled). Computed now from the Records; nothing is stored."""
    if not EARLIEST_FY <= financial_year <= LATEST_FY:
        raise ValidationError(
            [FieldError("financial_year", f"Enter a year from {EARLIEST_FY} to {LATEST_FY}.")]
        )
    rows = session.scalars(
        select(TaxRecord)
        .where(TaxRecord.record_type == record_types.INCOME_STATEMENT)
        .order_by(TaxRecord.id)
    )
    records = [_record(row) for row in rows]
    totals = record_types.label_totals(records, financial_year)
    # Take-home of the statements that state no net pay, worked out one statement at a time.
    zero = Money.zero(BASE_CURRENCY)
    worked_out: dict[int, Money] = {}
    for record in records:
        if (
            record.member_id is not None
            and record_types.overlaps(record, financial_year)
            and record.value.net_pay is None
        ):
            worked_out[record.member_id] = worked_out.get(
                record.member_id, zero
            ) + statement_type.worked_out_take_home(record.value)
    by_member: dict[int, dict[str, Money]] = {}
    for (member_id, label), total in totals.items():
        if member_id is not None and isinstance(total.total, Money):
            by_member.setdefault(member_id, {})[label] = total.total
    names = _members(session)
    ordered = [m for m in names if m in by_member]
    members = tuple(
        (names[m], reconcile.reconcile(by_member[m], m, worked_out.get(m, zero))) for m in ordered
    )
    household = (
        reconcile.reconcile_household(
            [by_member[m] for m in ordered], [worked_out.get(m, zero) for m in ordered]
        )
        if ordered
        else None
    )
    return Reconciliation(financial_year, members, household)


# --- the register: audit snapshots ----------------------------------------------------------


def _snapshot(row: TaxRecord) -> dict[str, Any]:
    """The record for the Audit trail: its columns, then its payload's fields spread out so the
    trail reads field by field (money as ``{minor, ccy}``, the work-use share as a percent text)."""
    payload = dict(_payload(row))
    share = payload.pop("work_use_ppm", None)
    snapshot: dict[str, Any] = {
        "member_id": row.member_id,
        "record_type": row.record_type,
        "from_on": row.from_on.isoformat(),
        "to_on": row.to_on.isoformat() if row.to_on else None,
        "description": row.description,
        "notes": row.notes,
        **payload,
    }
    if share is not None:
        snapshot["work_use_percent"] = Rate(share).to_percent()
    return snapshot


def _summary(view: RecordView) -> str:
    if view.home_office is not None:
        what = f"{view.home_office.total_hours} hours against {view.label.name}"
    elif view.car is not None:
        what = f"{view.car.total_km} km against {view.label.name}"
    elif view.entered is not None:
        what = f"{format_amount(view.entered)} against {view.label.name}"
    else:
        what = view.label.name
    return f"'{view.row.description}' ({what})"


# --- the register: validation ---------------------------------------------------------------


@dataclass(frozen=True)
class RecordInput:
    """What the household states. Amounts are decimal text in AUD (never a float); the work-use
    share is a percent text."""

    member_id: int | None
    record_type: str
    from_on: date
    description: str
    #: The Return label, for a ``line`` or a ``fact`` (an income statement feeds several).
    label: str = ""
    to_on: date | None = None
    notes: str | None = None
    #: A ``line``: the amount, and the part of it used for work (``"60"`` is 60%). A
    #: ``depreciating_asset``: the cost, and the same work-use share.
    amount: str | None = None
    work_use_percent: str | None = None
    #: A ``depreciating_asset``: the effective life in years (decimal text), and whether to deduct
    #: the work-use share of the cost in full in the year it started.
    effective_life_years: str | None = None
    claim_in_full: bool | None = None
    #: A ``home_office_hours``: the ``arrangement`` (``"regular"`` or ``"casual"``). Regular: the
    #: hours worked per home day (decimal text) and exactly one of ``home_weekdays`` and
    #: ``office_days_per_month`` (decimal text); each month is ``{"month": "2025-07",
    #: "working_days", "leave_days", "office_days"}`` in decimal text. Casual: each month is
    #: ``{"month", "hours"}`` and there is no hours per day or pattern. Both dates are required and
    #: fall in one financial year.
    arrangement: str | None = None
    hours_per_day: str | None = None
    home_weekdays: list[str] | None = None
    office_days_per_month: str | None = None
    months: list[dict[str, str]] | None = None
    #: A ``car_kilometres``: each trip is ``{"description", "km", "times", "on"}`` in text (``km``
    #: with at most one decimal place; ``times`` a whole number, blank for 1; ``on`` an optional
    #: ISO date within the record's dates). Both dates are required and fall in one financial year,
    #: and a Member has one such record for a financial year.
    trips: list[dict[str, str]] | None = None
    #: A ``fact``: ``True``/``False`` for a flag, a number of days.
    value: bool | int | None = None
    #: An ``income_statement``: each figure of ``statement_type.IncomeStatement`` as decimal text.
    statement: dict[str, str] | None = None


@dataclass(frozen=True)
class _Checked:
    member_id: int
    record_type: str
    from_on: date
    to_on: date | None
    description: str
    notes: str | None
    payload_json: str


def _line_payload(given: RecordInput, errors: list[FieldError]) -> dict[str, Any] | None:
    payload: dict[str, Any] = {"label": given.label}
    ok = True
    if given.amount is None or not given.amount.strip():
        errors.append(FieldError("amount", "Enter an amount."))
        ok = False
    else:
        try:
            amount = parse_amount(given.amount, BASE_CURRENCY)
        except MoneyError as error:
            errors.append(FieldError("amount", str(error)))
            ok = False
        else:
            if amount.amount_minor < 0:
                errors.append(FieldError("amount", "Enter 0 or more."))
                ok = False
            payload["amount"] = {"minor": amount.amount_minor, "ccy": amount.currency}
    share = (given.work_use_percent or "").strip()
    if share:
        try:
            rate = Rate.from_percent(share)
        except (RateError, TypeError) as error:
            errors.append(FieldError("work_use_percent", str(error)))
            ok = False
        else:
            if not 0 <= rate.ppm <= PPM:
                errors.append(FieldError("work_use_percent", "Enter a share from 0% to 100%."))
                ok = False
            payload["work_use_ppm"] = rate.ppm
    return payload if ok else None


def _asset_payload(given: RecordInput, errors: list[FieldError]) -> dict[str, Any] | None:
    payload: dict[str, Any] = {"label": given.label, "claim_in_full": bool(given.claim_in_full)}
    ok = True
    if given.amount is None or not given.amount.strip():
        errors.append(FieldError("amount", "Enter the cost."))
        ok = False
    else:
        try:
            cost = parse_amount(given.amount, BASE_CURRENCY)
        except MoneyError as error:
            errors.append(FieldError("amount", str(error)))
            ok = False
        else:
            if cost.amount_minor <= 0:
                errors.append(FieldError("amount", "Enter a cost more than 0."))
                ok = False
            payload["cost"] = {"minor": cost.amount_minor, "ccy": cost.currency}
    try:
        payload["effective_life_years"] = asset_type.life_text(
            asset_type.parse_life((given.effective_life_years or "").strip())
        )
    except tinput.InputError as error:
        errors.append(FieldError("effective_life_years", str(error)))
        ok = False
    share = (given.work_use_percent or "").strip() or "100"
    try:
        rate = Rate.from_percent(share)
    except (RateError, TypeError) as error:
        errors.append(FieldError("work_use_percent", str(error)))
        ok = False
    else:
        if not 0 < rate.ppm <= PPM:
            errors.append(FieldError("work_use_percent", "Enter a share above 0% and up to 100%."))
            ok = False
        payload["work_use_ppm"] = rate.ppm
    return payload if ok else None


def _one_year(given: RecordInput, what: str, errors: list[FieldError]) -> bool:
    """Both dates are given and fall in one financial year (``what`` names the record type)."""
    if given.to_on is None:
        errors.append(FieldError("to_on", "Enter the last day of the period."))
        return False
    if given.to_on >= given.from_on and hours_type.financial_year_of(
        given.to_on
    ) != hours_type.financial_year_of(given.from_on):
        year = hours_type.financial_year_of(given.from_on)
        errors.append(
            FieldError(
                "to_on",
                f"A {what} record sits within one financial year. Enter a record for each "
                f"year: this one runs from 1 July {year - 1} to 30 June {year}.",
            )
        )
        return False
    return True


def _hours_payload(given: RecordInput, errors: list[FieldError]) -> dict[str, Any] | None:
    ok = _one_year(given, "home office", errors)
    payload: dict[str, Any] = {"label": given.label}
    arrangement = given.arrangement
    if arrangement not in hours_type.ARRANGEMENTS:
        errors.append(FieldError("arrangement", "Choose regular or casual."))
        return None
    payload["arrangement"] = arrangement
    if not _hours_pattern(given, arrangement, payload, errors):
        ok = False
    rows = given.months or []
    if not rows:
        errors.append(FieldError("months", "Add at least one month."))
        return None
    if len(rows) > hours_type.MAX_MONTHS:
        errors.append(
            FieldError("months", f"A worksheet has at most {hours_type.MAX_MONTHS} months.")
        )
        return None
    first = (given.from_on.year, given.from_on.month)
    last = (given.to_on.year, given.to_on.month) if given.to_on is not None else None
    seen: set[str] = set()
    checked: list[dict[str, str]] = []
    for index, raw in enumerate(rows):
        row = _month_row(index, raw, arrangement, errors)
        if row is None:
            ok = False
            continue
        month = row["month"]
        name = hours_type.month_name(month)
        if month in seen:
            errors.append(FieldError(f"months.{index}.month", f"{name} is entered twice."))
            ok = False
        seen.add(month)
        when = (int(month[:4]), int(month[5:]))
        if when < first or (last is not None and when > last):
            errors.append(
                FieldError(f"months.{index}.month", f"{name} is outside the record's dates.")
            )
            ok = False
        checked.append(row)
    payload["months"] = checked
    return payload if ok else None


def _hours_pattern(
    given: RecordInput, arrangement: str, payload: dict[str, Any], errors: list[FieldError]
) -> bool:
    """The record-level fields of the arrangement, added to ``payload`` as canonical text. A regular
    record has the hours per day and exactly one of the weekdays or the office days a month; a
    casual record has none of the three."""
    if arrangement == hours_type.CASUAL:
        ok = True
        for field, present in (
            ("hours_per_day", (given.hours_per_day or "").strip() != ""),
            ("home_weekdays", given.home_weekdays is not None),
            ("office_days_per_month", (given.office_days_per_month or "").strip() != ""),
        ):
            if present:
                errors.append(FieldError(field, "A casual record does not have this."))
                ok = False
        return ok
    ok = True
    try:
        per_day = hours_type.parse_decimal(
            (given.hours_per_day or "").strip(), "hours per day", hours_type.MAX_HOURS_PER_DAY
        )
        if per_day <= 0:
            raise tinput.InputError("The hours per day are more than 0.")
        payload["hours_per_day"] = hours_type.hundredths_text(per_day)
    except tinput.InputError as error:
        errors.append(FieldError("hours_per_day", str(error)))
        ok = False
    monthly = (given.office_days_per_month or "").strip()
    if given.home_weekdays is not None and monthly:
        errors.append(
            FieldError(
                "home_weekdays",
                "Give the weekdays at home or the office days a month, not both.",
            )
        )
        return False
    try:
        if given.home_weekdays is not None:
            payload["home_weekdays"] = list(hours_type.parse_weekdays(given.home_weekdays))
        elif monthly:
            payload["office_days_per_month"] = hours_type.hundredths_text(
                hours_type.parse_decimal(monthly, "office days a month", hours_type.MAX_DAYS)
            )
        else:
            errors.append(
                FieldError(
                    "home_weekdays",
                    "Choose the weekdays usually worked at home, or give the office days a month.",
                )
            )
            ok = False
    except tinput.InputError as error:
        field = "home_weekdays" if given.home_weekdays is not None else "office_days_per_month"
        errors.append(FieldError(field, str(error)))
        ok = False
    return ok


_DAY_FIELDS = ("working_days", "leave_days", "office_days")


def _month_row(
    index: int, raw: dict[str, str], arrangement: str, errors: list[FieldError]
) -> dict[str, str] | None:
    """One worksheet row with each field as canonical text, or ``None`` with its errors collected
    against ``months.<index>.<field>``. A row has the shape of the arrangement."""
    where = f"months.{index}"
    ok = True
    month = str(raw.get("month", "")).strip()
    try:
        hours_type.parse_row({"month": month, **dict.fromkeys(_DAY_FIELDS, "0")})
    except tinput.InputError as error:
        errors.append(FieldError(f"{where}.month", str(error)))
        ok = False
    if arrangement == hours_type.CASUAL:
        return _casual_row(where, month, raw, ok, errors)
    if (raw.get("hours") or "").strip():
        errors.append(FieldError(f"{where}.hours", "A regular month holds days, not hours."))
        ok = False
    days: dict[str, int] = {}
    for key in _DAY_FIELDS:
        # Leave and office days left blank are none; the working days must be entered.
        text = (raw.get(key) or "").strip() or ("" if key == "working_days" else "0")
        try:
            days[key] = hours_type.parse_decimal(text, key.replace("_", " "), hours_type.MAX_DAYS)
        except tinput.InputError as error:
            errors.append(FieldError(f"{where}.{key}", str(error)))
            ok = False
    if not ok:
        return None
    if days["working_days"] - days["leave_days"] - days["office_days"] < 0:
        errors.append(
            FieldError(
                f"{where}.home_days",
                f"{hours_type.month_name(month)}: leave and office days are more than the "
                "working days.",
            )
        )
        return None
    return {
        "month": month,
        **{key: hours_type.hundredths_text(value) for key, value in days.items()},
    }


def _casual_row(
    where: str, month: str, raw: dict[str, str], ok: bool, errors: list[FieldError]
) -> dict[str, str] | None:
    """A casual row: the month and the actual hours worked from home (a blank is an error here:
    the screen leaves a blank month out)."""
    for key in _DAY_FIELDS:
        if (raw.get(key) or "").strip():
            errors.append(
                FieldError(f"{where}.{key}", "A casual month holds only the hours worked.")
            )
            ok = False
    try:
        hours = hours_type.parse_decimal(
            (raw.get("hours") or "").strip(), "hours", hours_type.MAX_MONTH_HOURS
        )
    except tinput.InputError as error:
        errors.append(FieldError(f"{where}.hours", str(error)))
        return None
    return {"month": month, "hours": hours_type.hundredths_text(hours)} if ok else None


def _car_trip(
    index: int, raw: dict[str, str], given: RecordInput, errors: list[FieldError]
) -> dict[str, Any] | None:
    """One trip with each field as canonical text, or ``None`` with its errors collected against
    ``trips.<index>.<field>``."""
    where = f"trips.{index}"
    ok = True
    row: dict[str, Any] = {}
    description = raw.get("description", "")
    km = raw.get("km", "")
    times = raw.get("times", "1")
    on = raw.get("on", "")
    try:
        row["description"] = car_type.parse_description(description)
    except tinput.InputError as error:
        errors.append(FieldError(f"{where}.description", str(error)))
        ok = False
    try:
        row["km"] = car_type.km_text(car_type.parse_km(km))
    except tinput.InputError as error:
        errors.append(FieldError(f"{where}.km", str(error)))
        ok = False
    try:
        row["times"] = car_type.parse_times(times)
    except tinput.InputError as error:
        errors.append(FieldError(f"{where}.times", str(error)))
        ok = False
    try:
        day = car_type.parse_on(on)
    except tinput.InputError as error:
        errors.append(FieldError(f"{where}.on", str(error)))
        ok = False
    else:
        if day is not None:
            if day < given.from_on or (given.to_on is not None and day > given.to_on):
                errors.append(FieldError(f"{where}.on", "The date is outside the record's dates."))
                ok = False
            row["on"] = day.isoformat()
    return row if ok else None


def _other_car_record(
    session: Session, given: RecordInput, record_id: int | None
) -> TaxRecord | None:
    """The Member's other car kilometres record in the same financial year, if there is one."""
    year = car_type.financial_year_of(given.from_on)
    rows = session.scalars(
        select(TaxRecord).where(
            TaxRecord.member_id == given.member_id,
            TaxRecord.record_type == record_types.CAR_KILOMETRES,
        )
    )
    return next(
        (r for r in rows if r.id != record_id and car_type.financial_year_of(r.from_on) == year),
        None,
    )


def _car_payload(
    session: Session, given: RecordInput, record_id: int | None, errors: list[FieldError]
) -> dict[str, Any] | None:
    ok = _one_year(given, "car kilometres", errors)
    if given.member_id is not None and _other_car_record(session, given, record_id) is not None:
        year = rulepack.fy_label(car_type.financial_year_of(given.from_on))
        errors.append(
            FieldError(
                "member_id",
                f"This Member already has car kilometres for FY {year}: add trips to that record.",
            )
        )
        ok = False
    rows = given.trips or []
    if not rows:
        errors.append(FieldError("trips", "Add at least one trip."))
        return None
    if len(rows) > car_type.MAX_TRIPS:
        errors.append(FieldError("trips", f"A record has at most {car_type.MAX_TRIPS} trips."))
        return None
    trips: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        trip = _car_trip(index, raw, given, errors)
        if trip is None:
            ok = False
        else:
            trips.append(trip)
    return {"label": given.label, "trips": trips} if ok else None


def _fact_payload(given: RecordInput, errors: list[FieldError]) -> dict[str, Any] | None:
    label = labels.get(given.label)
    kind = label.kind if label else labels.FLAG
    value = given.value
    if kind == labels.FLAG:
        if not isinstance(value, bool):
            errors.append(FieldError("value", "Choose yes or no."))
            return None
    elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(FieldError("value", "Enter a whole number of days, 0 or more."))
        return None
    return {"label": given.label, "value": value}


def _statement_payload(given: RecordInput, errors: list[FieldError]) -> dict[str, Any] | None:
    figures = given.statement or {}
    payload: dict[str, Any] = {}
    ok = True
    for name in (f.name for f in fields(statement_type.IncomeStatement)):
        text = (figures.get(name) or "").strip()
        if name in statement_type.TEXT_FIELDS:
            if len(text) > statement_type.TEXT_MAX:
                errors.append(
                    FieldError(name, f"Enter at most {statement_type.TEXT_MAX} characters.")
                )
                ok = False
            elif text:
                payload[name] = text
            continue
        if not text:
            if name in statement_type.REQUIRED:
                errors.append(FieldError(name, "Enter an amount (0 if there was none)."))
                ok = False
            continue
        try:
            amount = parse_amount(text, BASE_CURRENCY)
        except MoneyError as error:
            errors.append(FieldError(name, str(error)))
            ok = False
            continue
        if amount.amount_minor < 0 and name not in statement_type.NEGATIVE_OK:
            errors.append(FieldError(name, "Enter 0 or more."))
            ok = False
        payload[name] = {"minor": amount.amount_minor, "ccy": amount.currency}
    return payload if ok else None


def _check(session: Session, given: RecordInput, record_id: int | None = None) -> _Checked:
    errors: list[FieldError] = []
    description = given.description.strip()
    if not description:
        errors.append(FieldError("description", "Description is required."))
    elif len(description) > DESCRIPTION_MAX:
        errors.append(
            FieldError("description", f"Description must be at most {DESCRIPTION_MAX} characters.")
        )
    notes = (given.notes or "").strip() or None
    if notes is not None and len(notes) > NOTES_MAX:
        errors.append(FieldError("notes", f"Notes must be at most {NOTES_MAX} characters."))

    member_id = given.member_id or 0
    if given.member_id is None:
        errors.append(FieldError("member_id", "Choose a Member."))
    elif given.member_id not in {m.id for m in household.list_members(session)}:
        errors.append(FieldError("member_id", "This Member does not exist."))

    for field, day in (("from_on", given.from_on), ("to_on", given.to_on)):
        if day is not None and not EARLIEST_DATE <= day <= LATEST_DATE:
            errors.append(FieldError(field, f"Enter a date from {EARLIEST_DATE} to {LATEST_DATE}."))
    if given.to_on is not None and given.to_on < given.from_on:
        errors.append(FieldError("to_on", "The end date can't be before the start date."))

    record_type = record_types.TYPES.get(given.record_type)
    label = labels.get(given.label)
    payload: dict[str, Any] | None = None
    if record_type is None:
        errors.append(
            FieldError(
                "record_type",
                f"Record type '{given.record_type}' is not available. "
                f"Choose one of: {', '.join(t.name for t in record_types.TYPES.values())}.",
            )
        )
    elif record_type.key == record_types.INCOME_STATEMENT:
        payload = _statement_payload(given, errors)
    elif label is None:
        errors.append(FieldError("label", "Choose a Return label."))
    elif record_type.key not in label.record_types:
        errors.append(
            FieldError("label", f"'{label.name}' does not take a {record_type.name} record.")
        )
    else:
        if record_type.key == record_types.LINE:
            payload = _line_payload(given, errors)
        elif record_type.key == record_types.DEPRECIATING_ASSET:
            payload = _asset_payload(given, errors)
        elif record_type.key == record_types.HOME_OFFICE_HOURS:
            payload = _hours_payload(given, errors)
        elif record_type.key == record_types.CAR_KILOMETRES:
            payload = _car_payload(session, given, record_id, errors)
        else:
            payload = _fact_payload(given, errors)

    canonical = ""
    if payload is not None and record_type is not None and not errors:
        try:
            canonical = json.dumps(
                record_type.to_payload(record_type.parse(payload)),
                sort_keys=True,
                separators=(",", ":"),
            )
        except tinput.InputError as error:
            errors.append(FieldError("body", str(error)))
    if errors:
        raise ValidationError(errors)
    return _Checked(
        member_id, given.record_type, given.from_on, given.to_on, description, notes, canonical
    )


def _apply(row: TaxRecord, c: _Checked) -> None:
    row.member_id = c.member_id
    row.record_type = c.record_type
    row.from_on = c.from_on
    row.to_on = c.to_on
    row.description = c.description
    row.notes = c.notes
    row.payload_json = c.payload_json


# --- the register: writes -------------------------------------------------------------------


def create_record(session: Session, context: AuditContext, given: RecordInput) -> RecordView:
    c = _check(session, given)
    row = TaxRecord(created_at=datetime.now(UTC).replace(tzinfo=None))
    _apply(row, c)
    session.add(row)
    session.flush()
    view = _view_of(session, row)
    audit.record(
        session,
        context,
        entity_type="tax_record",
        entity_id=row.id,
        action="create",
        after=_snapshot(row),
        summary=f"Added Tax record {_summary(view)}",
    )
    return view


def update_record(
    session: Session, context: AuditContext, record_id: int, given: RecordInput
) -> RecordView:
    row = session.get(TaxRecord, record_id)
    if row is None:
        raise NotFoundError(f"Tax record {record_id} does not exist.")
    c = _check(session, given, record_id)
    before = _snapshot(row)
    _apply(row, c)
    session.flush()
    after = _snapshot(row)
    view = _view_of(session, row)
    if after != before:
        audit.record(
            session,
            context,
            entity_type="tax_record",
            entity_id=row.id,
            action="update",
            before=before,
            after=after,
            summary=f"Edited Tax record {_summary(view)}",
        )
    return view


def delete_record(session: Session, context: AuditContext, record_id: int) -> None:
    """Remove a record. A Label total is computed on demand, so it simply no longer includes it.

    Its Document links go with it, and the Audit trail records which documents were linked. The
    documents stay in the Library, unlinked (another record may use them, and one may be the only
    copy of a statement; a document is deleted only by an explicit action, ADR-0026 section 2)."""
    row = session.get(TaxRecord, record_id)
    if row is None:
        raise NotFoundError(f"Tax record {record_id} does not exist.")
    before = _snapshot(row)
    attached = library.documents_of(session, link_kind.KEY, record_id)
    if attached:
        before["evidence"] = [library.snapshot(a.document) for a in attached]
    summary = f"Deleted Tax record {_summary(_view_of(session, row))}"
    library.remove_links_to(session, link_kind.KEY, record_id)
    session.delete(row)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="tax_record",
        entity_id=record_id,
        action="delete",
        before=before,
        summary=summary,
    )


# --- Member delete guard --------------------------------------------------------------------


def member_delete_guard(session: Session, member_id: int) -> str | None:
    """Block deleting a Member who has Tax records (ADR-0009)."""
    held = session.scalar(
        select(func.count()).select_from(TaxRecord).where(TaxRecord.member_id == member_id)
    )
    return "they have Tax records" if held else None


def register() -> None:
    """Register ``tax``'s rule with ``household``. Idempotent; call at app start."""
    household.register_member_delete_guard(member_delete_guard)


# --- the estimate ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Built:
    """An estimate before Division 293: the pinned input, its result and how it was made."""

    input: TaxInput
    result: engine.TaxResult
    member_names: dict[int, str]
    pack: rulepack.RulePack
    #: The Label totals the estimate used (after any overrides), each with its Contributions.
    totals: tuple[record_types.LabelTotal, ...]
    #: What each contributing record is called. A hypothetical record has a negative id.
    descriptions: dict[int, str]
    #: Each override and what it did, as JSON-ready data (for the saved estimate's provenance).
    effects: tuple[dict[str, Any], ...]
    #: Every Income stream line considered for the base, counted or not, with its reason.
    prefill: tuple[prefill_module.PrefillLine, ...] = ()


def out_value(value: Total) -> Any:
    return tinput.money_out(value) if isinstance(value, Money) else value


def record_input_to_json(given: RecordInput) -> dict[str, Any]:
    """A record as the register takes it, as JSON-ready data (ISO dates, no empty fields)."""
    out: dict[str, Any] = {}
    for item in fields(given):
        value = getattr(given, item.name)
        if value is None or (value == "" and item.name in ("label", "notes")):
            continue
        out[item.name] = value.isoformat() if isinstance(value, date) else value
    return out


def record_input_from_json(raw: dict[str, Any]) -> RecordInput:
    """The inverse of ``record_input_to_json``; a bad or unknown field is a ``ValueError``."""
    known = {item.name for item in fields(RecordInput)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"A hypothetical record has no field {unknown[0]!r}.")
    body = dict(raw)
    try:
        for name in ("from_on", "to_on"):
            if body.get(name) is not None:
                body[name] = date.fromisoformat(body[name])
        return RecordInput(**body)
    except (TypeError, ValueError) as error:
        raise ValueError(str(error)) from None


def _hypothetical(
    session: Session, override: whatif.AddRecord, index: int, record_id: int
) -> tuple[Record, str]:
    """A hypothetical record as a ``Record`` with a negative id, checked as the register checks."""
    try:
        given = record_input_from_json(dict(override.record))
    except ValueError as error:
        raise ValidationError([FieldError(f"overrides[{index}]", str(error))]) from None
    try:
        checked = _check(session, given)
    except ValidationError as error:
        raise ValidationError(
            [FieldError(f"overrides[{index}].{e.field}", e.message) for e in error.errors]
        ) from None
    record_type = record_types.TYPES[checked.record_type]
    value = record_type.parse(tinput.loads(checked.payload_json))
    record = Record(
        record_id, checked.member_id, checked.record_type, checked.from_on, checked.to_on, value
    )
    return record, checked.description


def _contribution_out(items: Sequence[Contribution]) -> list[dict[str, Any]]:
    return [{"label": c.label, "value": out_value(c.value)} for c in items]


def _resolve(
    session: Session,
    overrides: Sequence[whatif.Override],
    members: set[int],
    record_ids: set[int],
    outside: str = "This Member is not part of the estimate.",
) -> tuple[list[tuple[Record, str]], list[FieldError]]:
    """Check each override against the Household and the register, as far as it can be checked
    without a Rule pack, and build the hypothetical records (with negative ids) with their
    descriptions. Every problem is collected against its override."""
    added: list[tuple[Record, str]] = []
    errors: list[FieldError] = []
    for index, item in enumerate(overrides):
        field = f"overrides[{index}]"
        if isinstance(item, whatif.ExcludeRecord) and item.record_id not in record_ids:
            errors.append(FieldError(field, f"Tax record {item.record_id} does not exist."))
        elif (
            isinstance(item, whatif.SetTotal | whatif.ExcludeLabel)
            and item.member_id not in members
        ):
            errors.append(FieldError(field, outside))
        elif isinstance(item, whatif.AddRecord):
            try:
                record, description = _hypothetical(session, item, index, -(len(added) + 1))
            except ValidationError as error:
                errors.extend(error.errors)
                continue
            if record.member_id not in members:
                errors.append(FieldError(f"{field}.member_id", outside))
                continue
            added.append((record, description))
    return added, errors


def check_overrides(session: Session, overrides: Sequence[whatif.Override]) -> list[FieldError]:
    """The problems a Tax what-if's overrides have now, against the Household and the register."""
    members = {m.id for m in household.list_members(session)}
    rows = set(session.scalars(select(TaxRecord.id)))
    return _resolve(session, overrides, members, rows, "This Member does not exist.")[1]


def build(
    session: Session,
    *,
    financial_year: int,
    dependants: int,
    member_ids: Sequence[int],
    overrides: Sequence[whatif.Override] = (),
    whatif_name: str | None = None,
) -> Built:
    """Work out the tax position of the given Members for one financial year, without saving.

    Each Member's Label totals come from the Tax register: records whose span overlaps the year,
    then any ``overrides`` of a Tax what-if applied (records excluded or added first, then the
    label-level ``set`` and ``exclude_label``). Every problem is collected, each against its own
    field, and raised together as one ``ValidationError`` (the API's 422 body).
    """
    errors: list[FieldError] = []
    if not EARLIEST_FY <= financial_year <= LATEST_FY:
        errors.append(
            FieldError("financial_year", f"Enter a year from {EARLIEST_FY} to {LATEST_FY}.")
        )
    if not 0 <= dependants <= MAX_DEPENDANTS:
        errors.append(
            FieldError("dependants", f"Enter a number of children from 0 to {MAX_DEPENDANTS}.")
        )
    if not 1 <= len(member_ids) <= tinput.MAX_MEMBERS:
        errors.append(FieldError("members", f"Enter one or {tinput.MAX_MEMBERS} Members."))
    if errors:
        raise ValidationError(errors)

    pack = _pack(session)
    if pack is None:
        raise ValidationError([FieldError("financial_year", "There is no Rule pack for tax.")])
    try:
        rules = pack_rules.build_rules(pack, financial_year)
    except pack_rules.TaxRulesError as error:
        raise ValidationError([FieldError("financial_year", str(error))]) from None

    known = {m.id: m for m in household.list_members(session)}
    seen: set[int] = set()
    for member_id in member_ids:
        if member_id not in known:
            errors.append(FieldError("members", f"Member {member_id} does not exist."))
        elif member_id in seen:
            errors.append(FieldError("members", "Enter each Member once."))
        seen.add(member_id)
    if errors:
        raise ValidationError(errors)

    rows = {row.id: row for row in session.scalars(select(TaxRecord).order_by(TaxRecord.id))}
    records = [_record(row) for row in rows.values()]
    descriptions = {rid: row.description for rid, row in rows.items()}
    record_rules = pack_rules.record_rules(pack)

    # Records first: an excluded one leaves, a hypothetical one joins (with a negative id).
    excluded = whatif.excluded_records(overrides)
    added, problems = _resolve(session, overrides, seen, set(rows))
    if problems:
        raise ValidationError(problems)
    descriptions.update({record.id: f"{text} (hypothetical)" for record, text in added})
    kept = [r for r in records if r.id not in excluded] + [record for record, _ in added]

    left_out = [
        u
        for u in _uncounted_of(kept, descriptions, financial_year, record_rules)
        if u.member_id in seen
    ]
    if left_out:
        raise ValidationError([FieldError("financial_year", _uncounted_message(left_out))])

    computed = record_types.label_totals(kept, financial_year, record_rules)
    # Income streams prefill the base where the register has no records for that income.
    lines = prefill_module.decide(
        prefill_module.forecast(_stream_facts(session, seen), financial_year, seen),
        computed.keys(),
    )
    for (member_id, label), amount in prefill_module.totals(lines).items():
        computed[(member_id, label)] = record_types.LabelTotal(member_id, label, amount, ())
    totals, replaced = whatif.apply_labels(computed, overrides)
    effects = _effects(
        overrides,
        records,
        [record for record, _ in added],
        replaced,
        financial_year,
        record_rules,
        descriptions,
    )

    by_member: dict[int, list[tuple[str, Total]]] = {member_id: [] for member_id in seen}
    final: list[record_types.LabelTotal] = []
    order = {label.key: i for i, label in enumerate(labels.all_labels())}
    for key in sorted(totals, key=lambda k: (k[0] if k[0] is not None else 0, order[k[1]])):
        total = totals[key]
        if labels.LABELS[total.label].direction == labels.MEMO:
            continue  # on no return: it feeds a view, not the estimate
        if total.member_id in by_member:
            by_member[total.member_id].append((total.label, total.total))
            final.append(total)
    inputs = [
        MemberInput(member_id, known[member_id].date_of_birth, tuple(held))
        for member_id, held in by_member.items()
    ]

    names = {member_id: known[member_id].name for member_id in seen}
    extra: list[Provenance] = []
    if whatif_name is not None:
        extra.append(Provenance("whatif", whatif_name))
        extra.extend(
            Provenance("override", _effect_text(effect, names, number))
            for number, effect in enumerate(effects, start=1)
        )
    try:
        tax_input = TaxInput(
            input_version=TAX_INPUT_VERSION,
            financial_year=financial_year,
            members=tuple(inputs),
            dependants=dependants,
            rules=rules,
            provenance=(
                Provenance("rule_pack", pack.version_label),
                *(
                    Provenance(
                        "captured_figure", f"{key} FY {rulepack.fy_label(financial_year)}: #{cid}"
                    )
                    for key, cid in pack_rules.captured_in_use(pack, financial_year)
                ),
                Provenance("totals", "register"),
                *(
                    Provenance(
                        "prefill"
                        if line.status == prefill_module.COUNTED
                        else "prefill_not_counted",
                        f"{names[line.member_id]}: {line.text}",
                    )
                    for line in lines
                ),
                *extra,
            ),
        )
    except tinput.InputError as error:
        raise ValidationError([FieldError("body", str(error))]) from None
    result = engine.estimate(tax_input)
    return Built(tax_input, result, names, pack, tuple(final), descriptions, effects, lines)


def _stream_facts(session: Session, member_ids: set[int]) -> list[prefill_module.StreamFacts]:
    """The Income streams owned by any of the Members, as plain facts for the prefill."""
    rows = session.scalars(
        select(IncomeStream)
        .join(IncomeStreamOwner, IncomeStreamOwner.stream_id == IncomeStream.id)
        .where(IncomeStreamOwner.member_id.in_(member_ids))
        .order_by(IncomeStream.id)
        .distinct()
    )
    return [
        prefill_module.StreamFacts(
            row.id,
            row.name,
            row.type_key,
            row.gross_annual,
            tuple((o.member_id, o.share_bp) for o in row.owners),
            row.start_on,
            row.end_on,
            row.start_event is not None or row.end_event is not None,
        )
        for row in rows
    ]


def _effects(
    overrides: Sequence[whatif.Override],
    records: Sequence[Record],
    added: Sequence[Record],
    replaced: dict[tuple[int, str], Total | None],
    financial_year: int,
    record_rules: RecordRules,
    descriptions: dict[int, str],
) -> tuple[dict[str, Any], ...]:
    """What each override did, in JSON-ready data: the figure it replaced, the Contributions it
    removed or the ones it added. Copied into the saved estimate, so the effect stays readable."""
    by_id = {r.id: r for r in records}
    adding = iter(added)
    out: list[dict[str, Any]] = []
    for item in overrides:
        detail = whatif.override_payload(item)
        if isinstance(item, whatif.SetTotal | whatif.ExcludeLabel):
            was = replaced.get((item.member_id, item.label))
            detail["replaced"] = None if was is None else out_value(was)
        elif isinstance(item, whatif.ExcludeRecord):
            record = by_id[item.record_id]
            detail["description"] = descriptions[item.record_id]
            detail["removed"] = _contribution_out(
                record_types.contributions_of(record, financial_year, record_rules)
                if record_types.overlaps(record, financial_year)
                else ()
            )
        else:
            record = next(adding)
            detail["record_id"] = record.id
            detail["description"] = descriptions[record.id]
            detail["member_id"] = record.member_id
            detail["added"] = _contribution_out(
                record_types.contributions_of(record, financial_year, record_rules)
                if record_types.overlaps(record, financial_year)
                else ()
            )
        out.append(detail)
    return tuple(out)


def _effect_text(effect: dict[str, Any], names: dict[int, str], number: int) -> str:
    """One override in words, for the input's provenance."""
    op = effect["op"]
    who = names.get(effect.get("member_id", 0), "")
    label = labels.LABELS[effect["label"]].name if "label" in effect else ""
    if op == whatif.SET:
        return f"{number}. Set {who}'s {label}"
    if op == whatif.EXCLUDE_LABEL:
        return f"{number}. Left out {who}'s {label}"
    if op == whatif.EXCLUDE_RECORD:
        return f"{number}. Left out record: {effect['description']}"
    return f"{number}. Added record: {effect['description']}"


def estimate(
    session: Session,
    *,
    financial_year: int,
    dependants: int,
    member_ids: Sequence[int],
) -> Estimate:
    """Calculate the tax position of the given Members for one financial year.

    Each Member's Label totals come from the Tax register: records whose span overlaps the year.
    Every problem is collected, each against its own field, and raised together as one
    ``ValidationError`` (the API's 422 body).
    """
    built = build(
        session, financial_year=financial_year, dependants=dependants, member_ids=member_ids
    )
    return Estimate(
        built.input,
        built.result,
        built.member_names,
        built.pack.version_label,
        _division_293(session, built.pack, built.result, built.member_names),
    )


# --- Division 293 --------------------------------------------------------------

DIV293_THRESHOLD = "tax.div293.threshold"
DIV293_RATE = "tax.div293.rate"
CONCESSIONAL_CAP = "tax.super.concessional_cap"
SG_RATE = "sg_rate"
STATEMENT = "statement"
INCOME_STATEMENT = "income_statement"
INCOME_STREAM = "income_stream"
NO_SOURCE = "none"


@dataclass(frozen=True)
class _Contributions:
    amount: Money
    kind: str
    lines: tuple[Div293Line, ...]
    notes: tuple[str, ...]


def _div293_rules(pack: rulepack.RulePack, fy: int) -> div293.Div293Rules | None:
    held = pack.years.get(fy, {})
    threshold, rate, cap = (
        held.get(DIV293_THRESHOLD),
        held.get(DIV293_RATE),
        held.get(CONCESSIONAL_CAP),
    )
    if not (isinstance(threshold, Money) and isinstance(rate, Rate) and isinstance(cap, Money)):
        return None
    if threshold.currency != BASE_CURRENCY or cap.currency != BASE_CURRENCY:
        return None
    return div293.Div293Rules(threshold, rate, cap)


def _statement_years(
    session: Session, fy: int, member_id: int
) -> list[tuple[Account, super_statements.SuperYear]]:
    """The Member's Super statements for ``fy``: those of an Account they solely own."""
    mine: list[tuple[Account, super_statements.SuperYear]] = []
    for year in super_statements.list_super_years(session, financial_year=fy):
        account = session.get(Account, year.account_id)
        if (
            account is not None
            and len(account.owners) == 1
            and account.owners[0].member_id == member_id
        ):
            mine.append((account, year))
    return mine


def _statement_contributions(session: Session, fy: int, member_id: int) -> list[Div293Line]:
    """One line per Income statement of the Member that overlaps ``fy``: its employer super plus
    its reportable employer super (salary sacrifice), which together are what the employer paid
    in. Reportable employer super is already in them, so it is not added to the income again."""
    rows = session.scalars(
        select(TaxRecord)
        .where(
            TaxRecord.member_id == member_id,
            TaxRecord.record_type == record_types.INCOME_STATEMENT,
        )
        .order_by(TaxRecord.from_on, TaxRecord.id)
    )
    lines: list[Div293Line] = []
    for row in rows:
        record = _record(row)
        if not record_types.overlaps(record, fy):
            continue
        value: statement_type.IncomeStatement = record.value
        payer = value.employer.strip() or row.description
        lines.append(
            Div293Line(
                f"Income statement: {payer}, employer super + reportable employer super",
                value.employer_super + value.reportable_employer_super,
                f"Income statement '{row.description}', FY {rulepack.fy_label(fy)}: employer "
                f"super {format_amount(value.employer_super)} + reportable employer super "
                f"{format_amount(value.reportable_employer_super)}",
            )
        )
    return lines


def _contributions(
    session: Session, pack: rulepack.RulePack, fy: int, member_id: int
) -> _Contributions:
    """Concessional contributions received in ``fy``, from the first source that has them, each
    named: the Super statements where they state it (the fund's own figure, by date received),
    then the Member's Income statements (employer super plus reportable employer super, by period
    worked), then the Income streams' employer super. Sources are never added together."""
    label = rulepack.fy_label(fy)
    zero = Money(0, BASE_CURRENCY)
    notes: list[str] = []
    mine = _statement_years(session, fy, member_id)
    stated = [(a, y) for a, y in mine if y.concessional_received is not None]
    if stated:
        lines = tuple(
            Div293Line(
                f"Concessional contributions received: {a.name}",
                y.concessional_received or zero,
                f"Super statement for {a.name}, FY {label}",
            )
            for a, y in stated
        )
        for a, y in mine:
            if y.concessional_received is None:
                notes.append(
                    f"The Super statement for {a.name} does not state concessional "
                    "contributions received, so it adds nothing here."
                )
        return _Contributions(_sum(lines), STATEMENT, lines, tuple(notes))
    for a, _y in mine:
        notes.append(
            f"The Super statement for {a.name} (FY {label}) does not state concessional "
            "contributions received, so another source is used instead."
        )
    from_statements = _statement_contributions(session, fy, member_id)
    if from_statements:
        notes.append(
            "These are the employer's figures by period worked, not by date received: a June "
            "quarter payment the fund receives in July counts in the next year on the fund's "
            "figure, so the estimate may differ until the Super statement is entered."
        )
        notes.append(
            "Personal contributions claimed as a deduction are not on the Income statement, so "
            "they are not counted here."
        )
        return _Contributions(
            _sum(from_statements), INCOME_STATEMENT, tuple(from_statements), tuple(notes)
        )
    sg = pack.years.get(fy, {}).get(SG_RATE)
    year_start, year_end = date(fy - 1, 7, 1), date(fy, 6, 30)
    streams = session.scalars(
        select(IncomeStream)
        .join(IncomeStreamOwner, IncomeStreamOwner.stream_id == IncomeStream.id)
        .where(IncomeStreamOwner.member_id == member_id)
        .order_by(IncomeStream.id)
    )
    found: list[Div293Line] = []
    for stream in streams:
        kind = INCOME_TYPES.get(stream.type_key)
        if kind is None or not kind.employer_super:
            continue
        if (stream.start_on and stream.start_on > year_end) or (
            stream.end_on and stream.end_on < year_start
        ):
            continue
        if not isinstance(sg, Rate):
            notes.append(f"The Rule pack holds no Superannuation Guarantee rate for FY {label}.")
            break
        found.append(
            Div293Line(
                f"Employer super: {stream.name}",
                ato_scale(stream.gross_annual, sg.ppm, PPM),
                f"Income stream {stream.name}: gross {format_amount(stream.gross_annual)} "
                f"x Superannuation Guarantee rate {sg.to_percent()}% (Rule pack {SG_RATE}, "
                f"FY {label})",
            )
        )
    if not found:
        notes.append(
            "There is no Super statement stating concessional contributions, no Income statement "
            "and no Income stream paying employer super for this Member and year, so none are "
            "counted."
        )
        return _Contributions(zero, NO_SOURCE, (), tuple(notes))
    notes.append(
        "Salary sacrifice into super is not in the Income stream, so it is not counted here."
    )
    return _Contributions(_sum(found), INCOME_STREAM, tuple(found), tuple(notes))


def _sum(lines: Sequence[Div293Line]) -> Money:
    return Money(sum(line.amount.amount_minor for line in lines), BASE_CURRENCY)


def _released(session: Session, fy: int, member_id: int) -> tuple[Money | None, str | None]:
    """Division 293 released from super in ``fy``, from the Super statements that state it."""
    stated = [
        (a, y.division_293_released)
        for a, y in _statement_years(session, fy, member_id)
        if y.division_293_released is not None
    ]
    if not stated:
        return None, None
    total = Money(sum(m.amount_minor for _, m in stated if m), BASE_CURRENCY)
    return total, "Super statement: " + ", ".join(a.name for a, _ in stated)


def _division_293(
    session: Session,
    pack: rulepack.RulePack,
    result: engine.TaxResult,
    names: dict[int, str],
) -> Division293:
    """The full Division 293 measure for each Member, with its working.

    It reads the pack itself rather than through ``TaxRules``, so an estimate still works for a
    year without the Division 293 figures; the measure then waits for them.
    """
    fy = result.financial_year
    reads = [(k, fy) for k in (DIV293_THRESHOLD, DIV293_RATE, CONCESSIONAL_CAP)]
    held = pack.years.get(fy, {})
    rules = _div293_rules(pack, fy)
    if rules is None:
        missing = [k for k, _ in reads if k not in held]
        return Division293((), (), pack_rules.figures_waiting(pack, reads, missing))
    figures = pack_rules.figures_used(pack, reads)
    label = rulepack.fy_label(fy)
    by_key = {f.key: f for f in figures}
    members: list[Div293Member] = []
    for m in result.members:
        got = _contributions(session, pack, fy, m.member_id)
        calc = div293.calculate(
            div293.Div293Input(
                m.member_id,
                fy,
                m.taxable_income,
                m.reportable_fringe_benefits,
                m.net_investment_losses,
                got.amount,
                rules,
            )
        )
        released, released_source = _released(session, fy, m.member_id)
        inputs = (
            Div293Line(
                "Taxable income",
                m.taxable_income,
                f"This estimate, from the Tax records for FY {label}",
            ),
            Div293Line(
                "Reportable fringe benefits",
                m.reportable_fringe_benefits,
                f"This estimate, from the Tax records for FY {label} (the Income statement)",
            ),
            Div293Line(
                "Net investment losses",
                m.net_investment_losses,
                "This estimate: investment deductions over investment income, plus any net "
                "rental property loss",
            ),
            *got.lines,
            Div293Line(
                "Concessional contributions cap",
                rules.cap,
                f"Rule pack {CONCESSIONAL_CAP}, FY {label}: {by_key[CONCESSIONAL_CAP].source}",
            ),
            Div293Line(
                "Division 293 threshold",
                rules.threshold,
                f"Rule pack {DIV293_THRESHOLD}, FY {label}: {by_key[DIV293_THRESHOLD].source}",
            ),
        )
        members.append(
            Div293Member(
                m.member_id,
                names[m.member_id],
                calc,
                inputs,
                rules.cap,
                got.amount,
                got.kind,
                released,
                released_source,
                got.notes,
            )
        )
    return Division293(tuple(members), figures, ())


# --- fixture --------------------------------------------------------------------------------

FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


def load_fixture_tax_records(session: Session, data_dir: Path) -> int:
    """Create the fixture's Tax records through the service, once, while there are none.

    Reads ``tax_records`` from ``<data_dir>/household.json``: each entry names its ``member``, the
    ``record_type``, ``from_on`` (and ``to_on``), ``description``, ``label`` and, for a line, the
    ``amount`` and an optional ``work_use_percent``, for a fact, its ``value``, for an
    ``income_statement``, its ``statement`` (each figure as an amount) or, for a
    ``depreciating_asset``, its cost as ``amount``, ``effective_life_years``, an optional
    ``work_use_percent`` and ``claim_in_full`` or, for ``home_office_hours``, its
    ``arrangement``, ``hours_per_day``, ``home_weekdays`` or ``office_days_per_month``, and
    ``months`` or, for ``car_kilometres``, its ``trips``.
    """
    path = data_dir / household.FIXTURE_FILENAME
    if not path.is_file():
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("tax_records", [])
    if not entries or session.scalar(select(func.count()).select_from(TaxRecord)):
        return 0
    ids = {m.name: m.id for m in household.list_members(session)}
    for e in entries:
        create_record(
            session,
            FIXTURE_CONTEXT,
            RecordInput(
                member_id=ids[e["member"]],
                record_type=e["record_type"],
                from_on=date.fromisoformat(e["from_on"]),
                to_on=date.fromisoformat(e["to_on"]) if e.get("to_on") else None,
                description=e["description"],
                notes=e.get("notes"),
                label=e.get("label", ""),
                amount=e.get("amount"),
                work_use_percent=e.get("work_use_percent"),
                effective_life_years=e.get("effective_life_years"),
                claim_in_full=e.get("claim_in_full"),
                arrangement=e.get("arrangement"),
                hours_per_day=e.get("hours_per_day"),
                home_weekdays=e.get("home_weekdays"),
                office_days_per_month=e.get("office_days_per_month"),
                months=e.get("months"),
                trips=e.get("trips"),
                value=e.get("value"),
                statement=e.get("statement"),
            ),
        )
    return len(entries)


def load_fixture_tax_evidence(session: Session, data_dir: Path) -> int:
    """Add the fixture's evidence files to the Library and link them, once, while nothing is linked.

    Reads ``tax_evidence`` from ``<data_dir>/household.json``: each entry names the ``member`` and
    ``record`` (the Tax record's description), the ``file`` (relative to the data directory) and
    its ``media_type``. Goes through the Library service, so a document is added and then linked to
    its Tax record, as an upload is. Run after ``load_fixture_tax_records``.
    """
    path = data_dir / household.FIXTURE_FILENAME
    if not path.is_file():
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("tax_evidence", [])
    if not entries or session.scalar(select(func.count()).select_from(DocumentLink)):
        return 0
    ids = {m.name: m.id for m in household.list_members(session)}
    done = 0
    for e in entries:
        record_id = session.scalars(
            select(TaxRecord.id).where(
                TaxRecord.member_id == ids[e["member"]], TaxRecord.description == e["record"]
            )
        ).first()
        source = data_dir / e["file"]
        if record_id is None or not source.is_file():
            continue
        added = library.add_document(
            session,
            FIXTURE_CONTEXT,
            data_dir,
            file_name=source.name,
            media_type=e["media_type"],
            content=source.read_bytes(),
        )
        library.link(session, FIXTURE_CONTEXT, added.document.id, link_kind.KEY, record_id)
        done += 1
    return done
