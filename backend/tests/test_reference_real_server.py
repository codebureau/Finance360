"""Reference-data writes over a real server (uvicorn, a random port) with a real sign-in and a
stale session, against the sample household. TestClient with an overridden principal hides what
this catches: the cookie, the origin check, commit-before-response and a session whose
``last_seen_at`` needs refreshing."""

from __future__ import annotations

import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from finance360.config import Settings
from finance360.main import create_app

REPO = Path(__file__).resolve().parents[2]
PASSWORD = "sample-fixture-passphrase"  # synthetic, seeded into the temporary instance


@pytest.fixture
def signed_in(tmp_path: Path) -> Iterator[httpx.Client]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = Settings(
        data_dir=data,
        port=port,
        git_sha="dev",
        static_dir=None,
        cookie_secure=False,  # plain http on 127.0.0.1
        owner_password=PASSWORD,
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=url, timeout=10, headers={"Origin": url}) as http:
        assert http.get("/api/instruments").status_code == 401  # nothing without a sign-in
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with sqlite3.connect(data / "finance360.sqlite") as db:  # a session idle for ten minutes
            db.execute("UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')")
        yield http
    server.should_exit = True
    thread.join(timeout=10)


def _by_kind(http: httpx.Client) -> dict[str, dict]:
    return {i["kind"]: i for i in http.get("/api/instruments").json()}


def test_instrument_writes_work_over_a_real_server_and_are_read_back_at_once(
    signed_in: httpx.Client,
) -> None:
    http = signed_in
    roster = _by_kind(http)
    assert set(roster) == {"etf", "share", "fund"}
    fund = roster["fund"]["id"]

    added = http.post(
        f"/api/instruments/{fund}/identifiers", json={"scheme": "name", "value": "SBG Fund Alias"}
    )
    assert added.status_code == 201
    assert "SBG Fund Alias" in [i["value"] for i in _by_kind(http)["fund"]["identifiers"]]

    clash = http.post(
        f"/api/instruments/{fund}/identifiers", json={"scheme": "ticker", "value": " sbme"}
    )
    assert clash.status_code == 422
    assert clash.json()["errors"][0]["field"] == "value"

    created = http.post(
        "/api/instruments",
        json={
            "name": "Sample Second Fund",
            "kind": "fund",
            "currency": "AUD",
            "identifiers": [{"scheme": "vendor_id", "value": "SSF-0002"}],
        },
    )
    assert created.status_code == 201
    found = http.get("/api/instruments/lookup", params={"value": "ssf-0002"})
    assert found.json()["id"] == created.json()["id"]

    trail = http.get("/api/audit", params={"entity_type": "instrument", "entity_id": fund}).json()
    assert trail["items"][0]["summary"].startswith("Added name 'SBG Fund Alias'")


def test_a_manual_price_over_a_real_server_wins_and_is_read_back_at_once(
    signed_in: httpx.Client,
) -> None:
    http = signed_in
    etf = _by_kind(http)["etf"]["id"]
    before = http.get(f"/api/instruments/{etf}/price", params={"on": "2026-09-01"}).json()
    assert before["source"] == "yfinance"

    added = http.post(
        f"/api/instruments/{etf}/prices",
        json={"as_at": "2026-09-01", "value": "123.456789", "provenance_note": "Checked"},
    )
    assert added.status_code == 201
    after = http.get(f"/api/instruments/{etf}/price", params={"on": "2026-09-01"}).json()
    assert (after["source"], after["value"]) == ("manual", "123.456789")

    wrong_ccy = http.post(
        f"/api/instruments/{etf}/prices",
        json={"as_at": "2026-09-02", "value": "1", "provenance_note": "x", "currency": "USD"},
    )
    assert wrong_ccy.status_code == 422 and wrong_ccy.json()["errors"][0]["field"] == "currency"

    fixed = http.put(
        f"/api/instruments/{etf}/prices/{added.json()['id']}",
        json={"as_at": "2026-09-01", "value": "124", "provenance_note": "Corrected"},
    )
    assert fixed.status_code == 200
    now = http.get(f"/api/instruments/{etf}/price", params={"on": "2026-09-01"}).json()
    assert now["value"] == "124.00"
    none = http.get(f"/api/instruments/{etf}/price", params={"on": "2026-07-01"}).json()
    assert none["result"] == "no_price" and none["value"] is None


def test_an_exposure_profile_over_a_real_server_supersedes_and_is_read_back_at_once(
    signed_in: httpx.Client,
) -> None:
    http = signed_in
    etf = _by_kind(http)["etf"]["id"]
    [fixture] = http.get(f"/api/instruments/{etf}/exposure-profiles").json()
    assert fixture["status"] == "current"

    body = {
        "as_at": fixture["as_at"],
        "provenance_note": "Corrected over a real server",
        "weights": [
            {"dimension_key": "asset_class", "label_key": "australian_shares", "percent": "55"},
            {"dimension_key": "asset_class", "label_key": "international_shares", "percent": "45"},
        ],
    }
    posted = http.post(f"/api/instruments/{etf}/exposure-profiles", json=body)
    assert posted.status_code == 201
    history = http.get(f"/api/instruments/{etf}/exposure-profiles").json()
    assert [p["status"] for p in history] == ["current", "corrected"]  # read back at once

    far_off = {**body, "weights": body["weights"][:1]}  # 55%: 45 points short
    rejected = http.post(f"/api/instruments/{etf}/exposure-profiles", json=far_off)
    assert rejected.status_code == 422
    assert rejected.json()["errors"][0]["field"] == "weights"
