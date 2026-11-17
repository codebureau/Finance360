"""AU Rule pack v1 (ADR-0018): golden tests at every transition date of the three ages, and
the test that pins the pack's revision to its content hash.

The expected ages are the published schedules (see ``rulepacks/au/ages.py`` for the sources),
written out here independently of the tables in code.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from finance360.core.rate import Rate
from finance360.core.rulepack import Age, materialise
from finance360.rulepacks import capture
from finance360.rulepacks.au import PACK, ages, tax_figures, tax_specs

#: (first date of birth of the band, age for the band). The day before each is the previous band.
PRESERVATION = [
    (date(1960, 7, 1), Age(56)),
    (date(1961, 7, 1), Age(57)),
    (date(1962, 7, 1), Age(58)),
    (date(1963, 7, 1), Age(59)),
    (date(1964, 7, 1), Age(60)),
]
AGE_PENSION = [
    (date(1952, 7, 1), Age(65, 6)),
    (date(1954, 1, 1), Age(66)),
    (date(1955, 7, 1), Age(66, 6)),
    (date(1957, 1, 1), Age(67)),
]


def bands(transitions: list[tuple[date, Age]], before_all: Age) -> list[tuple[date, Age, Age]]:
    """Each transition with the age on the day before it."""
    out = []
    previous = before_all
    for first, age in transitions:
        out.append((first, previous, age))
        previous = age
    return out


@pytest.mark.parametrize(
    ("first", "before", "on"), bands(PRESERVATION, Age(55)), ids=lambda v: str(v)
)
def test_preservation_age_changes_exactly_on_each_transition_date(
    first: date, before: Age, on: Age
) -> None:
    assert ages.preservation_age(first - timedelta(days=1)) == before
    assert ages.preservation_age(first) == on
    assert ages.preservation_age(first + timedelta(days=364)) == on  # the rest of that band


@pytest.mark.parametrize(
    ("first", "before", "on"), bands(AGE_PENSION, Age(65)), ids=lambda v: str(v)
)
def test_age_pension_age_changes_exactly_on_each_transition_date(
    first: date, before: Age, on: Age
) -> None:
    assert ages.age_pension_age(first - timedelta(days=1)) == before
    assert ages.age_pension_age(first) == on


def test_the_ends_of_both_schedules() -> None:
    assert ages.preservation_age(date(1940, 1, 1)) == Age(55)
    assert ages.preservation_age(date(2010, 5, 5)) == Age(60)
    assert ages.age_pension_age(date(1940, 1, 1)) == Age(65)
    assert ages.age_pension_age(date(2010, 5, 5)) == Age(67)


def test_age_pension_age_has_half_year_steps_held_as_exact_months() -> None:
    assert ages.age_pension_age(date(1953, 12, 31)) == Age(65, 6)
    assert ages.age_pension_age(date(1953, 12, 31)).total_months == 786
    assert ages.age_pension_age(date(1956, 12, 31)) == Age(66, 6)


def test_the_unrestricted_super_age_is_65_for_everyone() -> None:
    for born in (date(1950, 1, 1), date(1984, 3, 12), date(2020, 6, 30)):
        assert ages.unrestricted_super_age(born) == Age(65)


def test_the_pack_serves_all_three_by_key_and_each_has_a_source() -> None:
    born = date(1955, 12, 31)
    assert {r.key: r.evaluate(born) for r in PACK.ages} == {
        "preservation_age": Age(55),
        "unrestricted_super_age": Age(65),
        "age_pension_age": Age(66, 6),
    }
    assert all(r.source and r.label for r in PACK.ages)
    assert PACK.keys == (
        "sg_rate",
        "contributions_tax_rate",
        "preservation_age",
        "unrestricted_super_age",
        "age_pension_age",
    )


#: The Superannuation Guarantee charge percentage by financial year (the year it ends in), written
#: out independently of ``rulepacks/au/super_rates.py``. UNVERIFIED against the ATO page:
#: the owner confirms them at review.
SG_BY_FY = {2022: "10", 2023: "10.5", 2024: "11", 2025: "11.5", 2026: "12", 2027: "12"}


def test_the_sg_rate_and_contributions_tax_are_the_legislated_steps_for_each_year() -> None:
    for fy, percent in SG_BY_FY.items():
        assert PACK.years[fy]["sg_rate"] == Rate.from_percent(percent), fy
        assert PACK.years[fy]["contributions_tax_rate"] == Rate.from_percent("15"), fy
    assert sorted(PACK.years) == sorted(SG_BY_FY)


def test_the_sg_rate_steps_up_on_1_july_and_stays_at_12_percent_after_the_last_year() -> None:
    # A projection starting in FY 2023-24 (2023-10-25) sees 11%, 11.5% from 1 July 2024 (its
    # second year), 12% from 1 July 2025 and then 12% for ever (a cpi parameter carries forward).
    spans = materialise(PACK, 2024, 8, Rate.zero())
    assert [(sp.first_t, sp.values["sg_rate"]) for sp in spans] == [
        (0, Rate.from_percent("11")),
        (1, Rate.from_percent("11.5")),
        (2, Rate.from_percent("12")),
    ]
    assert all(sp.values["contributions_tax_rate"] == Rate.from_percent("15") for sp in spans)


def test_every_pack_parameter_names_its_source_and_says_it_is_unverified() -> None:
    # The tax. keys are captured by the guided script, which records the retrieval date instead.
    for param in PACK.params:
        if not param.key.startswith("tax."):
            assert param.source and "unverified" in param.source


def test_the_declared_tax_figures_are_well_formed_and_carry_no_value_in_the_specs() -> None:
    keys = [spec.key for spec in tax_specs.SPECS]
    assert len(set(keys)) == len(keys)
    for spec in tax_specs.SPECS:
        assert spec.key.startswith("tax.")
        assert spec.page_url.startswith("https://") and spec.where and spec.source
        assert spec.low >= 0 and spec.high > spec.low
        assert spec.kind != "money" or spec.unit in ("year", "fortnight", "hour", "km")
        assert spec.to_param().key == spec.key


def test_the_shipped_tax_figures_load_and_every_held_key_is_declared() -> None:
    data = capture.load_figures(
        tax_specs.SPECS, tax_figures.FIGURES, tax_figures.PROVISIONAL, tax_figures.RETRIEVED
    )
    declared = {spec.key for spec in tax_specs.SPECS}
    held = {key for values in data.figures.values() for key in values}
    assert held <= declared
    assert {p.key for p in PACK.params if p.key.startswith("tax.")} == held


# --- the pin -------------------------------------------------------------------------------

#: ADR-0018 section 4. If this test fails you changed pack data (a value, key, schema or source)
#: without recording it: bump ``rulepacks.au.REVISION``, then update both numbers here. A change to
#: an age function's result has no hash; the golden tests above fail for it, and it needs a bump
#: too.
PINNED_REVISION = 8
PINNED_HASH = "8c3a0f41389d78db6676360d9350cfa3206a9fe4ff6fea18b280e2b945227767"


def test_the_revision_and_content_hash_are_pinned() -> None:
    assert (PACK.revision, PACK.content_hash()) == (PINNED_REVISION, PINNED_HASH), (
        "AU pack data changed. Bump REVISION in rulepacks/au/__init__.py and update the pin."
    )


def test_the_version_label_is_what_a_saved_run_records() -> None:
    assert PACK.version_label == "au/2026-27/r8"
