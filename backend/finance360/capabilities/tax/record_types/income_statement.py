"""The ``income_statement`` Record type: one employer, one Member, one year (ADR-0019 section 9).

The figures of an employer's income statement, in the ATO's own fields. Three kinds of field:

* **Return label figures** feed a Return label: gross payments as salary, tax withheld, allowances
  (Item 2), the lump sums that are not tax-free (Item 3 and the unmodelled labels beside it),
  reportable fringe benefits and reportable employer super.
* **Memo figures** feed the five memo labels that are on no return and drive the Income
  reconciliation (employer super, novated lease costs paid from pay, other deductions, net pay
  received and insurance premiums paid from the super balance).
* **Information-only figures** feed nothing. They are on the statement and worth keeping (the gross
  amount, bonuses, paid leave and salary sacrifice that make up the total gross amount; the tax-free
  lump sum D; the deductions total; the taxable-employer part of the fringe benefits), so a year can
  be compared with the last. A memo label would put them in the Label totals and the trace for no
  reader, so they are not labels.

Every figure is already an amount on the statement, so a Contribution is the figure itself:
nothing is scaled, so nothing rounds. Three short text facts (the employer and the two types) are
kept as text. Every field added after the first version is optional, so older payloads still parse.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import Any

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import (
    InputError,
    as_obj,
    as_text,
    money_in,
    reject_floats,
)
from finance360.capabilities.tax.record_types.base import (
    Contribution,
    Record,
    RecordRules,
    RecordType,
    check_label,
)
from finance360.core.money import BASE_CURRENCY, Money

KEY = "income_statement"

_ZERO = Money.zero(BASE_CURRENCY)

#: The most characters of the employer and of each type.
TEXT_MAX = 80


@dataclass(frozen=True)
class IncomeStatement:
    gross_payments: Money
    tax_withheld: Money
    employer_super: Money
    #: Net pay received. ``None`` is "not stated" (it comes from payslips or the bank, not the
    #: ATO's statement), which is not zero: the Income reconciliation then works take-home out.
    net_pay: Money | None = None
    reportable_fringe_benefits: Money = _ZERO
    reportable_employer_super: Money = _ZERO
    novated_lease_post_tax: Money = _ZERO
    other_deductions: Money = _ZERO
    super_insurance_premiums: Money = _ZERO
    # Income, Salary and wages: the parts of the total gross amount. Information only.
    gross_amount: Money = _ZERO
    bonuses_commissions: Money = _ZERO
    paid_leave_amount: Money = _ZERO
    salary_sacrifice_amount: Money = _ZERO
    # Lump sums. D is tax-free and for the record only.
    lump_sum_a: Money = _ZERO
    lump_sum_b: Money = _ZERO
    lump_sum_d: Money = _ZERO
    lump_sum_e: Money = _ZERO
    lump_sum_w: Money = _ZERO
    allowances: Money = _ZERO
    deductions_total: Money = _ZERO
    #: Part of the reportable fringe benefits total, for a taxable employer. Information only.
    reportable_fringe_benefits_taxable: Money = _ZERO
    #: Short text facts. Not money.
    employer: str = ""
    leave_payment_type: str = ""
    salary_sacrifice_type: str = ""


#: The fields that are text, not money.
TEXT_FIELDS: tuple[str, ...] = ("employer", "leave_payment_type", "salary_sacrifice_type")

#: Every money field, in dataclass order.
MONEY_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(IncomeStatement) if f.name not in TEXT_FIELDS
)

#: Payload field -> the Return label it feeds, in the order of the Contributions. The first is the
#: label the register shows the record against. These are always emitted, even at zero.
FEEDS: tuple[tuple[str, str], ...] = (
    ("gross_payments", "salary"),
    ("tax_withheld", "tax_withheld"),
    ("reportable_fringe_benefits", "reportable_fringe_benefits"),
    ("reportable_employer_super", "reportable_employer_super"),
    ("employer_super", "employer_super"),
    ("novated_lease_post_tax", "novated_lease_post_tax"),
    ("other_deductions", "payroll_other_deductions"),
    ("net_pay", "net_pay_received"),
    ("super_insurance_premiums", "super_insurance_premiums"),
)

#: Fields added with the ATO breakdown. They feed a label only when they have an amount, so a
#: statement that has none of them adds nothing new to the Label totals (and nothing to the
#: Income reconciliation, which reads none of these labels).
#:
#: Sources (ATO, Individual tax return instructions, "3 Employer lump sum payments" and
#: "24 Other income", and the myTax "Salary, wages, allowances, tips, bonuses etc." page):
#:
#: * allowances: Item 2, allowances, tips, director's fees.
#: * Lump sum A: Item 3 (the tax withheld from it goes at label R).
#: * Lump sum B: Item 3 label H (only 5% of it is taxable; the engine does not model Item 3, so the
#:   whole amount is carried and the estimate is marked incomplete).
#: * Lump sum E (payments in arrears): declared at Item 24 Other income, with a schedule, and may
#:   attract the lump sum payment in arrears tax offset. Item 24 is modelled for plain other income
#:   only, so E feeds its own unmodelled label.
#: * Lump sum W (return to work payments): no ATO return-instruction source was found for its item.
#:   It is assessable, so it feeds an unmodelled label with no item reference.
#: * Lump sum D (early retirement scheme and genuine redundancy): tax-free, not included in the
#:   return, so it feeds nothing.
OPTIONAL_FEEDS: tuple[tuple[str, str], ...] = (
    ("allowances", "allowances"),
    ("lump_sum_a", "employer_lump_sum"),
    ("lump_sum_b", "employer_lump_sum"),
    ("lump_sum_e", "lump_sum_arrears"),
    ("lump_sum_w", "return_to_work_payments"),
)

#: Figures the statement prints as a negative amount, kept as printed: salary sacrifice reduces the
#: total gross amount (gross + bonuses + paid leave + salary sacrifice). Information only: it feeds
#: no Return label.
NEGATIVE_OK = ("salary_sacrifice_amount",)

#: A statement must state these; the others default to nothing. Net pay may be left out: it is not
#: on the ATO statement.
REQUIRED = ("gross_payments", "tax_withheld", "employer_super")


def parse(raw: object) -> IncomeStatement:
    reject_floats(raw)
    body = as_obj(raw, "An income statement")
    known = [f.name for f in fields(IncomeStatement)]
    for key in body:
        if key not in known:
            raise InputError(f"An income statement has no '{key}' figure.")
    values: dict[str, Any] = {}
    for name in known:
        if name not in body:
            if name in REQUIRED:
                raise InputError(f"An income statement needs '{name}'.")
            continue
        if name in TEXT_FIELDS:
            text = as_text(body[name], name).strip()
            if len(text) > TEXT_MAX:
                raise InputError(f"'{name}' is at most {TEXT_MAX} characters.")
            values[name] = text
            continue
        amount = money_in(body[name], name)
        if amount.currency != BASE_CURRENCY:
            raise InputError(f"An income statement is in {BASE_CURRENCY}; enter {name} converted.")
        if amount.amount_minor < 0 and name not in NEGATIVE_OK:
            raise InputError(f"'{name}' is 0 or more.")
        values[name] = amount
    taxable = values.get("reportable_fringe_benefits_taxable", _ZERO)
    total = values.get("reportable_fringe_benefits", _ZERO)
    if taxable > total:
        raise InputError(
            "The taxable employer part of the reportable fringe benefits cannot be more than "
            "the reportable fringe benefits total."
        )
    for _, label_key in (*FEEDS, *OPTIONAL_FEEDS):  # every label it feeds must take this type
        check_label(label_key, KEY, (labels.MONEY,))
    return IncomeStatement(**values)


def to_payload(value: IncomeStatement) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(value):
        item = getattr(value, f.name)
        if item is None:
            continue
        out[f.name] = (
            item if f.name in TEXT_FIELDS else {"minor": item.amount_minor, "ccy": item.currency}
        )
    return out


def contributions(
    record: Record, financial_year: int, rules: RecordRules
) -> tuple[Contribution, ...]:
    value: IncomeStatement = record.value
    # Net pay is emitted when stated (even at zero), never when absent: "not stated" is not zero.
    always = tuple(
        Contribution(record.id, label, getattr(value, name))
        for name, label in FEEDS
        if getattr(value, name) is not None
    )
    optional = tuple(
        Contribution(record.id, label, getattr(value, name))
        for name, label in OPTIONAL_FEEDS
        if getattr(value, name).amount_minor
    )
    return always + optional


# --- compared with last year -------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One figure this year, last year and the signed change (this year minus last year)."""

    field: str
    current: Money
    previous: Money
    change: Money


