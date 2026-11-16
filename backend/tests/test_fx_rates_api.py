"""FX rates through the API: HTTP -> service -> real temporary SQLite, including audit, the
missing-rate answer and the shipped sample household."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.config import Settings
from finance360.core.models.audit import AuditEvent
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


def _body(as_at: str, rate: str, ccy: str = "GBP", note: str = "Checked") -> dict[str, Any]:
    return {"from_ccy": ccy, "as_at": as_at, "rate": rate, "provenance_note": note}


def _convert(client: TestClient, on: str, amount: str = "100.00", ccy: str = "GBP") -> Any:
    res = client.get("/api/fx-rates/convert", params={"amount": amount, "currency": ccy, "on": on})
    assert res.status_code == 200
    return res.json()


def _audit_actions(client: TestClient) -> list[str]:
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        assert isinstance(s, Session)
        return [e.action for e in s.scalars(select(AuditEvent)) if e.entity_type == "fx_rate"]


def test_add_list_correct_and_remove_through_the_api(client: TestClient) -> None:
    assert client.get("/api/fx-rates").json() == []
    created = client.post("/api/fx-rates", json=_body("2026-01-05", "2.0143000001"))
    assert created.status_code == 201
    body = created.json()
    assert (body["from_ccy"], body["to_ccy"], body["rate"], body["status"]) == (
        "GBP",
        "AUD",
        "2.0143000001",
        "current",
    )
    assert client.post("/api/fx-rates", json=_body("2026-03-05", "2")).json()["rate"] == "2.00"

    corrected = client.put(f"/api/fx-rates/{body['id']}", json=_body("2026-01-05", "2.5"))
    assert corrected.status_code == 200 and corrected.json()["rate"] == "2.50"
    listed = client.get("/api/fx-rates").json()
    jan = [r for r in listed if r["as_at"] == "2026-01-05"]
    assert [(r["rate"], r["status"]) for r in jan] == [
        ("2.50", "current"),
        ("2.0143000001", "corrected"),
    ]
    assert jan[1]["superseded_by_id"] == jan[0]["id"]

    new_id = corrected.json()["id"]
    assert client.delete(f"/api/fx-rates/{new_id}").status_code == 204
    gone = [r for r in client.get("/api/fx-rates").json() if r["id"] == new_id]
    assert [(r["status"], r["superseded_by_id"]) for r in gone] == [("removed", None)]
    assert _audit_actions(client) == ["create", "create", "supersede", "supersede"]


def test_validation_is_422_with_field_messages_and_floats_are_refused(client: TestClient) -> None:
    res = client.post("/api/fx-rates", json=_body("2026-01-05", "abc", "XXX", " "))
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {"from_ccy", "rate", "provenance_note"}
    as_float = client.post("/api/fx-rates", json={**_body("2026-01-05", "2"), "rate": 2.5})
    assert as_float.status_code == 422
    malformed = client.post("/api/fx-rates", json=_body("2026-13-45", "2"))
    assert malformed.json()["errors"][0]["field"] == "as_at"
    assert client.post("/api/fx-rates", json=_body("2026-01-05", "2")).status_code == 201
    assert client.post("/api/fx-rates", json=_body("2026-01-05", "3")).status_code == 422


def test_unknown_rate_is_404(client: TestClient) -> None:
    assert client.put("/api/fx-rates/999", json=_body("2026-01-05", "2")).status_code == 404
    assert client.delete("/api/fx-rates/999").status_code == 404


def test_conversion_uses_the_older_rate_before_a_newer_one_and_the_newer_after(
    client: TestClient,
) -> None:
    client.post("/api/fx-rates", json=_body("2026-01-01", "2.0143"))
    before = _convert(client, "2026-05-01")
    assert (before["result"], before["converted"], before["rate"]) == (
        "converted",
        "201.43",
        "2.0143",
    )
    assert before["rate_as_at"] == "2026-01-01"
    client.post("/api/fx-rates", json=_body("2026-05-15", "2.1"))
    assert _convert(client, "2026-05-01")["converted"] == "201.43"
    after = _convert(client, "2026-06-01")
    assert (after["converted"], after["rate_as_at"]) == ("210.00", "2026-05-15")


def test_a_missing_rate_is_an_explicit_answer_not_zero_or_one(client: TestClient) -> None:
    client.post("/api/fx-rates", json=_body("2026-01-01", "2"))
    early = _convert(client, "2025-12-31")
    assert early["result"] == "no_rate"
    assert early["converted"] is None and early["rate"] is None and early["rate_as_at"] is None
    assert _convert(client, "2026-06-01", ccy="USD")["result"] == "no_rate"
    params = {"amount": "x", "currency": "GBP", "on": "2026-06-01"}
    bad = client.get("/api/fx-rates/convert", params=params)
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "amount"


def test_the_sample_household_has_gbp_rates_over_the_valuation_period(sample: TestClient) -> None:
    rates = sample.get("/api/fx-rates").json()
    assert len(rates) >= 4 and {r["from_ccy"] for r in rates} == {"GBP"}
    assert all(r["status"] == "current" and r["to_ccy"] == "AUD" for r in rates)
    dates = sorted(r["as_at"] for r in rates)
    assert dates[0] <= "2024-10-31" and dates[-1] >= "2026-06-30"
    assert any(len(r["rate"].split(".")[1]) > 2 for r in rates)  # precision beyond 2 dp
    assert _convert(sample, "2026-05-01")["rate"] == "2.0204"
    assert set(_audit_actions(sample)) == {"create"}
    with sample.app.state.session_factory() as s:  # type: ignore[attr-defined]
        sources = {e.source for e in s.scalars(select(AuditEvent)) if e.entity_type == "fx_rate"}
    assert sources == {"fixture"}


def test_a_conversion_too_large_for_money_is_a_field_error_not_a_500(client: TestClient) -> None:
    client.post("/api/fx-rates", json=_body("2026-01-01", "2.0143"))
    res = client.get(
        "/api/fx-rates/convert",
        params={"amount": "92233720368547758.07", "currency": "GBP", "on": "2026-05-01"},
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "amount"
