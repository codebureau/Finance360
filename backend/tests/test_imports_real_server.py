"""Import writes over a real server (uvicorn, a random port) with a real sign-in and a stale
session, against the sample household, plus concurrency on the two things that are shared
and counted: the same file added at once, and the same Account identifier added at once.

TestClient with an overridden principal hides what this catches: the cookie, the origin check,
commit-before-response, a session whose ``last_seen_at`` needs refreshing, and a raw
``application/octet-stream`` body arriving through a real socket."""

from __future__ import annotations

import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import uvicorn

from finance360.config import Settings
from finance360.main import create_app

REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
PASSWORD = "sample-fixture-passphrase"  # synthetic, seeded into the temporary instance
OCTET = {"Content-Type": "application/octet-stream"}
SECRET = "SAMPLE-PI-0001"
TYPE = "Managed Fund Transaction"


class Instance:
    def __init__(self, http: httpx.Client, db: Path) -> None:
        self.http = http
        self.db = db

    def count(self, table: str) -> int:
        with sqlite3.connect(self.db) as conn:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


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
    with httpx.Client(base_url=url, timeout=20, headers={"Origin": url}) as http:
        assert http.get("/api/imports").status_code == 401  # nothing without a sign-in
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with sqlite3.connect(data / "finance360.sqlite") as db:  # a session idle for ten minutes
            db.execute("UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')")
        yield Instance(http, data / "finance360.sqlite")
    server.should_exit = True
    thread.join(timeout=10)


def _file(name: str) -> bytes:
    return (IMPORTS / name).read_bytes()


def _pi(http: httpx.Client) -> int:
    return next(
        a["id"] for a in http.get("/api/accounts").json() if a["name"] == "Sample Brokerage account"
    )


def test_add_stage_readd_map_and_discard_work_over_a_real_server(instance: Instance) -> None:
    http = instance.http
    pi = _pi(http)

    found = http.post(
        "/api/imports/detect",
        content=_file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
        headers=OCTET,
    )
    assert found.status_code == 200 and [m["id"] for m in found.json()["matches"]] == [pi]
    assert SECRET not in found.text

    staged = http.post(
        "/api/imports",
        content=_file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
        headers=OCTET,
        params={
            "account_id": pi,
            "file_name": "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv",
        },
    )
    assert staged.status_code == 201 and staged.json()["row_count"] == 5
    imp = staged.json()["id"]
    # Read back at once: the write was committed before the response was sent.
    assert http.get(f"/api/imports/{imp}").json()["status"] == "staged"
    assert instance.count("staged_row") == 5

    again = http.post(
        "/api/imports",
        content=_file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
        headers=OCTET,
        params={"account_id": pi},
    )
    assert (again.status_code, again.json()["id"], again.json()["created"]) == (200, imp, False)
    assert (instance.count("import"), instance.count("staged_row")) == (1, 5)

    fund = next(i["id"] for i in http.get("/api/instruments").json() if i["kind"] == "fund")
    mapped = http.post(
        f"/api/imports/{imp}/references/map",
        json={"scheme": "ticker", "ref": "SNHX", "instrument_id": fund},
    )
    assert mapped.status_code == 200 and mapped.json()["commit_blocked"] is False

    discarded = http.post(f"/api/imports/{imp}/discard")
    assert discarded.status_code == 200 and discarded.json()["status"] == "discarded"
    assert http.post("/api/imports/detect", content=b"nope", headers=OCTET).status_code == 422


def test_identifiers_are_masked_over_a_real_server_and_only_reveal_returns_the_value(
    instance: Instance,
) -> None:
    http = instance.http
    pi = _pi(http)
    listed = http.get(f"/api/accounts/{pi}/identifiers")
    assert SECRET not in listed.text and listed.json()[0]["masked_value"].endswith("0001")
    ident = listed.json()[0]["id"]
    assert http.post(f"/api/accounts/{pi}/identifiers/{ident}/reveal").json() == {"value": SECRET}
    added = http.post(f"/api/accounts/{pi}/identifiers", json={"kind": "last4", "value": "4455"})
    assert added.status_code == 201 and "4455" not in added.text
    assert http.delete(f"/api/accounts/{pi}/identifiers/{added.json()['id']}").status_code == 204


