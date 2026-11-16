"""The ordered Drawdown policy per Scenario (ADR-0016 section 3): HTTP, services, real
temporary SQLite and the engine. The synthetic household: Alex and Jordan, the fixture's Household
defaults and its custom Scenario, which states a non-default order."""

from __future__ import annotations

import itertools
import shutil
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from finance360.capabilities.projection import service
from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client, without_income_streams

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
DEFAULT = ["cash", "investments", "superannuation", "overseas_pensions"]
INVESTMENTS_FIRST = ["investments", "cash", "superannuation", "overseas_pensions"]
CUSTOM = "Retire early, spend more"


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield without_income_streams(c)


def by_name(client: TestClient) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in client.get("/api/scenarios").json()}


def keys(categories: list[dict[str, Any]]) -> list[str]:
    return [c["key"] for c in categories]


def set_order(client: TestClient, scenario_id: int, order: list[str] | None) -> Any:
    return client.put(f"/api/scenarios/{scenario_id}/drawdown-order", json={"order": order})


def project(client: TestClient, scenario_id: int) -> dict[str, Any]:
    res = client.get(f"/api/projection/scenarios/{scenario_id}", params={"as_at": AS_AT})
    assert res.status_code == 200, res.text
    return res.json()


def first_spending_year(p: dict[str, Any]) -> dict[str, Any]:
    return next(r for r in p["rows"] if r["need"] != "0.00")


def withdrawn(row: dict[str, Any]) -> dict[str, Decimal]:
    return {c["category_key"]: Decimal(c["withdrawn"]) for c in row["categories"]}


def test_no_stated_order_means_the_default_constant(sample: TestClient) -> None:
    likely = by_name(sample)["Likely"]
    assert likely["drawdown_is_default"] is True
    assert keys(likely["drawdown_order"]) == DEFAULT == list(service.DRAWDOWN_ORDER)
    assert keys(project(sample, likely["id"])["categories"]) == DEFAULT


def test_the_fixtures_custom_scenario_states_a_non_default_order(sample: TestClient) -> None:
    custom = by_name(sample)[CUSTOM]
    assert custom["drawdown_is_default"] is False
    assert keys(custom["drawdown_order"]) == INVESTMENTS_FIRST


def test_the_order_decides_which_category_funds_spending_first(sample: TestClient) -> None:
    """Hand-checked on the same Assumptions: Likely draws Cash before Investments, a copy of it
    with Investments first draws only Investments. Either way the year's need is met exactly."""
    likely = by_name(sample)["Likely"]
    copy = sample.post("/api/scenarios", json={"name": "Likely, investments first"}).json()
    assert set_order(sample, copy["id"], INVESTMENTS_FIRST).status_code == 200

    default_row = first_spending_year(project(sample, likely["id"]))
    custom_row = first_spending_year(project(sample, copy["id"]))
    assert default_row["need"] == custom_row["need"] == "70000.00"
    opening = {c["category_key"]: Decimal(c["opening"]) for c in default_row["categories"]}

    # Cash first: Cash is drained before Investments takes the rest.
    cash_first = withdrawn(default_row)
    assert cash_first["cash"] > 0 and cash_first["investments"] > 0
    assert cash_first["superannuation"] == cash_first["overseas_pensions"] == 0
    assert sum(cash_first.values()) == Decimal("70000.00")
    assert cash_first["investments"] == Decimal("70000.00") - cash_first["cash"]

    # Investments first: it alone covers the need, so Cash is untouched this year.
    assert withdrawn(custom_row) == {
        "investments": Decimal("70000.00"),
        "cash": Decimal("0.00"),
        "superannuation": Decimal("0.00"),
        "overseas_pensions": Decimal("0.00"),
    }
    assert opening["investments"] > Decimal("70000.00")


@pytest.mark.parametrize("order", list(itertools.permutations(DEFAULT)))
def test_every_order_is_applied_as_given(sample: TestClient, order: tuple[str, ...]) -> None:
    """For any order, a Category is drawn on only once every Category ahead of it is empty."""
    copy = sample.post("/api/scenarios", json={"name": "Permuted"}).json()
    assert keys(set_order(sample, copy["id"], list(order)).json()["drawdown_order"]) == list(order)
    p = project(sample, copy["id"])
    assert keys(p["categories"]) == list(order)
    for row in p["rows"]:
        closing = {c["category_key"]: Decimal(c["closing"]) for c in row["categories"]}
        drawn = withdrawn(row)
        assert sum(drawn.values()) + Decimal(row["shortfall"]) == Decimal(row["need"])
        for i, later in enumerate(order):
            if drawn[later] > 0:
                assert all(closing[earlier] == 0 for earlier in order[:i]), (row["t"], order)


