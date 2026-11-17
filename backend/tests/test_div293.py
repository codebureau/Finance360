"""Division 293: the pure measure, golden answers worked by hand.

The figures are invented round numbers, not the Rule pack's: threshold 250,000, rate 15%,
cap 30,000.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.tax import div293
from finance360.core.money import BASE_CURRENCY, Money
from finance360.core.rate import Rate


def aud(dollars: int) -> Money:
    return Money(dollars * 100, BASE_CURRENCY)


RULES = div293.Div293Rules(aud(250_000), Rate.from_percent("15"), aud(30_000))


def run(
    taxable: int,
    contributions: int,
    fringe: int = 0,
    losses: int = 0,
    rules: div293.Div293Rules = RULES,
) -> div293.Div293Result:
    return div293.calculate(
        div293.Div293Input(
            1, 2026, aud(taxable), aud(fringe), aud(losses), aud(contributions), rules
        )
    )


def test_under_the_threshold_nothing_is_due() -> None:
    result = run(taxable=200_000, contributions=20_000)
    # 200,000 + 20,000 = 220,000, under 250,000.
    assert result.div293_income == aud(220_000)
    assert result.income_over_threshold == aud(0)
    assert result.liability == aud(0)
    assert not result.applies


def test_exactly_at_the_threshold_nothing_is_due() -> None:
    assert run(taxable=230_000, contributions=20_000).liability == aud(0)


def test_over_by_less_than_the_contributions_taxes_the_excess_income() -> None:
    # 240,000 + 20,000 = 260,000: 10,000 over, less than the 20,000 contributions.
    result = run(taxable=240_000, contributions=20_000)
    assert result.income_over_threshold == aud(10_000)
    assert result.taxed_amount == aud(10_000)
    assert result.liability == aud(1_500)  # 15% of 10,000
    assert result.applies


def test_over_by_more_than_the_contributions_taxes_the_contributions() -> None:
    # 300,000 + 25,000 = 325,000: 75,000 over, more than the 25,000 contributions.
    result = run(taxable=300_000, contributions=25_000)
    assert result.taxed_amount == aud(25_000)
    assert result.liability == aud(3_750)  # 15% of 25,000


def test_excess_contributions_reduce_the_low_tax_contributions() -> None:
    # 35,000 received against a 30,000 cap: 5,000 excess, 30,000 low tax contributions.
    result = run(taxable=400_000, contributions=35_000)
    assert result.excess_contributions == aud(5_000)
    assert result.low_tax_contributions == aud(30_000)
    assert result.div293_income == aud(430_000)
    assert result.liability == aud(4_500)  # 15% of 30,000, not of 35,000


def test_fringe_benefits_and_net_investment_losses_count() -> None:
    # 200,000 + 30,000 fringe + 10,000 losses + 20,000 contributions = 260,000: 10,000 over.
    result = run(taxable=200_000, contributions=20_000, fringe=30_000, losses=10_000)
    assert result.div293_income == aud(260_000)
    assert result.liability == aud(1_500)


def test_reportable_employer_super_is_not_added_a_second_time() -> None:
    """The reportable employer super is part of the contributions the fund received. The measure
    has no input for it, so it cannot be added on top; the contributions are counted once."""
    fields = {f for f in div293.Div293Input.__dataclass_fields__}
    assert not any("reportable_employer_super" in f for f in fields)
    result = run(taxable=240_000, contributions=20_000)
    assert result.div293_income == aud(240_000 + 20_000)  # 20,000 once, never 40,000


def test_the_working_ends_at_the_liability() -> None:
    result = run(taxable=240_000, contributions=20_000)
    assert [s.title for s in result.steps] == [
        "Excess concessional contributions",
        "Low tax contributions",
        "Division 293 income",
        "Income over the threshold",
        "Amount taxed",
        "Division 293 tax",
    ]
    assert result.steps[-1].result.value == "1500.00"


def test_rate_rounds_by_the_ato_step() -> None:
    # 15% of 0.05 = 0.0075, which rounds to 0.01 (nearest cent).
    rules = div293.Div293Rules(aud(0), Rate.from_percent("15"), aud(30_000))
    result = div293.calculate(
        div293.Div293Input(1, 2026, aud(0), aud(0), aud(0), Money(5, BASE_CURRENCY), rules)
    )
    assert result.liability == Money(1, BASE_CURRENCY)


@pytest.mark.parametrize(("taxed_cents", "tax_cents"), [(10, 2), (30, 4)])
def test_a_half_cent_of_division_293_tax_goes_to_the_even_cent(
    taxed_cents: int, tax_cents: int
) -> None:
    # 15% of 0.10 is 0.015 (up to 0.02); 15% of 0.30 is 0.045 (down to 0.04): both even.
    rules = div293.Div293Rules(aud(0), Rate.from_percent("15"), aud(30_000))
    result = div293.calculate(
        div293.Div293Input(
            1, 2026, aud(0), aud(0), aud(0), Money(taxed_cents, BASE_CURRENCY), rules
        )
    )
    assert result.liability == Money(tax_cents, BASE_CURRENCY)


@pytest.mark.parametrize(
    ("received", "status"),
    [(0, "ok"), (26_999, "ok"), (27_000, "near"), (30_000, "near"), (30_001, "over")],
)
def test_cap_status_warns_within_ten_percent_and_over(received: int, status: str) -> None:
    assert div293.cap_status(aud(received), aud(30_000)) == status


def test_emission_carries_the_amount_and_no_payer() -> None:
    result = run(taxable=240_000, contributions=20_000)
    assert div293.emission(result) == div293.Div293Emission(1, 2026, aud(1_500), None)


@given(
    taxable=st.integers(0, 2_000_000),
    contributions=st.integers(0, 200_000),
    fringe=st.integers(0, 200_000),
    losses=st.integers(0, 200_000),
)
def test_the_liability_never_exceeds_the_rate_on_either_bound(
    taxable: int, contributions: int, fringe: int, losses: int
) -> None:
    result = run(taxable, contributions, fringe, losses)
    assert result.liability.amount_minor >= 0
    # Never more than 15% of the low tax contributions or of the income over the threshold
    # (plus one cent of rounding).
    assert (
        result.liability.amount_minor <= result.low_tax_contributions.amount_minor * 15 // 100 + 1
    )
    assert (
        result.liability.amount_minor <= result.income_over_threshold.amount_minor * 15 // 100 + 1
    )
    assert result.low_tax_contributions + result.excess_contributions == aud(contributions)
