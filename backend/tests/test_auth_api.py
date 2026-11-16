"""The sign-in flow over HTTP against a real temporary SQLite database (ADR-0010).

These tests sign in for real. Every other API test overrides ``current_principal`` instead
(``support.owner_client``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pyotp
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from finance360.capabilities.auth import totp
from finance360.capabilities.auth.policy import TRUSTED_CAP, utcnow
from finance360.capabilities.auth.types import AuthAttempt, Principal, Role
from finance360.config import Settings
from finance360.core.models.audit import AuditEvent
from finance360.core.models.auth import AuthEvent, AuthSession, Login
from finance360.main import create_app
from finance360.plugins.auth import REGISTRY
from support import ORIGIN, TestClient, as_owner

pytestmark = pytest.mark.usefixtures("fast_hash")

PASSWORD = "synthetic-test-passphrase"
WRONG = "not-the-passphrase-at-all"
NEW = "another-synthetic-passphrase"


def settings_for(data: Path, **overrides: object) -> Settings:
    base = {
        "data_dir": data,
        "port": 8000,
        "git_sha": "dev",
        "static_dir": None,
        "cookie_secure": False,  # TestClient speaks plain http; the Secure default has its own test
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


Make = Callable[..., TestClient]


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    clients: list[TestClient] = []

    def build(*, seed: bool = True, **overrides: object) -> TestClient:
        if seed:
            overrides.setdefault("owner_password", PASSWORD)
        client = TestClient(
            create_app(settings_for(tmp_path / "data", **overrides)), headers=ORIGIN
        )
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in reversed(clients):
        client.__exit__(None, None, None)


def login(client: TestClient, password: str = PASSWORD, **extra: object):  # noqa: ANN201
    return client.post("/api/auth/login", json={"password": password, **extra})


def events(client: TestClient) -> list[str]:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as s:
        return list(s.scalars(select(AuthEvent.kind).order_by(AuthEvent.id)))


# --- default deny ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/household"),
        ("PUT", "/api/household"),
        ("GET", "/api/accounts"),
        ("POST", "/api/accounts"),
        ("GET", "/api/catalogue"),
        ("GET", "/api/auth/security"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/password"),
        ("POST", "/api/auth/totp/begin"),
    ],
)
def test_protected_routes_answer_401_without_a_session(make: Make, method: str, path: str) -> None:
    assert make().request(method, path, json={}).status_code == 401


def test_health_and_status_are_public(make: Make) -> None:
    client = make()
    assert client.get("/health").status_code == 200
    assert client.get("/api/auth/status").json() == {
        "setup_required": False,
        "totp_required": False,
        "authenticated": False,
    }


def test_api_docs_are_not_served(make: Make) -> None:
    client = make()
    assert all(client.get(p).status_code == 404 for p in ("/docs", "/redoc", "/openapi.json"))


def test_a_garbage_cookie_is_401(make: Make) -> None:
    client = make()
    client.cookies.set("f360_session", "forged")
    assert client.get("/api/household").status_code == 401


# --- the cookie ------------------------------------------------------------------------------


def test_login_sets_an_httponly_samesite_strict_cookie_and_it_unlocks_the_api(make: Make) -> None:
    client = make()
    response = login(client)
    assert response.status_code == 200
    assert response.json() == {
        "setup_required": False,
        "totp_required": False,
        "authenticated": True,
    }
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("f360_session=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert "Max-Age" not in cookie  # a normal Session is a browser-session cookie
    assert client.get("/api/household").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_the_cookie_is_secure_by_default(tmp_path: Path) -> None:
    settings = Settings.from_env({"FINANCE360_DATA_DIR": str(tmp_path / "d")})
    assert settings.cookie_secure
    seeded = Settings(**{**settings.__dict__, "owner_password": PASSWORD})
    with TestClient(create_app(seeded), headers=ORIGIN) as client:
        assert "Secure" in login(client).headers["set-cookie"]


def test_trust_device_gives_a_long_lived_cookie_and_session(make: Make) -> None:
    client = make()
    response = login(client, trust_device=True)
    assert f"Max-Age={int(TRUSTED_CAP.total_seconds())}" in response.headers["set-cookie"]
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        row = s.scalars(select(AuthSession)).one()
        assert row.trusted
        assert row.expires_at - row.created_at == timedelta(days=90)


def test_an_expired_session_is_401(make: Make) -> None:
    client = make()
    login(client)
    with client.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        s.scalars(select(AuthSession)).one().expires_at = utcnow() - timedelta(seconds=1)
    assert client.get("/api/household").status_code == 401


# --- credentials and lockout -------------------------------------------------------------------


def test_wrong_credentials_give_a_uniform_401(make: Make) -> None:
    client = make()
    wrong = login(client, WRONG)
    assert (wrong.status_code, wrong.json()) == (401, {"message": "Invalid credentials."})
    assert "set-cookie" not in wrong.headers
    assert events(client) == ["setup", "login_failed"]  # the failure was committed


def test_five_failures_lock_with_429_and_retry_after_even_for_the_right_password(
    make: Make,
) -> None:
    client = make()
    for _ in range(5):
        assert login(client, WRONG).status_code == 401
    locked = login(client)
    assert locked.status_code == 429
    assert 0 < int(locked.headers["retry-after"]) <= 60
    assert "set-cookie" not in locked.headers
    assert {"login_failed", "lockout"} <= set(events(client))


def test_login_before_setup_is_invalid_not_a_crash(make: Make) -> None:
    assert login(make(seed=False)).status_code == 401


# --- logout ----------------------------------------------------------------------------------


def test_logout_ends_the_session_server_side(make: Make) -> None:
    client = make()
    login(client)
    token = client.cookies["f360_session"]
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert (
        'f360_session=""' in response.headers["set-cookie"]
        or "Max-Age=0" in response.headers["set-cookie"]
    )
    client.cookies.set("f360_session", token)  # replaying the old cookie must not work
    assert client.get("/api/household").status_code == 401
    assert events(client)[-1] == "logout"


# --- cross-site protection ---------------------------------------------------------------------


def test_unsafe_requests_from_another_origin_are_refused(make: Make) -> None:
    client = make()
    login(client)
    evil = {"Origin": "http://evil.example"}
    response = client.put("/api/household", json={"name": "x"}, headers=evil)
    assert response.status_code == 403
    assert client.get("/api/household").json()["name"] != "x"


def test_unsafe_requests_without_origin_or_referer_are_refused(make: Make) -> None:
    client = make()
    login(client)
    client.headers.pop("Origin")
    assert login(client).status_code == 403
    assert client.put("/api/household", json={"name": "x"}).status_code == 403
    assert client.get("/api/household").status_code == 200  # safe methods need nothing


def test_referer_stands_in_for_a_missing_origin(make: Make) -> None:
    client = make()
    client.headers.pop("Origin")
    response = client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"Referer": "http://localhost/login"},
    )
    assert response.status_code == 200


# --- first-run setup ---------------------------------------------------------------------------


def waiting_code(client: TestClient) -> str:
    return (client.app.state.settings.data_dir / "setup-code").read_text().strip()  # type: ignore[attr-defined]


def test_a_new_instance_waits_for_setup_and_writes_a_code_file(make: Make) -> None:
    client = make(seed=False)
    assert client.get("/api/auth/status").json()["setup_required"] is True
    assert len(waiting_code(client)) >= 20


def test_the_setup_code_is_never_in_a_response_or_a_log(
    make: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = make(seed=False)
    code = waiting_code(client)
    status = client.get("/api/auth/status")
    bad = client.post(
        "/api/auth/setup", json={"setup_code": "wrong-code-wrong", "password": PASSWORD}
    )
    assert code not in status.text + bad.text
    assert code not in caplog.text


def test_setup_needs_the_code_then_signs_in_and_consumes_it(make: Make) -> None:
    client = make(seed=False)
    code = waiting_code(client)
    wrong = client.post("/api/auth/setup", json={"setup_code": "nope", "password": PASSWORD})
    assert wrong.status_code == 403
    assert client.get("/api/auth/status").json()["setup_required"] is True
    short = client.post("/api/auth/setup", json={"setup_code": code, "password": "short"})
    assert short.status_code == 422
    assert short.json()["errors"][0]["field"] == "password"
    ok = client.post("/api/auth/setup", json={"setup_code": code, "password": PASSWORD})
    assert ok.status_code == 200
    assert ok.json()["authenticated"] is True
    assert client.get("/api/household").status_code == 200  # signed in
    assert not (client.app.state.settings.data_dir / "setup-code").exists()  # type: ignore[attr-defined]
    again = client.post("/api/auth/setup", json={"setup_code": code, "password": NEW})
    assert again.status_code == 409
    assert events(client)[:2] == ["setup", "login"]


def test_a_restart_before_setup_keeps_the_waiting_code(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with TestClient(create_app(settings_for(data))):
        first = (data / "setup-code").read_text()
    with TestClient(create_app(settings_for(data))):
        assert (data / "setup-code").read_text() == first


# --- seeding -----------------------------------------------------------------------------------


def test_a_seeded_instance_needs_no_setup_and_has_no_code(make: Make) -> None:
    client = make()
    assert client.get("/api/auth/status").json()["setup_required"] is False
    assert not (client.app.state.settings.data_dir / "setup-code").exists()  # type: ignore[attr-defined]
    assert login(client).status_code == 200


def test_seeding_never_overwrites_an_existing_login(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with TestClient(create_app(settings_for(data, owner_password=PASSWORD))):
        pass
    with TestClient(create_app(settings_for(data, owner_password=NEW)), headers=ORIGIN) as client:
        assert login(client, NEW).status_code == 401
        assert login(client, PASSWORD).status_code == 200


def test_a_seeded_password_that_breaks_the_policy_stops_startup_without_echoing_it(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="at least 12") as failure:
        with TestClient(create_app(settings_for(tmp_path / "d", owner_password="short"))):
            pass
    assert "short" not in str(failure.value).replace("at least", "")


def test_an_unknown_provider_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FINANCE360_AUTH_PROVIDERS"):
        create_app(settings_for(tmp_path / "d", auth_providers=("password", "nonesuch")))


# --- audit mapping -----------------------------------------------------------------------------


def test_writes_are_audited_as_the_login(make: Make) -> None:
    client = make()
    login(client)
    assert client.put("/api/household", json={"name": "Renamed"}).status_code == 200
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        event = s.scalars(select(AuditEvent).order_by(AuditEvent.id.desc())).first()
        assert event is not None
        assert (event.actor_kind, event.actor_ref, event.source) == ("owner", "login:1", "api")


def test_a_viewer_can_read_but_not_write(make: Make) -> None:
    from finance360.capabilities.auth.types import Principal, Role

    client = make()
    as_owner(client.app, Principal(1, Role.VIEWER))  # type: ignore[arg-type]
    assert client.get("/api/household").status_code == 200
    assert client.put("/api/household", json={"name": "x"}).status_code == 403


# --- security settings: password ---------------------------------------------------------------


def test_changing_the_password_keeps_this_session_and_ends_the_others(make: Make) -> None:
    client = make()
    login(client)
    other = TestClient(client.app, headers=ORIGIN)
    assert login(other).status_code == 200
    bad = client.post("/api/auth/password", json={"current_password": WRONG, "new_password": NEW})
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "current_password"
    weak = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert weak.json()["errors"][0]["field"] == "new_password"
    ok = client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW})
    assert ok.status_code == 204
    assert client.get("/api/household").status_code == 200
    assert other.get("/api/household").status_code == 401
    client.post("/api/auth/logout")
    assert login(client, PASSWORD).status_code == 401
    assert login(client, NEW).status_code == 200
    assert "password_changed" in events(client)


def test_wrong_current_passwords_count_toward_lockout(make: Make) -> None:
    client = make()
    login(client)
    for _ in range(5):
        client.post("/api/auth/password", json={"current_password": WRONG, "new_password": NEW})
    locked = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
    )
    assert locked.status_code == 429


# --- security settings: TOTP -------------------------------------------------------------------


def current_code(secret: str) -> str:
    return pyotp.TOTP(secret).at(totp.step_of(utcnow()) * totp.STEP_SECONDS)


def rewind(client: TestClient) -> None:
    with client.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        s.scalars(select(Login)).one().totp_last_step = totp.step_of(utcnow()) - 2


def test_totp_enrolment_login_replay_and_disable_end_to_end(make: Make) -> None:
    client = make()
    login(client)
    assert client.get("/api/auth/security").json() == {"totp_enabled": False}

    begun = client.post("/api/auth/totp/begin")
    assert begun.status_code == 200
    secret = begun.json()["secret"]
    assert begun.json()["uri"].startswith("otpauth://totp/") and secret in begun.json()["uri"]
    assert client.get("/api/auth/status").json()["totp_required"] is False  # not yet confirmed

    wrong = client.post("/api/auth/totp/confirm", json={"code": "000000"})
    assert wrong.status_code == 422
    assert (
        client.post("/api/auth/totp/confirm", json={"code": current_code(secret)}).status_code
        == 204
    )
    assert client.get("/api/auth/security").json() == {"totp_enabled": True}
    assert client.get("/api/auth/status").json()["totp_required"] is True
    assert client.post("/api/auth/totp/begin").status_code == 409

    # A code is good once, and the confirming code's step is spent. Rewinding the stored step
    # stands in for "a later 30-second window" without sleeping.
    rewind(client)
    client.post("/api/auth/logout")
    assert login(client).status_code == 401  # no code
    code = current_code(secret)
    assert login(client, totp=code).status_code == 200
    client.post("/api/auth/logout")
    assert login(client, totp=code).status_code == 401  # the same code, replayed

    rewind(client)
    assert login(client, totp=code).status_code == 200
    rewind(client)
    no = client.post("/api/auth/totp/disable", json={"password": WRONG, "code": code})
    assert no.status_code == 422
    ok = client.post("/api/auth/totp/disable", json={"password": PASSWORD, "code": code})
    assert ok.status_code == 204
    assert client.get("/api/auth/security").json() == {"totp_enabled": False}
    assert {"totp_begin", "totp_confirmed", "totp_disabled"} <= set(events(client))


def test_each_request_uses_one_database_session(make: Make) -> None:
    """Router-level auth plus the route's own session must share one transaction (SQLite)."""
    client = make()
    login(client)
    opened = 0
    original = client.app.state.session_factory.begin  # type: ignore[attr-defined]

    def counting():  # noqa: ANN202
        nonlocal opened
        opened += 1
        return original()

    client.app.state.session_factory.begin = counting  # type: ignore[attr-defined]
    assert client.put("/api/household", json={"name": "One session"}).status_code == 200
    assert opened == 1


