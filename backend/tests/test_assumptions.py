"""The pure Assumption resolver and value text (ADR-0016 sections 1 and 2). No database.

Invariants proved with Hypothesis: the resolution precedence, an unset key always errors (naming
it), and a rate round-trips exactly through its text. No floats anywhere.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.projection import assumptions as a
from finance360.capabilities.projection import spending
from finance360.capabilities.projection.assumptions import (
    CATEGORY,
    HOUSEHOLD,
    LAYER_HOUSEHOLD,
    LAYER_SCENARIO,
    MEMBER,
    Row,
)
from finance360.core.money import Money
from finance360.core.rate import PPM, Rate

CATS = a.PROJECTED_CATEGORIES
MEMBERS = (1, 2)
RETIRE = spending.LAST_RETIREMENT


def aud(minor: int) -> Money:
    return Money(minor, "AUD")


#: The Household default spending list the helpers resolve with unless a test says otherwise.
PHASES = (spending.Phase(RETIRE, 0, aud(7_000_000)), spending.Phase(RETIRE, 10, aud(5_500_000)))


def complete(rate: str = "5", age: int = 60) -> list[Row]:
    """A Household default layer that states every key (the first Category differs)."""
    rows = [
        Row("inflation", HOUSEHOLD, "", Rate.from_percent("2.5")),
        Row("annual_saving", HOUSEHOLD, "", Money(0, "AUD")),
        Row("horizon_age", HOUSEHOLD, "", 95),
    ]
    rows += [Row("return_rate", CATEGORY, c, Rate.from_percent(rate)) for c in CATS]
    rows += [Row("retirement_age", MEMBER, str(m), age) for m in MEMBERS]
    return rows


def resolve(
    defaults: list[Row],
    overrides: list[Row] | None = None,
    *,
    default_phases: tuple[spending.Phase, ...] = PHASES,
    override_phases: tuple[spending.Phase, ...] = (),
) -> a.Resolved:
    return a.resolve(
        defaults,
        overrides or [],
        member_ids=MEMBERS,
        default_phases=default_phases,
        override_phases=override_phases,
    )


def test_a_layer_that_states_everything_resolves_with_defaults_as_the_source() -> None:
    resolved = resolve(complete())
    assert resolved.returns["cash"] == Rate.from_percent("5")
    assert resolved.retirement_ages == {1: 60, 2: 60}
    assert {p.layer for p in resolved.provenance} == {LAYER_HOUSEHOLD}


def test_the_scenario_states_only_what_differs_and_the_rest_comes_from_the_default() -> None:
    over = [Row("return_rate", CATEGORY, "cash", Rate.from_percent("1.5"))]
    resolved = resolve(complete(), over)
    assert resolved.returns["cash"] == Rate.from_percent("1.5")
    assert resolved.returns["investments"] == Rate.from_percent("5")
    layers = {(p.key, p.scope_ref): p.layer for p in resolved.provenance}
    assert layers[("return_rate", "cash")] == LAYER_SCENARIO
    assert layers[("return_rate", "investments")] == LAYER_HOUSEHOLD


def test_values_are_absolute_never_deltas() -> None:
    # A Scenario value of 0 means 0, not "no change".
    over = [Row("annual_saving", HOUSEHOLD, "", Money(0, "AUD"))]
    assert resolve(complete(), over).annual_saving == Money(0, "AUD")


def test_the_default_spending_list_is_used_when_the_scenario_states_none() -> None:
    resolved = resolve(complete())
    assert (resolved.spending_phases, resolved.spending_layer) == (PHASES, LAYER_HOUSEHOLD)


def test_a_scenarios_spending_list_replaces_the_default_as_a_whole() -> None:
    # Not merged: the default's second phase does not survive the Scenario's single phase.
    own = (spending.Phase(RETIRE, 0, aud(8_500_000)),)
    resolved = resolve(complete(), override_phases=own)
    assert (resolved.spending_phases, resolved.spending_layer) == (own, LAYER_SCENARIO)


def test_a_scenario_phase_at_another_point_does_not_inherit_the_defaults_phase_there() -> None:
    own = (spending.Phase(spending.START, 0, aud(1_000_000)),)
    assert resolve(complete(), override_phases=own).spending_phases == own


def test_the_resolved_list_is_in_a_fixed_order() -> None:
    shuffled = (PHASES[1], PHASES[0])
    assert resolve(complete(), default_phases=shuffled).spending_phases == PHASES


def test_no_spending_phase_in_any_layer_errors_naming_it() -> None:
    with pytest.raises(a.UnresolvedError) as raised:
        resolve(complete(), default_phases=())
    assert [(m.key, m.scope_ref) for m in raised.value.missing] == [("spending_phases", "")]


def test_the_scenarios_wider_scope_beats_the_defaults_more_specific_one() -> None:
    # ADR-0016 order: scenario specific, scenario wider, default specific, default wider.
    scopes = [(CATEGORY, "cash"), (HOUSEHOLD, "")]
    defaults = [Row("k", CATEGORY, "cash", 1), Row("k", HOUSEHOLD, "", 2)]
    scenario = [Row("k", HOUSEHOLD, "", 3)]
    won = a.lookup("k", scopes, defaults, scenario)
    assert (won.value, won.layer) == (3, LAYER_SCENARIO)  # type: ignore[union-attr]
    both = a.lookup("k", scopes, defaults, [*scenario, Row("k", CATEGORY, "cash", 4)])
    assert both.value == 4  # type: ignore[union-attr]
    plain = a.lookup("k", scopes, defaults, [])
    assert plain.value == 1  # type: ignore[union-attr]
    wider = a.lookup("k", scopes, [Row("k", HOUSEHOLD, "", 2)], [])
    assert wider.value == 2  # type: ignore[union-attr]


_scope = st.sampled_from([(CATEGORY, "cash"), (HOUSEHOLD, "")])
_rows = st.lists(st.tuples(_scope, st.integers(0, 1000)), max_size=2, unique_by=lambda t: t[0])


def _layer(entries: list[tuple[tuple[str, str], int]]) -> list[Row]:
    return [Row("k", scope[0], scope[1], value) for scope, value in entries]


@given(defaults=_rows, scenario=_rows)
def test_resolution_precedence_holds_for_any_layers(
    defaults: list[tuple[tuple[str, str], int]], scenario: list[tuple[tuple[str, str], int]]
) -> None:
    scopes = [(CATEGORY, "cash"), (HOUSEHOLD, "")]  # most specific first
    won = a.lookup("k", scopes, _layer(defaults), _layer(scenario))
    stated_by_scenario = dict(scenario)
    stated_by_default = dict(defaults)
    if not scenario and not defaults:
        assert won is None
        return
    assert won is not None
    if scenario:
        # What the Scenario states beats anything it does not, whatever the default says.
        assert won.layer == LAYER_SCENARIO
        expected = next(stated_by_scenario[s] for s in scopes if s in stated_by_scenario)
    else:
        assert won.layer == LAYER_HOUSEHOLD
        expected = next(stated_by_default[s] for s in scopes if s in stated_by_default)
    assert won.value == expected  # then the more specific scope wins within the layer


@given(
    st.sampled_from(
        ["inflation", "annual_saving", "horizon_age"]
        + [f"return_rate.{c}" for c in CATS]
        + [f"retirement_age.{m}" for m in MEMBERS]
    ),
    st.booleans(),
)
def test_an_unset_key_always_errors_and_names_the_key(
    removed: str, stated_by_scenario: bool
) -> None:
    key, _, ref = removed.partition(".")
    defaults = [r for r in complete() if not (r.key == key and r.scope_ref == ref)]
    with pytest.raises(a.UnresolvedError) as raised:
        resolve(defaults)
    assert [(m.key, m.scope_ref) for m in raised.value.missing] == [(key, ref)]
    assert key in str(raised.value)
    # Stating it in either layer resolves it: no silent zero in between.
    original = next(r for r in complete() if r.key == key and r.scope_ref == ref)
    resolved = (
        resolve(defaults, [original]) if stated_by_scenario else resolve([*defaults, original])
    )
    assert resolved.annual_saving.currency == "AUD"


def test_every_unset_key_is_reported_together() -> None:
    with pytest.raises(a.UnresolvedError) as raised:
        resolve([], default_phases=())
    keys = {m.key for m in raised.value.missing}
    assert keys == {
        "inflation",
        "spending_phases",
        "annual_saving",
        "horizon_age",
        "return_rate",
        "retirement_age",
    }


@given(st.integers(-PPM + 1, PPM))
def test_a_ppm_rate_round_trips_exactly_through_its_text(ppm: int) -> None:
    rate = Rate(ppm)
    text = a.format_value(rate)
    assert a.parse_value("inflation", text) == rate


@given(st.integers(0, a.MAX_AGE))
def test_an_age_round_trips_through_its_text(age: int) -> None:
    assert a.parse_value("retirement_age", a.format_value(age)) == age


@given(st.integers(0, 10**12))
def test_money_round_trips_exactly_through_its_text(minor: int) -> None:
    money = Money(minor, "AUD")
    assert a.parse_value("annual_saving", a.format_value(money)) == money


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("inflation", "2.55555"),  # finer than 4 decimal places of a percent: never rounded
        ("inflation", "abc"),
        ("retirement_age", "6x"),
        ("retirement_age", "121"),
        ("retirement_age", "-1"),
        ("annual_saving", "-5"),
        ("annual_saving", "1.234"),
    ],
)
def test_bad_text_is_refused(key: str, text: str) -> None:
    with pytest.raises(a.AssumptionValueError):
        a.parse_value(key, text)


# --- the rule.<key> override hook (ADR-0018 section 5) -------------------------------------

RULE_KEYS = ("preservation_age", "unrestricted_super_age", "age_pension_age")


def test_a_scenario_override_of_a_pack_age_beats_the_household_default() -> None:
    from finance360.core.rulepack import Age

    defaults = [*complete(), Row("rule.age_pension_age", HOUSEHOLD, "", Age(66))]
    over = [Row("rule.age_pension_age", HOUSEHOLD, "", Age(67, 6))]
    resolved = a.resolve(
        defaults, over, member_ids=MEMBERS, rule_keys=RULE_KEYS, default_phases=PHASES
    )
    stated = resolved.rule_overrides["age_pension_age"]
    assert (stated.value, stated.layer) == (Age(67, 6), LAYER_SCENARIO)
    only_default = a.resolve(
        defaults, [], member_ids=MEMBERS, rule_keys=RULE_KEYS, default_phases=PHASES
    )
    assert only_default.rule_overrides["age_pension_age"].layer == LAYER_HOUSEHOLD
    assert only_default.rule_overrides["age_pension_age"].value == Age(66)


def test_a_pack_value_nobody_overrides_is_absent_and_changes_no_other_value() -> None:
    plain = a.resolve(
        complete(), [], member_ids=MEMBERS, rule_keys=RULE_KEYS, default_phases=PHASES
    )
    assert plain.rule_overrides == {}
    assert plain == a.resolve(
        complete(), [], member_ids=MEMBERS, default_phases=PHASES
    )  # the hook adds nothing


def test_only_the_packs_own_keys_are_looked_up() -> None:
    from finance360.core.rulepack import Age

    stray = [Row("rule.not_in_the_pack", HOUSEHOLD, "", Age(70))]
    assert (
        a.resolve(
            complete(), stray, member_ids=MEMBERS, rule_keys=RULE_KEYS, default_phases=PHASES
        ).rule_overrides
        == {}
    )


def test_a_rule_key_is_admitted_from_the_registered_pack_schema_only() -> None:
    spec = a.spec_of("rule.age_pension_age")
    assert spec is not None and spec.kind == "age" and spec.scopes == (HOUSEHOLD,)
    assert a.spec_of("rule.nonsense") is None and a.spec_of("nonsense") is None
    assert a.spec_of("inflation") is a.KEYS["inflation"]


def test_a_rule_age_parses_and_formats_exactly() -> None:
    from finance360.core.rulepack import Age

    assert a.parse_value("rule.age_pension_age", "66y6m") == Age(66, 6)
    assert a.format_value(Age(66, 6)) == "66y6m" and a.format_value(Age(67)) == "67"
    for bad in ("66.5", "sixty", ""):
        with pytest.raises(a.AssumptionValueError):
            a.parse_value("rule.age_pension_age", bad)


# --- real wage growth (ADR-0017 section 5) ---------------------------------------


def with_growth(
    defaults: list[Row], overrides: list[Row] | None = None, who: tuple[int, ...] = (1, 2)
) -> a.Resolved:
    return a.resolve(
        defaults,
        overrides or [],
        member_ids=MEMBERS,
        wage_member_ids=who,
        default_phases=PHASES,
    )


def test_real_wage_growth_is_asked_only_of_members_with_a_salary() -> None:
    resolved = resolve(complete())  # nobody is salaried: nothing is required of the household
    assert resolved.real_wage_growth == {}
    assert "real_wage_growth" not in {p.key for p in resolved.provenance}


def test_a_members_own_wage_growth_beats_the_households_and_a_scenarios_beats_both() -> None:
    household = Row("real_wage_growth", HOUSEHOLD, "", Rate.from_percent("1"))
    own = Row("real_wage_growth", MEMBER, "1", Rate.from_percent("2.5"))
    scenario_wide = Row("real_wage_growth", HOUSEHOLD, "", Rate.from_percent("0.5"))
    resolved = with_growth([*complete(), household, own])
    assert resolved.real_wage_growth == {1: Rate.from_percent("2.5"), 2: Rate.from_percent("1")}
    # The Scenario's household value beats the Household default's member value (the layer is
    # more specific than the scope), exactly as for retirement age.
    resolved = with_growth([*complete(), household, own], [scenario_wide])
    assert resolved.real_wage_growth == {1: Rate.from_percent("0.5"), 2: Rate.from_percent("0.5")}
    listed = [(p.key, p.scope_ref, p.layer) for p in resolved.provenance if "wage" in p.key]
    assert listed == [("real_wage_growth", "", LAYER_SCENARIO)]  # one source, listed once


def test_a_salaried_member_with_no_wage_growth_anywhere_is_a_named_missing_value() -> None:
    with pytest.raises(a.UnresolvedError) as raised:
        with_growth(complete(), who=(2,))
    assert [(m.key, m.scope_ref) for m in raised.value.missing] == [("real_wage_growth", "2")]


def test_wage_growth_keys_and_scopes() -> None:
    spec = a.KEYS["real_wage_growth"]
    assert (spec.kind, spec.scopes) == ("rate", (HOUSEHOLD, MEMBER))
    assert a.parse_value("real_wage_growth", "-1.5") == Rate.from_percent("-1.5")
