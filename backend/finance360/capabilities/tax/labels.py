"""The Return label registry (ADR-0019 section 2). Pure data.

A **Return label** is a registered item of the tax return that a Record type contributes to, with a
value kind of money, days or flag. **Every return item the product knows about is registered,
modelled or not**: the engine (``engine.py``) reads only the ``modelled`` ones, and any label with
a non-zero total that it does not model is reported and marks the estimate incomplete, never
ignored.

The return item references (``1``, ``D5``) follow the individual tax return. Those of the unmodelled
items were written from memory of the current return and are to be checked against it.

Only the ``line`` Record type exists to feed money labels until a richer one replaces it, and
``fact`` feeds flags and days (ADR-0019 section 2). Tax records themselves are a later slice; the
declaration is here so the registry is complete.
"""

from __future__ import annotations

from dataclasses import dataclass

MONEY = "money"
DAYS = "days"
FLAG = "flag"
KINDS = (MONEY, DAYS, FLAG)

INCOME = "income"
DEDUCTION = "deduction"
OFFSET = "offset"
CREDIT = "credit"
FACT = "fact"
MEMO = "memo"
DIRECTIONS = (INCOME, DEDUCTION, OFFSET, CREDIT, FACT, MEMO)

#: Where the screen groups a label. The order is the order shown.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("employment", "Employment"),
    ("investment", "Investment income"),
    ("capital_gains", "Capital gains"),
    ("rental", "Rental property"),
    ("business", "Sole trader"),
    ("other_income", "Other income"),
    ("deductions", "Deductions"),
    ("medicare", "Medicare, health cover and HELP"),
    ("income_statement", "Income statement figures (not on the return)"),
    ("other_items", "Other return items (not modelled)"),
)


@dataclass(frozen=True)
class Label:
    key: str
    #: The return item reference (``1``, ``D5``); empty where the return has none.
    return_item: str
    #: Plain-language name and description, so a person (or an AI) can find it.
    name: str
    description: str
    direction: str
    kind: str
    #: Whether the engine reads it. An unmodelled label with a figure makes an estimate incomplete.
    modelled: bool
    #: Which Record types may feed it.
    record_types: tuple[str, ...]
    section: str


def _money(
    key: str,
    item: str,
    name: str,
    direction: str,
    section: str,
    description: str = "",
    *,
    modelled: bool = True,
    record_types: tuple[str, ...] = ("line",),
) -> Label:
    return Label(key, item, name, description, direction, MONEY, modelled, record_types, section)


def _memo(key: str, name: str, description: str) -> Label:
    """A ``memo`` label: on no return, fed by an income statement, read by the reconciliation."""
    return Label(
        key,
        "",
        name,
        description,
        MEMO,
        MONEY,
        False,
        ("income_statement",),
        "income_statement",
    )


#: The Record types that feed a money label a depreciating asset's or a home office schedule's
#: deduction also counts toward.
LINE_OR_ASSET = ("line", "depreciating_asset", "home_office_hours")

#: The Record types that feed work-related car expenses: a car kilometres schedule's deduction
#: (cents per kilometre) counts toward it as well as a plain line.
LINE_OR_CAR = ("line", "car_kilometres")

#: The Record types that feed a money label an income statement also reports.
LINE_OR_STATEMENT = ("line", "income_statement")


#: The unmodelled labels an income statement's lump sums feed (as well as a plain line).
STATEMENT_UNMODELLED = frozenset(
    {"employer_lump_sum", "lump_sum_arrears", "return_to_work_payments"}
)


def _flag(key: str, item: str, name: str, section: str, description: str) -> Label:
    return Label(key, item, name, description, FACT, FLAG, True, ("fact",), section)