@pytest.mark.parametrize(
    ("order", "message"),
    [
        (["cash", "cash", "investments", "superannuation"], "'cash' appears more than once."),
        (["cash", "property", "investments", "superannuation"], "'property' is not a projected"),
        (["cash", "investments"], "Missing: superannuation, overseas_pensions."),
        ([], "Cash must stay in the drawdown order."),
        (["investments", "superannuation", "overseas_pensions"], "Cash must stay in the drawdown"),
    ],
)
def test_an_invalid_order_is_rejected_with_a_field_error(
    sample: TestClient, order: list[str], message: str
) -> None:
    custom = by_name(sample)[CUSTOM]
    res = set_order(sample, custom["id"], order)
    assert res.status_code == 422
    [error] = res.json()["errors"]
    assert error["field"] == "drawdown_order" and message in error["message"]
    assert keys(by_name(sample)[CUSTOM]["drawdown_order"]) == INVESTMENTS_FIRST  # nothing stored


def test_an_unknown_scenario_is_not_found(sample: TestClient) -> None:
    assert set_order(sample, 999, DEFAULT).status_code == 404


def test_reset_restores_the_default_and_a_whole_list_replaces_the_old_one(
    sample: TestClient,
) -> None:
    custom = by_name(sample)[CUSTOM]
    swapped = ["overseas_pensions", "superannuation", "cash", "investments"]
    assert keys(set_order(sample, custom["id"], swapped).json()["drawdown_order"]) == swapped
    reset = set_order(sample, custom["id"], None).json()
    assert reset["drawdown_is_default"] is True and keys(reset["drawdown_order"]) == DEFAULT
    assert keys(project(sample, custom["id"])["categories"]) == DEFAULT


def test_changes_are_audited_with_before_and_after(sample: TestClient) -> None:
    likely = by_name(sample)["Likely"]
    set_order(sample, likely["id"], INVESTMENTS_FIRST)
    set_order(sample, likely["id"], INVESTMENTS_FIRST)  # unchanged: not a change
    set_order(sample, likely["id"], None)
    trail = sample.get("/api/audit", params={"entity_type": "scenario"}).json()["items"]
    updates = [
        t
        for t in trail
        if t["action"] == "update" and "Drawdown policy of 'Likely'" in t["summary"]
    ]
    assert [t["summary"] for t in updates] == [
        "Changed the Drawdown policy of 'Likely' from investments, cash, superannuation, "
        "overseas_pensions to the default order",
        "Changed the Drawdown policy of 'Likely' from the default order to investments, cash, "
        "superannuation, overseas_pensions",
    ]


def test_a_copy_of_a_scenario_keeps_its_order(sample: TestClient) -> None:
    custom = by_name(sample)[CUSTOM]
    made = sample.post("/api/scenarios", json={"name": "Copy", "copy_from": custom["id"]}).json()
    assert keys(made["drawdown_order"]) == INVESTMENTS_FIRST
    assert made["drawdown_is_default"] is False


def test_a_saved_run_keeps_the_order_it_used(sample: TestClient) -> None:
    custom = by_name(sample)[CUSTOM]
    run = sample.post(
        "/api/projection/runs", json={"scenario_id": custom["id"], "as_at": AS_AT}
    ).json()
    saved = sample.get(f"/api/projection/runs/{run['id']}").json()
    assert keys(saved["categories"]) == INVESTMENTS_FIRST
    assert withdrawn(first_spending_year(saved))["investments"] > 0

    set_order(sample, custom["id"], None)  # the Scenario moves on...
    assert keys(project(sample, custom["id"])["categories"]) == DEFAULT
    assert sample.get(f"/api/projection/runs/{run['id']}").json() == saved  # ...the run does not


def test_the_stored_run_input_carries_the_order(sample: TestClient) -> None:
    from sqlalchemy import select

    from finance360.capabilities.projection import input as pinput
    from finance360.core.models.projection_run import ProjectionRun

    custom = by_name(sample)[CUSTOM]
    sample.post("/api/projection/runs", json={"scenario_id": custom["id"], "as_at": AS_AT})
    factory = sample.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        stored = pinput.from_json(session.scalars(select(ProjectionRun)).one().input_json)
    assert stored.drawdown_order == tuple(INVESTMENTS_FIRST)


def test_the_comparison_says_when_the_orders_differ(sample: TestClient) -> None:
    compared = sample.get("/api/projection/compare", params={"as_at": AS_AT}).json()
    assert compared["drawdown_differs"] is True
    orders = {s["scenario"]["name"]: keys(s["drawdown_order"]) for s in compared["scenarios"]}
    assert orders["Likely"] == DEFAULT and orders[CUSTOM] == INVESTMENTS_FIRST

    set_order(sample, by_name(sample)[CUSTOM]["id"], None)
    same = sample.get("/api/projection/compare", params={"as_at": AS_AT}).json()
    assert same["drawdown_differs"] is False
