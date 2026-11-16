"""The read-only catalogue API on a fresh instance (HTTP -> service -> real temporary SQLite)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / "data", port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def test_a_fresh_instance_lists_classes_categories_and_account_types(client: TestClient) -> None:
    body = client.get("/api/catalogue").json()
    assert body["version"] == 2
    assert [c["class"] for c in body["classes"]] == ["asset", "liability"]
    asset, liability = body["classes"]
    assert [c["name"] for c in asset["categories"]] == [
        "Cash", "Investments", "Superannuation", "Overseas pensions", "Property", "Vehicles",
    ]  # fmt: skip
    assert [c["name"] for c in liability["categories"]] == ["Loans & credit"]
    cash = asset["categories"][0]
    assert [t["name"] for t in cash["account_types"]] == [
        "Transaction account", "Savings account", "Offset account",
    ]  # fmt: skip
    assert {t["behaviour_key"] for c in body["classes"] for k in c["categories"]
            for t in k["account_types"]} == {"simple_balance", "holdings_based"}  # fmt: skip
    assert sum(len(k["account_types"]) for c in body["classes"] for k in c["categories"]) == 11


def test_restarting_does_not_duplicate_the_catalogue(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", port=8000, git_sha="dev", static_dir=None)
    for _ in range(2):
        with owner_client(create_app(settings)) as c:
            classes = c.get("/api/catalogue").json()["classes"]
            assert sum(len(cl["categories"]) for cl in classes) == 7
