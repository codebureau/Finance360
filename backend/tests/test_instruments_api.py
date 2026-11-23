"""Instruments through the API: HTTP -> service -> real temporary SQLite, including the shipped
sample household's roster, rejection of duplicate identifiers and the audit trail."""

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


def _body(name: str = "Sample ETF", **extra: Any) -> dict[str, Any]:
    return {"name": name, "kind": "etf", "currency": "AUD", **extra}


def test_create_get_edit_and_list(client: TestClient) -> None:
    assert client.get("/api/instruments").json() == []
    res = client.post(
        "/api/instruments",
        json=_body(identifiers=[{"scheme": "ticker", "value": "sbme"}], notes="Invented"),
    )
    assert res.status_code == 201
    body = res.json()
    assert (body["name"], body["kind"], body["currency"], body["notes"]) == (
        "Sample ETF",
        "etf",
        "AUD",
        "Invented",
    )
    assert [(i["scheme"], i["value"]) for i in body["identifiers"]] == [("ticker", "sbme")]
    assert client.get(f"/api/instruments/{body['id']}").json() == body

    edited = client.put(f"/api/instruments/{body['id']}", json=_body("Sample ETF Renamed"))
    assert edited.status_code == 200 and edited.json()["name"] == "Sample ETF Renamed"
    assert edited.json()["notes"] is None  # PUT replaces the editable fields
    assert [i["value"] for i in edited.json()["identifiers"]] == ["sbme"]  # identifiers untouched
    assert [i["name"] for i in client.get("/api/instruments").json()] == ["Sample ETF Renamed"]


def test_a_duplicate_ticker_is_rejected_with_the_shared_error_shape(client: TestClient) -> None:
    client.post(
        "/api/instruments", json=_body("First", identifiers=[{"scheme": "ticker", "value": "SBME"}])
    )
    other = client.post("/api/instruments", json=_body("Second")).json()["id"]
    res = client.post(
        f"/api/instruments/{other}/identifiers", json={"scheme": "ticker", "value": " sbme "}
    )
    assert res.status_code == 422
    [error] = res.json()["errors"]
    assert error["field"] == "value" and "already identifies First" in error["message"]
    assert client.get(f"/api/instruments/{other}").json()["identifiers"] == []


def test_identifiers_are_added_and_removed(client: TestClient) -> None:
    fund = client.post("/api/instruments", json={**_body("Sample Fund"), "kind": "fund"}).json()[
        "id"
    ]
    added = client.post(
        f"/api/instruments/{fund}/identifiers", json={"scheme": "name", "value": "Sample Growth"}
    )
    assert added.status_code == 201
    [ident] = added.json()["identifiers"]
    gone = client.delete(f"/api/instruments/{fund}/identifiers/{ident['id']}")
    assert gone.status_code == 200 and gone.json()["identifiers"] == []
    assert client.delete(f"/api/instruments/{fund}/identifiers/{ident['id']}").status_code == 404


def test_validation_and_not_found(client: TestClient) -> None:
    res = client.post("/api/instruments", json={"name": "", "kind": "bond", "currency": "ZZZ"})
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {"name", "kind", "currency"}
    assert client.get("/api/instruments/999").status_code == 404
    assert client.put("/api/instruments/999", json=_body()).status_code == 404


def test_lookup_resolves_exactly_one_instrument(client: TestClient) -> None:
    etf = client.post(
        "/api/instruments",
        json=_body(
            identifiers=[
                {"scheme": "ticker", "value": "XYZ"},
                {"scheme": "isin", "value": "AU0000SMPL01"},
            ]
        ),
    ).json()["id"]
    client.post(
        "/api/instruments", json=_body("Other", identifiers=[{"scheme": "name", "value": "xyz"}])
    )
    assert (
        client.get("/api/instruments/lookup", params={"value": "au0000smpl01"}).json()["id"] == etf
    )
    assert client.get("/api/instruments/lookup", params={"value": "nope"}).status_code == 404
    ambiguous = client.get("/api/instruments/lookup", params={"value": "XYZ"})
    assert ambiguous.status_code == 422 and ambiguous.json()["errors"][0]["field"] == "scheme"
    pinned = client.get("/api/instruments/lookup", params={"value": "XYZ", "scheme": "ticker"})
    assert pinned.json()["id"] == etf


def test_changes_are_in_the_audit_trail(client: TestClient) -> None:
    i = client.post("/api/instruments", json=_body()).json()["id"]
    client.post(
        f"/api/instruments/{i}/identifiers", json={"scheme": "name", "value": "Sample Alias"}
    )
    trail = client.get("/api/audit", params={"entity_type": "instrument", "entity_id": i}).json()
    summaries = [e["summary"] for e in trail["items"]]
    assert summaries[0].startswith("Added name 'Sample Alias'") and len(summaries) == 2


def test_the_sample_household_has_the_five_invented_instruments(sample: TestClient) -> None:
    listed = sample.get("/api/instruments").json()
    assert {i["name"]: i["kind"] for i in listed} == {
        "Sample Broad Market ETF": "etf",
        "Sample Mining Ltd": "share",
        "Sample Balanced Growth Fund": "fund",
        "Sample Global Equity ETF": "etf",
        "Sample Diversified Fund": "fund",
    }
    schemes = {i["name"]: {x["scheme"] for x in i["identifiers"]} for i in listed}
    assert schemes["Sample Broad Market ETF"] == {"ticker", "yahoo", "isin", "name"}
    assert schemes["Sample Balanced Growth Fund"] == {"vendor_id", "isin", "name"}
    assert schemes["Sample Global Equity ETF"] == {"ticker", "name"}  # AUD-listed, no provider
    found = sample.get("/api/instruments/lookup", params={"value": "sample mining  limited"}).json()
    assert found["name"] == "Sample Mining Ltd"
    dup = sample.post(
        f"/api/instruments/{found['id']}/identifiers", json={"scheme": "ticker", "value": "sbme"}
    )
    assert dup.status_code == 422
