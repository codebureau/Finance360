"""Scenario and Assumption services and the seeder against a real migrated SQLite database
(ADR-0016 sections 1, 2, 5 and 6)."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.projection import assumptions, scenarios, seed, spending
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.models.catalogue import Meta
from finance360.core.models.scenario import Assumption, Scenario
from finance360.core.money import Money
from finance360.core.rate import PPM, Rate

CTX = AuditContext("owner", "login:1", "api")
TODAY = date(2026, 10, 25)
Change = scenarios.Change


@pytest.fixture
def s(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        household.ensure_household(session)
        household.add_member(session, CTX, "Alex Sample", date(1984, 3, 12), today=TODAY)
        household.add_member(session, CTX, "Jordan Sample", date(1986, 9, 30), today=TODAY)
        seed.seed_assumptions(session)
        session.commit()
        yield session


def default_changes() -> list[Change]:
    return [
        Change("annual_saving", "household", "", "0"),
        Change("retirement_age", "member", "1", "60"),
        Change("retirement_age", "member", "2", "62"),
    ]


def set_defaults(session: Session, spending: str = "70000") -> None:
    """Complete the Household defaults: the Assumptions and one spending phase."""
    scenarios.set_assumptions(session, CTX, None, default_changes())
    scenarios.set_spending_phases(session, CTX, None, [("last_retirement", 0, spending)])


def scenario(session: Session, key: str) -> Scenario:
    return session.scalars(select(Scenario).where(Scenario.key == key)).one()


def default_rows(session: Session) -> dict[tuple[str, str], Assumption]:
    return {(r.key, r.scope_ref): r for r in scenarios.layer_rows(session, None)}


def events(session: Session, entity_type: str) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == entity_type).order_by(AuditEvent.id)
        )
    )


# --- seeding -------------------------------------------------------------------------------


def test_the_seed_gives_household_defaults_and_best_likely_worst(s: Session) -> None:
    assert [(x.key, x.name) for x in scenarios.list_scenarios(s)] == [
        ("best", "Best"),
        ("likely", "Likely"),
        ("worst", "Worst"),
    ]
    assert Rate(default_rows(s)[("return_rate", "investments")].value_rate_ppm or 0) == Rate(70_000)
    assert default_rows(s)[("horizon_age", "")].value_int == 95
    assert scenarios.layer_rows(s, scenario(s, "likely").id) == []  # Likely is the default
    best = {r.scope_ref: r.value_rate_ppm for r in scenarios.layer_rows(s, scenario(s, "best").id)}
    worst = {
        r.scope_ref: r.value_rate_ppm for r in scenarios.layer_rows(s, scenario(s, "worst").id)
    }
    for category in ("cash", "investments", "superannuation", "overseas_pensions"):
        likely = default_rows(s)[("return_rate", category)].value_rate_ppm
        assert worst[category] < likely < best[category]  # type: ignore[operator]
    assert worst[""] > default_rows(s)[("inflation", "")].value_rate_ppm  # type: ignore[operator]
    assert s.get(Meta, seed.SEED_VERSION_KEY).value == str(seed.ASSUMPTION_SEED_VERSION)  # type: ignore[union-attr]
    # Spending, saving and ages are the household's own: not seeded.
    assert not {k for k, _ in default_rows(s)} & {"annual_saving", "retirement_age"}
    assert scenarios.layer_phases(s, None) == []


def test_the_seed_gives_a_household_default_real_wage_growth_of_zero(s: Session) -> None:
    row = default_rows(s)[("real_wage_growth", "")]
    assert (row.value_rate_ppm, row.scope_type) == (0, "household")


def test_a_database_seeded_before_wage_growth_gets_only_that_row_on_the_version_bump(
    s: Session,
) -> None:
    """The live-data guard: version 1 had no ``real_wage_growth``. Bumping to 2 adds
    it and nothing else, so a default or an override the household removed does not come back."""
    s.delete(default_rows(s)[("real_wage_growth", "")])
    s.delete(default_rows(s)[("horizon_age", "")])  # removed on purpose
    worst = scenario(s, "worst").id
    scenarios.set_assumptions(s, CTX, worst, [Change("inflation", "household", "", None)])
    s.get(Meta, seed.SEED_VERSION_KEY).value = "1"  # type: ignore[union-attr]
    s.flush()
    seed.seed_assumptions(s)
    s.flush()
    rows = default_rows(s)
    assert rows[("real_wage_growth", "")].value_rate_ppm == 0  # added
    assert ("horizon_age", "") not in rows  # not resurrected
    assert [r.key for r in scenarios.layer_rows(s, worst)].count("inflation") == 0
    assert s.get(Meta, seed.SEED_VERSION_KEY).value == "2"  # type: ignore[union-attr]


def test_seeding_twice_changes_nothing_and_never_overwrites_an_edit(s: Session) -> None:
    scenarios.set_assumptions(s, CTX, None, [Change("inflation", "household", "", "4")])
    scenarios.set_assumptions(
        s, CTX, scenario(s, "best").id, [Change("return_rate", "category", "cash", "9")]
    )
    scenarios.set_assumptions(  # an override the user removed ("inherit again")
        s, CTX, scenario(s, "worst").id, [Change("inflation", "household", "", None)]
    )
    before = s.scalar(select(func.count()).select_from(Assumption))
    seed.seed_assumptions(s)
    s.flush()
    assert s.scalar(select(func.count()).select_from(Assumption)) == before
    assert default_rows(s)[("inflation", "")].value_rate_ppm == 40_000
    best_cash = {r.scope_ref: r for r in scenarios.layer_rows(s, scenario(s, "best").id)}["cash"]
    assert best_cash.value_rate_ppm == 90_000
    assert [r.key for r in scenarios.layer_rows(s, scenario(s, "worst").id)].count("inflation") == 0


def test_a_later_seed_version_adds_only_what_is_missing(s: Session) -> None:
    scenarios.set_assumptions(s, CTX, None, [Change("inflation", "household", "", "4")])
    s.delete(default_rows(s)[("horizon_age", "")])
    s.get(Meta, seed.SEED_VERSION_KEY).value = "0"  # type: ignore[union-attr]
    s.flush()
    seed.seed_assumptions(s)
    assert default_rows(s)[("horizon_age", "")].value_int == 95  # came back
    assert default_rows(s)[("inflation", "")].value_rate_ppm == 40_000  # the edit survived


def test_seeding_is_not_audited(s: Session) -> None:
    assert events(s, "assumption") == [] and events(s, "scenario") == []


# --- audit ---------------------------------------------------------------------------------


def test_setting_updating_and_removing_an_assumption_each_write_an_audit_event(s: Session) -> None:
    likely = scenario(s, "likely").id
    scenarios.set_assumptions(s, CTX, likely, [Change("annual_saving", "household", "", "80000")])
    scenarios.set_assumptions(
        s, CTX, likely, [Change("annual_saving", "household", "", "85000.50")]
    )
    scenarios.set_assumptions(s, CTX, likely, [Change("annual_saving", "household", "", None)])
    created, updated, deleted = events(s, "assumption")
    assert (created.action, updated.action, deleted.action) == ("create", "update", "delete")
    assert created.before is None
    assert created.after == {
        "scenario": "Likely",
        "key": "annual_saving",
        "scope_type": "household",
        "scope_ref": "",
        "value": {"minor": 8_000_000, "ccy": "AUD"},
    }
    assert updated.before["value"] == {"minor": 8_000_000, "ccy": "AUD"}  # type: ignore[index]
    assert updated.after["value"] == {"minor": 8_500_050, "ccy": "AUD"}  # type: ignore[index]
    assert deleted.after is None
    assert deleted.before["value"] == {"minor": 8_500_050, "ccy": "AUD"}  # type: ignore[index]
    assert "inherits again" in deleted.summary
    assert created.entity_id == updated.entity_id == deleted.entity_id


def test_a_rate_is_audited_as_a_percent_and_an_age_as_a_whole_number(s: Session) -> None:
    scenarios.set_assumptions(
        s, CTX, None, [Change("inflation", "household", "", "3.25"), *default_changes()[1:2]]
    )
    by_key = {e.after["key"]: e.after["value"] for e in events(s, "assumption")}  # type: ignore[index]
    assert by_key == {"inflation": "3.25", "retirement_age": 60}  # the default row was 2.5 before


def test_an_unchanged_value_writes_nothing(s: Session) -> None:
    scenarios.set_assumptions(s, CTX, None, [Change("inflation", "household", "", "2.5")])
    assert events(s, "assumption") == []


def test_scenario_create_update_delete_are_audited(s: Session) -> None:
    made = scenarios.create_scenario(s, CTX, "  Lean years ", "Notes here")
    scenarios.update_scenario(s, CTX, made.id, "Lean years", "Changed")
    scenarios.delete_scenario(s, CTX, made.id)
    created, updated, deleted = events(s, "scenario")
    assert (created.action, updated.action, deleted.action) == ("create", "update", "delete")
    assert created.after["name"] == "Lean years"  # type: ignore[index]
    assert (updated.before["notes"], updated.after["notes"]) == ("Notes here", "Changed")  # type: ignore[index]
    assert deleted.before["name"] == "Lean years"  # type: ignore[index]


def test_a_failed_edit_writes_neither_rows_nor_audit(s: Session) -> None:
    with pytest.raises(ValidationError) as raised:
        scenarios.set_assumptions(
            s,
            CTX,
            None,
            [
                Change("inflation", "household", "", "5"),
                Change("retirement_age", "member", "1", "6x"),
            ],
        )
    assert [e.field for e in raised.value.errors] == ["retirement_age.1"]
    assert default_rows(s)[("inflation", "")].value_rate_ppm == 25_000
    assert events(s, "assumption") == []


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (Change("nonsense", "household", "", "1"), "nonsense"),
        (Change("inflation", "category", "cash", "1"), "inflation.cash"),
        (Change("return_rate", "account", "7", "1"), "return_rate.7"),  # reserved, not stored
        (Change("return_rate", "category", "property", "1"), "return_rate.property"),
        (Change("retirement_age", "member", "99", "60"), "retirement_age.99"),
        (Change("inflation", "household", "", "2.55555"), "inflation"),
        (Change("annual_saving", "household", "", "-1"), "annual_saving"),
        (Change("annual_spending", "household", "", "70000"), "annual_spending"),  # retired key
    ],
)
def test_the_registry_refuses_a_bad_key_scope_or_value(
    s: Session, change: Change, field: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        scenarios.set_assumptions(s, CTX, None, [change])
    assert [e.field for e in raised.value.errors] == [field]


# --- the Household default changed: which Scenarios did not move -------------------------


def test_changing_a_default_names_the_scenarios_that_override_it(s: Session) -> None:
    reviews = scenarios.set_assumptions(
        s, CTX, None, [Change("return_rate", "category", "investments", "6")]
    )
    assert [(r.key, r.scope_ref, r.scenarios) for r in reviews] == [
        ("return_rate", "investments", ("Best", "Worst"))  # not Likely: it states nothing
    ]
    # A key no Scenario overrides is not flagged; editing a Scenario's own layer never is.
    assert scenarios.set_assumptions(s, CTX, None, default_changes()) == []
    best = scenario(s, "best").id
    assert (
        scenarios.set_assumptions(s, CTX, best, [Change("inflation", "household", "", "9")]) == []
    )


def test_removing_a_default_also_flags_the_scenarios_that_override_it(s: Session) -> None:
    reviews = scenarios.set_assumptions(s, CTX, None, [Change("inflation", "household", "", None)])
    assert [(r.key, r.scenarios) for r in reviews] == [("inflation", ("Worst",))]


# --- resolution against stored rows --------------------------------------------------------


def test_an_unset_key_is_an_error_naming_it(s: Session) -> None:
    with pytest.raises(ValidationError) as raised:
        scenarios.resolve_for(s, scenario(s, "likely").id)
    assert {e.field for e in raised.value.errors} == {
        "spending_phases",
        "annual_saving",
        "retirement_age.1",
        "retirement_age.2",
    }
    assert any("Alex Sample" in e.message for e in raised.value.errors)  # a Member by name
    set_defaults(s)
    scenarios.resolve_for(s, scenario(s, "likely").id)


def test_best_and_worst_resolve_over_the_household_default(s: Session) -> None:
    set_defaults(s)
    _, likely = scenarios.resolve_for(s, scenario(s, "likely").id)
    _, best = scenarios.resolve_for(s, scenario(s, "best").id)
    _, worst = scenarios.resolve_for(s, scenario(s, "worst").id)
    assert best.returns["investments"].ppm > likely.returns["investments"].ppm
    assert worst.returns["investments"].ppm < likely.returns["investments"].ppm
    assert worst.inflation.ppm > likely.inflation.ppm == best.inflation.ppm
    plan = (spending.Phase(spending.LAST_RETIREMENT, 0, Money(7_000_000, "AUD")),)
    assert best.spending_phases == likely.spending_phases == plan


def test_an_unknown_scenario_is_not_found(s: Session) -> None:
    with pytest.raises(NotFoundError):
        scenarios.resolve_for(s, 999)


# --- clone, delete, names ------------------------------------------------------------------


_names = itertools.count()
_overrides = st.dictionaries(
    st.sampled_from(["cash", "investments", "superannuation", "overseas_pensions"]),
    st.integers(-PPM + 1, PPM),
)


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(overrides=_overrides, own_spending=st.one_of(st.none(), st.integers(1, 10**9)))
def test_a_clone_resolves_equal_to_its_source(
    s: Session, overrides: dict[str, int], own_spending: int | None
) -> None:
    set_defaults(s)
    source = scenarios.create_scenario(s, CTX, f"Source {next(_names)}")
    changes = [
        Change("return_rate", "category", c, assumptions.format_value(Rate(ppm)))
        for c, ppm in overrides.items()
    ]
    scenarios.set_assumptions(s, CTX, source.id, changes)
    if own_spending is not None:
        text_ = assumptions.format_value(Money(own_spending, "AUD"))
        scenarios.set_spending_phases(s, CTX, source.id, [("last_retirement", 5, text_)])
    clone = scenarios.create_scenario(s, CTX, f"Clone {next(_names)}", clone_from=source.id)
    assert scenarios.resolve_for(s, clone.id)[1] == scenarios.resolve_for(s, source.id)[1]
    assert len(scenarios.layer_rows(s, clone.id)) == len(scenarios.layer_rows(s, source.id))
    assert scenarios.layer_phases(s, clone.id) == scenarios.layer_phases(s, source.id)
    # No parent link: editing the source afterwards does not move the clone.
    scenarios.set_assumptions(s, CTX, source.id, [Change("inflation", "household", "", "7")])
    assert scenarios.resolve_for(s, clone.id)[1].inflation == Rate(25_000)


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(ppm=st.integers(-PPM + 1, PPM), minor=st.integers(0, 10**13), age=st.integers(0, 120))
def test_stored_values_round_trip_exactly_through_the_database(
    s: Session, ppm: int, minor: int, age: int
) -> None:
    likely = scenario(s, "likely").id
    scenarios.set_assumptions(
        s,
        CTX,
        likely,
        [
            Change("inflation", "household", "", assumptions.format_value(Rate(ppm))),
            Change("annual_saving", "household", "", assumptions.format_value(Money(minor, "AUD"))),
            Change("horizon_age", "household", "", str(age)),
        ],
    )
    s.expire_all()
    stored = {r.key: r for r in scenarios.layer_rows(s, likely)}
    assert stored["inflation"].value_rate_ppm == ppm
    assert (stored["annual_saving"].value_minor, stored["annual_saving"].value_ccy) == (
        minor,
        "AUD",
    )
    assert stored["horizon_age"].value_int == age


def test_a_seeded_scenario_can_be_edited_but_not_deleted(s: Session) -> None:
    best = scenario(s, "best")
    scenarios.update_scenario(s, CTX, best.id, "Best case", "Optimistic")
    assert scenario(s, "best").name == "Best case"
    with pytest.raises(ValidationError, match="built in"):
        scenarios.delete_scenario(s, CTX, best.id)


def test_deleting_a_scenario_removes_its_overrides_and_leaves_the_defaults(s: Session) -> None:
    made = scenarios.create_scenario(s, CTX, "Lean years", clone_from=scenario(s, "best").id)
    assert len(scenarios.layer_rows(s, made.id)) == 4
    defaults = len(scenarios.layer_rows(s, None))
    scenarios.delete_scenario(s, CTX, made.id)
    assert s.get(Scenario, made.id) is None
    assert (
        s.scalar(
            select(func.count()).select_from(Assumption).where(Assumption.scenario_id == made.id)
        )
        == 0
    )
    assert len(scenarios.layer_rows(s, None)) == defaults
    assert len(scenarios.layer_rows(s, scenario(s, "best").id)) == 4  # the source is untouched
    deleted = events(s, "scenario")[-1]
    assert len(deleted.before["overrides"]) == 4  # type: ignore[index]


def test_scenario_names_are_required_and_unique_ignoring_case(s: Session) -> None:
    with pytest.raises(ValidationError) as blank:
        scenarios.create_scenario(s, CTX, "  ")
    assert blank.value.errors[0].field == "name"
    with pytest.raises(ValidationError) as clash:
        scenarios.create_scenario(s, CTX, "likely")
    assert clash.value.errors[0].message == "A Scenario with this name already exists."
    scenarios.update_scenario(s, CTX, scenario(s, "likely").id, "LIKELY", None)  # itself is fine


# --- the schema's own guards ---------------------------------------------------------------


def _insert(session: Session, **cols: object) -> None:
    base: dict[str, object] = {
        "scenario_id": None,
        "key": "k",
        "scope_type": "household",
        "scope_ref": "",
        "value_rate_ppm": None,
        "value_int": 1,
        "value_minor": None,
        "value_ccy": None,
    }
    base |= cols
    session.execute(
        text(
            "INSERT INTO assumption (scenario_id, key, scope_type, scope_ref, value_rate_ppm, "
            "value_int, value_minor, value_ccy) VALUES (:scenario_id, :key, :scope_type, "
            ":scope_ref, :value_rate_ppm, :value_int, :value_minor, :value_ccy)"
        ),
        base,
    )


@pytest.mark.parametrize(
    "cols",
    [
        {"value_int": None},  # no value
        {"value_rate_ppm": 5},  # two values
        {"value_int": None, "value_minor": 5},  # money without a currency
        {"scope_type": "account", "scope_ref": "7"},  # reserved scope
        {"scope_type": "category", "scope_ref": ""},  # a scope with no reference
        {"value_int": None, "value_rate_ppm": 1_000_001},  # above 100%
    ],
)
def test_the_database_refuses_a_malformed_row(s: Session, cols: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        _insert(s, **cols)
    s.rollback()


def test_the_database_refuses_a_duplicate_in_either_layer(s: Session) -> None:
    _insert(s, key="once")
    with pytest.raises(IntegrityError):
        _insert(s, key="once")  # the Household default layer (a NULL scenario_id)
    s.rollback()
    likely = scenario(s, "likely").id
    _insert(s, key="once", scenario_id=likely)
    with pytest.raises(IntegrityError):
        _insert(s, key="once", scenario_id=likely)
    s.rollback()


# --- the fixture ---------------------------------------------------------------------------


def test_the_fixture_sets_defaults_and_custom_scenarios_once(s: Session, tmp_path: Path) -> None:
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures" / "sample_household" / "household.json"
    )
    (tmp_path / "household.json").write_bytes(fixture.read_bytes())
    assert scenarios.load_fixture_assumptions(s, tmp_path) == 2
    assert scenarios.load_fixture_assumptions(s, tmp_path) == 0  # only while there are none
    customs = s.scalars(select(Scenario).where(Scenario.key.is_(None)).order_by(Scenario.id)).all()
    assert [c.name for c in customs] == ["Retire early, spend more", "Tax on, Alex's pay climbs"]
    custom = customs[0]
    assert custom.name == "Retire early, spend more"
    assert len(scenarios.layer_rows(s, custom.id)) == 2
    _, resolved = scenarios.resolve_for(s, scenario(s, "likely").id)
    retire = spending.LAST_RETIREMENT
    start = spending.START  # the working years, for the fixture's salaries (ADR-0017 s5)
    assert resolved.spending_phases == (
        spending.Phase(start, 0, Money(10_000_000, "AUD")),
        spending.Phase(retire, 0, Money(7_000_000, "AUD")),
        spending.Phase(retire, 10, Money(5_500_000, "AUD")),
    )
    assert sorted(resolved.retirement_ages.values()) == [60, 62]
    _, early = scenarios.resolve_for(s, custom.id)
    # The Scenario's list replaces the default's whole: the default's second phase is not kept.
    assert early.spending_phases == (
        spending.Phase(start, 0, Money(11_000_000, "AUD")),
        spending.Phase(retire, 0, Money(8_500_000, "AUD")),
    )
    assert early.spending_layer == "scenario" and resolved.spending_layer == "household"
    assert early.returns["investments"] == Rate(65_000)
    assert early.returns["cash"] == resolved.returns["cash"]  # inherited
