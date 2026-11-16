"""Invented tax rules and input builders for the tax tests.

EVERY FIGURE HERE IS INVENTED. They are deliberately unlike any official rate, threshold or cap, and
the financial years are fictional (2096 and 2097), so a test of the engine's logic can never be read
as a statement of the law. The engine is tested for what it does with the rules it is given, never
for what the rules should be; official figures reach the product only through the guided capture
(``scripts/rulepack_update.py``).
"""

from __future__ import annotations

from datetime import date
from typing import cast

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import (
    TAX_INPUT_VERSION,
    MemberInput,
    Provenance,
    TaxInput,
    Total,
)
from finance360.capabilities.tax.rules import Band, TaxRules
from finance360.core.money import Money, parse_amount
from finance360.core.rate import Rate
from finance360.core.rulepack import RulePack

FY = 2097  # a fictional year with 365 days
LEAP_FY = 2096  # a fictional year containing 29 February


def aud(text: str) -> Money:
    return parse_amount(text, "AUD")


def pct(text: str) -> Rate:
    return Rate.from_percent(text)


def invented_rules(
    fy: int = FY,
    *,
    provisional: tuple[tuple[str, str], ...] = (),
    instant: str | None = "800",
) -> TaxRules:
    return TaxRules(
        financial_year=fy,
        resident_brackets=(
            Band(aud("0"), pct("0")),
            Band(aud("12000"), pct("7")),
            Band(aud("41000"), pct("22")),
            Band(aud("93000"), pct("37")),
            Band(aud("160000"), pct("51")),
        ),
        lito_max=aud("450"),
        lito_taper1_from=aud("30000"),
        lito_taper1_rate=pct("3"),
        lito_taper2_from=aud("38000"),
        lito_taper2_rate=pct("1"),
        medicare_levy_rate=pct("3"),
        medicare_single_lower=aud("20000"),
        medicare_family_lower=aud("33000"),
        medicare_family_per_child=aud("3000"),
        medicare_shade_in_rate=pct("9"),
        medicare_family_reduction_rate=pct("6"),
        mls_single_tier1=aud("90000"),
        mls_single_tier2=aud("105000"),
        mls_single_tier3=aud("140000"),
        mls_family_tier1=aud("180000"),
        mls_family_tier2=aud("210000"),
        mls_family_tier3=aud("280000"),
        mls_rate_tier1=pct("0.8"),
        mls_rate_tier2=pct("1.2"),
        mls_rate_tier3=pct("1.8"),
        mls_family_per_child_after_first=aud("1500"),
        help_t1=aud("50000"),
        help_r1=pct("2"),
        help_t2=aud("60000"),
        help_r2=pct("4"),
        help_t3=aud("80000"),
        help_top_rate=pct("7"),
        sbito_rate=pct("12"),
        sbito_cap=aud("900"),
        cgt_discount_rate=pct("40"),
        standard_work_deduction=aud(instant) if instant is not None else None,
        provisional=provisional,
    )


def member(member_id: int, /, **totals: str | int | bool) -> MemberInput:
    """A Member with totals given by label key: amounts as text, days as int, flags as bool."""
    typed: list[tuple[str, Total]] = []
    for key, raw in totals.items():
        if labels.LABELS[key].kind == labels.MONEY:
            typed.append((key, aud(str(raw))))
        else:
            typed.append((key, cast(Total, raw)))
    return MemberInput(member_id, date(1980, 1, 1), tuple(typed))


def invented_pack(
    fy: int = FY,
    *,
    drop: tuple[str, ...] = (),
    provisional: dict[str, str] | None = None,
    instant: str | None = "800",
) -> RulePack:
    """The shipped AU pack with its tax figures replaced by the invented ones for ``fy`` only.

    ``drop`` removes pack keys from that year (a partly captured year); ``provisional`` marks keys.
    """
    from finance360.capabilities.tax.pack_rules import OPTIONAL_KEY, REQUIRED
    from finance360.core.rulepack import TAX_PREFIX, Bracket, Value
    from finance360.rulepacks.au import PACK
    from finance360.rulepacks.au.tax_specs import SPECS

    rules = invented_rules(fy, instant=instant)
    values: dict[str, Value] = {}
    for attribute, key in REQUIRED.items():
        value = getattr(rules, attribute)
        values[key] = (
            tuple(Bracket(b.over, b.rate) for b in value) if isinstance(value, tuple) else value
        )
    if rules.standard_work_deduction is not None:
        values[OPTIONAL_KEY] = rules.standard_work_deduction
    for key in drop:
        del values[key]
    years = {
        year: {k: v for k, v in held.items() if not k.startswith(TAX_PREFIX)}
        for year, held in PACK.years.items()
    }
    years.setdefault(fy, {}).update(values)
    return RulePack(
        pack_id=PACK.pack_id,
        jurisdiction=PACK.jurisdiction,
        revision=PACK.revision,
        params=(
            *(p for p in PACK.params if not p.key.startswith(TAX_PREFIX)),
            *(s.to_param() for s in SPECS if s.key in values),
        ),
        years=years,
        ages=PACK.ages,
        provisional={fy: dict(provisional)} if provisional else {},
    )


def tax_input(
    *members: MemberInput,
    fy: int = FY,
    dependants: int = 0,
    rules: TaxRules | None = None,
) -> TaxInput:
    return TaxInput(
        input_version=TAX_INPUT_VERSION,
        financial_year=fy,
        members=members,
        dependants=dependants,
        rules=rules or invented_rules(fy),
        provenance=(Provenance("rule_pack", "invented/test"),),
    )
