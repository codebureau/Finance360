"""AU Rule pack (ADR-0018): ages fixed by law, and the Superannuation Guarantee parameters.

Typed data in code, changed only by a release. A change to any value, key, source or age result
needs a ``REVISION`` bump and the pinned hash in ``tests/test_rulepack_au.py`` updated.

Revision 2 (B7) adds ``sg_rate`` and ``contributions_tax_rate`` for FY 2021-22 to
2026-27 (``super_rates``), both ``cpi`` parameters. The last known FY is 2026-27 (``2027``), so
the version label is ``au/2026-27/r3``. Age Pension parameters arrive with B11.

This pack also carries the ``tax.`` keys (``tax_specs``: declarations, no figures) and their values
(``tax_figures``: written by ``scripts/rulepack_update.py``, which bumps ``REVISION``). A key
joins the pack only once it has a figure, so a pack with none captured is unchanged.
"""

from __future__ import annotations

from finance360.core.rulepack import CPI, RATE, AgeRule, Param, RulePack, Value
from finance360.rulepacks import capture
from finance360.rulepacks.au import ages, super_rates, tax_figures, tax_specs

_TAX = capture.load_figures(
    tax_specs.SPECS, tax_figures.FIGURES, tax_figures.PROVISIONAL, tax_figures.RETRIEVED
)
_TAX_PARAMS, _TAX_YEARS = capture.pack_parts(tax_specs.SPECS, _TAX)

_YEARS: dict[int, dict[str, Value]] = {
    fy: {
        "sg_rate": super_rates.SG_RATE[fy],
        "contributions_tax_rate": super_rates.CONTRIBUTIONS_TAX_RATE[fy],
    }
    for fy in sorted(super_rates.SG_RATE)
}
for _fy, _values in _TAX_YEARS.items():
    _YEARS.setdefault(_fy, {}).update(_values)

REVISION = 7

PACK = RulePack(
    pack_id="au",
    jurisdiction="au",
    revision=REVISION,
    params=(
        Param("sg_rate", RATE, CPI, super_rates.SG_RATE_SOURCE),
        Param(
            "contributions_tax_rate",
            RATE,
            CPI,
            super_rates.CONTRIBUTIONS_TAX_RATE_SOURCE,
        ),
        *_TAX_PARAMS,
    ),
    years=_YEARS,
    provisional=_TAX.provisional,
    retrieved=_TAX.retrieved,
    declared=tuple(spec.to_param() for spec in tax_specs.SPECS),
    specs=tuple(tax_specs.SPECS),
    ages=(
        AgeRule(
            "preservation_age",
            "Preservation age",
            ages.PRESERVATION_AGE_SOURCE,
            ages.preservation_age,
        ),
        AgeRule(
            "unrestricted_super_age",
            "Unrestricted super age",
            ages.UNRESTRICTED_SUPER_AGE_SOURCE,
            ages.unrestricted_super_age,
        ),
        AgeRule(
            "age_pension_age",
            "Age Pension age",
            ages.AGE_PENSION_AGE_SOURCE,
            ages.age_pension_age,
        ),
    ),
)
