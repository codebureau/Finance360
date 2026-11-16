"""The tax estimate engine (ADR-0019 s3). Pure functions over an immutable ``TaxInput``.

``estimate(TaxInput) -> TaxResult`` is the only entry point. No I/O, no clock, no Rule pack and no
database: the service builds the ``TaxRules`` from the pack and the input carries them. The result
carries ``TAX_ENGINE_VERSION``; any change to what the engine returns for a given input bumps it,
and a golden test pins it (ADR-0015 s3).

Per Member first, then the family tests that need both Members (the Medicare levy reduction and the
Medicare levy surcharge). Two Members are assessed as a couple. Every rate applies through the named
ATO steps in ``core.money`` (the only place tax rounds); taxable income is truncated to whole
dollars. All figures are integer minor units of AUD.

**Unmodelled items are reported, never ignored.** Every Return label with a non-zero total that the
registry marks as not modelled is listed in the result, and the estimate is marked ``incomplete``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import MemberInput, TaxInput, days_in_fy
from finance360.capabilities.tax.rules import Band, TaxRules
from finance360.core.money import BASE_CURRENCY, Money, ato_scale, ato_whole_dollars, format_amount
from finance360.core.rate import PPM, Rate

TAX_ENGINE_VERSION = 2

ZERO = Money(0, BASE_CURRENCY)

SINGLE = "single"
FAMILY = "family"


@dataclass(frozen=True)
class MemberResult:
    member_id: int
    total_income: Money
    total_deductions: Money
    taxable_income: Money
    #: The reportable fringe benefits (grossed up, as the employer reports them).
    reportable_fringe_benefits: Money
    #: Total net investment losses: financial investment loss plus net rental property loss.
    net_investment_losses: Money
    tax_on_taxable_income: Money
    low_income_offset: Money
    small_business_offset: Money
    #: The offsets applied: they cannot take the tax on taxable income below zero.
    offsets: Money
    medicare_levy: Money
    medicare_levy_surcharge: Money
    surcharge_rate: Rate
    help_repayment: Money
    #: Tax on taxable income, less offsets, plus Medicare levy, surcharge and HELP.
    total_tax: Money
    tax_withheld: Money
    franking_credits: Money
    payg_instalments: Money
    credits: Money
    #: Credits less total tax: positive is a refund, negative is an amount payable.
    net_outcome: Money
    notes: tuple[str, ...]


@dataclass(frozen=True)
class HouseholdResult:
    """The Members' figures added: the exact sum of each line."""

    taxable_income: Money
    tax_on_taxable_income: Money
    offsets: Money
    medicare_levy: Money
    medicare_levy_surcharge: Money
    help_repayment: Money
    total_tax: Money
    credits: Money
    net_outcome: Money
    surcharge_income: Money
    #: ``single`` or ``family``: which set of surcharge thresholds applied.
    surcharge_thresholds: str
    surcharge_tier: int


@dataclass(frozen=True)
class Unmodelled:
    """A Return label with a figure that the engine does not read."""

    member_id: int
    label_key: str
    value: Money


@dataclass(frozen=True)
class TaxResult:
    engine_version: int
    input_version: int
    financial_year: int
    #: True when a figure was entered for a Return label the engine does not model.
    incomplete: bool
    #: ``(pack key, note)`` for each rule figure of the year that is not yet law.
    provisional: tuple[tuple[str, str], ...]
    members: tuple[MemberResult, ...]
    household: HouseholdResult
    unmodelled: tuple[Unmodelled, ...]

    @property
    def has_provisional(self) -> bool:
        return bool(self.provisional)


# --- small money helpers -------------------------------------------------------------------


def _pos(money: Money) -> Money:
    return money if money.amount_minor > 0 else ZERO


def _total(values: Sequence[Money]) -> Money:
    return Money(sum(v.amount_minor for v in values), BASE_CURRENCY)


def _rate(money: Money, rate: Rate) -> Money:
    """ATO step: a rate applied to an amount."""
    return ato_scale(money, rate.ppm, PPM)


# --- the tax rules, one function each ------------------------------------------------------


