"""The Age Pension means test (B11): golden cases worked by hand, and properties.

Every case states its parameters explicitly (``P``, below), so none depends on the Rule pack's
values, and its answer was worked by hand in the comment beside it. ``P`` uses the figures
Services Australia was reported to publish from 20 September 2026 (assets limits from 1 July
2026), retrieved 2026-11-21 from secondary sources.

**No published worked example was found to cite**: the Services Australia pages block automated
reads. So each answer below is worked from the rules, in the comment, not copied from a
calculator. Replace a case with a published one (page and retrieval date beside it) when the
household finds one.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.projection.age_pension import (
    AgePensionParams,
    age_pension_annual,
    maximum_annual,
)
from finance360.core.money import CurrencyMismatchError, Money, MoneyError, parse_amount
from finance360.core.rate import Rate


def aud(text: str) -> Money:
    return parse_amount(text, "AUD")


P = AgePensionParams(
    max_basic_single=aud("1135.40"),
    max_basic_couple_each=aud("855.90"),
    pension_supplement_single=aud("88.20"),
    pension_supplement_couple_each=aud("66.50"),
    income_free_area_single=aud("226.00"),
    income_free_area_couple=aud("396.00"),
    income_taper=Rate.from_percent("50"),
    assets_threshold_homeowner_single=aud("333000"),
    assets_threshold_homeowner_couple=aud("499000"),
    assets_threshold_non_homeowner_single=aud("600000"),
    assets_threshold_non_homeowner_couple=aud("766000"),
    assets_taper=Rate.from_percent("0.3"),
    deeming_threshold_single=aud("66800"),
    deeming_threshold_couple=aud("110600"),
    deeming_rate_lower=Rate.from_percent("1.75"),
    deeming_rate_upper=Rate.from_percent("3.75"),
)

# Maximum a year (26 fortnights):
#   single: (1135.40 + 88.20) = 1223.60 a fortnight -> 31,813.60
#   couple: 2 x (855.90 + 66.50) = 1844.80 a fortnight -> 47,964.80
# Free area a year: single 226 x 26 = 5,876.00; couple 396 x 26 = 10,296.00.
SINGLE_MAX = aud("31813.60")
COUPLE_MAX = aud("47964.80")


def pension(
    *,
    couple: bool = False,
    homeowner: bool = True,
    assets: str = "0",
    financial: str = "0",
    income: str = "0",
) -> Money:
    return age_pension_annual(
        P,
        couple=couple,
        homeowner=homeowner,
        assets=aud(assets),
        financial_assets=aud(financial),
        other_income=aud(income),
    )


def test_the_maximum_rates_are_basic_plus_pension_supplement_for_a_year() -> None:
    assert maximum_annual(P, couple=False) == SINGLE_MAX
    assert maximum_annual(P, couple=True) == COUPLE_MAX


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # 1. Single homeowner, assets under the limit, deemed income under the free area.
        #    Deeming: 66,800 x 1.75% = 1,169.00; (100,000 - 66,800) x 3.75% = 1,245.00; total
        #    2,414.00 < 5,876.00 so no income reduction. Assets 300,000 < 333,000. Full pension.
        (dict(assets="300000", financial="100000"), "31813.60"),
        # 2. Single homeowner, assets over the limit only.
        #    Deeming: 1,169.00 + (150,000 - 66,800) x 3.75% = 3,120.00 -> 4,289.00 < free area.
        #    Assets: 400,000 - 333,000 = 67,000 -> 3 x 67 = 201.00 a fortnight x 26 = 5,226.00.
        #    31,813.60 - 5,226.00 = 26,587.60.
        (dict(assets="400000", financial="150000"), "26587.60"),
        # 3. Single homeowner, income test lower. Deeming: 1,169.00 + (200,000 - 66,800) x 3.75%
        #    = 4,995.00 -> 6,164.00; plus 10,000.00 other = 16,164.00. Over the free area by
        #    16,164.00 - 5,876.00 = 10,288.00; half is 5,144.00. Assets 200,000 under the limit.
        #    31,813.60 - 5,144.00 = 26,669.60.
        (dict(assets="200000", financial="200000", income="10000"), "26669.60"),
        # 4. Single homeowner, assets test lower. Deeming 1,169.00 + 33,200 x 3.75% (1,245.00)
        #    = 2,414.00, no reduction. Assets 600,000 - 333,000 = 267,000 -> 801.00 a fortnight
        #    x 26 = 20,826.00. 31,813.60 - 20,826.00 = 10,987.60.
        (dict(assets="600000", financial="100000"), "10987.60"),
        # 5. Single non-homeowner: limit 600,000. Assets 700,000, financial 50,000 (875.00
        #    deemed), other 30,000 -> income 30,875.00; over the free area by 24,999.00; half is
        #    12,499.50 -> income test 19,314.10. Assets: 100,000 over -> 300.00 x 26 = 7,800.00
        #    -> 24,013.60. The lower, 19,314.10, applies.
        (dict(homeowner=False, assets="700000", financial="50000", income="30000"), "19314.10"),
        # 6. Couple homeowner. Deeming: 110,600 x 1.75% = 1,935.50; (250,000 - 110,600) x 3.75%
        #    = 5,227.50 -> 7,163.00; plus 4,000.00 -> 11,163.00; over 10,296.00 by 867.00; half
        #    is 433.50 -> 47,531.30. Assets: 700,000 - 499,000 = 201,000 -> 603.00 x 26 =
        #    15,678.00 -> 32,286.80. The lower, 32,286.80, applies.
        (dict(couple=True, assets="700000", financial="250000", income="4000"), "32286.80"),
        # 7. Couple non-homeowner. Deeming 1,935.50 + (300,000 - 110,600) x 3.75% (7,102.50) =
        #    9,038.00 < 10,296.00. Assets: 800,000 - 766,000 = 34,000 -> 102.00 x 26 = 2,652.00.
        #    47,964.80 - 2,652.00 = 45,312.80.
        (dict(couple=True, homeowner=False, assets="800000", financial="300000"), "45312.80"),
        # 8. Couple homeowner under every limit: the full couple pension.
        (dict(couple=True, assets="450000", financial="90000"), "47964.80"),
        # 9. A couple's limit is combined: 599,000 is 100,000 over 499,000 -> 300.00 x 26 =
        #    7,800.00 off 47,964.80.
        (dict(couple=True, assets="599000"), "40164.80"),
        # 10. At the full-pension limit: full. One dollar over: 0.003 x 26 = 0.078 off
        #     31,813.60 = 31,813.522, which rounds to 31,813.52.
        (dict(assets="333000"), "31813.60"),
        (dict(assets="333001"), "31813.52"),
        # 11. The assets cut-out is where 1,223.60 a fortnight is used up:
        #     333,000 + 1,223.60 / 0.003 = 740,866.67. At 740,866 the reduction is
        #     407,866 x 0.078 = 31,813.548, leaving 0.052, which rounds to 0.05. At 740,867 the
        #     reduction (31,813.626) exceeds the maximum, so nothing is paid.
        (dict(assets="740866"), "0.05"),
        (dict(assets="740867"), "0.00"),
        (dict(assets="2000000"), "0.00"),
        # 12. The income cut-out, single: 5,876.00 + 2 x 31,813.60 = 69,503.20 a year.
        (dict(income="69503.20"), "0.00"),
        (dict(income="69503.18"), "0.01"),
    ],
)
def test_golden_cases(kwargs: dict[str, Any], expected: str) -> None:
    assert pension(**kwargs) == aud(expected)


def test_a_half_cent_rounds_once_to_the_even_cent() -> None:
    # Income 5,876.01 is 0.01 over the free area: reduction 0.005, so 31,813.595 -> .60 (even).
    assert pension(income="5876.01") == aud("31813.60")
    # Income 5,876.03: reduction 0.015, so 31,813.585 -> .58 (even).
    assert pension(income="5876.03") == aud("31813.58")


@pytest.mark.parametrize(
    "kwargs",
    [{"financial": "10", "assets": "5"}, {"income": "-1"}, {"assets": "-1"}],
)
def test_impossible_inputs_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(MoneyError):
        pension(**kwargs)


def test_mixed_currencies_are_refused() -> None:
    with pytest.raises(CurrencyMismatchError):
        age_pension_annual(
            P,
            couple=False,
            homeowner=True,
            assets=Money(0, "USD"),
            financial_assets=Money(0, "USD"),
            other_income=Money(0, "USD"),
        )


# --- properties ----------------------------------------------------------------------------

_cents = st.integers(min_value=0, max_value=5_000_000_00)
_extra = st.integers(min_value=0, max_value=1_000_000_00)


def _pension(couple: bool, homeowner: bool, assets: int, financial: int, income: int) -> Money:
    return age_pension_annual(
        P,
        couple=couple,
        homeowner=homeowner,
        assets=Money(assets, "AUD"),
        financial_assets=Money(min(assets, financial), "AUD"),
        other_income=Money(income, "AUD"),
    )


@given(st.booleans(), st.booleans(), _cents, _cents, _cents)
def test_never_negative_and_never_above_the_maximum(
    couple: bool, homeowner: bool, assets: int, financial: int, income: int
) -> None:
    got = _pension(couple, homeowner, assets, financial, income)
    assert 0 <= got.amount_minor <= maximum_annual(P, couple=couple).amount_minor


@given(st.booleans(), st.booleans(), _cents, _cents, _cents, _extra)
def test_more_assets_never_raises_the_pension(
    couple: bool, homeowner: bool, assets: int, financial: int, income: int, extra: int
) -> None:
    # The extra assets are not financial: deemed income is held, the assets test alone moves.
    financial = min(assets, financial)
    base = _pension(couple, homeowner, assets, financial, income)
    more = _pension(couple, homeowner, assets + extra, financial, income)
    assert more.amount_minor <= base.amount_minor


@given(st.booleans(), st.booleans(), _cents, _cents, _cents, _extra)
def test_more_income_never_raises_the_pension(
    couple: bool, homeowner: bool, assets: int, financial: int, income: int, extra: int
) -> None:
    base = _pension(couple, homeowner, assets, financial, income)
    more = _pension(couple, homeowner, assets, financial, income + extra)
    assert more.amount_minor <= base.amount_minor


@given(st.booleans(), st.booleans(), _cents, _cents, _cents, _extra)
def test_more_financial_assets_never_raises_the_pension(
    couple: bool, homeowner: bool, assets: int, financial: int, income: int, extra: int
) -> None:
    # More financial assets raise total assets and deemed income together.
    financial = min(assets, financial)
    base = _pension(couple, homeowner, assets, financial, income)
    more = age_pension_annual(
        P,
        couple=couple,
        homeowner=homeowner,
        assets=Money(assets + extra, "AUD"),
        financial_assets=Money(financial + extra, "AUD"),
        other_income=Money(income, "AUD"),
    )
    assert more.amount_minor <= base.amount_minor
