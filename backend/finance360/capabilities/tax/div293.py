"""Division 293 tax: the pure measure (ADR-0019 s10). No I/O, no clock, no Rule pack.

Division 293 income is taxable income plus reportable fringe benefits plus total net investment
losses plus low tax contributions. Low tax contributions are the concessional contributions the fund
received in the year, less any excess above the cap. Reportable employer super is **not** added on
top: it is part of the concessional contributions already, and adding it would count it twice.

The liability is the rate times the **lesser** of the low tax contributions and the Division 293
income above the threshold, and nothing when Division 293 income is at or below the threshold.

The service builds a ``Div293Input`` (and says where each figure came from); this module only does
the arithmetic, in integer minor units, through the ATO rounding step in ``core.money``. It is kept
apart from ``engine`` so an estimate's saved shape and version are untouched by it. Unused cap from
earlier years (carry-forward) and the tax on excess contributions are not modelled.

``emission`` is the one value projection will later map (ADR-0019 s10): the Member, the financial
year, the amount and the payer, which stays unset until the payer Assumption exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from finance360.capabilities.tax.record_types.base import Operand, Step
from finance360.core.money import BASE_CURRENCY, Money, ato_scale, format_amount
from finance360.core.rate import PPM, Rate

ZERO = Money(0, BASE_CURRENCY)

#: Within this share of the cap (ninths of ten: 90%) the cap check warns.
NEAR_CAP_NUMERATOR = 9
NEAR_CAP_DENOMINATOR = 10

OK = "ok"
NEAR = "near"
OVER = "over"


@dataclass(frozen=True)
class Div293Rules:
    threshold: Money
    rate: Rate
    #: The concessional contributions cap for the financial year.
    cap: Money


@dataclass(frozen=True)
class Div293Input:
    member_id: int
    financial_year: int
    taxable_income: Money
    reportable_fringe_benefits: Money
    net_investment_losses: Money
    #: Concessional contributions the fund received in the year (before the cap is applied).
    concessional_contributions: Money
    rules: Div293Rules


@dataclass(frozen=True)
class Div293Result:
    member_id: int
    financial_year: int
    #: Concessional contributions received over the cap.
    excess_contributions: Money
    low_tax_contributions: Money
    div293_income: Money
    #: Division 293 income above the threshold, never below zero.
    income_over_threshold: Money
    #: The lesser of the low tax contributions and the income over the threshold.
    taxed_amount: Money
    liability: Money
    #: ``ok``, ``near`` (within 10% of the cap) or ``over``.
    cap_status: str
    steps: tuple[Step, ...]

    @property
    def applies(self) -> bool:
        return self.liability.amount_minor > 0


@dataclass(frozen=True)
class Div293Emission:
    """What projection will map later: a financial year's liability, by Member (ADR-0019 s10)."""

    member_id: int
    financial_year: int
    amount: Money
    #: ``super`` or ``own``; unset until the payer Assumption exists.
    payer: str | None = None


def _pos(money: Money) -> Money:
    return money if money.amount_minor > 0 else ZERO


def _op(money: Money, label: str, operator: str | None = None) -> Operand:
    return Operand(format_amount(money), "AUD", label, operator)


def cap_status(received: Money, cap: Money) -> str:
    """``over`` above the cap, ``near`` from 90% of it, else ``ok``."""
    if received > cap:
        return OVER
    if received.amount_minor * NEAR_CAP_DENOMINATOR >= cap.amount_minor * NEAR_CAP_NUMERATOR:
        return NEAR
    return OK


def calculate(inp: Div293Input) -> Div293Result:
    rules = inp.rules
    received = inp.concessional_contributions
    excess = _pos(received - rules.cap)
    low_tax = received - excess
    income = (
        inp.taxable_income + inp.reportable_fringe_benefits + inp.net_investment_losses + low_tax
    )
    over = _pos(income - rules.threshold)
    taxed = low_tax if low_tax < over else over
    liability = ato_scale(taxed, rules.rate.ppm, PPM)
    steps = (
        Step(
            "Excess concessional contributions",
            (
                _op(received, "received by the fund"),
                _op(rules.cap, "cap for the year", "-"),
            ),
            _op(excess, "excess"),
            "Never below zero: an amount at or under the cap has no excess.",
        ),
        Step(
            "Low tax contributions",
            (_op(received, "received by the fund"), _op(excess, "excess", "-")),
            _op(low_tax, "low tax contributions"),
        ),
        Step(
            "Division 293 income",
            (
                _op(inp.taxable_income, "taxable income"),
                _op(inp.reportable_fringe_benefits, "reportable fringe benefits", "+"),
                _op(inp.net_investment_losses, "net investment losses", "+"),
                _op(low_tax, "low tax contributions", "+"),
            ),
            _op(income, "Division 293 income"),
            "Reportable employer super is not added: it is already in the contributions.",
        ),
        Step(
            "Income over the threshold",
            (_op(income, "Division 293 income"), _op(rules.threshold, "threshold", "-")),
            _op(over, "over the threshold"),
            "Never below zero: nothing is due when Division 293 income is at or under it.",
        ),
        Step(
            "Amount taxed",
            (
                _op(low_tax, "low tax contributions"),
                _op(over, "income over the threshold", "or"),
            ),
            _op(taxed, "the lesser of the two"),
            "Division 293 tax applies to the lesser of the two amounts.",
        ),
        Step(
            "Division 293 tax",
            (
                _op(taxed, "amount taxed"),
                Operand(rules.rate.to_percent(), "%", "Division 293 rate", "×"),
            ),
            _op(liability, "Division 293 tax"),
        ),
    )
    return Div293Result(
        member_id=inp.member_id,
        financial_year=inp.financial_year,
        excess_contributions=excess,
        low_tax_contributions=low_tax,
        div293_income=income,
        income_over_threshold=over,
        taxed_amount=taxed,
        liability=liability,
        cap_status=cap_status(received, rules.cap),
        steps=steps,
    )


def emission(result: Div293Result) -> Div293Emission:
    return Div293Emission(result.member_id, result.financial_year, result.liability)