def bracket_tax(income: Money, bands: tuple[Band, ...]) -> Money:
    """Tax on ``income`` from a marginal table; each band's tax rounds on its own."""
    tax = ZERO
    for i, band in enumerate(bands):
        if income <= band.over:
            break
        top = bands[i + 1].over if i + 1 < len(bands) else None
        upper = income if top is None or income < top else top
        tax = tax + _rate(upper - band.over, band.rate)
    return tax


def low_income_offset(taxable: Money, rules: TaxRules) -> Money:
    """The low income tax offset: the maximum, reduced at two rates as income rises."""
    first_to = taxable if taxable < rules.lito_taper2_from else rules.lito_taper2_from
    value = rules.lito_max - _rate(_pos(first_to - rules.lito_taper1_from), rules.lito_taper1_rate)
    value = value - _rate(_pos(taxable - rules.lito_taper2_from), rules.lito_taper2_rate)
    return _pos(value)


def help_repayment(income: Money, rules: TaxRules) -> Money:
    """HELP repayment: nothing to the first threshold, a marginal rate to the third, then a rate on
    all income above it."""
    if income <= rules.help_t1:
        return ZERO
    if income <= rules.help_t2:
        return _rate(income - rules.help_t1, rules.help_r1)
    if income <= rules.help_t3:
        return _rate(rules.help_t2 - rules.help_t1, rules.help_r1) + _rate(
            income - rules.help_t2, rules.help_r2
        )
    return _rate(income, rules.help_top_rate)


@dataclass(frozen=True)
class NetGain:
    net: Money
    loss_carried_forward: Money


def net_capital_gain(discountable: Money, other: Money, losses: Money, rules: TaxRules) -> NetGain:
    """Losses offset gains that are not discountable first, then discountable gains; the discount
    applies to what remains of the discountable gains."""
    from_other = losses if losses < other else other
    rest = losses - from_other
    from_discountable = rest if rest < discountable else discountable
    taxed_share = ato_scale(
        discountable - from_discountable, PPM - rules.cgt_discount_rate.ppm, PPM
    )
    net = (other - from_other) + taxed_share
    return NetGain(net, losses - from_other - from_discountable)


def medicare_levy(
    taxable: Money, family_taxable: Money | None, dependants: int, rules: TaxRules
) -> Money:
    """The levy on ``taxable``, reduced for low income. A family test applies when
    ``family_taxable`` is given; the lower levy wins."""
    full = _rate(taxable, rules.medicare_levy_rate)
    if taxable <= rules.medicare_single_lower:
        individual = ZERO
    else:
        shade = _rate(taxable - rules.medicare_single_lower, rules.medicare_shade_in_rate)
        individual = full if full < shade else shade
    if family_taxable is None:
        return individual
    threshold = rules.medicare_family_lower + rules.medicare_family_per_child * dependants
    family = full
    if family_taxable <= threshold:
        family = ZERO
    else:
        reduction = _rate(threshold, rules.medicare_levy_rate) - _rate(
            family_taxable - threshold, rules.medicare_family_reduction_rate
        )
        if reduction.amount_minor > 0:
            family = _pos(
                full - ato_scale(reduction, taxable.amount_minor, family_taxable.amount_minor)
            )
    return individual if individual < family else family


def surcharge_tier(income: Money, tiers: tuple[Money, Money, Money]) -> int:
    return 3 if income > tiers[2] else 2 if income > tiers[1] else 1 if income > tiers[0] else 0


# --- stage 1: everything that depends only on one Member -----------------------------------


@dataclass(frozen=True)
class _Stage:
    member_id: int
    notes: tuple[str, ...]
    total_income: Money
    total_deductions: Money
    taxable_income: Money
    tax_on_taxable_income: Money
    low_income_offset: Money
    small_business_offset: Money
    offsets: Money
    income_for_tests: Money
    fringe_benefits: Money
    net_investment_losses: Money
    tax_withheld: Money
    franking_credits: Money
    payg_instalments: Money
    exempt_days: int
    help_debt: bool


