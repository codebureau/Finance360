"""Lockout holds under concurrency (ADR-0010 "5 failures, then locked").

The failure counter is a read-modify-write, so a naive implementation lets a burst of parallel
wrong guesses all pass the lock check before any of them counts. These tests fire real parallel
requests at a real uvicorn (as test_commit_before_response does) and at the service layer.
"""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.auth import logins, totp
from finance360.capabilities.auth.types import LockedOut
from finance360.config import Settings
from finance360.main import create_app

pytestmark = pytest.mark.usefixtures("fast_hash")

PASSWORD = "synthetic-test-passphrase"
T0 = datetime(2026, 1, 5, 9, 0, 0)
LIMIT = 5


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data = tmp_path / "data"
    settings = Settings(
        data_dir=data,
        port=port,
        git_sha="dev",
        static_dir=None,
        cookie_secure=False,
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
    yield f"http://127.0.0.1:{port}", data
    server.should_exit = True
    thread.join(timeout=10)


@pytest.mark.parametrize("burst", [40, 200])
def test_a_parallel_burst_of_wrong_passwords_is_locked_after_five(
    server: tuple[str, Path], burst: int
) -> None:
    url, data = server
    start = threading.Barrier(burst)

    def guess(_: int) -> int:
        with httpx.Client(base_url=url, timeout=60, headers={"Origin": url}) as http:
            start.wait()
            return http.post("/api/auth/login", json={"password": "wrong-wrong-wrong"}).status_code

    with ThreadPoolExecutor(max_workers=burst) as pool:
        codes = Counter(pool.map(guess, range(burst)))

    assert set(codes) <= {401, 429}, codes
    assert codes[401] <= LIMIT, f"{codes[401]} wrong guesses were verified before the lock: {codes}"
    assert codes[429] == burst - codes[401]
    with sqlite3.connect(data / "finance360.sqlite") as db:
        failed, locked_until = db.execute("SELECT failed_count, locked_until FROM login").fetchone()
    assert failed >= codes[401], "the failure counter went backwards"
    assert locked_until is not None
    # and the right password is refused while locked
    with httpx.Client(base_url=url, headers={"Origin": url}) as http:
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429


# --- the other paths that count failures, at the service layer ---------------------------------


def _hammer(factory: sessionmaker[Session], attempt, n: int = 30) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """Run ``attempt(session)`` from n threads at once; returns (refused-as-wrong, locked)."""
    start = threading.Barrier(n)

    def one(_: int) -> str:
        start.wait()
        try:
            with factory.begin() as s:
                return "ok" if attempt(s) else "wrong"
        except LockedOut:
            return "locked"

    with ThreadPoolExecutor(max_workers=n) as pool:
        outcome = Counter(pool.map(one, range(n)))
    assert set(outcome) <= {"wrong", "locked"}, outcome
    return outcome["wrong"], outcome["locked"]


def _failed_count(factory: sessionmaker[Session]) -> int:
    with factory() as s:
        login = logins.get_login(s)
        assert login is not None and login.locked_until is not None
        return login.failed_count


def test_wrong_current_passwords_in_parallel_lock_after_five(
    session_factory: sessionmaker[Session], fast_hash: None
) -> None:
    with session_factory.begin() as s:
        logins.create_login(s, PASSWORD, now=T0)

    def attempt(s: Session) -> bool:
        login = logins.get_login(s)
        assert login is not None
        return logins.confirm_password(s, login, "wrong-wrong-wrong", datetime.now())

    wrong, locked = _hammer(session_factory, attempt)
    assert wrong <= LIMIT and wrong + locked == 30
    assert _failed_count(session_factory) >= wrong


def test_wrong_totp_codes_in_parallel_lock_after_five(
    session_factory: sessionmaker[Session], fast_hash: None
) -> None:
    with session_factory.begin() as s:
        login = logins.create_login(s, PASSWORD, now=T0)
        logins.totp_begin(s, login, T0)

    def attempt(s: Session) -> bool:
        login = logins.get_login(s)
        assert login is not None
        return logins.totp_confirm(s, login, "000000", datetime.now())

    wrong, locked = _hammer(session_factory, attempt)
    assert wrong <= LIMIT and wrong + locked == 30
    assert _failed_count(session_factory) >= wrong


def test_wrong_disable_attempts_in_parallel_lock_after_five(
    session_factory: sessionmaker[Session], fast_hash: None
) -> None:
    now = datetime.now()
    with session_factory.begin() as s:
        login = logins.create_login(s, PASSWORD, now=now)
        login.totp_secret = totp.new_secret()

    def attempt(s: Session) -> bool:
        login = logins.get_login(s)
        assert login is not None
        return logins.totp_disable(s, login, "wrong-wrong-wrong", "000000", datetime.now())

    wrong, locked = _hammer(session_factory, attempt)
    assert wrong <= LIMIT and wrong + locked == 30
    assert _failed_count(session_factory) >= wrong
