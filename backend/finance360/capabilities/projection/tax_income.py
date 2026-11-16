"""Tax into the projection: take-home pay and Division 293 timing (ADR-0019 s1 and s10).

Used only by ``projection.service``, which is the one place in this Capability that may use Tax
(import-linter keeps the projection's pure modules and engine clear of it). It calls Tax's *pure*
modules and nothing else of Tax: ``input``, ``engine``, ``div293`` and ``pack_rules``. The
projection engine and ``ProjectionInput`` do not change: this module only decides the amounts the
service already puts into an ``Inflow`` (take-home pay) and, for a Member who pays their own
Division 293, an ``Outflow``.

For each projection year with a salary in force, it builds a ``TaxInput`` for the Household:

* each Member's salary is the sum of their streams' gross pay for that year (the stated gross grown
  by the real wage growth, exactly as ``income.flows`` grows it), and nothing else is entered, so
  deductions, HELP, other income and dependants are **not** modelled and the working says so;
* the Rule pack's figures for the year come from ``pack_rules.build_carried_rules``: the exact
  year when the pack holds it, otherwise carried forward from the latest year that does (ADR-0018
  section 3: ``cpi`` repeats, ``frozen`` thresholds fall in real terms by the run's inflation);
* a Member's take-home pay is their gross pay less their total tax from the engine (income tax,
  Medicare levy, surcharge), split across their streams in proportion to gross with ``allocate``
  so the parts sum exactly.

**Division 293** (one-year lag, ADR-0019 section 10). The liability for financial year N is paid in
projection year ``t + 1``, from this module's own estimate for year ``t`` (``div293.calculate`` on
the year's taxable income and the employer super paid). The first projected year also pays the
liability for the year just ended, which is estimated from year-0 figures. A payment that would fall
after the horizon is listed but not paid. Both payers use the one lag function ``payment_year``: the
``own`` payer becomes an ``Outflow`` and the ``super`` payer a ``LineDebit`` on the Member's own
``superannuation`` line (ADR-0028).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from finance360.capabilities.projection import assumptions, income
from finance360.capabilities.projection.input import REF_MAX, LineDebit, Outflow
from finance360.capabilities.tax import div293, engine, pack_rules, prefill
from finance360.capabilities.tax.input import (
    TAX_INPUT_VERSION,
    InputError,
    MemberInput,
    Provenance,
    TaxInput,
)
from finance360.core import income_types
from finance360.core.money import BASE_CURRENCY, Money, allocate, scale
from finance360.core.rate import PPM, Rate
from finance360.core.rulepack import RulePack, fy_label

#: The Projection Category and ``LineDebit.reason`` a Division 293 paid by the fund uses.
SUPER_CATEGORY = "superannuation"
DIV293_REASON = "div293"
#: The Return label the salary of a stream is entered on.
SALARY_LABEL = prefill.LABEL_FOR_KIND[income_types.EMPLOYMENT]
#: The most Members one estimate covers (``tax.input.MAX_MEMBERS``).
MAX_MEMBERS = 2
#: Division 293 is paid this many projection years after the year it is for (ADR-0019 section 10).
LAG_YEARS = 1


def payment_year(liability_year: int) -> int:
    """The projection year in which the Division 293 for projection year ``liability_year`` is paid:
    the year whose financial year is the next one (ADR-0019 section 10, ADR-0028). The one place
    the lag is decided, shared by the ``own`` Outflow and the ``super`` line debit (ADR-0028). A
    result at or past the horizon falls after it, and the caller drops that payment with a note."""
    return liability_year + LAG_YEARS


class TaxIncomeError(ValueError):
    """Tax cannot be estimated for this projection; the message says why."""


@dataclass(frozen=True)
class MemberYear:
    member_id: int
    gross: Money
    #: Income tax, Medicare levy and any surcharge (the engine's total tax).
    tax: Money
    take_home: Money


@dataclass(frozen=True)
class YearUsed:
    """One projection year's estimate, with the figures it used."""

    t: int
    fy: int
    members: tuple[MemberYear, ...]
    #: The oldest financial year a Rule pack figure was taken from.
    source_fy: int
    #: How many frozen thresholds were eroded by inflation (carried from an earlier year).
    frozen_eroded: int

    @property
    def carried(self) -> bool:
        return self.source_fy < self.fy

    @property
    def gross(self) -> Money:
        return _total(m.gross for m in self.members)

    @property
    def tax(self) -> Money:
        return _total(m.tax for m in self.members)

    @property
    def take_home(self) -> Money:
        return _total(m.take_home for m in self.members)


