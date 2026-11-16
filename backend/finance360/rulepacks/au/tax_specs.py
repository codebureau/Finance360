"""The ``tax.`` figures the AU Rule pack will hold, declared for guided capture.

This module holds **no figure**. Each ``FigureSpec`` names the page to open, where on it to look,
the unit and a deliberately wide plausible range (a guard against a wrong unit or a slipped decimal
point, not a statement of the figure). ``scripts/rulepack_update.py`` walks these and writes the
values to ``tax_figures.py``.

The list is the shape the estimate engine needs: resident brackets, the low income tax
offset, the Medicare levy and its low income reduction, the Medicare levy surcharge, the HELP
repayment thresholds and rates, the small business income tax offset, the capital gains discount
(added with the estimate engine: it needs it and it is law, so it is a figure and not code),
the instant work-related deduction and the diminishing value multiplier for depreciating assets
(a whole percent, because a ``Rate`` stops at 100%, and read by the Record type, not the estimate
engine), the fixed rate per hour for home office use (money per hour, read by its Record type), and
the rate per kilometre and the kilometre limit per car for the cents per kilometre method (read
by its Record type), and the Division 293 threshold and rate and the concessional contributions
cap (read by the Division 293 working, not by the estimate engine, so a year without them still
estimates).
Nothing else is declared until a rule needs it.

**Each spec's ``page_url`` is the page that holds that figure**, not a parent index: the
capture screen opens it and groups carried figures by it. Addresses are not fetched (official sites
block automated reads), so a spec carries ``url_confirmed=False`` until a person has opened it;
correct an address here after opening it, and set ``url_confirmed=True``. An address is guidance
(outside the content hash), so correcting one needs no revision bump.

Carry-forward policy (used only when projection later asks for future years): a threshold or
cap not indexed by law is ``frozen``; a rate repeats (``cpi``). ``brackets`` carry as ``cpi``
because the contract only erodes a single money value, which is a decision for
the projection to make.
"""

from __future__ import annotations

from finance360.core.rulepack import BRACKETS, CPI, FROZEN, INT, MONEY, RATE, FigureSpec

_ATO = "https://www.ato.gov.au/"
_RATES = _ATO + "tax-rates-and-codes/tax-rates-australian-residents"
_MEDICARE = _ATO + "individuals-and-families/medicare-and-private-health-insurance/"
_LEVY_PAGE = _MEDICARE + "medicare-levy/what-is-the-medicare-levy"
_LOW_INCOME_PAGE = (
    _MEDICARE
    + "medicare-levy/medicare-levy-reduction/medicare-levy-reduction-for-low-income-earners"
)
_MLS_PAGE = (
    _MEDICARE + "medicare-levy-surcharge/medicare-levy-surcharge-income-thresholds-and-rates"
)
_HELP_PAGE = (
    _ATO + "tax-rates-and-codes/study-and-training-support-loans-rates-and-repayment-thresholds"
)
_LITO_PAGE = (
    _ATO
    + "individuals-and-families/income-deductions-offsets-and-records/tax-offsets/"
    + "low-income-tax-offset"
)
_SBITO_PAGE = (
    _ATO
    + "businesses-and-organisations/income-deductions-and-concessions/"
    + "income-and-deductions-for-business/concessions-offsets-and-rebates/"
    + "small-business-income-tax-offset"
)
_CGT_PAGE = _ATO + "individuals-and-families/investments-and-assets/capital-gains-tax/cgt-discount"
_WORK_PAGE = (
    _ATO
    + "individuals-and-families/income-deductions-offsets-and-records/deductions-you-can-claim/"
    + "work-related-deductions/standard-deduction-for-work-related-expenses"
)

_SUPER_RATES = (
    _ATO + "tax-rates-and-codes/key-superannuation-rates-and-thresholds/contributions-caps"
)
_DIV293_PAGE = _ATO + "tax-rates-and-codes/key-superannuation-rates-and-thresholds/division-293-tax"
_DIV293 = "ATO, Division 293 tax on concessional contributions"
_CAP = "ATO, Key superannuation rates and thresholds: contributions caps"