_LABELS: tuple[Label, ...] = (
    # Employment
    _money(
        "salary",
        "1",
        "Salary and wages (gross)",
        INCOME,
        "employment",
        record_types=LINE_OR_STATEMENT,
    ),
    _money(
        "tax_withheld",
        "1",
        "Tax withheld from employment",
        CREDIT,
        "employment",
        record_types=LINE_OR_STATEMENT,
    ),
    _money(
        "allowances",
        "2",
        "Allowances, tips and director's fees",
        INCOME,
        "employment",
        record_types=LINE_OR_STATEMENT,
    ),
    Label(
        "reportable_fringe_benefits",
        "IT1",
        "Reportable fringe benefits amount",
        "The grossed-up amount from the income statement. Not taxed, but it counts for the "
        "Medicare levy surcharge and HELP.",
        FACT,
        MONEY,
        True,
        LINE_OR_STATEMENT,
        "employment",
    ),
    Label(
        "reportable_employer_super",
        "IT2",
        "Reportable employer super contributions",
        "Salary sacrifice and other contributions you had a say in. Counts for the Medicare levy "
        "surcharge and HELP.",
        FACT,
        MONEY,
        True,
        LINE_OR_STATEMENT,
        "employment",
    ),
    # Investment income
    _money("interest", "10", "Gross interest", INCOME, "investment"),
    _money("dividends_unfranked", "11", "Dividends: unfranked amount", INCOME, "investment"),
    _money("dividends_franked", "11", "Dividends: franked amount", INCOME, "investment"),
    _money(
        "franking_credits",
        "11",
        "Dividends: franking credits",
        CREDIT,
        "investment",
        "Assessable, then credited against the tax.",
    ),
    _money(
        "tfn_withheld",
        "10/11",
        "TFN amounts withheld from interest and dividends",
        CREDIT,
        "investment",
    ),
    _money(
        "trust_income",
        "13",
        "Managed fund and trust distributions (not capital gains)",
        INCOME,
        "investment",
        "As the managed fund tax statement prints it: the unfranked distribution (13U) plus the "
        "franked distribution (13C), which already includes its franking credit. Excludes "
        "capital gains (enter those under capital gains).",
    ),
    _money(
        "trust_franking_credits",
        "13",
        "Managed fund franking credits",
        CREDIT,
        "investment",
        "The franking credit line of the statement (13Q). A credit only: it is already inside "
        "the franked distribution (13C), so it is not added to income again.",
    ),
    # Capital gains
    _money(
        "gains_discountable",
        "18",
        "Capital gains held 12 months or more (before the discount)",
        INCOME,
        "capital_gains",
        "Includes discountable gains from managed fund statements, grossed up.",
    ),
    _money(
        "gains_other",
        "18",
        "Other capital gains (not discountable)",
        INCOME,
        "capital_gains",
    ),
    _money(
        "capital_losses",
        "18",
        "Capital losses (this year and carried forward)",
        DEDUCTION,
        "capital_gains",
    ),
    # Rental property (the Member's share)
    _money("rent_gross", "21", "Gross rent", INCOME, "rental"),
    _money("rent_interest", "21", "Rental: interest on the loan", DEDUCTION, "rental"),
    _money(
        "rent_capital_works",
        "21",
        "Rental: capital works and depreciation",
        DEDUCTION,
        "rental",
    ),
    _money("rent_other", "21", "Rental: other expenses", DEDUCTION, "rental"),
    # Sole trader
    _money("business_income", "P8/15", "Business income", INCOME, "business"),
    _money(
        "business_expenses",
        "P8/15",
        "Business expenses",
        DEDUCTION,
        "business",
        record_types=(*LINE_OR_ASSET, "car_kilometres"),
    ),
    _money(
        "payg_instalments",
        "",
        "PAYG instalments paid",
        CREDIT,
        "business",
        "Instalments paid during the year are a credit against the assessment.",
    ),
    _flag(
        "business_loss_rules_met",
        "P9",
        "Non-commercial loss rules met",
        "business",
        "If not met, a business loss is deferred rather than offset against other income.",
    ),
    _flag(
        "sbito_eligible",
        "",
        "Eligible for the small business income tax offset",
        "business",
        "Tick if the business qualifies for the offset.",
    ),
    # Other income
    _money(
        "other_income",
        "24",
        "Other taxable income",
        INCOME,
        "other_income",
        "Other assessable amounts not entered elsewhere.",
    ),
    # Deductions
    _money(
        "work_car",
        "D1",
        "Work-related car expenses",
        DEDUCTION,
        "deductions",
        record_types=LINE_OR_CAR,
    ),
    _money("work_travel", "D2", "Work-related travel expenses", DEDUCTION, "deductions"),
    _money(
        "work_clothing", "D3", "Work clothing, laundry and dry-cleaning", DEDUCTION, "deductions"
    ),
    _money("work_self_education", "D4", "Work-related self-education", DEDUCTION, "deductions"),
    _money(
        "work_other",
        "D5",
        "Other work-related expenses",
        DEDUCTION,
        "deductions",
        "Including working from home, phone and tools.",
        record_types=LINE_OR_ASSET,
    ),
    _money("low_value_pool", "D6", "Low-value pool deduction", DEDUCTION, "deductions"),
    _money(
        "interest_deductions",
        "D7",
        "Interest deductions",
        DEDUCTION,
        "deductions",
        "Interest on loans used to earn investment income.",
    ),
    _money("dividend_deductions", "D8", "Dividend deductions", DEDUCTION, "deductions"),
    _money("gifts_donations", "D9", "Gifts and donations", DEDUCTION, "deductions"),
    _money("tax_affairs", "D10", "Cost of managing tax affairs", DEDUCTION, "deductions"),
    _money(
        "personal_super_contributions",
        "D12",
        "Personal super contributions (claimed)",
        DEDUCTION,
        "deductions",
        "Needs a notice of intent acknowledged by the fund. Counts as reportable super for the "
        "Medicare levy surcharge and HELP.",
    ),
    _money(
        "other_deductions",
        "D15",
        "Other deductions",
        DEDUCTION,
        "deductions",
        "For example income protection premiums.",
    ),
    _flag(
        "use_instant_deduction",
        "",
        "Use the instant work-related expenses deduction if higher",
        "deductions",
        "Applies in place of D1 to D6 when the work expenses are below the instant amount, for a "
        "year that has one.",
    ),
    # Medicare, health cover and HELP
    Label(
        "surcharge_exempt_days",
        "M2",
        "Days exempt from the Medicare levy surcharge",
        "Days with appropriate private hospital cover (for the Member, any spouse and dependants), "
        "or otherwise exempt. Leave at 0 for no exemption.",
        FACT,
        DAYS,
        True,
        ("fact",),
        "medicare",
    ),
    _flag("help_debt", "", "Has a HELP or study loan debt", "medicare", ""),
    # Income statement figures that are on no return (memo): they feed the Income reconciliation
    _memo(
        "employer_super",
        "Employer super (income statement)",
        "Super the employer paid on top of the pay. With gross pay it is the total package.",
    ),
    _memo(
        "novated_lease_post_tax",
        "Novated lease costs paid from pay (post-tax)",
        "The lease costs taken from net pay. The reportable fringe benefits amount is a "
        "separate, grossed-up figure.",
    ),
    _memo(
        "payroll_other_deductions",
        "Other deductions from pay",
        "Anything else taken from pay after tax, such as a union fee or a payroll giving gift.",
    ),
    _memo(
        "net_pay_received",
        "Net pay received (take-home)",
        "The pay that reached the bank account.",
    ),
    _memo(
        "super_insurance_premiums",
        "Insurance premiums paid from the super balance",
        "Not paid from pay, so not part of the package. Shown as a note.",
    ),
    # Return items the engine does not model. A figure here marks the estimate incomplete.
    *(
        _money(
            key,
            item,
            name,
            direction,
            "other_items",
            modelled=False,
            record_types=LINE_OR_STATEMENT if key in STATEMENT_UNMODELLED else ("line",),
        )
        for key, item, name, direction in (
            ("employer_lump_sum", "3", "Employer lump sum payments", INCOME),
            (
                "lump_sum_arrears",
                "24",
                "Employer lump sum payments in arrears (lump sum E)",
                INCOME,
            ),
            ("return_to_work_payments", "", "Return to work payments (lump sum W)", INCOME),
            ("termination_payments", "4", "Employment termination payments", INCOME),
            (
                "government_allowances",
                "5",
                "Australian Government allowances and payments",
                INCOME,
            ),
            (
                "government_pensions",
                "6",
                "Australian Government pensions and allowances",
                INCOME,
            ),
            (
                "annuities_income_streams",
                "7",
                "Australian annuities and superannuation income streams",
                INCOME,
            ),
            ("super_lump_sums", "8", "Australian superannuation lump sum payments", INCOME),
            ("attributed_personal_services", "9", "Attributed personal services income", INCOME),
            ("employee_share_schemes", "12", "Employee share schemes", INCOME),
            ("personal_services_income", "14", "Personal services income", INCOME),
            ("deferred_business_losses", "16", "Deferred non-commercial business losses", INCOME),
            ("farm_management_deposits", "17", "Net farm management deposits", INCOME),
            ("foreign_entities", "19", "Foreign entities", INCOME),
            ("foreign_source_income", "20", "Foreign source income and assets", INCOME),
            ("life_insurance_bonuses", "22", "Bonuses from life insurance companies", INCOME),
            (
                "first_home_super_released",
                "23",
                "First home super saver released amount",
                INCOME,
            ),
            (
                "foreign_pension_deduction",
                "D11",
                "Undeducted purchase price of a foreign pension",
                DEDUCTION,
            ),
            ("project_pool", "D13", "Deduction for project pool", DEDUCTION),
            ("forestry_scheme", "D14", "Forestry managed investment scheme deduction", DEDUCTION),
            ("seniors_pensioners_offset", "T1", "Seniors and pensioners tax offset", OFFSET),
        )
    ),
)

LABELS: dict[str, Label] = {label.key: label for label in _LABELS}
if len(LABELS) != len(_LABELS):  # a duplicate key is a coding error, caught at import
    raise ValueError("Return label keys must be unique.")

MODELLED: frozenset[str] = frozenset(k for k, label in LABELS.items() if label.modelled)


def all_labels() -> tuple[Label, ...]:
    """Every registered Return label, in the order the screen shows them."""
    return _LABELS


def get(key: str) -> Label | None:
    return LABELS.get(key)
