"""Life events and super access (ADR-0017 sections 3 and 4).

Pure logic first (the date-to-year rule, the access-year formula, with Hypothesis), then the whole
path against the synthetic household: Alex (born 1984-03-12) and Jordan (born 1986-09-30), both with
preservation age 60, at ``AS_AT``. Alex is 42 and Jordan 40 on that date.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.projection import life_events, rules
from finance360.capabilities.projection.input import age_on, anniversary
from finance360.config import Settings
from finance360.core.models.projection_run import ProjectionRun
from finance360.core.rulepack import Age
from finance360.main import create_app
from finance360.rulepacks.au import PACK
from support import TestClient, owner_client, without_income_streams

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
ALEX, JORDAN = 1, 2


# --- pure: dates ---------------------------------------------------------------------------


def test_a_date_at_an_age_is_the_birthday_and_clamps_to_the_end_of_the_month() -> None:
    assert life_events.date_at_age(date(1984, 3, 12), Age(60)) == date(2044, 3, 12)
    assert life_events.date_at_age(date(1955, 12, 31), Age(66, 6)) == date(2022, 6, 30)
    assert life_events.date_at_age(date(1957, 1, 1), Age(66, 6)) == date(2023, 7, 1)


def test_a_leap_day_birthday_falls_on_28_february_in_a_common_year() -> None:
    born = date(1988, 2, 29)
    assert life_events.date_at_age(born, Age(40)) == date(2028, 2, 29)
    assert life_events.date_at_age(born, Age(41)) == date(2029, 2, 28)
    assert life_events.date_at_age(born, Age(66, 6)) == date(2054, 8, 29)


def test_the_year_rule_takes_the_first_year_starting_on_or_after_the_date() -> None:
    as_at = date(2026, 10, 25)
    assert life_events.year_index(date(2027, 10, 25), as_at) == 1  # the day a year starts
    assert life_events.year_index(date(2027, 10, 26), as_at) == 2  # a day later: the next year
    assert life_events.year_index(date(2027, 10, 24), as_at) == 1
    assert life_events.year_index(as_at, as_at) == 0
    assert life_events.year_index(date(2001, 1, 1), as_at) == 0  # already past: never below 0


def test_years_start_on_the_leap_day_anniversary_rule() -> None:
    as_at = date(2028, 2, 29)  # year 1 starts on 28 February 2029
    assert life_events.year_index(date(2029, 2, 28), as_at) == 1
    assert life_events.year_index(date(2029, 3, 1), as_at) == 2
    assert life_events.year_index(date(2032, 2, 29), as_at) == 4  # year 4 starts 29 Feb 2032
    assert life_events.year_index(date(2032, 2, 28), as_at) == 4  # year 3 starts 28 Feb 2031


def test_a_leap_day_member_retires_a_year_earlier_than_b1_when_the_age_falls_on_a_year_start() -> (
    None
):
    """The one documented difference from B1's ``retirement_age - age_now`` (module docstring)."""
    born, as_at = date(1984, 2, 29), date(2024, 2, 28)  # age 39: the birthday is tomorrow
    assert age_on(born, as_at) == 39
    retire_at = life_events.date_at_age(born, Age(41))  # 28 February 2025
    assert retire_at == date(2025, 2, 28) == anniversary(as_at, 2025)
    assert life_events.year_index(retire_at, as_at) == 1  # B1 said 41 - 39 = 2


# --- pure: the year rule against B1, and the access-year formula ----------------------------

DOBS = st.dates(min_value=date(1930, 1, 1), max_value=date(2030, 12, 31))


@given(
    dob=DOBS,
    offset=st.integers(min_value=0, max_value=36_000),
    retirement_age=st.integers(min_value=0, max_value=100),
)
def test_the_year_rule_matches_b1_for_every_date_of_birth(
    dob: date, offset: int, retirement_age: int
) -> None:
    as_at = dob + timedelta(days=offset)
    b1 = max(0, retirement_age - age_on(dob, as_at))
    year = life_events.year_index(life_events.date_at_age(dob, Age(retirement_age)), as_at)
    if dob.month == 2 and dob.day == 29 and as_at.month == 2 and as_at.day in (28, 29):
        # The leap-day difference: the age falls on or beside a year start (one day apart).
        assert abs(year - b1) <= 1
    else:
        assert year == b1