def _assess(member: MemberInput, inp: TaxInput) -> _Stage:
    rules = inp.rules
    m = member.money
    notes: list[str] = []

    employment = m("salary") + m("allowances")
    interest = m("interest")
    dividends = m("dividends_unfranked") + m("dividends_franked") + m("franking_credits")
    trust = m("trust_income") + m("trust_franking_credits")

    gain = net_capital_gain(m("gains_discountable"), m("gains_other"), m("capital_losses"), rules)
    if gain.loss_carried_forward.amount_minor > 0:
        notes.append(f"Capital loss of {format_amount(gain.loss_carried_forward)} carried forward.")

    net_rent = m("rent_gross") - m("rent_interest") - m("rent_capital_works") - m("rent_other")

    net_business = m("business_income") - m("business_expenses")
    if net_business.amount_minor < 0 and not member.flag("business_loss_rules_met"):
        notes.append(
            f"Business loss of {format_amount(-net_business)} deferred (non-commercial loss "
            "rules not met)."
        )
        net_business = ZERO

    total_income = _total(
        [
            employment,
            interest,
            dividends,
            trust,
            gain.net,
            net_rent,
            net_business,
            m("other_income"),
        ]
    )

    work_actual = _total(
        [m(k) for k in ("work_car", "work_travel", "work_clothing", "work_self_education")]
        + [m("work_other"), m("low_value_pool")]
    )
    work_related = work_actual
    instant = rules.standard_work_deduction
    if (
        instant is not None
        and member.flag("use_instant_deduction")
        and employment.amount_minor > 0
        and work_actual < instant
    ):
        work_related = instant
        notes.append("Instant work-related expenses deduction used in place of D1 to D6.")
    investment_deductions = m("interest_deductions") + m("dividend_deductions")
    other_deductions = _total(
        [
            m("gifts_donations"),
            m("tax_affairs"),
            m("personal_super_contributions"),
            m("other_deductions"),
        ]
    )
    total_deductions = work_related + investment_deductions + other_deductions

    raw = total_income - total_deductions
    taxable = _pos(ato_whole_dollars(raw))
    if raw.amount_minor < 0:
        notes.append(f"Tax loss of {format_amount(-raw)} carried forward.")

    financial_loss = _pos(investment_deductions - (interest + dividends + trust))
    net_investment_loss = financial_loss + _pos(-net_rent)
    reportable_super = m("reportable_employer_super") + m("personal_super_contributions")
    fringe = m("reportable_fringe_benefits")

    basic_tax = bracket_tax(taxable, rules.resident_brackets)
    lito = low_income_offset(taxable, rules)
    sbito = ZERO
    if member.flag("sbito_eligible") and net_business.amount_minor > 0 and taxable.amount_minor > 0:
        business = net_business if net_business < taxable else taxable
        share = ato_scale(basic_tax, business.amount_minor, taxable.amount_minor)
        capped = _rate(share, rules.sbito_rate)
        sbito = capped if capped < rules.sbito_cap else rules.sbito_cap
    wanted = lito + sbito
    offsets = basic_tax if basic_tax < wanted else wanted

    return _Stage(
        member_id=member.member_id,
        notes=tuple(notes),
        total_income=total_income,
        total_deductions=total_deductions,
        taxable_income=taxable,
        tax_on_taxable_income=basic_tax,
        low_income_offset=lito,
        small_business_offset=sbito,
        offsets=offsets,
        income_for_tests=taxable + fringe + reportable_super + net_investment_loss,
        fringe_benefits=fringe,
        net_investment_losses=net_investment_loss,
        tax_withheld=m("tax_withheld") + m("tfn_withheld"),
        franking_credits=m("franking_credits") + m("trust_franking_credits"),
        payg_instalments=m("payg_instalments"),
        exempt_days=member.days("surcharge_exempt_days"),
        help_debt=member.flag("help_debt"),
    )


# --- stage 2: the family tests and the final position --------------------------------------


def _unmodelled(inp: TaxInput) -> tuple[Unmodelled, ...]:
    found: list[Unmodelled] = []
    for member in inp.members:
        for key, value in member.totals:
            label = labels.get(key)
            if label is None or label.modelled or label.direction == labels.MEMO:
                continue
            if isinstance(value, Money) and value.amount_minor != 0:
                found.append(Unmodelled(member.member_id, key, value))
    return tuple(found)


