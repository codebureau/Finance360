"""Commit over a real server (uvicorn, a random port) with a real sign-in and a stale session,
against the sample household, plus the concurrency test that matters: the same staged Import
committed by many requests at once writes its Transactions exactly once.

TestClient with an overridden principal hides what this catches: the cookie, the origin check,
commit-before-response, and a session whose ``last_seen_at`` needs refreshing (a write that starts
the request's database transaction before the commit does)."""

from __future__ import annotations

import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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


class Instance:
    def __init__(self, http: httpx.Client, db: Path) -> None:
        self.http = http
        self.db = db

    def scalar(self, sql: str) -> Any:
        with sqlite3.connect(self.db) as conn:
            return conn.execute(sql).fetchone()[0]


@pytest.fixture
def instance(tmp_path: Path) -> Iterator[Instance]:
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
        assert http.post("/api/imports/1/commit").status_code == 401  # nothing without a sign-in
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with sqlite3.connect(data / "finance360.sqlite") as db:  # a session idle for ten minutes
            db.execute("UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')")
        yield Instance(http, data / "finance360.sqlite")
    server.should_exit = True
    thread.join(timeout=10)


def _pi(http: httpx.Client) -> int:
    return next(
        a["id"] for a in http.get("/api/accounts").json() if a["name"] == "Sample Brokerage account"
    )


def _stage(http: httpx.Client, name: str) -> dict[str, Any]:
    response = http.post(
        "/api/imports",
        content=(IMPORTS / name).read_bytes(),
        headers=OCTET,
        params={"account_id": _pi(http), "file_name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _match_snhx(http: httpx.Client, import_id: int) -> None:
    made = http.post(
        f"/api/imports/{import_id}/references/create",
        json={
            "scheme": "ticker",
            "ref": "SNHX",
            "name": "Sample New Horizons ETF",
            "kind": "etf",
            "currency": "AUD",
        },
    )
    assert made.status_code == 200, made.text


def test_the_sample_files_commit_diff_and_supersede_over_a_real_server(instance: Instance) -> None:
    http = instance.http
    trades = _stage(http, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
    assert http.post(f"/api/imports/{trades['id']}/commit").status_code == 422  # SNHX unmatched
    assert instance.scalar('SELECT count(*) FROM "transaction"') == 0

    alone = http.get(f"/api/imports/{trades['id']}/diff").json()
    assert len(alone["warnings"]) == 5  # no cash Stream yet: every trade is unmatched, none blocks
    _match_snhx(http, trades["id"])
    done = http.post(f"/api/imports/{trades['id']}/commit")
    assert done.status_code == 200 and done.json()["status"] == "committed"
    # read back at once: the write was committed before the response was sent
    assert instance.scalar("SELECT count(*) FROM \"transaction\" WHERE stream = 'trade'") == 5
    assert instance.scalar("SELECT count(*) FROM \"transaction\" WHERE stream = 'cash'") == 0

    cash = _stage(http, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
    warned = http.get(f"/api/imports/{cash['id']}/diff").json()["warnings"]
    assert len(warned) == 1 and "Sample New Horizons ETF" in warned[0]["message"]
    assert http.post(f"/api/imports/{cash['id']}/commit").status_code == 200

    later = _stage(http, "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_05-Oct-2026.csv")
    diff = http.get(f"/api/imports/{later['id']}/diff").json()
    assert (diff["added"], diff["removed"], diff["unchanged"]) == (1, 0, 7)
    assert http.post(f"/api/imports/{later['id']}/commit").status_code == 200

    listed = http.get(f"/api/accounts/{_pi(http)}/transactions", params={"stream": "cash"}).json()
    assert (listed["current_cash"], listed["current_trade"]) == (15, 5)
    commits = instance.scalar(
        "SELECT count(*) FROM audit_event WHERE entity_type = 'import' AND actor_kind = 'import'"
    )
    assert commits == 3  # one audit event per commit, not per row
    assert (
        instance.scalar('SELECT count(*) FROM "transaction" WHERE superseded_at IS NOT NULL') == 0
    )


def test_the_same_import_committed_at_once_writes_its_transactions_exactly_once(
    instance: Instance,
) -> None:
    http = instance.http
    imp = _stage(http, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")

    def commit(_: int) -> httpx.Response:
        return http.post(f"/api/imports/{imp['id']}/commit")

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(commit, range(8)))
    codes = sorted(r.status_code for r in responses)
    assert codes == [200] + [422] * 7, [(r.status_code, r.text[:90]) for r in responses]
    assert instance.scalar('SELECT count(*) FROM "transaction"') == 14  # written once, not 8 times
    commits = (
        "SELECT count(*) FROM audit_event WHERE entity_type = 'import' AND actor_kind = 'import'"
    )
    assert instance.scalar(commits) == 1
    assert instance.scalar("SELECT group_concat(status) FROM import") == "committed"


def test_two_overlapping_imports_committed_at_once_leave_one_consistent_result(
    instance: Instance,
) -> None:
    """Different Imports of one Import source and Stream commit in turn, never interleaved: the
    result is the same as committing them one after the other."""
    http = instance.http
    first = _stage(http, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
    later = _stage(http, "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_05-Oct-2026.csv")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda i: http.post(f"/api/imports/{i}/commit"), [first["id"], later["id"]])
        )
    assert [r.status_code for r in results] == [200, 200], [r.text[:90] for r in results]
    current = instance.scalar(
        "SELECT count(*) FROM \"transaction\" WHERE superseded_at IS NULL AND stream = 'cash'"
    )
    # whichever order they ran in, each cash row is current once: 14 + the one new October row
    assert current == 15
