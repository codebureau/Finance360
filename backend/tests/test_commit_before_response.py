"""A change is committed before its response is sent (ADR-0009 section 5).

TestClient hides the defect this guards against: it runs the app to completion before returning.
A real server does not, so these tests drive a real uvicorn on a random port and read back
immediately after every write.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from finance360.config import Settings
from finance360.main import create_app

ROUNDS = 50
PASSWORD = "sample-fixture-passphrase"  # synthetic, seeded into the temporary instance


@pytest.fixture
def server_url(tmp_path: Path) -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = Settings(
        data_dir=tmp_path / "data",
        port=port,
        git_sha="dev",
        static_dir=None,
        cookie_secure=False,  # plain http on 127.0.0.1; the Secure default is tested separately
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
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_a_created_member_and_account_are_readable_immediately(server_url: str) -> None:
    with httpx.Client(base_url=server_url, timeout=10) as http:
        http.headers["Origin"] = server_url  # a browser sends this on every unsafe request
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        owner = http.post(
            "/api/household/members", json={"name": "Alex Sample", "date_of_birth": "1984-03-12"}
        ).json()["id"]
        for i in range(ROUNDS):
            member = http.post(
                "/api/household/members",
                json={"name": f"Member {i}", "date_of_birth": "1990-01-01"},
            )
            assert member.status_code == 201
            names = [m["name"] for m in http.get("/api/household").json()["members"]]
            assert f"Member {i}" in names, f"Member {i} missing right after its 201"

            account = http.post(
                "/api/accounts",
                json={
                    "name": f"Account {i}",
                    "category_key": "cash",
                    "account_type_key": "transaction_account",
                    "owners": [{"member_id": owner}],
                },
            )
            assert account.status_code == 201
            assert http.get(f"/api/accounts/{account.json()['id']}").status_code == 200, (
                f"Account {i} not readable right after its 201"
            )
            assert f"Account {i}" in [a["name"] for a in http.get("/api/accounts").json()]


def test_signing_in_over_a_real_server_unlocks_and_logout_locks_again(server_url: str) -> None:
    """login -> cookie -> protected route -> logout, on a real socket (no override)."""
    with httpx.Client(base_url=server_url, timeout=10, headers={"Origin": server_url}) as http:
        assert http.get("/api/household").status_code == 401
        wrong = http.post("/api/auth/login", json={"password": "wrong-wrong-wrong"})
        assert wrong.status_code == 401
        assert http.get("/api/household").status_code == 401

        login = http.post("/api/auth/login", json={"password": PASSWORD})
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert http.get("/api/household").status_code == 200

        assert http.post("/api/auth/logout").status_code == 204
        assert http.get("/api/household").status_code == 401


def test_a_created_valuation_is_readable_immediately_and_a_close_leaves_a_zero(
    server_url: str,
) -> None:
    with httpx.Client(base_url=server_url, timeout=10) as http:
        http.headers["Origin"] = server_url
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        owner = http.post(
            "/api/household/members", json={"name": "Alex Sample", "date_of_birth": "1984-03-12"}
        ).json()["id"]
        account = http.post(
            "/api/accounts",
            json={
                "name": "Everyday",
                "category_key": "cash",
                "account_type_key": "transaction_account",
                "owners": [{"member_id": owner}],
            },
        ).json()["id"]
        for i in range(ROUNDS):
            day = f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}"
            created = http.post(
                f"/api/accounts/{account}/valuations",
                json={
                    "as_at": day,
                    "amount": f"{i}.50",
                    "source": "manual",
                    "provenance_note": "n",
                },
            )
            assert created.status_code == 201
            seen = {v["id"]: v for v in http.get(f"/api/accounts/{account}/valuations").json()}
            assert created.json()["id"] in seen, f"Valuation {i} missing right after its 201"

            corrected = http.put(
                f"/api/valuations/{created.json()['id']}",
                json={
                    "as_at": day,
                    "amount": f"{i}.75",
                    "source": "manual",
                    "provenance_note": "n",
                },
            )
            assert corrected.status_code == 200
            history = http.get(f"/api/accounts/{account}/valuations").json()
            statuses = {v["id"]: v["status"] for v in history}
            assert statuses[corrected.json()["id"]] == "current"
            assert statuses[created.json()["id"]] == "corrected", "correction not visible after 200"

        closed = http.post(
            f"/api/accounts/{account}/close",
            json={"closed_on": "2026-06-30", "confirm_non_zero": True},
        )
        assert closed.status_code == 200
        latest = http.get(f"/api/accounts/{account}/valuations").json()[0]
        assert (latest["as_at"], latest["amount"]) == ("2026-06-30", "0.00")