@given(
    dob=DOBS,
    offset=st.integers(min_value=0, max_value=36_000),
    age=st.builds(Age.from_months, st.integers(min_value=0, max_value=1_200)),
)
def test_a_year_index_is_the_first_year_starting_on_or_after_the_date(
    dob: date, offset: int, age: Age
) -> None:
    as_at = dob + timedelta(days=offset)
    on = life_events.date_at_age(dob, age)
    t = life_events.year_index(on, as_at)
    assert anniversary(as_at, as_at.year + t) >= on or t == 0
    assert t == 0 or anniversary(as_at, as_at.year + t - 1) < on


@given(
    retirement=st.integers(min_value=0, max_value=80),
    preservation=st.integers(min_value=0, max_value=80),
    unrestricted=st.integers(min_value=0, max_value=80),
)
def test_super_opens_when_retired_and_past_preservation_age_or_at_65(
    retirement: int, preservation: int, unrestricted: int
) -> None:
    year = life_events.super_access_year(retirement, preservation, unrestricted)
    assert year in (retirement, preservation, unrestricted)
    assert year <= unrestricted
    assert year >= retirement or year == unrestricted
    assert year >= preservation or year == unrestricted
    if preservation <= retirement <= unrestricted:
        assert year == retirement  # B1's "at retirement" still holds here


def test_resolve_lists_each_members_events_in_date_order_with_their_source() -> None:
    members = [(ALEX, date(1984, 3, 12)), (JORDAN, date(1986, 9, 30))]
    used = rules.member_ages(PACK, members, {})
    events = life_events.resolve(members, {ALEX: 55, JORDAN: 62}, used, date(2026, 10, 25))
    assert [e.on for e in events] == sorted(e.on for e in events)
    alex = [e for e in events if e.member_id == ALEX]
    assert [(e.kind, e.on, e.year, e.source) for e in alex] == [
        ("retirement", date(2039, 3, 12), 13, "scenario"),
        ("preservation_age", date(2044, 3, 12), 18, "rule_pack"),
        ("unrestricted_super_age", date(2049, 3, 12), 23, "rule_pack"),
        ("age_pension_age", date(2051, 3, 12), 25, "rule_pack"),
    ]
    assert life_events.member_super_access_years(events) == {ALEX: 18, JORDAN: 22}


# --- the whole path ------------------------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield without_income_streams(c)