def financial_year_of_span(from_on: date, to_on: date | None) -> int:
    """The year a statement belongs to: the one its last day falls in (1 July 2025 is FY 2026)."""
    day = to_on or from_on
    return day.year + 1 if day.month >= 7 else day.year


def match_key(employer: str, description: str) -> str:
    """How two years' statements are matched as the same employer's.

    A statement has no employer identity beyond what it is told: the ``employer`` text if given,
    otherwise its description (so a statement entered before the employer field existed still
    matches one with the same description). Case and runs of spaces do not matter.
    """
    return " ".join((employer.strip() or description).casefold().split())


def compare(current: IncomeStatement, previous: IncomeStatement) -> tuple[Change, ...]:
    """Every money figure of ``current`` beside ``previous``, in the statement's field order.

    Integer minor units throughout: the change is exact and signed (negative where the figure fell).
    """
    return tuple(
        Change(
            name,
            getattr(current, name) or _ZERO,
            getattr(previous, name) or _ZERO,
            (getattr(current, name) or _ZERO) - (getattr(previous, name) or _ZERO),
        )
        for name in MONEY_FIELDS
    )


TYPE = RecordType(KEY, "Income statement", parse, to_payload, lambda v: FEEDS[0][1], contributions)


def worked_out_take_home(value: IncomeStatement) -> Money:
    """Take-home for a statement that does not state net pay: the package minus the other slices,
    which is gross pay minus tax withheld, novated lease costs and other deductions (the employer
    super is in both the package and the super slice). Never below zero."""
    left = value.gross_payments - value.tax_withheld
    left = left - value.novated_lease_post_tax - value.other_deductions
    return left if left.amount_minor > 0 else _ZERO
