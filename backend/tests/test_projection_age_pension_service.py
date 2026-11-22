"""The Age Pension in a projection, end to end (ADR-0029): the service resolves the Household facts,
the Rule pack figures, eligibility, other income, Property and Vehicles into the input; the engine
pays it; the run records the working and provenance and reopens without reading anything live.

Integration tests against a real temporary SQLite database and the synthetic household (invented
people, accounts and figures). The household has two Members born in the 1980s, so Age Pension age
(67) falls inside the horizon.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.projection import assumptions, engine, result_json, runs
from finance360.capabilities.projection import input as pinput
from finance360.capabilities.projection.sustainable import sustainable_spending
from finance360.config import Settings
from finance360.core import rulepack
from finance360.core.models.projection_run import ProjectionRun
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
FACTS = "/api/household/facts"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def factory_of(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory  # type: ignore[attr-defined, no-any-return]


def cents(text: str) -> int:
    return round(float(text) * 100)


def scenario_id(client: TestClient, name: str = "Likely") -> int:
    return next(s["id"] for s in client.get("/api/scenarios").json() if s["name"] == name)


def live(client: TestClient, name: str = "Likely", as_at: str = AS_AT) -> dict[str, Any]:
    res = client.get(
        f"/api/projection/scenarios/{scenario_id(client, name)}", params={"as_at": as_at}
    )
    assert res.status_code == 200, res.text
    return res.json()


def pension_rows(p: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in p["rows"] if r["age_pension"] is not None]


def cash_credited(row: dict[str, Any]) -> int:
    return cents(next(c for c in row["categories"] if c["category_key"] == "cash")["credited"])


# --- the observable outcome -------------------------------------------------------------------


def test_the_pension_is_paid_to_each_member_from_their_own_age_pension_age_year(
    client: TestClient,
) -> None:
    p = live(client)
    used = p["age_pension"]
    assert used["status"] == "calculated" and used["reasons"] == []
    eligible = {e["member_id"]: e for e in used["eligibility"]}
    # Born 1984 and 1986: 67 in 2051 and 2053; the year that starts on or after that date.
    assert {m["id"]: eligible[m["id"]]["on"] for m in p["members"]} == {
        eligible[m]["member_id"]: eligible[m]["on"] for m in eligible
    }
    assert sorted(e["on"] for e in eligible.values()) == ["2051-03-12", "2053-09-30"]
    first, second = sorted(eligible.values(), key=lambda e: e["year"])
    assert first["year"] < second["year"]

    rows = pension_rows(p)
    assert [r["t"] for r in rows] == list(range(first["year"], p["years"]))
    # Before anyone is eligible there is no pension; from the first year only that Member is paid
    # (half the couple's total) and only until the other is eligible.
    start = rows[0]["age_pension"]
    assert [x["member_id"] for x in start["paid"]] == [first["member_id"]]
    assert cents(start["paid"][0]["amount"]) * 2 in (
        cents(start["total"]) - 1,
        cents(start["total"]),
        cents(start["total"]) + 1,
    )
    both = next(r for r in rows if r["t"] == second["year"])["age_pension"]
    assert sorted(x["member_id"] for x in both["paid"]) == sorted(eligible)
    assert sum(cents(x["amount"]) for x in both["paid"]) == cents(both["total"])
    # The pension is credited to cash, like salary: the cash Category's credit is the amount paid
    # (the household has retired by then, so no other inflow).
    assert cash_credited(rows[0]) == cents(start["paid"][0]["amount"])
    assert cash_credited(next(r for r in rows if r["t"] == second["year"])) == cents(both["total"])


def test_the_working_carries_what_was_tested_and_which_test_bound(client: TestClient) -> None:
    p = live(client)
    for row in pension_rows(p):
        w = row["age_pension"]
        assert w["couple"] is True and w["homeowner"] is True
        categories = {c["category_key"]: cents(c["amount"]) for c in w["categories"]}
        assert {"cash", "investments"} <= set(categories)  # counted from year 0
        assert cents(w["financial_assets"]) == sum(categories.values())
        assert cents(w["assets"]) == (
            cents(w["financial_assets"]) + cents(w["property_assets"]) + cents(w["vehicle_assets"])
        )
        total, maximum = cents(w["total"]), cents(w["maximum"])
        assert total == min(cents(w["income_test"]), cents(w["assets_test"]))
        assert 0 <= total <= maximum
        reduced = (cents(w["income_test"]) < maximum, cents(w["assets_test"]) < maximum)
        assert (w["bound"] == "none") == (reduced == (False, False))
        if w["bound"] == "income":
            assert cents(w["income_test"]) <= cents(w["assets_test"])
        if w["bound"] == "assets":
            assert cents(w["assets_test"]) < cents(w["income_test"])
    # The Vehicle is flat in nominal terms, so it falls in real terms; the home is not counted.
    cars = [cents(r["age_pension"]["vehicle_assets"]) for r in pension_rows(p)]
    assert cars == sorted(cars, reverse=True) and cars[0] > cars[-1] > 0
    assert len({r["age_pension"]["property_assets"] for r in pension_rows(p)}) == 1
    assert cents(pension_rows(p)[0]["age_pension"]["property_assets"]) == 0
    # Super counts only from its owner's age: the first Member's alone at first.
    first = pension_rows(p)[0]["age_pension"]
    assert "superannuation" in {c["category_key"] for c in first["categories"]}


def test_provenance_lists_facts_eligibility_rules_assets_and_simplifications(
    client: TestClient,
) -> None:
    used = live(client)["age_pension"]
    facts = {f["key"]: f for f in used["facts"]}
    assert (facts["couple_status"]["value"], facts["couple_status"]["label"]) == (
        "couple",
        "default, not entered",
    )
    assert (facts["homeowner"]["value"], facts["homeowner"]["source"]) == ("yes", "default")
    assert (facts["principal_home"]["value"], facts["principal_home"]["label"]) == (
        "Family Home",
        "entered",
    )
    # Each rule value: key, financial year, value, origin and the provisional flag.
    pack = rulepack.get(assumptions.JURISDICTION)
    assert pack is not None
    keys = {r["key"] for r in used["rules"]}
    assert len(keys) == 16 and all(k.startswith("age_pension.") for k in keys)
    assert {r["financial_year"] for r in used["rules"]} == {"2026-27"}
    assert {r["from_year"] for r in used["rules"]} == {0}  # every key is cpi: one span
    assert {r["origin"] for r in used["rules"]} == {"shipped"}
    single = next(r for r in used["rules"] if r["key"] == "age_pension.max_basic.single")
    assert single["value"] == "1135.40"
    assert all(
        r["provisional"] == (pack.provisional_note(2027, r["key"]) is not None)
        for r in used["rules"]
    )
    assert any(r["provisional"] and r["provisional_note"] for r in used["rules"])
    # Each Property and Vehicle Account with its value and Valuation date; the home is exempt.
    assets = {a["name"]: a for a in used["assets"]}
    home = assets["Family Home"]
    assert (home["role"], home["value"], home["valued_on"]) == (
        "exempt_home",
        "822091.58",
        "2026-09-26",
    )
    car = assets["Family Car"]
    assert (car["role"], car["value"], car["valued_on"]) == ("assessed", "26089.07", "2026-06-23")
    assert used["left_out"] == []
    # The simplifications that apply: debt is not deducted from the Vehicle, and super drawn before
    # Age Pension age is not counted. No Property is assessed, so no rent or constant-value note.
    assert [x["key"] for x in used["simplifications"]] == [
        "no_debt_deduction",
        "retirement_super_before_age",
    ]
    assert all(x["text"] for x in used["simplifications"])


# --- the "not calculated, because ..." states ------------------------------------------------


def put_facts(client: TestClient, **body: Any) -> None:
    res = client.put(FACTS, json=body)
    assert res.status_code == 200, res.text


def second_property(client: TestClient) -> int:
    owner = client.get("/api/household").json()["members"][0]["id"]
    res = client.post(
        "/api/accounts",
        json={
            "name": "Sample Unit",
            "category_key": "property",
            "account_type_key": "investment_property",
            "ownership": "sole",
            "owners": [{"member_id": owner}],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_a_household_that_does_not_fit_runs_with_no_pension_and_says_why(
    client: TestClient,
) -> None:
    before = live(client)
    put_facts(client, couple_status="single")
    p = live(client)
    assert p["age_pension"]["status"] == "not_modelled"
    assert p["age_pension"]["reasons"] and p["age_pension"]["rules"] == []
    assert pension_rows(p) == []
    assert len(p["rows"]) == len(before["rows"])  # still a whole projection, never a failed run


def test_a_homeowner_with_several_property_accounts_and_no_choice_asks_for_one(
    client: TestClient,
) -> None:
    put_facts(client, principal_home_account_id=None)
    second_property(client)
    p = live(client)
    assert p["age_pension"]["status"] == "needs_home_choice"
    assert p["age_pension"]["reasons"] == ["Choose which Property Account is the principal home."]
    assert pension_rows(p) == []


def test_a_non_home_property_is_assessed_and_flags_the_rent_and_constant_value_simplifications(
    client: TestClient,
) -> None:
    unit = second_property(client)
    put_facts(client)  # the entered home (Family Home) stays
    # A Valuation for the second Property, so it is counted.
    res = client.post(
        f"/api/accounts/{unit}/valuations",
        json={
            "as_at": "2026-10-01",
            "amount": "400000.00",
            "source": "manual",
            "provenance_note": "Invented for the test.",
        },
    )
    assert res.status_code in (200, 201), res.text
    p = live(client)
    used = p["age_pension"]
    assets = {a["name"]: a for a in used["assets"]}
    assert assets["Sample Unit"]["role"] == "assessed"
    assert assets["Family Home"]["role"] == "exempt_home"
    first = pension_rows(p)[0]["age_pension"]
    assert cents(first["property_assets"]) == 40_000_000
    keys = [s["key"] for s in used["simplifications"]]
    assert keys == [
        "no_debt_deduction",
        "retirement_super_before_age",
        "rent_not_counted",
        "property_held_constant",
    ]


def test_no_pension_figures_for_the_first_financial_year_is_a_stated_state(
    client: TestClient,
) -> None:
    # FY 2025-26 has no Age Pension figures in the pack (it publishes 2026-27 only): the run goes
    # ahead with no pension and says why, never a silent zero.
    p = live(client, as_at="2026-01-15")
    assert p["age_pension"]["status"] == "no_figures"
    assert "2025-26" in p["age_pension"]["reasons"][0]
    assert pension_rows(p) == []


# --- saved runs ----------------------------------------------------------------------------


def save(client: TestClient) -> dict[str, Any]:
    res = client.post(
        "/api/projection/runs", json={"scenario_id": scenario_id(client), "as_at": AS_AT}
    )
    assert res.status_code == 201, res.text
    return res.json()


def detail(client: TestClient, run_id: int) -> dict[str, Any]:
    res = client.get(f"/api/projection/runs/{run_id}")
    assert res.status_code == 200, res.text
    return res.json()


def test_a_saved_run_reopens_with_its_working_and_provenance(client: TestClient) -> None:
    saved = save(client)
    p = live(client)
    reopened = detail(client, saved["id"])
    assert reopened["age_pension"] == p["age_pension"]
    assert [r["age_pension"] for r in reopened["rows"]] == [r["age_pension"] for r in p["rows"]]
    assert pension_rows(reopened)  # stored in result_json, so no re-run is needed
    with factory_of(client)() as session:
        row = session.scalars(select(ProjectionRun)).one()
        assert (row.engine_version, row.input_version) == (3, 3)
        stored = json.loads(row.input_json)["age_pension"]
        assert stored["couple"] is True and len(stored["spans"]) == 1


def test_a_change_of_pack_figures_or_facts_never_alters_a_saved_run(client: TestClient) -> None:
    saved = save(client)
    before = detail(client, saved["id"])
    # A captured figure replaces a shipped one (a new effective pack), and a Household fact changes.
    res = client.post(
        "/api/rule-figures",
        json={
            "financial_year": 2027,
            "key": "age_pension.max_basic.couple_each",
            "value": "900.00",
            "quote": "An invented sentence.",
        },
    )
    assert res.status_code == 201, res.text
    put_facts(client, homeowner=False)
    now = live(client)
    assert now["rules"]["pack_version"] != before["rules"]["pack_version"]
    changed = next(
        r for r in now["age_pension"]["rules"] if r["key"] == "age_pension.max_basic.couple_each"
    )
    assert (changed["value"], changed["origin"]) == ("900.00", "captured")
    assert now["rows"] != before["rows"]  # the live projection moved...
    after = detail(client, saved["id"])
    assert after == before  # ...the saved run did not


def test_a_run_saved_by_engine_2_reopens_and_reruns_through_the_upcaster(
    client: TestClient,
) -> None:
    """ADR-0029 section 6: input version 2 had no ``age_pension``, result version 2 no per-year
    working, and provenance none. It loads, shows as engine 2's work, and a re-run with the
    current engine reproduces its figures exactly (no pension is added to a run saved without)."""
    saved = save(client)
    with factory_of(client).begin() as session:
        row = session.get(ProjectionRun, saved["id"])
        assert row is not None
        stored = json.loads(row.input_json)
        stored.pop("age_pension")
        stored["input_version"] = 2
        # The engine-2 result is the one the current engine gives the version-2 input: with no
        # ``age_pension`` the engine is the old one (its golden tests are unchanged).
        old_input = pinput.from_json(json.dumps(stored))
        assert old_input.age_pension is None and old_input.input_version == pinput.INPUT_VERSION
        payload = result_json.result_payload(engine.project(old_input))
        payload["engine_version"], payload["input_version"] = 2, 2
        for year in payload["years"]:
            year.pop("age_pension")
        provenance = json.loads(row.provenance_json)
        provenance.pop("age_pension")
        prepared = runs._prepared_from(provenance, old_input)  # pyright: ignore[reportPrivateUsage]
        assert prepared.spending is not None
        most = sustainable_spending(
            old_input, prepared.spending, prepared.last_retirement_year, prepared.fixed_outflows
        )
        legacy = ProjectionRun(
            created_at=row.created_at,
            label="Saved by engine 2",
            scenario_id=row.scenario_id,
            scenario_name=row.scenario_name,
            engine_version=2,
            input_version=2,
            rule_pack_version=row.rule_pack_version,
            input_json=json.dumps(stored, sort_keys=True),
            result_json=json.dumps(payload, sort_keys=True),
            sustainable_json=result_json.sustainable_to_json(most),
            provenance_json=json.dumps(provenance, sort_keys=True),
            funds_last_to_age=row.funds_last_to_age,
            lasts_beyond_horizon=row.lasts_beyond_horizon,
        )
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id
    old = detail(client, legacy_id)
    assert (old["engine_version"], old["input_version"]) == (2, 2)
    assert old["run"]["current_engine"] is False
    assert old["age_pension"] is None and pension_rows(old) == []
    again = client.post(f"/api/projection/runs/{legacy_id}/rerun")
    assert again.status_code == 201, again.text
    redone = detail(client, again.json()["id"])
    assert (redone["engine_version"], redone["input_version"]) == (3, 3)
    assert redone["rows"] == old["rows"] and redone["sustainable"] == old["sustainable"]
    assert redone["age_pension"] is None  # the snapshot calculated none, and none is added
    with factory_of(client)() as session:
        stored_old = session.get(ProjectionRun, legacy_id)
        assert stored_old is not None and stored_old.engine_version == 2  # untouched
