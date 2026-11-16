"""Scenarios and Assumptions end to end: HTTP, services, real temporary SQLite, the engine
(ADR-0016). The synthetic household: Alex (born 1984-03-12) and Jordan (born
1986-09-30), the fixture's Household defaults and its one custom Scenario."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client, without_income_streams

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
ALEX, JORDAN = 1, 2


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield without_income_streams(c)


def scenarios_by_name(client: TestClient) -> dict[str, dict[str, Any]]:
    res = client.get("/api/scenarios")
    assert res.status_code == 200
    return {s["name"]: s for s in res.json()}


def project(client: TestClient, name: str) -> dict[str, Any]:
    scenario_id = scenarios_by_name(client)[name]["id"]
    res = client.get(f"/api/projection/scenarios/{scenario_id}", params={"as_at": AS_AT})
    assert res.status_code == 200, res.text
    return res.json()


def set_default(client: TestClient, *changes: dict[str, Any]) -> dict[str, Any]:
    res = client.put("/api/assumptions", json={"changes": list(changes)})
    assert res.status_code == 200, res.text
    return res.json()


def household(key: str, value: str | None) -> dict[str, Any]:
    return {"key": key, "scope_type": "household", "scope_ref": "", "value": value}


def category(key: str, ref: str, value: str | None) -> dict[str, Any]:
    return {"key": key, "scope_type": "category", "scope_ref": ref, "value": value}


def member(member_id: int, value: str | None) -> dict[str, Any]:
    return {
        "key": "retirement_age",
        "scope_type": "member",
        "scope_ref": str(member_id),
        "value": value,
    }


def provenance(p: dict[str, Any]) -> dict[tuple[str, str], tuple[str, str]]:
    return {(x["key"], x["scope_ref"]): (x["value"], x["layer"]) for x in p["provenance"]}


def test_the_seeded_scenarios_and_the_fixtures_custom_one_are_listed(sample: TestClient) -> None:
    by_name = scenarios_by_name(sample)
    assert list(by_name) == [
        "Best",
        "Likely",
        "Worst",
        "Retire early, spend more",
        "Tax on, Alex's pay climbs",
    ]
    assert [by_name[n]["key"] for n in by_name] == ["best", "likely", "worst", None, None]
    assert [by_name[n]["override_count"] for n in by_name] == [4, 0, 5, 2, 3]


def test_likely_projects_identically_to_the_pre_scenario_single_projection(
    sample: TestClient,
) -> None:
    """Golden: Likely states nothing, so it is the Household default, and the figures are exactly
    what the ad-hoc request with those same Assumptions produced before Scenarios existed."""
    before = sample.post(
        "/api/projection/run",
        json={
            "as_at": AS_AT,
            "inflation": "2.5",
            "returns": [
                {"category": "cash", "rate": "3"},
                {"category": "investments", "rate": "7"},
                {"category": "superannuation", "rate": "7"},
                {"category": "overseas_pensions", "rate": "6"},
            ],
            "retirement_ages": [{"member_id": ALEX, "age": 60}, {"member_id": JORDAN, "age": 62}],
            "spending_phases": [
                {"anchor": "last_retirement", "offset_years": 0, "annual_amount": "70000.00"},
                {"anchor": "last_retirement", "offset_years": 10, "annual_amount": "55000.00"},
            ],
            "annual_saving": "20000.00",
            "horizon_age": 95,
        },
    )
    assert before.status_code == 200, before.text
    after = project(sample, "Likely")
    ignored = ("scenario", "provenance")
    # The ad hoc request names no layer; a Scenario's run says which supplied the spending phases.
    expected = before.json()
    assert expected["spending"]["layer"] is None and after["spending"]["layer"] == "household"
    after["spending"]["layer"] = None
    assert {k: v for k, v in after.items() if k not in ignored} == expected
    assert after["scenario"]["key"] == "likely"


def test_switching_best_likely_worst_changes_the_results_in_order(sample: TestClient) -> None:
    best, likely, worst = (project(sample, n) for n in ("Best", "Likely", "Worst"))
    ends = [Decimal(p["rows"][-1]["closing"]) for p in (best, likely, worst)]
    assert ends[0] > ends[1] > ends[2]
    sustainable = [Decimal(p["sustainable"]["annual_spending"]) for p in (best, likely, worst)]
    assert sustainable[0] > sustainable[1] > sustainable[2]
    assert best["rows"][0]["opening"] == likely["rows"][0]["opening"]  # the same Position


def test_provenance_says_which_layer_supplied_each_value(sample: TestClient) -> None:
    best = provenance(project(sample, "Best"))
    assert best[("return_rate", "investments")] == ("10.5", "scenario")
    assert best[("inflation", "")] == ("2.5", "household")
    assert ("annual_spending", "") not in best  # retired: spending is a list of phases
    assert project(sample, "Best")["spending"]["layer"] == "household"
    custom = provenance(project(sample, "Retire early, spend more"))
    assert custom[("retirement_age", str(ALEX))] == ("55", "scenario")
    assert custom[("retirement_age", str(JORDAN))] == ("62", "household")
    assert project(sample, "Retire early, spend more")["spending"]["layer"] == "scenario"
    assert custom[("return_rate", "cash")] == ("3", "household")


def test_editing_a_household_default_flags_the_scenarios_that_override_it(
    sample: TestClient,
) -> None:
    saved = set_default(sample, category("return_rate", "investments", "6.5"))
    assert saved["review"] == [
        {
            "key": "return_rate",
            "scope_type": "category",
            "scope_ref": "investments",
            "scenarios": ["Best", "Worst", "Retire early, spend more"],
        }
    ]
    # Likely moved with the default; Best did not (absolute values, not deltas).
    assert provenance(project(sample, "Likely"))[("return_rate", "investments")] == (
        "6.5",
        "household",
    )
    assert provenance(project(sample, "Best"))[("return_rate", "investments")][0] == "10.5"
    # The same flags are readable on the layer, for the editing form.
    rows = sample.get("/api/assumptions").json()["rows"]
    investments = next(
        r for r in rows if (r["key"], r["scope_ref"]) == ("return_rate", "investments")
    )
    assert investments["overridden_by"] == ["Best", "Worst", "Retire early, spend more"]
    # A default nothing overrides gives no review.
    assert set_default(sample, household("annual_saving", "25000"))["review"] == []


def test_a_scenario_layer_is_edited_and_a_removed_override_inherits_again(
    sample: TestClient,
) -> None:
    worst = scenarios_by_name(sample)["Worst"]["id"]
    res = sample.put(
        "/api/assumptions",
        json={"scenario_id": worst, "changes": [category("return_rate", "cash", None)]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["review"] == []
    p = provenance(project(sample, "Worst"))
    assert p[("return_rate", "cash")] == ("3", "household")  # inherits the default again


def test_an_unset_key_returns_422_naming_it(sample: TestClient) -> None:
    cleared = sample.put("/api/spending-phases", json={"phases": []})
    assert cleared.status_code == 200, cleared.text
    likely = scenarios_by_name(sample)["Likely"]["id"]
    res = sample.get(f"/api/projection/scenarios/{likely}", params={"as_at": AS_AT})
    assert res.status_code == 422
    assert [e["field"] for e in res.json()["errors"]] == ["spending_phases"]
    assert "Spending has no phases" in res.json()["errors"][0]["message"]


def test_validation_errors_use_the_shared_shape_and_write_nothing(sample: TestClient) -> None:
    res = sample.put(
        "/api/assumptions",
        json={"changes": [household("inflation", "9"), member(ALEX, "sixty")]},
    )
    assert res.status_code == 422
    assert res.json() == {
        "errors": [{"field": "retirement_age.1", "message": "Enter a whole number of years."}]
    }
    rows = {
        (r["key"], r["scope_ref"]): r["value"]
        for r in sample.get("/api/assumptions").json()["rows"]
    }
    assert rows[("inflation", "")] == "2.5"
    assert sample.get("/api/assumptions", params={"scenario_id": 999}).status_code == 404


def test_scenarios_are_added_cloned_renamed_and_deleted(sample: TestClient) -> None:
    custom = scenarios_by_name(sample)["Retire early, spend more"]
    made = sample.post(
        "/api/scenarios", json={"name": "Copy of early", "notes": "n", "copy_from": custom["id"]}
    )
    assert made.status_code == 201
    assert made.json()["override_count"] == 2
    assert provenance(project(sample, "Copy of early")) == provenance(
        project(sample, "Retire early, spend more")
    )
    # The clone has its own copy of the source's spending list, not a link to it.
    assert made.json()["spending_is_default"] is False
    assert (
        project(sample, "Copy of early")["spending"]
        == project(sample, "Retire early, spend more")["spending"]
    )
    clash = sample.post("/api/scenarios", json={"name": "best"})
    assert clash.status_code == 422 and clash.json()["errors"][0]["field"] == "name"
    renamed = sample.put(f"/api/scenarios/{made.json()['id']}", json={"name": "Early v2"})
    assert renamed.json()["name"] == "Early v2"
    assert sample.delete(f"/api/scenarios/{made.json()['id']}").status_code == 204
    assert "Early v2" not in scenarios_by_name(sample)
    builtin = sample.delete(f"/api/scenarios/{scenarios_by_name(sample)['Best']['id']}")
    assert builtin.status_code == 422 and "built in" in builtin.json()["errors"][0]["message"]


def test_changes_are_in_the_audit_trail(sample: TestClient) -> None:
    set_default(sample, household("inflation", "3"))
    trail = sample.get("/api/audit", params={"entity_type": "assumption"}).json()["items"]
    assert trail[0]["summary"] == "Changed Inflation per year in Household default from 2.5 to 3"
    assert (trail[0]["entity_type"], trail[0]["action"]) == ("assumption", "update")