def test_overriding_the_principal_works_on_a_bare_app(tmp_path: Path) -> None:
    app: FastAPI = as_owner(create_app(settings_for(tmp_path / "d")))
    with TestClient(app, headers=ORIGIN) as client:
        assert client.get("/api/household").status_code == 200


# --- the seam: a second provider needs no change to core code ---------------------------------


class StubProvider:
    """A header-free stand-in for a future SSO provider: accepts one fixed secret."""

    key = "stub"

    def authenticate(self, req: AuthAttempt) -> Principal | None:
        if req.fields.get("password") == "stub-provider-secret":
            return Principal(1, Role.OWNER)
        return None


def test_a_stub_provider_signs_in_through_the_seam_and_the_password_route_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(REGISTRY, "stub", StubProvider())
    # A Login row exists (a Session belongs to one) and the seeded password would work if the
    # password provider were active.
    settings = settings_for(tmp_path / "d", auth_providers=("stub",), owner_password=PASSWORD)
    with TestClient(create_app(settings), headers=ORIGIN) as client:
        assert login(client, PASSWORD).status_code == 401  # the password provider is not active
        assert client.get("/api/household").status_code == 401
        ok = login(client, "stub-provider-secret")
        assert ok.status_code == 200
        assert client.get("/api/household").status_code == 200  # Session, guard and audit as usual
        assert client.put("/api/household", json={"name": "Via stub"}).status_code == 200
        with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
            event = s.scalars(select(AuditEvent).order_by(AuditEvent.id.desc())).first()
            assert event is not None and event.actor_ref == "login:1"


# --- caching ----------------------------------------------------------------------------------


def test_api_and_auth_responses_are_never_cached(make: Make) -> None:
    client = make()
    assert login(client).headers["cache-control"] == "no-store"
    assert client.get("/api/household").headers["cache-control"] == "no-store"
    assert client.get("/api/auth/status").headers["cache-control"] == "no-store"
    assert client.post("/api/auth/logout").headers["cache-control"] == "no-store"
    assert client.get("/api/household").headers["cache-control"] == "no-store"  # the 401 too