def factory_of(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory  # type: ignore[attr-defined, no-any-return]


def body(alex: int, jordan: int, spending: str = "70000.00") -> dict[str, Any]:
    return {
        "as_at": AS_AT,
        "inflation": "2.5",
        "returns": [
            {"category": "cash", "rate": "3"},
            {"category": "investments", "rate": "7"},
            {"category": "superannuation", "rate": "7"},
            {"category": "overseas_pensions", "rate": "6"},
        ],
        "retirement_ages": [
            {"member_id": ALEX, "age": alex},
            {"member_id": JORDAN, "age": jordan},
        ],
        "spending_phases": [
            {"anchor": "last_retirement", "offset_years": 0, "annual_amount": spending}
        ],
        "annual_saving": None,
        "horizon_age": 95,
    }


def project(client: TestClient, alex: int, jordan: int, spending: str = "70000.00") -> Any:
    res = client.post("/api/projection/run", json=body(alex, jordan, spending))
    assert res.status_code == 200, res.text
    return res.json()


def access(p: dict[str, Any]) -> dict[int, int]:
    return {a["member_id"]: a["year"] for a in p["super_access"]}


def shortfall_years(p: dict[str, Any]) -> list[int]:
    return [r["t"] for r in p["rows"] if r["shortfall"] != "0.00"]


def scenario_projection(client: TestClient, name: str) -> Any:
    sid = next(s["id"] for s in client.get("/api/scenarios").json() if s["name"] == name)
    res = client.get(f"/api/projection/scenarios/{sid}", params={"as_at": AS_AT})
    assert res.status_code == 200, res.text
    return res.json()


def test_retiring_at_or_after_preservation_age_opens_super_at_retirement_as_before(
    sample: TestClient,
) -> None:
    p = project(sample, 60, 62)  # preservation 60: ages 18 and 22 years on
    assert access(p) == {ALEX: 18, JORDAN: 22}
    assert {m["id"]: m["retirement_year"] for m in p["members"]} == {ALEX: 18, JORDAN: 22}


def test_retiring_after_65_opens_super_at_65(sample: TestClient) -> None:
    p = project(sample, 70, 62)
    assert {m["id"]: m["retirement_year"] for m in p["members"]}[ALEX] == 28
    assert access(p)[ALEX] == 23  # 65 - 42


def test_retiring_before_preservation_age_leaves_super_shut_until_it_and_a_shortfall_shows(
    sample: TestClient,
) -> None:
    """Both retire at 55 and spend 150,000: Cash and Investments run out in year 15 to 16, the
    overseas pension in year 17, and Alex's super does not open until preservation age (year 18)."""
    p = project(sample, 55, 55, "150000.00")
    assert {m["id"]: m["retirement_year"] for m in p["members"]} == {ALEX: 13, JORDAN: 15}
    assert access(p) == {ALEX: 18, JORDAN: 20}  # not 13 and 15: the pre-B6 "at retirement"
    first = shortfall_years(p)[0]
    assert first == 17
    row = next(r for r in p["rows"] if r["t"] == first)
    assert row["shortfall"] == "32660.33"
    by_category = {c["category_key"]: c for c in row["categories"]}
    assert by_category["cash"]["closing"] == "0.00"
    assert by_category["investments"]["closing"] == "0.00"
    assert by_category["overseas_pensions"]["closing"] == "0.00"
    assert by_category["superannuation"]["withdrawn"] == "0.00"  # still shut
    # The gap closes the year super opens: no shortfall from year 18 until the money runs out.
    assert not any(18 <= t <= 20 for t in shortfall_years(p))
    after = next(r for r in p["rows"] if r["t"] == 18)
    assert (
        next(c for c in after["categories"] if c["category_key"] == "superannuation")["withdrawn"]
        != "0.00"
    )


def test_the_same_household_retiring_at_preservation_age_has_no_gap(sample: TestClient) -> None:
    gap = project(sample, 55, 55, "150000.00")
    none = project(sample, 60, 60, "150000.00")
    assert shortfall_years(gap)[0] == 17
    assert shortfall_years(none)[0] > 17


def test_life_events_are_listed_in_date_order_with_source_and_age(sample: TestClient) -> None:
    p = project(sample, 55, 62)
    events = p["life_events"]
    assert [e["on"] for e in events] == sorted(e["on"] for e in events)
    jordan = [e for e in events if e["member_id"] == JORDAN]
    assert [(e["kind"], e["on"], e["age_years"], e["age_months"], e["year"]) for e in jordan] == [
        ("preservation_age", "2046-09-30", 60, 0, 20),
        ("retirement", "2048-09-30", 62, 0, 22),
        ("unrestricted_super_age", "2051-09-30", 65, 0, 25),
        ("age_pension_age", "2053-09-30", 67, 0, 27),
    ]
    assert {e["source"] for e in jordan if e["kind"] == "retirement"} == {"scenario"}
    assert {e["source"] for e in jordan if e["kind"] != "retirement"} == {"rule_pack"}


def test_a_rule_override_moves_the_access_year(sample: TestClient) -> None:
    before = scenario_projection(sample, "Retire early, spend more")
    assert access(before)[ALEX] == 18  # retires at 55 (year 13); preservation age 60
    custom = next(
        s["id"] for s in sample.get("/api/scenarios").json() if s["name"].startswith("Retire early")
    )
    res = sample.put(
        "/api/assumptions",
        json={
            "scenario_id": custom,
            "changes": [{"key": "rule.preservation_age", "scope_type": "household", "value": "57"}],
        },
    )
    assert res.status_code == 200, res.text
    after = scenario_projection(sample, "Retire early, spend more")
    assert access(after)[ALEX] == 15  # 57 - 42
    preservation = next(
        e
        for e in after["life_events"]
        if e["member_id"] == ALEX and e["kind"] == "preservation_age"
    )
    assert (preservation["year"], preservation["source"]) == (15, "scenario")


def test_a_saved_run_keeps_the_access_years_and_life_events_it_used(sample: TestClient) -> None:
    custom = next(
        s["id"] for s in sample.get("/api/scenarios").json() if s["name"].startswith("Retire early")
    )
    saved = sample.post("/api/projection/runs", json={"scenario_id": custom, "as_at": AS_AT})
    assert saved.status_code == 201, saved.text
    run_id = saved.json()["id"]
    live = scenario_projection(sample, "Retire early, spend more")
    # Change the rule afterwards: the saved run must not move, the live Scenario does.
    sample.put(
        "/api/assumptions",
        json={
            "scenario_id": custom,
            "changes": [{"key": "rule.preservation_age", "scope_type": "household", "value": "56"}],
        },
    )
    reopened = sample.get(f"/api/projection/runs/{run_id}").json()
    assert access(reopened) == access(live) == {ALEX: 18, JORDAN: 22}
    assert reopened["life_events"] == live["life_events"]
    assert access(scenario_projection(sample, "Retire early, spend more"))[ALEX] == 14
    # The access year is in the stored input; the events are in the provenance.
    with factory_of(sample)() as session:
        row = session.scalars(select(ProjectionRun)).one()
        lines = json.loads(row.input_json)["lines"]
        provenance = json.loads(row.provenance_json)
    alex_super = next(
        x for x in lines if x["member_id"] == ALEX and x["category_key"] == "superannuation"
    )
    assert alex_super["accessible_from_year"] == 18
    kinds = {(e["member_id"], e["kind"]): e for e in provenance["life_events"]}
    assert kinds[(ALEX, "retirement")]["year"] == 13
    assert kinds[(ALEX, "preservation_age")]["on"] == "2044-03-12"
    assert kinds[(ALEX, "preservation_age")]["source"] == "rule_pack"


def test_a_run_saved_before_life_events_still_renders_with_its_stored_access_years(
    sample: TestClient,
) -> None:
    custom = next(
        s["id"] for s in sample.get("/api/scenarios").json() if s["name"].startswith("Retire early")
    )
    header = sample.post("/api/projection/runs", json={"scenario_id": custom, "as_at": AS_AT})
    run_id = header.json()["id"]
    with factory_of(sample)() as session:
        row = session.get(ProjectionRun, run_id)
        assert row is not None
        provenance = json.loads(row.provenance_json)
        del provenance["life_events"]
        legacy = ProjectionRun(
            created_at=row.created_at,
            label="Legacy",
            scenario_id=row.scenario_id,
            scenario_name=row.scenario_name,
            engine_version=row.engine_version,
            input_version=row.input_version,
            rule_pack_version=row.rule_pack_version,
            input_json=row.input_json,
            result_json=row.result_json,
            sustainable_json=row.sustainable_json,
            provenance_json=json.dumps(provenance),
            funds_last_to_age=row.funds_last_to_age,
            lasts_beyond_horizon=row.lasts_beyond_horizon,
        )
        session.add(legacy)
        session.commit()
        run_id = legacy.id
    reopened = sample.get(f"/api/projection/runs/{run_id}")
    assert reopened.status_code == 200, reopened.text
    out = reopened.json()
    assert out["life_events"] is None and out["rows"]
    assert access(out)[ALEX] == 18  # from the stored input, not from anything live