_DOLLAR = 100  # minor units per dollar


def _money(
    key: str,
    label: str,
    source: str,
    page_url: str,
    where: str,
    high_dollars: int,
) -> FigureSpec:
    return FigureSpec(
        key, MONEY, FROZEN, label, source, page_url, where, 0, high_dollars * _DOLLAR, "year"
    )


def _rate(
    key: str, label: str, source: str, page_url: str, where: str, high_percent: int = 60
) -> FigureSpec:
    return FigureSpec(
        key, RATE, CPI, label, source, page_url, where, 0, high_percent * (1_000_000 // 100)
    )


_RESIDENT = "ATO, Tax rates: Australian residents"
_LITO = "ATO, Low income tax offset"
_LEVY = "ATO, Medicare levy"
_LOW_INCOME = "ATO, Medicare levy reduction for low income earners"
_MLS = "ATO, Medicare levy surcharge income, thresholds and rates"
_HELP = "ATO, Study and training support loans: repayment thresholds and rates"
_SBITO = "ATO, Small business income tax offset"
_WORK = "ATO, Work-related expenses: instant deduction"
_CGT = "ATO, Capital gains tax discount"
_DEPRECIATION = "ATO, Uniform capital allowance system: calculate decline in value"
_HOME_OFFICE = "ATO, Working from home expenses: fixed rate method"
_HOME_OFFICE_PAGE = (
    "https://www.ato.gov.au/individuals-and-families/income-deductions-offsets-and-records/"
    "deductions-you-can-claim/work-related-deductions/working-from-home-expenses/"
    "fixed-rate-method"
)
_CAR = "ATO, Car expenses: cents per kilometre method"
_CAR_PAGE = (
    "https://www.ato.gov.au/individuals-and-families/income-deductions-offsets-and-records/"
    "deductions-you-can-claim/work-related-deductions/cars-transport-and-travel/"
    "motor-vehicle-and-car-expenses/expenses-for-a-car-you-own-or-lease/"
    "cents-per-kilometre-method"
)
_DEPRECIATION_PAGE = (
    "https://www.ato.gov.au/businesses-and-organisations/income-deductions-and-concessions/"
    "depreciation-and-capital-expenses-and-allowances/in-detail/depreciating-assets/"
    "uniform-capital-allowance-system-calculate-decline-in-value"
)

_MLS_WHERE = (
    "The surcharge table: the income range for each tier, in the single and family columns."
)
_HELP_WHERE = (
    "The repayment table for the year: the income at which each repayment step starts, and "
    "the rate that applies in it."
)

SPECS: tuple[FigureSpec, ...] = (
    FigureSpec(
        "tax.resident_brackets",
        BRACKETS,
        CPI,
        "Resident tax brackets",
        _RESIDENT,
        _RATES,
        "The resident rates table for the year: each band's lower income threshold and its "
        "rate. Enter the first band as 0, then one over:rate pair per band.",
        0,
        2_000_000 * _DOLLAR,
        "year",
        scheduled_change=(
            "Legislated: the 16% band's rate falls to 15% from 1 July 2026 (FY 2026-27) and "
            "to 14% from 1 July 2027 (FY 2027-28). Check the second band's rate on the page."
        ),
    ),
    _money(
        "tax.lito.max",
        "Low income tax offset: maximum offset",
        _LITO,
        _LITO_PAGE,
        "The maximum offset amount for the year.",
        5_000,
    ),
    _money(
        "tax.lito.taper1_from",
        "Low income tax offset: first reduction starts at income",
        _LITO,
        _LITO_PAGE,
        "The taxable income above which the offset first starts to reduce.",
        500_000,
    ),
    _rate(
        "tax.lito.taper1_rate",
        "Low income tax offset: first reduction rate",
        _LITO,
        _LITO_PAGE,
        "Cents per dollar the offset falls by in the first reduction range, as a percentage.",
        20,
    ),
    _money(
        "tax.lito.taper2_from",
        "Low income tax offset: second reduction starts at income",
        _LITO,
        _LITO_PAGE,
        "The taxable income above which the offset reduces at the second rate.",
        500_000,
    ),
    _rate(
        "tax.lito.taper2_rate",
        "Low income tax offset: second reduction rate",
        _LITO,
        _LITO_PAGE,
        "Cents per dollar the offset falls by in the second reduction range, as a percentage.",
        20,
    ),
    _rate(
        "tax.medicare.levy_rate",
        "Medicare levy rate",
        _LEVY,
        _LEVY_PAGE,
        "The levy rate as a percentage of taxable income.",
        10,
    ),
    _money(
        "tax.medicare.low_income.single_lower",
        "Medicare levy low income reduction: single lower threshold",
        _LOW_INCOME,
        _LOW_INCOME_PAGE,
        "The taxable income at or below which a single person pays no levy.",
        500_000,
    ),
    _money(
        "tax.medicare.low_income.family_lower",
        "Medicare levy low income reduction: family lower threshold",
        _LOW_INCOME,
        _LOW_INCOME_PAGE,
        "The family income at or below which a family pays no levy.",
        500_000,
    ),
    _money(
        "tax.medicare.low_income.family_per_child",
        "Medicare levy low income reduction: family threshold increase per dependent child",
        _LOW_INCOME,
        _LOW_INCOME_PAGE,
        "The amount the family threshold rises for each dependent child.",
        100_000,
    ),
    _rate(
        "tax.medicare.low_income.shade_in_rate",
        "Medicare levy low income reduction: single shade-in rate",
        _LOW_INCOME,
        _LOW_INCOME_PAGE,
        "Cents per dollar of income over the lower threshold that is charged while the levy "
        "phases in, as a percentage.",
        40,
    ),
    _rate(
        "tax.medicare.low_income.family_reduction_rate",
        "Medicare levy low income reduction: family reduction rate",
        _LOW_INCOME,
        _LOW_INCOME_PAGE,
        "The amount by which the family levy is reduced per dollar of family income over the "
        "family threshold, as a percentage.",
        10,
    ),
    *(
        _money(
            f"tax.mls.single_tier{n}",
            f"Medicare levy surcharge: single, tier {n} starts at income",
            _MLS,
            _MLS_PAGE,
            _MLS_WHERE,
            1_000_000,
        )
        for n in (1, 2, 3)
    ),
    *(
        _money(
            f"tax.mls.family_tier{n}",
            f"Medicare levy surcharge: family, tier {n} starts at income",
            _MLS,
            _MLS_PAGE,
            _MLS_WHERE,
            2_000_000,
        )
        for n in (1, 2, 3)
    ),
    *(
        _rate(
            f"tax.mls.rate_tier{n}",
            f"Medicare levy surcharge: tier {n} rate",
            _MLS,
            _MLS_PAGE,
            "The surcharge rate for the tier, as a percentage of income.",
            10,
        )
        for n in (1, 2, 3)
    ),
    _money(
        "tax.mls.family_per_child_after_first",
        "Medicare levy surcharge: family threshold increase per dependent child after the first",
        _MLS,
        _MLS_PAGE,
        "The amount each family tier rises for every dependent child after the first.",
        100_000,
    ),
    _money(
        "tax.help.t1",
        "HELP repayment: first threshold (repayment starts above)",
        _HELP,
        _HELP_PAGE,
        _HELP_WHERE,
        500_000,
    ),
    _rate(
        "tax.help.r1",
        "HELP repayment: rate between the first and second thresholds",
        _HELP,
        _HELP_PAGE,
        _HELP_WHERE,
        30,
    ),
    _money(
        "tax.help.t2",
        "HELP repayment: second threshold",
        _HELP,
        _HELP_PAGE,
        _HELP_WHERE,
        500_000,
    ),
    _rate(
        "tax.help.r2",
        "HELP repayment: rate between the second and third thresholds",
        _HELP,
        _HELP_PAGE,
        _HELP_WHERE,
        30,
    ),
    _money(
        "tax.help.t3",
        "HELP repayment: third threshold",
        _HELP,
        _HELP_PAGE,
        _HELP_WHERE,
        500_000,
    ),
    _rate(
        "tax.help.top_rate",
        "HELP repayment: rate on all income above the third threshold",
        _HELP,
        _HELP_PAGE,
        _HELP_WHERE,
        30,
    ),
    _rate(
        "tax.sbito.rate",
        "Small business income tax offset: rate",
        _SBITO,
        _SBITO_PAGE,
        "The offset as a percentage of the tax on net small business income.",
        30,
    ),
    _money(
        "tax.sbito.cap",
        "Small business income tax offset: cap",
        _SBITO,
        _SBITO_PAGE,
        "The most the offset can be for the year.",
        10_000,
    ),
    _rate(
        "tax.cgt.discount_rate",
        "Capital gains tax discount rate",
        _CGT,
        _CGT_PAGE,
        "The discount applied to a capital gain on an asset held 12 months or more, as a "
        "percentage of the gain.",
        100,
    ),
    _money(
        "tax.standard_work_deduction",
        "Instant work-related expenses deduction (leave blank for a year without one)",
        _WORK,
        _WORK_PAGE,
        "The fixed amount claimable without records, if the year has one.",
        5_000,
    ),
    FigureSpec(
        "tax.depreciation.dv_percent",
        INT,
        CPI,
        "Depreciation: diminishing value multiplier",
        _DEPRECIATION,
        _DEPRECIATION_PAGE,
        "The diminishing value formula: base value x (days held / 365) x (the percentage / the "
        "asset's effective life). Enter that percentage as a whole number, such as 200 for 200%.",
        100,
        300,
        value_suffix="%",
        url_confirmed=True,  # opened by the owner on capture, 2026-11-04
    ),
    FigureSpec(
        "tax.home_office.rate_per_hour",
        MONEY,
        FROZEN,
        "Working from home: fixed rate per hour",
        _HOME_OFFICE,
        _HOME_OFFICE_PAGE,
        "The rate for the income year, in cents per hour. Enter it in dollars and cents per hour.",
        10,  # 10 cents
        500,  # $5.00
        "hour",
        url_confirmed=True,  # opened by the owner on capture, 2026-11-04
    ),
    FigureSpec(
        "tax.car.rate_per_km",
        MONEY,
        FROZEN,
        "Car expenses: cents per kilometre rate",
        _CAR,
        _CAR_PAGE,
        "The rate for the income year, in cents per kilometre. Enter it in dollars and cents per "
        "kilometre, not in cents.",
        10,  # 10 cents
        300,  # $3.00
        "km",
        url_confirmed=True,  # opened by the owner on capture, 2026-11-05
    ),
    FigureSpec(
        "tax.car.max_km",
        INT,
        CPI,
        "Car expenses: kilometre limit per car",
        _CAR,
        _CAR_PAGE,
        "The most work kilometres that can be claimed for one car in the income year. Enter it as "
        "a whole number of kilometres.",
        1_000,
        20_000,
        value_suffix=" km",
        url_confirmed=True,  # opened by the owner on capture, 2026-11-05
    ),
    _money(
        "tax.div293.threshold",
        "Division 293 threshold",
        _DIV293,
        _DIV293_PAGE,
        "The income above which Division 293 tax can apply (Division 293 income, not taxable "
        "income alone).",
        2_000_000,
    ),
    _rate(
        "tax.div293.rate",
        "Division 293 tax rate",
        _DIV293,
        _DIV293_PAGE,
        "The additional tax on the lesser of the low tax contributions and the income over the "
        "threshold, as a percentage.",
        60,
    ),
    _money(
        "tax.super.concessional_cap",
        "Concessional contributions cap",
        _CAP,
        _SUPER_RATES,
        "The general concessional contributions cap for the financial year.",
        200_000,
    ),
)
