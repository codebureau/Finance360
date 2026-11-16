"""Prices through the API: HTTP -> service -> real temporary SQLite, including the explicit
no-Price answer, source precedence and the shipped sample household's history with a gap."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]


def _settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir, port=8000, git_sha="dev", static_dir=None)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with owner_client(create_app(_settings(tmp_path / "data"))) as c:
        yield c


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    with owner_client(create_app(_settings(data))) as c:
        yield c


def _instrument(client: TestClient, currency: str = "AUD") -> int:
    res = client.post(
        "/api/instruments", json={"name": "Sample ETF", "kind": "etf", "currency": currency}
    )
    return res.json()["id"]


def _body(as_at: str, value: str, note: str | None = "Checked", **extra: Any) -> dict[str, Any]:
    return {"as_at": as_at, "value": value, "provenance_note": note, **extra}


def _on(client: TestClient, instrument: int, on: str) -> Any:
    res = client.get(f"/api/instruments/{instrument}/price", params={"on": on})
    assert res.status_code == 200
    return res.json()


def test_add_list_correct_and_remove_through_the_api(client: TestClient) -> None:
    i = _instrument(client)
    assert client.get(f"/api/instruments/{i}/prices").json() == []
    created = client.post(f"/api/instruments/{i}/prices", json=_body("2026-09-01", "98.1234"))
    assert created.status_code == 201
    body = created.json()
    assert (body["value"], body["currency"], body["source"], body["status"]) == (
        "98.1234",
        "AUD",
        "manual",
        "current",
    )
    assert (
        client.post(f"/api/instruments/{i}/prices", json=_body("2026-09-03", "100")).json()["value"]
        == "100.00"
    )

    corrected = client.put(
        f"/api/instruments/{i}/prices/{body['id']}", json=_body("2026-09-01", "99")
    )
    assert corrected.status_code == 200 and corrected.json()["value"] == "99.00"
    sep1 = [
        p for p in client.get(f"/api/instruments/{i}/prices").json() if p["as_at"] == "2026-09-01"
    ]
    assert [(p["value"], p["status"]) for p in sep1] == [
        ("99.00", "current"),
        ("98.1234", "corrected"),
    ]

    gone = client.delete(f"/api/instruments/{i}/prices/{corrected.json()['id']}")
    assert gone.status_code == 204
    assert _on(client, i, "2026-09-02")["result"] == "no_price"


def test_validation_uses_the_shared_error_shape(client: TestClient) -> None:
    i = _instrument(client)
    res = client.post(
        f"/api/instruments/{i}/prices", json=_body("2026-09-01", "0", None, currency="USD")
    )
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {"value", "provenance_note", "currency"}
    assert client.get("/api/instruments/999/prices").status_code == 404
    assert client.get("/api/instruments/999/price", params={"on": "2026-09-01"}).status_code == 404
    assert client.delete(f"/api/instruments/{i}/prices/999").status_code == 404


def test_lookup_answers_with_the_price_and_its_as_at_or_explicitly_none(client: TestClient) -> None:
    i = _instrument(client)
    client.post(f"/api/instruments/{i}/prices", json=_body("2026-09-02", "11.5"))
    found = _on(client, i, "2026-09-10")
    assert found == {
        "result": "price",
        "on": "2026-09-10",
        "value": "11.50",
        "currency": "AUD",
        "as_at": "2026-09-02",
        "source": "manual",
    }
    none = _on(client, i, "2026-09-01")
    assert none["result"] == "no_price" and none["value"] is None and none["as_at"] is None


def test_the_api_cannot_enter_a_provider_price(client: TestClient) -> None:
    i = _instrument(client)
    res = client.post(
        f"/api/instruments/{i}/prices", json=_body("2026-09-01", "10", source="yfinance")
    )
    assert res.status_code == 201 and res.json()["source"] == "manual"  # the field is ignored


def test_corrections_and_removals_are_in_the_audit_trail(client: TestClient) -> None:
    i = _instrument(client)
    p = client.post(f"/api/instruments/{i}/prices", json=_body("2026-09-01", "10")).json()["id"]
    client.put(f"/api/instruments/{i}/prices/{p}", json=_body("2026-09-01", "11"))
    trail = client.get("/api/audit", params={"entity_type": "price"}).json()
    assert [e["action"] for e in trail["items"]] == ["supersede", "create"]
    assert "(was 10.00)" in trail["items"][0]["summary"]


def test_the_sample_household_etf_has_a_history_with_a_gap_and_a_manual_override_wins(
    sample: TestClient,
) -> None:
    etf = next(i["id"] for i in sample.get("/api/instruments").json() if i["kind"] == "etf")
    history = sample.get(f"/api/instruments/{etf}/prices").json()
    dates = {p["as_at"] for p in history}
    assert "2026-08-14" in dates and "2026-08-24" in dates and "2026-08-19" not in dates
    assert {p["source"] for p in history} == {"yfinance"}
    # inside the gap: the last Price before it, with its own date
    assert _on(sample, etf, "2026-08-19")["as_at"] == "2026-08-14"
    # before the first Price: explicitly none, not zero
    assert _on(sample, etf, "2026-07-31")["result"] == "no_price"
    # a manual Price on an existing date wins the tie
    before = _on(sample, etf, "2026-09-01")
    assert before["source"] == "yfinance"
    override = sample.post(f"/api/instruments/{etf}/prices", json=_body("2026-09-01", "123.456789"))
    assert override.status_code == 201
    after = _on(sample, etf, "2026-09-01")
    assert (after["source"], after["value"]) == ("manual", "123.456789")
    # the provider's Price is still there beneath it
    both = [
        p for p in sample.get(f"/api/instruments/{etf}/prices").json() if p["as_at"] == "2026-09-01"
    ]
    assert {p["source"] for p in both} == {"manual", "yfinance"}


def test_the_sample_fund_has_sparse_manual_prices(sample: TestClient) -> None:
    fund = next(i["id"] for i in sample.get("/api/instruments").json() if i["kind"] == "fund")
    history = sample.get(f"/api/instruments/{fund}/prices").json()
    assert [(p["as_at"], p["source"]) for p in history] == [
        ("2026-09-30", "manual"),
        ("2026-06-30", "manual"),
        ("2026-03-31", "manual"),
    ]
    assert _on(sample, fund, "2026-08-15")["as_at"] == "2026-06-30"
