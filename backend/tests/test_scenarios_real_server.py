"""Assumption, Scenario and income stream writes over a real server (uvicorn, a random port) with
a real sign-in and a stale session, against the sample household. TestClient with an overridden
principal hides what this catches: the cookie, the origin check, commit-before-response and a
session whose ``last_seen_at`` needs refreshing."""

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
        assert http.get("/api/scenarios").status_code == 401  # nothing without a sign-in
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with sqlite3.connect(data / "finance360.sqlite") as db:  # a session idle for ten minutes
            db.execute("UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')")
        yield http
    server.should_exit = True
    thread.join(timeout=10)


def _investments(http: httpx.Client) -> dict:
    rows = http.get("/api/assumptions").json()["rows"]
    return next(r for r in rows if (r["key"], r["scope_ref"]) == ("return_rate", "investments"))


def test_assumption_writes_work_over_a_real_server_and_are_read_back_at_once(
    signed_in: httpx.Client,
) -> None:
    http = signed_in
    roster = {s["name"]: s["id"] for s in http.get("/api/scenarios").json()}
    assert list(roster)[:3] == ["Best", "Likely", "Worst"]
    before = http.get(f"/api/projection/scenarios/{roster['Likely']}").json()

    saved = http.put(
        "/api/assumptions",
        json={
            "changes": [
                {
                    "key": "return_rate",
                    "scope_type": "category",
                    "scope_ref": "investments",
                    "value": "4.5",
                }
            ]
        },
    )
    assert saved.status_code == 200
    assert saved.json()["review"][0]["scenarios"][:2] == ["Best", "Worst"]
    assert _investments(http)["value"] == "4.5"  # committed before the response returned

    after = http.get(f"/api/projection/scenarios/{roster['Likely']}").json()
    assert after["rows"][-1]["closing"] != before["rows"][-1]["closing"]

    made = http.post(
        "/api/scenarios", json={"name": "Real server copy", "copy_from": roster["Best"]}
    )
    assert made.status_code == 201 and made.json()["override_count"] == 4
    bad = http.put(
        "/api/assumptions",
        json={"changes": [{"key": "inflation", "scope_type": "household", "value": "x"}]},
    )
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "inflation"
    assert http.delete(f"/api/scenarios/{made.json()['id']}").status_code == 204

    trail = http.get("/api/audit", params={"entity_type": "assumption"}).json()
    assert trail["items"][0]["summary"].startswith("Changed Return per year (investments)")


def test_saving_a_run_works_over_a_real_server_and_the_run_is_immutable(
    signed_in: httpx.Client,
) -> None:
    """The new write path: a real sign-in and a stale session, committed before the
    response returns, readable at once, audited, and untouched by a later Assumption change."""
    http = signed_in
    likely = next(s["id"] for s in http.get("/api/scenarios").json() if s["name"] == "Likely")

    saved = http.post("/api/projection/runs", json={"scenario_id": likely, "label": "Real server"})
    assert saved.status_code == 201, saved.text
    run_id = saved.json()["id"]
    assert [r["id"] for r in http.get("/api/projection/runs").json()] == [run_id]  # at once
    before = http.get(f"/api/projection/runs/{run_id}").json()

    changed = http.put(
        "/api/assumptions",
        json={
            "changes": [
                {
                    "key": "return_rate",
                    "scope_type": "category",
                    "scope_ref": "investments",
                    "value": "1.5",
                }
            ]
        },
    )
    assert changed.status_code == 200
    assert http.get(f"/api/projection/runs/{run_id}").json() == before

    trail = http.get("/api/audit", params={"entity_type": "projection_run"}).json()
    assert [e["summary"] for e in trail["items"]] == [
        "Saved a projection run of 'Likely' (Real server)"
    ]


def test_setting_a_drawdown_order_works_over_a_real_server(signed_in: httpx.Client) -> None:
    """The new write path: a real sign-in and a stale session, stored before the
    response returns, read back at once, rejected as a field error, and used by the projection."""
    http = signed_in
    likely = next(s["id"] for s in http.get("/api/scenarios").json() if s["name"] == "Likely")
    order = ["superannuation", "cash", "investments", "overseas_pensions"]

    saved = http.put(f"/api/scenarios/{likely}/drawdown-order", json={"order": order})
    assert saved.status_code == 200, saved.text
    assert [c["key"] for c in saved.json()["drawdown_order"]] == order
    listed = next(s for s in http.get("/api/scenarios").json() if s["id"] == likely)
    assert listed["drawdown_is_default"] is False  # stored before the response returned
    run = http.get(f"/api/projection/scenarios/{likely}").json()
    assert [c["key"] for c in run["categories"]] == order

    bad = http.put(f"/api/scenarios/{likely}/drawdown-order", json={"order": ["cash", "cash"]})
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "drawdown_order"
    trail = http.get("/api/audit", params={"entity_type": "scenario"}).json()["items"]
    assert trail[0]["summary"].startswith("Changed the Drawdown policy of 'Likely'")


