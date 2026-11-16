"""Compare Scenarios side by side: one call resolves and projects each Scenario."""

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
TAX_ON = "Tax on, Alex's pay climbs"
NAMES = ["Best", "Likely", "Worst", "Retire early, spend more", TAX_ON]


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield without_income_streams(c)


def compare(client: TestClient) -> dict[str, Any]:
    res = client.get("/api/projection/compare", params={"as_at": AS_AT})
    assert res.status_code == 200, res.text
    return res.json()


def single(client: TestClient, scenario_id: int) -> dict[str, Any]:
    res = client.get(f"/api/projection/scenarios/{scenario_id}", params={"as_at": AS_AT})
    assert res.status_code == 200, res.text
    return res.json()


def test_each_headline_equals_that_scenarios_own_projection(sample: TestClient) -> None:
    body = compare(sample)
    assert [s["scenario"]["name"] for s in body["scenarios"]] == NAMES
    for item in body["scenarios"]:
        own = single(sample, item["scenario"]["id"])
        assert item["resolved"] is True and item["errors"] == []
        assert item["funds_last_to_age"] == own["funds_last_to_age"]
        assert item["lasts_beyond_horizon"] == own["lasts_beyond_horizon"]
        assert item["sustainable"] == own["sustainable"]
        assert item["horizon_age"] == own["reference_member"]["age_now"] + own["years"]
        assert item["provenance"] == own["provenance"]


def test_sustainable_income_is_ordered_best_likely_worst(sample: TestClient) -> None:
    by_name = {s["scenario"]["name"]: s for s in compare(sample)["scenarios"]}
    income = [Decimal(by_name[n]["sustainable"]["annual_spending"]) for n in NAMES[:3]]
    assert income[0] >= income[1] >= income[2]


def test_differing_lists_only_assumptions_that_are_not_the_same_everywhere(
    sample: TestClient,
) -> None:
    body = compare(sample)
    differing = {(d["key"], d["scope_ref"]) for d in body["differing"]}
    # The custom Scenario overrides these two (and its own spending list, below); Best and Worst
    # override return rates.
    assert ("annual_spending", "") not in differing  # retired: spending is a list of phases
    assert ("return_rate", "investments") in differing
    assert ("retirement_age", "1") in differing
    # Nobody overrides the horizon age: it is the Household default in every Scenario.
    assert ("horizon_age", "") not in differing
    assert ("retirement_age", "2") not in differing
    custom = next(s for s in body["scenarios"] if s["scenario"]["key"] is None)
    values = {(p["key"], p["scope_ref"]): (p["value"], p["layer"]) for p in custom["provenance"]}
    assert values[("retirement_age", "1")] == ("55", "scenario")


def test_differing_spending_phases_are_reported_with_each_scenarios_list(
    sample: TestClient,
) -> None:
    body = compare(sample)
    assert body["spending_differs"] is True  # the custom Scenario states its own list
    by_name = {s["scenario"]["name"]: s for s in body["scenarios"]}
    likely, custom = by_name["Likely"]["spending"], by_name["Retire early, spend more"]["spending"]
    assert (likely["layer"], custom["layer"]) == ("household", "scenario")
    assert [(p["offset_years"], p["annual_amount"]) for p in likely["phases"]] == [
        (0, "70000.00"),
        (10, "55000.00"),
    ]
    assert [(p["offset_years"], p["annual_amount"]) for p in custom["phases"]] == [(0, "85000.00")]
    # Best, Likely and Worst all use the Household default list.
    assert by_name["Best"]["spending"] == by_name["Likely"]["spending"]


def test_spending_phases_do_not_differ_when_every_scenario_uses_the_same_list(
    sample: TestClient,
) -> None:
    custom = next(s for s in compare(sample)["scenarios"] if s["scenario"]["key"] is None)
    res = sample.put(
        "/api/spending-phases", json={"scenario_id": custom["scenario"]["id"], "phases": None}
    )
    assert res.status_code == 200, res.text
    body = compare(sample)
    assert body["spending_differs"] is False


def test_an_unresolvable_scenario_names_its_key_and_the_others_still_project(
    sample: TestClient,
) -> None:
    removed = sample.put("/api/spending-phases", json={"phases": []})
    assert removed.status_code == 200, removed.text
    body = compare(sample)
    by_name = {s["scenario"]["name"]: s for s in body["scenarios"]}
    for name in ("Best", "Likely", "Worst"):
        item = by_name[name]
        assert item["resolved"] is False
        assert [e["field"] for e in item["errors"]] == ["spending_phases"]
        assert "no phases" in item["errors"][0]["message"]
        assert item["sustainable"] is None and item["funds_last_to_age"] is None
    custom = by_name["Retire early, spend more"]  # states its own spending, so it resolves
    assert custom["resolved"] is True
    own = single(sample, custom["scenario"]["id"])
    assert custom["funds_last_to_age"] == own["funds_last_to_age"]
    assert body["differing"] == []  # only one Scenario resolved: nothing to differ from
