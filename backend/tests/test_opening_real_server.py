"""Recording an Opening position over a real server (uvicorn, a random port) with a real sign-in
and a stale session, against the sample household.

TestClient with an overridden principal hides what this catches: the cookie, the origin check,
commit-before-response, and a session whose ``last_seen_at`` needs refreshing. The walk is the
"How to test" of the PR: stage the sell-only variant, see the warning, record an earlier Opening
position, and see the warning gone."""

from __future__ import annotations

import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from finance360.config import Settings
from finance360.main import create_app

REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
PASSWORD = "sample-fixture-passphrase"  # synthetic, seeded into the temporary instance
OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture
def instance(tmp_path: Path) -> Iterator[tuple[httpx.Client, Path]]:
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
    with httpx.Client(base_url=url, timeout=30, headers={"Origin": url}) as http:
        body: dict[str, Any] = {"kind": "cash", "occurred_on": "2026-08-31", "amount": "1.00"}
        assert http.post("/api/accounts/1/opening-positions", json=body).status_code == 401
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with sqlite3.connect(data / "finance360.sqlite") as db:  # a session idle for ten minutes
            db.execute("UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')")
        yield http, data / "finance360.sqlite"
    server.should_exit = True
    thread.join(timeout=10)


def test_an_opening_position_clears_the_missing_opening_warning_over_a_real_server(
    instance: tuple[httpx.Client, Path],
) -> None:
    http, db = instance
    pi = next(
        a["id"] for a in http.get("/api/accounts").json() if a["name"] == "Sample Brokerage account"
    )
    sbme = next(i["id"] for i in http.get("/api/instruments").json() if "Broad" in i["name"])
    name = "investment_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv"
    staged = http.post(
        "/api/imports",
        content=(IMPORTS / name).read_bytes(),
        headers=OCTET,
        params={"account_id": pi, "file_name": name},
    )
    assert staged.status_code == 201, staged.text
    diff = f"/api/imports/{staged.json()['id']}/diff"
    codes = [w["code"] for w in http.get(diff).json()["warnings"]]
    assert "negative_holding" in codes

    made = http.post(
        f"/api/accounts/{pi}/opening-positions",
        json={
            "kind": "holding",
            "occurred_on": "2026-08-31",
            "instrument_id": sbme,
            "quantity": "50",
        },
    )
    assert made.status_code == 201, made.text
    # read back at once: the write was committed before the response was sent
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            'SELECT kind, stream, import_id, quantity FROM "transaction" WHERE account_id = ?',
            (pi,),
        ).fetchone()
        audited = conn.execute(
            "SELECT count(*) FROM audit_event WHERE entity_type = 'transaction'"
        ).fetchone()[0]
    assert row[:3] == ("opening_balance", "trade", None) and audited == 1
    assert "negative_holding" not in [w["code"] for w in http.get(diff).json()["warnings"]]
    shown = http.get(f"/api/accounts/{pi}/holdings", params={"as_at": "2026-09-01"}).json()
    assert [(h["instrument_name"], h["units"]) for h in shown["holdings"]] == [
        ("Sample Broad Market ETF", "50")
    ]