def test_setting_spending_phases_works_over_a_real_server(signed_in: httpx.Client) -> None:
    """The new write path: a real sign-in and a stale session, stored before the
    response returns, read back at once, rejected as field errors, and used by the projection."""
    http = signed_in
    roster = {s["name"]: s["id"] for s in http.get("/api/scenarios").json()}
    phases = [
        {"anchor": "start", "offset_years": 0, "annual_amount": "90000"},  # the salaries need it
        {"anchor": "last_retirement", "offset_years": 0, "annual_amount": "60000"},
        {"anchor": "last_retirement", "offset_years": 8, "annual_amount": "45000"},
    ]

    saved = http.put("/api/spending-phases", json={"phases": phases})
    assert saved.status_code == 200, saved.text
    assert [p["annual_amount"] for p in saved.json()["phases"]] == [
        "90000.00",
        "60000.00",
        "45000.00",
    ]
    assert saved.json()["review"][0]["scenarios"] == ["Retire early, spend more"]
    stored = http.get("/api/spending-phases").json()["phases"]  # committed before it returned
    assert [p["offset_years"] for p in stored] == [0, 0, 8]
    run = http.get(f"/api/projection/scenarios/{roster['Likely']}").json()
    assert [p["offset_years"] for p in run["spending"]["phases"]] == [0, 0, 8]

    bad = http.put(
        "/api/spending-phases",
        json={"phases": [{"anchor": "last_retirement", "offset_years": 0, "annual_amount": "0"}]},
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["field"] == "spending_phases.0.annual_amount"
    assert http.get("/api/spending-phases").json()["phases"] == stored  # nothing was written

    trail = http.get("/api/audit", params={"entity_type": "spending_phase"}).json()["items"]
    assert trail[0]["summary"].startswith("Removed a spending phase from Household default")


def test_income_stream_writes_work_over_a_real_server(signed_in: httpx.Client) -> None:
    """The new write path: a real sign-in and a stale session, stored before the
    response returns, read back at once, rejected as field errors, audited, and used by the
    projection."""
    http = signed_in
    likely = next(s["id"] for s in http.get("/api/scenarios").json() if s["name"] == "Likely")
    streams = {s["name"]: s for s in http.get("/api/income-streams").json()}
    assert len(streams) == 2  # the synthetic salaries, loaded at start-up
    jordan = next(s for s in streams.values() if s["owners"][0]["member_name"] == "Jordan Sample")

    made = http.post(
        "/api/income-streams",
        json={
            "name": "Real server side job",
            "owners": [{"member_id": jordan["owners"][0]["member_id"]}],
            "gross_annual": "10000.00",
            "take_home_annual": "9000.00",
        },
    )
    assert made.status_code == 201, made.text
    stream_id = made.json()["id"]
    assert made.json()["end_event"] == "retirement"  # a salary ends at retirement by default
    assert any(s["id"] == stream_id for s in http.get("/api/income-streams").json())  # at once
    year0 = http.get(f"/api/projection/scenarios/{likely}").json()["rows"][0]
    cash = next(c for c in year0["categories"] if c["category_key"] == "cash")
    assert cash["credited"] == "166000.00"  # 91,000 + 66,000 + 9,000 take-home

    edited = http.put(
        f"/api/income-streams/{stream_id}",
        json={**made.json(), "owners": [{"member_id": jordan["owners"][0]["member_id"]}]}
        | {"take_home_annual": "8000.00"},
    )
    assert edited.status_code == 200 and edited.json()["take_home_annual"] == "8000.00"
    bad = http.put(
        f"/api/income-streams/{stream_id}",
        json={**edited.json(), "owners": [{"member_id": 1}, {"member_id": 2}]},
    )
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "owners"
    assert http.get(f"/api/income-streams/{stream_id}").json()["take_home_annual"] == "8000.00"

    assert http.delete(f"/api/income-streams/{stream_id}").status_code == 204
    assert http.get(f"/api/income-streams/{stream_id}").status_code == 404
    trail = http.get("/api/audit", params={"entity_type": "income_stream"}).json()["items"]
    assert [e["action"] for e in trail[:3]] == ["delete", "update", "create"]
