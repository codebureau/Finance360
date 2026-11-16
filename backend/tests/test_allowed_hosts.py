"""Allowed hosts, the proxy peer and the Origin check, against a real server (ADR-0012).

Real uvicorn on a random port and a real sign-in. The proxy's address is injected (a real compose
network is exercised by the local Docker simulation in docs/deploy.md): the test server plays the
app, and "the proxy" is whichever peer address the test says it is.
"""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from finance360.capabilities.auth import hosts
from finance360.config import Settings
from finance360.main import create_app

pytestmark = pytest.mark.usefixtures("fast_hash")

PASSWORD = "synthetic-test-passphrase"
TAILNET = "finance.tailnet.example"
LAN = "192.0.2.10:8843"
ALLOWED = ("localhost", "127.0.0.1", LAN, TAILNET)
LOCALHOST_PEER = frozenset({"127.0.0.1"})
NOT_THE_PROXY: frozenset[str] = frozenset({"172.30.0.9"})

#: As Caddy forwards a request that arrived at the tailnet name (Host is the proxied address).
FORWARDED = {"Host": "proxied.invalid", "X-Forwarded-Host": TAILNET}

Start = Callable[..., tuple[str, Path]]


@pytest.fixture
def start(tmp_path: Path) -> Iterator[Start]:
    servers: list[tuple[uvicorn.Server, threading.Thread]] = []

    def launch(proxy: frozenset[str] = NOT_THE_PROXY, *, secure: bool = False) -> tuple[str, Path]:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        data = tmp_path / f"data-{port}"
        settings = Settings(
            data_dir=data,
            port=port,
            git_sha="dev",
            static_dir=None,
            cookie_secure=secure,
            owner_password=PASSWORD,
            allowed_hosts=ALLOWED,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(settings, proxy_addresses=lambda: proxy),
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.05)
        servers.append((server, thread))
        return f"http://127.0.0.1:{port}", data

    yield launch
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=10)


def _login(url: str, headers: dict[str, str]) -> httpx.Response:
    return httpx.post(f"{url}/api/auth/login", json={"password": PASSWORD}, headers=headers)


# --- unlisted hosts ----------------------------------------------------------------------------


def test_a_listed_host_is_served_and_an_unlisted_one_gets_421(start: Start) -> None:
    url, _ = start()
    assert httpx.get(f"{url}/api/auth/status").status_code == 200  # Host 127.0.0.1:<port>
    assert httpx.get(f"{url}/api/auth/status", headers={"Host": "localhost:1"}).status_code == 200
    assert httpx.get(f"{url}/api/auth/status", headers={"Host": LAN}).status_code == 200
    # a port-qualified entry matches only that port
    assert httpx.get(f"{url}/api/auth/status", headers={"Host": "192.0.2.10:9"}).status_code == 421
    refused = httpx.get(f"{url}/api/auth/status", headers={"Host": "evil.example"})
    assert refused.status_code == 421
    assert refused.json() == {"message": "Host not allowed."}
    # signing in is refused too, before any password is checked
    assert _login(url, {"Host": "evil.example", "Origin": "http://evil.example"}).status_code == 421


def test_health_answers_for_any_host(start: Start) -> None:
    url, _ = start()
    assert httpx.get(f"{url}/health", headers={"Host": "evil.example"}).status_code == 200


# --- X-Forwarded-Host: only from the proxy -----------------------------------------------------


def test_a_forwarded_host_from_a_peer_that_is_not_the_proxy_is_ignored(start: Start) -> None:
    url, _ = start(NOT_THE_PROXY)
    # an unlisted Host cannot be rescued by a listed forwarded name
    assert httpx.get(f"{url}/api/auth/status", headers=FORWARDED).status_code == 421
    # a listed Host cannot be pointed at an unlisted forwarded name either: Host decides
    spoof = {"X-Forwarded-Host": "evil.example"}
    assert httpx.get(f"{url}/api/auth/status", headers=spoof).status_code == 200
    # and a cross-site Origin is not blessed by a forwarded name that matches it
    forged = {"Origin": f"https://{TAILNET}", "X-Forwarded-Host": TAILNET}
    response = _login(url, forged)
    assert response.status_code == 403
    assert response.json() == {"message": "Cross-site request refused."}
    assert not response.cookies