@dataclass(frozen=True)
class Div293Used:
    """One Member's Division 293 for one financial year, how it was reached and when it is paid."""

    member_id: int
    #: The financial year the liability is for.
    for_fy: int
    amount: Money
    #: ``own`` or ``super``.
    payer: str
    #: The projection year it is paid in; None when it falls after the horizon (or, in a run saved
    #: by engine 1 with the ``super`` payer, was not modelled).
    paid_year: int | None
    #: True for every payment of a new run. False only in a run saved by engine 1, whose ``super``
    #: payer was not yet modelled (nothing was taken): kept so that run still displays as computed.
    modelled: bool
    #: True for the year just before the first projected year, estimated from year-0 figures.
    estimated_from_year_0: bool
    taxable_income: Money
    low_tax_contributions: Money
    div293_income: Money
    threshold: Money
    rate: Rate


@dataclass(frozen=True)
class PayerUsed:
    member_id: int
    payer: str
    #: ``scenario`` or ``household`` for a stated payer, ``default`` when none was stated.
    layer: str


@dataclass(frozen=True)
class DebitUsed:
    """A ``LineDebit`` with the source that produced it (ADR-0028 s5), kept with a saved run."""

    debit: LineDebit
    #: The financial year the Division 293 is for.
    for_fy: int
    estimated_from_year_0: bool
    #: Which layer stated the Member's ``div293_payer``: ``scenario``, ``household`` or ``default``.
    payer_layer: str
    rule_pack_version: str


@dataclass(frozen=True)
class TaxUsed:
    """What a run recorded about tax (kept with a saved run, so a later engine or pack change
    never changes it)."""

    tax_engine_version: int
    tax_input_version: int
    rule_pack_version: str
    years: tuple[YearUsed, ...]
    payments: tuple[Div293Used, ...]
    payers: tuple[PayerUsed, ...]
    #: True when the pack lacks the Division 293 figures, so none was estimated.
    div293_unavailable: bool = False

    def outflows(self) -> tuple[Outflow, ...]:
        """The Division 293 the household pays itself, one ``Outflow`` per year it is paid."""
        by_year: dict[int, Money] = {}
        zero = Money.zero(BASE_CURRENCY)
        for p in self.payments:
            if p.payer == assumptions.PAYER_OWN and p.modelled and p.paid_year is not None:
                by_year[p.paid_year] = by_year.get(p.paid_year, zero) + p.amount
        return tuple(Outflow(amount, year, year + 1) for year, amount in sorted(by_year.items()))

    def debits(self) -> tuple[DebitUsed, ...]:
        """The Division 293 the Member's fund pays: one ``LineDebit`` per payment, on that
        Member's own ``superannuation`` line in the year it is paid (ADR-0028 s3)."""
        layer = {p.member_id: p.layer for p in self.payers}
        found: list[DebitUsed] = []
        for p in self.payments:
            if p.payer != assumptions.PAYER_SUPER or not p.modelled or p.paid_year is None:
                continue
            ref = f"FY{fy_label(p.for_fy)}"
            if p.estimated_from_year_0:
                ref += ", estimated from year-0 figures"
            debit = LineDebit(
                p.member_id, SUPER_CATEGORY, p.paid_year, p.amount, DIV293_REASON, ref[:REF_MAX]
            )
            found.append(
                DebitUsed(
                    debit,
                    p.for_fy,
                    p.estimated_from_year_0,
                    layer.get(p.member_id, "default"),
                    self.rule_pack_version,
                )
            )
        return tuple(found)


@dataclass(frozen=True)
class Estimated:
    """The take-home pay to bake into each stream's ``Inflow``, and what was used."""

    #: Stream id -> projection year -> estimated take-home pay.
    take_home: Mapping[int, Mapping[int, Money]]
    used: TaxUsed


def _total(values: Iterable[Money]) -> Money:
    total = Money.zero(BASE_CURRENCY)
    for value in values:
        total = total + value
    return total


def label_of(stream: income.Stream) -> str:
    """The Return label a stream's gross is entered on, or empty when it has none."""
    kind = income_types.TYPES.get(stream.type_key)
    return prefill.LABEL_FOR_KIND.get(kind.tax_kind, "") if kind else ""