def estimate(inp: TaxInput) -> TaxResult:
    """The Household's tax position for the input's financial year."""
    rules = inp.rules
    stages = [_assess(member, inp) for member in inp.members]
    is_family = inp.has_spouse or inp.dependants > 0

    family_taxable = _total([s.taxable_income for s in stages])
    surcharge_income = _total([s.income_for_tests for s in stages])
    extra = rules.mls_family_per_child_after_first * max(0, inp.dependants - 1)
    family_tiers = (
        rules.mls_family_tier1 + extra,
        rules.mls_family_tier2 + extra,
        rules.mls_family_tier3 + extra,
    )
    single_tiers = (rules.mls_single_tier1, rules.mls_single_tier2, rules.mls_single_tier3)
    tier = surcharge_tier(surcharge_income, family_tiers if is_family else single_tiers)
    tier_rates = (rules.mls_rate_tier1, rules.mls_rate_tier2, rules.mls_rate_tier3)

    year_days = days_in_fy(inp.financial_year)
    members: list[MemberResult] = []
    for stage in stages:
        notes = list(stage.notes)
        levy = medicare_levy(
            stage.taxable_income,
            family_taxable if is_family else None,
            inp.dependants,
            rules,
        )

        rate = Rate.zero() if tier == 0 else tier_rates[tier - 1]
        if (
            rate.ppm > 0
            and inp.has_spouse
            and stage.income_for_tests <= rules.medicare_single_lower
        ):
            rate = Rate.zero()
            notes.append("No surcharge: own income at or below the low-income threshold.")
        full_year = _rate(stage.taxable_income + stage.fringe_benefits, rate)
        liable_days = year_days - stage.exempt_days
        surcharge = ato_scale(full_year, liable_days, year_days)
        if rate.ppm > 0 and liable_days == 0:
            rate = Rate.zero()

        help_amount = help_repayment(stage.income_for_tests, rules) if stage.help_debt else ZERO
        total_tax = stage.tax_on_taxable_income - stage.offsets + levy + surcharge + help_amount
        credits = stage.tax_withheld + stage.franking_credits + stage.payg_instalments
        members.append(
            MemberResult(
                member_id=stage.member_id,
                total_income=stage.total_income,
                total_deductions=stage.total_deductions,
                taxable_income=stage.taxable_income,
                reportable_fringe_benefits=stage.fringe_benefits,
                net_investment_losses=stage.net_investment_losses,
                tax_on_taxable_income=stage.tax_on_taxable_income,
                low_income_offset=stage.low_income_offset,
                small_business_offset=stage.small_business_offset,
                offsets=stage.offsets,
                medicare_levy=levy,
                medicare_levy_surcharge=surcharge,
                surcharge_rate=rate,
                help_repayment=help_amount,
                total_tax=total_tax,
                tax_withheld=stage.tax_withheld,
                franking_credits=stage.franking_credits,
                payg_instalments=stage.payg_instalments,
                credits=credits,
                net_outcome=credits - total_tax,
                notes=tuple(notes),
            )
        )

    unmodelled = _unmodelled(inp)
    household = HouseholdResult(
        taxable_income=family_taxable,
        tax_on_taxable_income=_total([r.tax_on_taxable_income for r in members]),
        offsets=_total([r.offsets for r in members]),
        medicare_levy=_total([r.medicare_levy for r in members]),
        medicare_levy_surcharge=_total([r.medicare_levy_surcharge for r in members]),
        help_repayment=_total([r.help_repayment for r in members]),
        total_tax=_total([r.total_tax for r in members]),
        credits=_total([r.credits for r in members]),
        net_outcome=_total([r.net_outcome for r in members]),
        surcharge_income=surcharge_income,
        surcharge_thresholds=FAMILY if is_family else SINGLE,
        surcharge_tier=tier,
    )
    return TaxResult(
        engine_version=TAX_ENGINE_VERSION,
        input_version=inp.input_version,
        financial_year=inp.financial_year,
        incomplete=bool(unmodelled),
        provisional=rules.provisional,
        members=tuple(members),
        household=household,
        unmodelled=unmodelled,
    )