def test_the_forwarded_host_is_honoured_from_the_proxy_and_signs_in(start: Start) -> None:
    url, _ = start(LOCALHOST_PEER)
    headers = {**FORWARDED, "Origin": f"https://{TAILNET}"}
    response = _login(url, headers)
    assert response.status_code == 200, response.text
    assert response.cookies
    # the forwarded name still has to be on the list, and Origin has to match it
    unlisted = {"Host": "proxied.invalid", "X-Forwarded-Host": "evil.example"}
    assert httpx.get(f"{url}/api/auth/status", headers=unlisted).status_code == 421
    evil = {**FORWARDED, "Origin": "https://evil.example"}
    assert _login(url, evil).status_code == 403
    # only the first value counts (a chain of proxies), as Caddy sets it
    chained = {**headers, "X-Forwarded-Host": f"{TAILNET}, evil.example"}
    assert _login(url, chained).status_code == 200


# --- Secure cookie, real sign-in, a stale session ----------------------------------------------


def test_sign_in_through_the_proxy_sets_a_secure_cookie_that_keeps_working(start: Start) -> None:
    url, data = start(LOCALHOST_PEER, secure=True)
    headers = {"Host": "proxied.invalid", "X-Forwarded-Host": LAN, "Origin": f"https://{LAN}"}
    login = _login(url, headers)
    assert login.status_code == 200, login.text
    set_cookie = login.headers["set-cookie"]
    assert "Secure" in set_cookie and "HttpOnly" in set_cookie
    cookie = set_cookie.split(";")[0]
    # the browser would send it over https; replay it by hand (httpx will not over plain http)
    with sqlite3.connect(data / "finance360.sqlite") as db:
        db.execute("UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')")
    after = httpx.get(
        f"{url}/api/auth/status",
        headers={"Host": "proxied.invalid", "X-Forwarded-Host": LAN, "Cookie": cookie},
    )
    assert after.status_code == 200
    assert after.json()["authenticated"] is True


# --- the pure parts ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("LOCALHOST:8360", True),  # a portless entry matches any port
        ("192.0.2.10:8843", True),
        ("192.0.2.10", False),  # a port-qualified entry needs its port
        ("192.0.2.10:80", False),
        ("evil.example", False),
        ("localhost.evil.example", False),
        ("", False),
    ],
)
def test_host_allowed(host: str, expected: bool) -> None:
    assert hosts.host_allowed(host, ("localhost", "192.0.2.10:8843")) is expected


def test_effective_host_uses_the_forwarded_name_only_from_the_proxy() -> None:
    headers = {"host": "proxied.invalid", "x-forwarded-host": f"{TAILNET}, other.example"}
    proxy = lambda: frozenset({"172.30.0.5"})  # noqa: E731
    assert hosts.effective_host(headers, "172.30.0.5", proxy) == TAILNET
    assert hosts.effective_host(headers, "::ffff:172.30.0.5", proxy) == TAILNET
    assert hosts.effective_host(headers, "172.30.0.6", proxy) == "proxied.invalid"
    assert hosts.effective_host(headers, None, proxy) == "proxied.invalid"
    assert hosts.effective_host({"host": "x"}, "172.30.0.5", proxy) == "x"


def test_proxy_peers_resolves_the_service_name_and_caches_briefly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    answers = [[(0, 0, 0, "", ("172.30.0.5", 0))], [(0, 0, 0, "", ("172.30.0.7", 0))]]

    def fake(name: str, port: object) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        calls.append(name)
        return answers[len(calls) - 1]

    monkeypatch.setattr(hosts.socket, "getaddrinfo", fake)
    now = [0.0]
    peers = hosts.ProxyPeers("proxy", ttl=5, clock=lambda: now[0])
    assert peers() == frozenset({"172.30.0.5"})
    assert peers() == frozenset({"172.30.0.5"})
    assert calls == ["proxy"]  # cached
    now[0] = 6.0
    assert peers() == frozenset({"172.30.0.7"})  # a restarted proxy has a new address


def test_proxy_peers_is_empty_when_the_name_does_not_resolve() -> None:
    assert hosts.ProxyPeers("no-such-service.invalid")() == frozenset()