def estimate(
    *,
    pack: RulePack,
    first_fy: int,
    years: int,
    inflation: Rate,
    members: Sequence[tuple[int, date]],
    streams: Sequence[income.Stream],
    spans: Sequence[income.Span],
    growth: Mapping[int, Rate],
    rates: income.RatesFor | None,
    payers: Mapping[int, PayerUsed],
) -> Estimated:
    """Estimate take-home pay for every year a taxed stream is in force, and Division 293.

    ``growth`` is the real wage growth for each stream owner. ``rates`` is the SG rate by year (None
    when no stream pays employer super). ``payers`` holds each Member's payer (a Member absent pays
    the default, ``super``).
    """
    if not 1 <= len(members) <= MAX_MEMBERS:
        raise TaxIncomeError(
            f"Tax in the projection needs one or {MAX_MEMBERS} Members; this Household has "
            f"{len(members)}."
        )
    zero = Money.zero(BASE_CURRENCY)
    taxed = [(st, sp) for st, sp in zip(streams, spans, strict=True) if label_of(st)]
    take_home: dict[int, dict[int, Money]] = {st.id: {} for st, _ in taxed}
    year_rows: list[YearUsed] = []
    payments: list[Div293Used] = []
    unavailable = False

    for t in range(years):
        fy = first_fy + t
        live = [
            (st, sp, income.grown(st.gross_annual, growth[st.member_id], t))
            for st, sp in taxed
            if sp.in_force(t)
        ]
        if not live:
            continue
        gross_of: dict[int, Money] = {}
        for st, _sp, gross in live:
            gross_of[st.member_id] = gross_of.get(st.member_id, zero) + gross
        try:
            carried = pack_rules.build_carried_rules(pack, fy, inflation)
            tax_input = TaxInput(
                TAX_INPUT_VERSION,
                fy,
                tuple(
                    MemberInput(
                        member_id,
                        born_on,
                        ((SALARY_LABEL, gross_of[member_id]),) if member_id in gross_of else (),
                    )
                    for member_id, born_on in members
                ),
                0,
                carried.rules,
                (Provenance("rule_pack", pack.version_label),),
            )
        except (pack_rules.TaxRulesError, InputError) as error:
            raise TaxIncomeError(str(error)) from None
        result = engine.estimate(tax_input)
        by_member = {r.member_id: r for r in result.members}

        member_rows: list[MemberYear] = []
        for member_id, _born in members:
            gross = gross_of.get(member_id)
            if gross is None:
                continue
            tax = by_member[member_id].total_tax
            member_rows.append(MemberYear(member_id, gross, tax, gross - tax))
            mine = [(st, g) for st, _sp, g in live if st.member_id == member_id]
            shares = allocate(tax, {st.id: g.amount_minor for st, g in mine})
            for st, g in mine:
                take_home[st.id][t] = g - shares[st.id]
        year_rows.append(
            YearUsed(t, fy, tuple(member_rows), carried.source_fy, carried.frozen_eroded)
        )

        rules293 = pack_rules.carried_div293_rules(pack, fy, inflation)
        if rules293 is None:
            unavailable = True
            continue
        for member_id, _born in members:
            if member_id not in gross_of:
                continue
            paid = zero
            if rates is not None:
                for st, _sp, gross in live:
                    if st.member_id == member_id and income_types.TYPES[st.type_key].employer_super:
                        paid = paid + scale(gross, rates.sg[t].ppm, PPM)
            calc = div293.calculate(
                div293.Div293Input(
                    member_id,
                    fy,
                    by_member[member_id].taxable_income,
                    zero,
                    zero,
                    paid,
                    rules293,
                )
            )
            if not calc.applies:
                continue
            payer = payers.get(member_id) or PayerUsed(
                member_id, assumptions.DEFAULT_PAYER, "default"
            )
            taxable = by_member[member_id].taxable_income
            payments.append(_payment(calc, rules293, payer, taxable, payment_year(t), years, False))
            if t == 0:  # the year just ended, estimated from year-0 figures (ADR-0019 section 10)
                payments.append(
                    _payment(calc, rules293, payer, taxable, payment_year(t - 1), years, True, -1)
                )

    payments.sort(
        key=lambda p: (
            p.paid_year if p.paid_year is not None else years,
            p.member_id,
            p.for_fy,
        )
    )
    used = TaxUsed(
        engine.TAX_ENGINE_VERSION,
        TAX_INPUT_VERSION,
        pack.version_label,
        tuple(year_rows),
        tuple(payments),
        tuple(payers[m] for m in sorted(payers)),
        unavailable,
    )
    return Estimated(take_home, used)


def _payment(
    calc: div293.Div293Result,
    rules: div293.Div293Rules,
    payer: PayerUsed,
    taxable_income: Money,
    paid_year: int,
    years: int,
    estimated: bool,
    fy_offset: int = 0,
) -> Div293Used:
    return Div293Used(
        calc.member_id,
        calc.financial_year + fy_offset,
        calc.liability,
        payer.payer,
        paid_year if paid_year < years else None,
        True,
        estimated,
        taxable_income,
        calc.low_tax_contributions,
        calc.div293_income,
        rules.threshold,
        rules.rate,
    )
