"""The ``age_pension.`` figures the AU Rule pack holds, declared for guided capture (B11).

This module holds **no figure**: it names the page to open, where on it to look, the unit and a
deliberately wide plausible range for each key (a guard against a wrong unit or a slipped decimal
point). ``scripts/rulepack_update.py --prefix age_pension.`` walks these and writes the values to
``tax_figures.py`` (the one captured-figures module of this pack, whatever the prefix).

Fortnightly figures are stored as published, per fortnight; the means test (a pure function in
``capabilities/projection/age_pension.py``) converts to a year. Couple figures are the pair's
combined amount for the free areas and the asset limits, and *each* partner's amount for the
payment rates, as Services Australia publishes them.

**Carry-forward policy: every key is ``cpi``**, stated per group:

* Payment rates (``max_basic``, ``pension_supplement``): indexed twice a year to the greater of
  consumer prices and the pensioner cost-of-living index, and benchmarked to male total average
  weekly earnings. In the real terms the engine works in, that is constant: ``cpi``. Wage
  growth above prices is not modelled (the ``wages`` policy of ADR-0018 is not built).
* Income free areas and asset limits: indexed to consumer prices each 1 July: ``cpi``.
* Deeming thresholds: indexed to consumer prices each 1 July: ``cpi``.
* Tapers and deeming rates: a rate repeats (``cpi``). The deeming rates are set by Government and
  move with the cash rate, which is not predictable; the last known rate is repeated.

Nothing is ``frozen``: no Age Pension figure is fixed in nominal dollars.

The energy supplement is not declared: it is paid to few people and is outside this slice.
**Each spec's ``page_url``** is the page that holds the figure. Addresses are not fetched
(official sites block automated reads), so each spec carries ``url_confirmed=False`` until a
person has opened it; correct it here after opening it. An address is guidance (outside the
content hash), so correcting one needs no revision bump.
"""

from __future__ import annotations

from finance360.core.rulepack import CPI, MONEY, RATE, FigureSpec

_SA = "https://www.servicesaustralia.gov.au/"
_RATES_PAGE = _SA + "how-much-age-pension-you-can-get"
_INCOME_PAGE = _SA + "income-test-for-age-pension"
_ASSETS_PAGE = _SA + "assets-test-for-age-pension"
_DEEMING_PAGE = _SA + "deeming"

_RATES = "Services Australia, Age Pension: how much you can get"
_INCOME = "Services Australia, Income test for Age Pension"
_ASSETS = "Services Australia, Assets test for Age Pension"
_DEEMING = "Services Australia, Deeming"

_DOLLAR = 100  # minor units per dollar
_PERCENT = 1_000_000 // 100  # rate ppm per percent


def _money(
    key: str,
    label: str,
    source: str,
    page_url: str,
    where: str,
    high_dollars: int,
    unit: str | None,
) -> FigureSpec:
    return FigureSpec(
        key, MONEY, CPI, label, source, page_url, where, 0, high_dollars * _DOLLAR, unit
    )


def _rate(
    key: str, label: str, source: str, page_url: str, where: str, high_percent: int
) -> FigureSpec:
    return FigureSpec(key, RATE, CPI, label, source, page_url, where, 0, high_percent * _PERCENT)


_RATES_WHERE = (
    "The maximum payment rates table: the {what} row, the {who} column, in dollars per fortnight. "
    "Use the figure for the current indexation date."
)

SPECS: tuple[FigureSpec, ...] = (
    *(
        _money(
            f"age_pension.{key}.{who}",
            f"Age Pension: maximum {name}, {text}",
            _RATES,
            _RATES_PAGE,
            _RATES_WHERE.format(what=name, who=text),
            5_000,
            "fortnight",
        )
        for key, name in (("max_basic", "basic rate"), ("pension_supplement", "pension supplement"))
        for who, text in (("single", "single"), ("couple_each", "couple, each"))
    ),
    _money(
        "age_pension.income_free_area.single",
        "Age Pension: income free area, single",
        _INCOME,
        _INCOME_PAGE,
        "The income test table: how much a single person can earn each fortnight before the "
        "pension reduces (the free area).",
        3_000,
        "fortnight",
    ),
    _money(
        "age_pension.income_free_area.couple",
        "Age Pension: income free area, couple combined",
        _INCOME,
        _INCOME_PAGE,
        "The income test table: how much a couple can earn combined each fortnight before the "
        "pension reduces (the combined free area).",
        5_000,
        "fortnight",
    ),
    _rate(
        "age_pension.income_taper",
        "Age Pension: income test reduction rate (combined)",
        _INCOME,
        _INCOME_PAGE,
        "Cents the pension reduces by for each dollar of income over the free area, as a "
        "percentage of that dollar. For a couple the page says 25 cents each: enter the "
        "combined figure (50).",
        100,
    ),
    *(
        _money(
            f"age_pension.assets_threshold.{key}",
            f"Age Pension: assets test full-pension limit, {text}",
            _ASSETS,
            _ASSETS_PAGE,
            f"The assets test table: the most assets, other than the home for a homeowner, that "
            f"still pays the full pension: {text}. For a couple, the combined figure.",
            5_000_000,
            None,
        )
        for key, text in (
            ("homeowner_single", "homeowner, single"),
            ("homeowner_couple", "homeowner, couple combined"),
            ("non_homeowner_single", "non-homeowner, single"),
            ("non_homeowner_couple", "non-homeowner, couple combined"),
        )
    ),
    _rate(
        "age_pension.assets_taper",
        "Age Pension: assets test reduction rate",
        _ASSETS,
        _ASSETS_PAGE,
        "The pension reduces by $3 each fortnight for every $1,000 of assets over the limit. "
        "Enter that as a percentage of the assets: $3 per $1,000 is 0.3.",
        2,
    ),
    _money(
        "age_pension.deeming.threshold_single",
        "Deeming: threshold, single",
        _DEEMING,
        _DEEMING_PAGE,
        "The deeming rates table: the amount of a single person's financial investments the "
        "lower rate applies to; the higher rate applies above it.",
        1_000_000,
        None,
    ),
    _money(
        "age_pension.deeming.threshold_couple",
        "Deeming: threshold, couple combined",
        _DEEMING,
        _DEEMING_PAGE,
        "The deeming rates table: the amount of a couple's combined financial investments the "
        "lower rate applies to; the higher rate applies above it.",
        2_000_000,
        None,
    ),
    _rate(
        "age_pension.deeming.rate_lower",
        "Deeming: lower rate",
        _DEEMING,
        _DEEMING_PAGE,
        "The deeming rates table: the rate on financial investments up to the threshold, as a "
        "percentage a year.",
        20,
    ),
    _rate(
        "age_pension.deeming.rate_upper",
        "Deeming: upper rate",
        _DEEMING,
        _DEEMING_PAGE,
        "The deeming rates table: the rate on financial investments above the threshold, as a "
        "percentage a year.",
        20,
    ),
)