def test_the_same_file_added_at_once_makes_exactly_one_import(instance: Instance) -> None:
    http = instance.http
    pi = _pi(http)
    assert instance.count("import_source") == 0  # the first add also races to create it

    def add_file(_: int) -> httpx.Response:
        return http.post(
            "/api/imports",
            content=_file("cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv"),
            headers=OCTET,
            params={
                "account_id": pi,
                "file_name": "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv",
            },
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(add_file, range(8)))
    assert sorted(r.status_code for r in responses) == [200] * 7 + [201], [
        (r.status_code, r.text[:80]) for r in responses
    ]
    assert len({r.json()["id"] for r in responses}) == 1
    assert (instance.count("import"), instance.count("import_source")) == (1, 1)
    assert instance.count("staged_row") == 14  # staged once, not eight times
    imports_audited = instance.count("audit_event WHERE entity_type = 'import'")
    assert imports_audited == 1


def test_the_same_identifier_added_at_once_is_stored_once(instance: Instance) -> None:
    http = instance.http
    everyday = next(
        a["id"] for a in http.get("/api/accounts").json() if a["name"].startswith("Everyday")
    )
    audited_before = instance.count("audit_event WHERE entity_type = 'account_identifier'")

    def add(_: int) -> httpx.Response:
        return http.post(
            f"/api/accounts/{everyday}/identifiers",
            json={
                "kind": "member_number",
                "value": "SAMPLE-MEMBER-77",
                "institution": "Sample Bank",
            },
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(add, range(6)))
    assert sorted(r.status_code for r in responses) == [201] + [422] * 5, [
        (r.status_code, r.text[:80]) for r in responses
    ]
    assert len(http.get(f"/api/accounts/{everyday}/identifiers").json()) == 1
    audited = instance.count("audit_event WHERE entity_type = 'account_identifier'")
    assert audited == audited_before + 1  # one write audited, whatever the fixture already has


def test_mapping_a_new_transaction_type_works_over_a_real_server(instance: Instance) -> None:
    """The new write route: sign-in, origin check and a stale session, then the
    mapping reaches the Import source and unblocks commit."""
    http = instance.http
    member = http.get("/api/household").json()["members"][0]["id"]
    made = http.post(
        "/api/accounts",
        json={
            "name": "Sample Kids investment (Sample Bank)",
            "category_key": "investments",
            "account_type_key": "brokerage_account",
            "ownership": "sole",
            "owners": [{"member_id": member}],
        },
    )
    assert made.status_code == 201, made.text
    name = "investment_transactions_accSAMPLE-KIDS-0001_03-Aug-2026_30-Sep-2026.csv"
    staged = http.post(
        "/api/imports",
        content=_file(name),
        headers=OCTET,
        params={"account_id": made.json()["id"], "file_name": name},
    )
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["commit_blocked"] and body["unmapped_count"] == 3
    assert body["unmapped_types"] == [
        {"raw_type": TYPE, "row_count": 3, "choices": ["buy", "sell", "ignore"]}
    ]
    url = f"/api/imports/{body['id']}"
    assert http.post(f"{url}/commit").status_code == 422
    wrong = http.post(f"{url}/types/map", json={"raw_type": TYPE, "kind": "fee"})
    assert wrong.status_code == 422 and wrong.json()["errors"][0]["field"] == "kind"

    mapped = http.post(f"{url}/types/map", json={"raw_type": TYPE, "kind": "buy"})
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["unmapped_types"] == [] and mapped.json()["unmapped_count"] == 0
    updates = instance.count(
        "audit_event WHERE entity_type = 'import_source' AND action = 'update'"
    )
    assert updates == 1
    assert [r["ref"] for r in mapped.json()["unresolved"]] == ["SKGF"]  # now the references block
    fund = {
        "scheme": "ticker",
        "ref": "SKGF",
        "name": "Sample Kids Growth Fund",
        "kind": "fund",
        "currency": "AUD",
    }
    assert http.post(f"{url}/references/create", json=fund).status_code == 200
    assert http.post(f"{url}/commit").json()["status"] == "committed"
    assert instance.count("\"transaction\" WHERE stream = 'trade'") == 5
