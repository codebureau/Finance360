"""Fetch now over a real server (uvicorn, a random port): a real sign-in, a stale session, the
sample household, and the sample transport (real parser, real inversion, real fetch service, no
network). TestClient with an overridden principal hides what this catches: the cookie, the origin
check, the claim made on the request's own session, commit-before-response and a Session whose
``last_seen_at`` needs refreshing."""

from __future__ import annotations

import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn

from finance360.config import Settings
from finance360.main import create_app

REPO = Path(__file__).resolve().parents[2]
PASSWORD = "sample-fixture-passphrase"  # synthetic, seeded into the temporary instance


@pytest.fixture
def signed_in(tmp_path: Path) -> Iterator[tuple[httpx.Client, Path]]:
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
        price_provider="yfinance",
        fx_provider="rba",
        provider_transport="sample",
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
    with httpx.Client(base_url=url, timeout=30, headers={"Origin": url}) as http:
        assert http.get("/api/fetch/status").status_code == 401  # nothing without a sign-in
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        _wait_until_idle(http)  # the start-up catch-up runs first (nothing has ever succeeded)
        with sqlite3.connect(data / "finance360.sqlite") as db:  # a session idle for ten minutes
            db.execute("UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')")
        yield http, data
    server.should_exit = True
    thread.join(timeout=10)


def _wait_until_idle(http: httpx.Client) -> dict:
    deadline = time.monotonic() + 120
    while True:
        status = http.get("/api/fetch/status").json()
        runs = [d["latest"] for d in status["datasets"]]
        if all(r is not None and r["status"] != "running" for r in runs):
            return status
        assert time.monotonic() < deadline, "the fetch never finished"
        time.sleep(0.1)


def _prices(http: httpx.Client, kind: str) -> list[dict]:
    inst = next(i for i in http.get("/api/instruments").json() if i["kind"] == kind)
    return http.get(f"/api/instruments/{inst['id']}/prices").json()


def test_fetch_now_works_over_a_real_server_with_a_stale_session(
    signed_in: tuple[httpx.Client, Path],
) -> None:
    http, _data = signed_in
    before = http.get("/api/fetch/runs").json()
    assert {r["trigger"] for r in before} == {"catch-up"}  # the app caught up by itself at start

    started = http.post("/api/fetch/run")  # claims on the request's own session
    assert started.status_code == 202, started.text
    assert {r["trigger"] for r in started.json()} == {"manual"}
    status = _wait_until_idle(http)

    prices_run, fx_run = (d["latest"] for d in status["datasets"])
    assert (prices_run["trigger"], prices_run["status"]) == ("manual", "success")
    assert (fx_run["trigger"], fx_run["status"]) == ("manual", "success")
    assert len(http.get("/api/fetch/runs").json()) == len(before) + 2

    # provider Prices are in the Instrument's history, with the symbol in the note
    etf = _prices(http, "etf")
    fetched = [p for p in etf if p["source"] == "yfinance" and p["status"] == "current"]
    assert fetched and all("symbol SBME.AX" in p["provenance_note"] for p in fetched)
    assert max(p["as_at"] for p in fetched) >= (date.today() - timedelta(days=5)).isoformat()
    # a second press changes nothing: idempotent end to end (no new current rows, same ones)
    current_before = {(p["as_at"], p["value"]) for p in etf if p["status"] == "current"}
    assert http.post("/api/fetch/run").status_code == 202
    _wait_until_idle(http)
    current_after = {
        (p["as_at"], p["value"]) for p in _prices(http, "etf") if p["status"] == "current"
    }
    assert current_after == current_before

    # the FX rate arrives with source rba, inverted once from the published figure
    rba = [r for r in http.get("/api/fx-rates").json() if r["source"] == "rba"]
    assert rba and all(r["from_ccy"] == "GBP" and r["to_ccy"] == "AUD" for r in rba)
    assert "A$1 =" in rba[0]["provenance_note"] and "inverted once" in rba[0]["provenance_note"]
    position = http.get("/api/position").json()
    gbp = [
        a
        for c in position["classes"]
        for cat in c["categories"]
        for a in cat["accounts"]
        if a["currency"] == "GBP"
    ]
    assert [a["rate_source"] for a in gbp] == ["rba"]

    # and the manual FX rates the household entered are still there, untouched
    manual = [r for r in http.get("/api/fx-rates").json() if r["source"] == "manual"]
    assert len(manual) == 6


def test_a_failing_instrument_is_an_error_row_and_changes_no_existing_price(
    signed_in: tuple[httpx.Client, Path],
) -> None:
    http, _data = signed_in
    created = http.post(
        "/api/instruments",
        json={
            "name": "Sample Unreachable Fund",
            "kind": "fund",
            "currency": "AUD",
            "identifiers": [{"scheme": "yahoo", "value": "FAILING.AX"}],
        },
    )
    assert created.status_code == 201
    before = {
        i["id"]: http.get(f"/api/instruments/{i['id']}/prices").json()
        for i in http.get("/api/instruments").json()
    }

    assert http.post("/api/fetch/run").status_code == 202
    status = _wait_until_idle(http)

    run = status["datasets"][0]["latest"]
    assert run["status"] == "partial"
    assert run["error"].startswith("1 of 3 Instruments failed. Sample Unreachable Fund:")
    detail = http.get(f"/api/fetch/runs/{run['id']}").json()
    failed = [r for r in detail["results"] if r["outcome"] == "error"]
    assert [(r["target"], r["symbol"]) for r in failed] == [
        ("Sample Unreachable Fund", "FAILING.AX")
    ]
    assert http.get(f"/api/instruments/{created.json()['id']}/prices").json() == []
    for instrument_id, rows in before.items():  # earlier rows are all still there, unchanged
        now = http.get(f"/api/instruments/{instrument_id}/prices").json()
        assert all(row in now for row in rows)
