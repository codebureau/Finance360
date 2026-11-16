"""Auth services against a real, migrated temporary SQLite database (ADR-0010)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pyotp
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select, text, update
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.auth import logins, passwords, sessions, setup_code, totp
from finance360.capabilities.auth import service as auth_service
from finance360.capabilities.auth.events import record_event
from finance360.capabilities.auth.policy import (
    NORMAL_SESSION,
    TRUSTED_CAP,
    TRUSTED_IDLE,
    lock_seconds,
)
from finance360.capabilities.auth.types import AuthAttempt, LockedOut, Principal, Role
from finance360.core.models.auth import AuthEvent, AuthSession, Login
from finance360.plugins.auth import REGISTRY
from finance360.plugins.auth.password import PasswordProvider

PASSWORD = "synthetic-test-passphrase"
T0 = datetime(2026, 1, 5, 9, 0, 0)


@pytest.fixture
def db(session_factory: sessionmaker[Session], fast_hash: None) -> Iterator[Session]:
    with session_factory() as session:
        yield session


def attempt(db: Session, now: datetime, **fields: str | None) -> AuthAttempt:
    return AuthAttempt(
        headers={"user-agent": "pytest"}, client="203.0.113.9", fields=fields, session=db, now=now
    )


def events(db: Session) -> list[str]:
    return list(db.scalars(select(AuthEvent.kind).order_by(AuthEvent.id)))


# --- passwords -------------------------------------------------------------------------------


def test_hashes_use_the_documented_argon2id_parameters() -> None:
    phc = passwords.hash_password(PASSWORD)  # real parameters: this test doesn't use fast_hash
    assert phc.startswith("$argon2id$")
    assert "m=65536,t=3,p=1" in phc
    assert passwords.verify_password(phc, PASSWORD)
    assert not passwords.verify_password(phc, PASSWORD + "x")
    assert not passwords.needs_rehash(phc)


def test_a_garbage_hash_never_verifies() -> None:
    assert not passwords.verify_password("not-a-hash", PASSWORD)


# --- the Login -------------------------------------------------------------------------------


def test_create_login_hashes_the_password_and_allows_only_one(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    assert login.role == "owner"
    assert PASSWORD not in login.password_hash
    assert passwords.verify_password(login.password_hash, PASSWORD)
    with pytest.raises(logins.LoginExists):
        logins.create_login(db, PASSWORD + "2", now=T0)
    assert events(db) == ["setup"]


def test_create_login_enforces_the_password_policy(db: Session) -> None:
    with pytest.raises(logins.PasswordPolicyError):
        logins.create_login(db, "short", now=T0)
    assert logins.get_login(db) is None


def test_seeding_creates_the_login_only_when_none_exists_and_never_overwrites(db: Session) -> None:
    assert logins.seed_owner(db, PASSWORD, now=T0)
    first_hash = logins.get_login(db).password_hash  # type: ignore[union-attr]
    assert not logins.seed_owner(db, "a-completely-different-one", now=T0)
    login = logins.get_login(db)
    assert login is not None
    assert login.password_hash == first_hash
    assert passwords.verify_password(login.password_hash, PASSWORD)


def test_a_seeded_password_goes_through_the_same_policy(db: Session) -> None:
    with pytest.raises(logins.PasswordPolicyError):
        logins.seed_owner(db, "short", now=T0)


# --- Sessions --------------------------------------------------------------------------------


def test_only_the_hash_of_the_session_id_is_stored(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    token = sessions.issue(db, login.id, trusted=False, now=T0)
    row = db.scalars(select(AuthSession)).one()
    assert row.id_hash == sessions.hash_token(token)
    assert token not in row.id_hash
    assert len(token) >= 43  # 256 bits, base64url
    assert len(row.id_hash) == 64


def test_session_ids_are_unique(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    tokens = {sessions.issue(db, login.id, trusted=False, now=T0) for _ in range(20)}
    assert len(tokens) == 20


def test_a_normal_session_lasts_twelve_hours_absolute(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    token = sessions.issue(db, login.id, trusted=False, now=T0)
    assert sessions.resolve(db, token, T0 + NORMAL_SESSION - timedelta(seconds=1)) is not None
    # activity does not extend a normal Session
    assert sessions.resolve(db, token, T0 + NORMAL_SESSION) is None


def test_a_trusted_session_slides_for_90_days_idle_but_not_past_a_year(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    token = sessions.issue(db, login.id, trusted=True, now=T0)
    now = T0
    for _ in range(3):  # stay active every 60 days
        now += timedelta(days=60)
        assert sessions.resolve(db, token, now) is not None
    row = db.scalars(select(AuthSession)).one()
    assert row.expires_at == now + TRUSTED_IDLE
    # keep going: the sliding window is clamped at one year after creation
    for _ in range(4):
        now += timedelta(days=60)
        sessions.resolve(db, token, min(now, T0 + TRUSTED_CAP - timedelta(days=1)))
    assert db.scalars(select(AuthSession)).one().expires_at <= T0 + TRUSTED_CAP
    assert sessions.resolve(db, token, T0 + TRUSTED_CAP) is None
    # and an idle gap longer than 90 days ends it
    other = sessions.issue(db, login.id, trusted=True, now=T0)
    assert sessions.resolve(db, other, T0 + TRUSTED_IDLE) is None


def test_last_seen_updates_at_most_once_a_minute(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    token = sessions.issue(db, login.id, trusted=False, now=T0)
    sessions.resolve(db, token, T0 + timedelta(seconds=59))
    assert db.scalars(select(AuthSession)).one().last_seen_at == T0
    sessions.resolve(db, token, T0 + timedelta(seconds=60))
    assert db.scalars(select(AuthSession)).one().last_seen_at == T0 + timedelta(seconds=60)


def test_unknown_tokens_resolve_to_nothing(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    assert sessions.resolve(db, "nonsense", T0) is None


def test_revoke_deletes_and_revoke_all_can_keep_the_callers_own(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    a, b, c = (sessions.issue(db, login.id, trusted=False, now=T0) for _ in range(3))
    sessions.revoke(db, a)
    assert sessions.resolve(db, a, T0) is None
    assert sessions.revoke_all(db, login.id, except_token=b) == 1
    assert sessions.resolve(db, b, T0) is not None
    assert sessions.resolve(db, c, T0) is None


def test_expired_rows_are_purged_at_login(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    login = logins.get_login(db)
    assert login is not None
    stale = sessions.issue(db, login.id, trusted=False, now=T0)
    later = T0 + timedelta(days=2)
    outcome = auth_service.login(
        db, [PasswordProvider()], attempt(db, later, password=PASSWORD), trust_device=False
    )
    assert outcome.kind == "ok"
    assert db.get(AuthSession, sessions.hash_token(stale)) is None
    assert db.scalars(select(AuthSession)).one() is not None


# --- sign-in through the password provider -----------------------------------------------------


def sign_in(db: Session, now: datetime, **fields: str | None) -> auth_service.LoginOutcome:
    return auth_service.login(
        db, [REGISTRY["password"]], attempt(db, now, **fields), trust_device=False
    )


def test_right_password_signs_in_and_wrong_one_does_not(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    assert sign_in(db, T0, password="not-the-password").kind == "invalid"
    ok = sign_in(db, T0, password=PASSWORD)
    assert ok.kind == "ok"
    assert ok.token
    assert events(db) == ["setup", "login_failed", "login"]


def test_signing_in_with_no_login_is_simply_invalid(db: Session) -> None:
    assert sign_in(db, T0, password=PASSWORD).kind == "invalid"


def test_auth_events_never_contain_secrets(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    sign_in(db, T0, password="wrong-password-guess")
    outcome = sign_in(db, T0, password=PASSWORD)
    blob = " ".join(str(e.detail) for e in db.scalars(select(AuthEvent)))
    for secret in (PASSWORD, "wrong-password-guess", outcome.token or "?"):
        assert secret not in blob
    assert "203.0.113.9" in blob  # the non-sensitive facts are there


def test_five_failures_lock_the_login_even_for_the_right_password(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    for _ in range(5):
        assert sign_in(db, T0, password="wrong-password-guess").kind == "invalid"
    locked = sign_in(db, T0, password=PASSWORD)
    assert (locked.kind, locked.retry_after) == ("locked", 60)
    assert "lockout" in events(db)
    still = sign_in(db, T0 + timedelta(seconds=30), password=PASSWORD)
    assert (still.kind, still.retry_after) == ("locked", 30)
    assert sign_in(db, T0 + timedelta(seconds=60), password=PASSWORD).kind == "ok"


def test_a_further_failure_after_the_lock_doubles_it(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    now = T0
    for _ in range(5):
        sign_in(db, now, password="wrong-password-guess")
    now += timedelta(seconds=60)
    assert sign_in(db, now, password="wrong-password-guess").kind == "invalid"  # failure no. 6
    assert sign_in(db, now, password=PASSWORD).retry_after == lock_seconds(6) == 120


def test_success_resets_the_counter(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    for _ in range(4):
        sign_in(db, T0, password="wrong-password-guess")
    assert sign_in(db, T0, password=PASSWORD).kind == "ok"
    login = logins.get_login(db)
    assert login is not None
    assert (login.failed_count, login.locked_until) == (0, None)
    for _ in range(4):  # four more failures are still below the threshold
        assert sign_in(db, T0, password="wrong-password-guess").kind == "invalid"
    assert sign_in(db, T0, password=PASSWORD).kind == "ok"


def test_the_failure_counter_survives_a_restart(
    session_factory: sessionmaker[Session], fast_hash: None
) -> None:
    with session_factory.begin() as s:
        logins.create_login(s, PASSWORD, now=T0)
    for _ in range(5):
        with session_factory.begin() as s:  # a fresh transaction (and process) each time
            sign_in(s, T0, password="wrong-password-guess")
    with session_factory.begin() as s:
        assert sign_in(s, T0, password=PASSWORD).kind == "locked"


def test_the_provider_raises_lockedout_for_a_locked_login(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    for _ in range(5):
        sign_in(db, T0, password="wrong-password-guess")
    with pytest.raises(LockedOut) as locked:
        PasswordProvider().authenticate(attempt(db, T0, password=PASSWORD))
    assert locked.value.retry_after == 60


def test_a_stale_hash_is_upgraded_on_login(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    weaker = PasswordHasher(memory_cost=512, time_cost=1, parallelism=1)
    login.password_hash = weaker.hash(PASSWORD)
    assert passwords.needs_rehash(login.password_hash)
    assert sign_in(db, T0, password=PASSWORD).kind == "ok"
    assert not passwords.needs_rehash(login.password_hash)
    assert passwords.verify_password(login.password_hash, PASSWORD)


def test_a_provider_never_touches_sessions(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    principal = PasswordProvider().authenticate(attempt(db, T0, password=PASSWORD))
    assert isinstance(principal, Principal)
    assert principal.role is Role.OWNER
    assert db.scalars(select(AuthSession)).all() == []


def test_the_registry_holds_the_password_provider() -> None:
    assert set(REGISTRY) == {"password"}
    assert REGISTRY["password"].key == "password"


# --- TOTP ------------------------------------------------------------------------------------


def enrol(db: Session, now: datetime) -> tuple[Login, str]:
    login = logins.create_login(db, PASSWORD, now=T0)
    secret, uri = logins.totp_begin(db, login, now)
    assert uri.startswith("otpauth://totp/") and secret in uri
    code = pyotp.TOTP(secret).at(totp.step_of(now) * totp.STEP_SECONDS)
    assert logins.totp_confirm(db, login, code, now)
    return login, secret


def code_at(secret: str, now: datetime) -> str:
    return pyotp.TOTP(secret).at(totp.step_of(now) * totp.STEP_SECONDS)


def test_login_is_unchanged_until_totp_enrolment_is_confirmed(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    logins.totp_begin(db, login, T0)
    assert login.totp_secret is None and login.totp_pending_secret
    assert sign_in(db, T0, password=PASSWORD).kind == "ok"


def test_a_wrong_confirmation_code_leaves_the_pending_secret_unpromoted(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    logins.totp_begin(db, login, T0)
    assert not logins.totp_confirm(db, login, "000000", T0)
    assert login.totp_secret is None


def test_once_enrolled_login_needs_the_code_and_a_code_works_once(db: Session) -> None:
    login, secret = enrol(db, T0)
    later = T0 + timedelta(minutes=5)
    assert sign_in(db, later, password=PASSWORD).kind == "invalid"  # no code
    assert sign_in(db, later, password=PASSWORD, totp="000000").kind == "invalid"
    code = code_at(secret, later)
    assert sign_in(db, later, password=PASSWORD, totp=code).kind == "ok"
    assert login.totp_last_step == totp.step_of(later)
    assert sign_in(db, later, password=PASSWORD, totp=code).kind == "invalid"  # replay


def test_a_right_code_with_a_wrong_password_fails_and_counts(db: Session) -> None:
    login, secret = enrol(db, T0)
    later = T0 + timedelta(minutes=5)
    assert sign_in(db, later, password="nope-nope-nope-nope", totp=code_at(secret, later)).kind == (
        "invalid"
    )
    assert login.failed_count == 1


def test_begin_refuses_when_already_enrolled_and_disable_needs_password_and_code(
    db: Session,
) -> None:
    login, secret = enrol(db, T0)
    with pytest.raises(logins.TotpStateError):
        logins.totp_begin(db, login, T0)
    later = T0 + timedelta(minutes=5)
    assert not logins.totp_disable(db, login, "wrong-password-guess", code_at(secret, later), later)
    assert not logins.totp_disable(db, login, PASSWORD, "000000", later)
    assert login.totp_secret == secret
    assert logins.totp_disable(db, login, PASSWORD, code_at(secret, later), later)
    assert login.totp_secret is None and login.totp_last_step is None
    assert sign_in(db, later, password=PASSWORD).kind == "ok"
    assert {"totp_begin", "totp_confirmed", "totp_disabled"} <= set(events(db))


def test_confirm_without_begin_is_a_state_error(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    with pytest.raises(logins.TotpStateError):
        logins.totp_confirm(db, login, "123456", T0)


# --- password change and reset -----------------------------------------------------------------


def test_changing_the_password_revokes_every_other_session(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    mine = sessions.issue(db, login.id, trusted=False, now=T0)
    other = sessions.issue(db, login.id, trusted=True, now=T0)
    logins.change_password(db, login, "another-synthetic-pass", now=T0, keep_token=mine)
    assert sessions.resolve(db, mine, T0) is not None
    assert sessions.resolve(db, other, T0) is None
    assert sign_in(db, T0, password=PASSWORD).kind == "invalid"
    assert sign_in(db, T0, password="another-synthetic-pass").kind == "ok"
    assert "password_changed" in events(db)


def test_a_new_password_must_meet_the_policy(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    with pytest.raises(logins.PasswordPolicyError):
        logins.change_password(db, login, "short", now=T0, keep_token=None)


def test_reset_sets_a_password_clears_totp_and_lock_and_revokes_all(db: Session) -> None:
    login, secret = enrol(db, T0)
    token = sessions.issue(db, login.id, trusted=True, now=T0)
    for _ in range(5):
        sign_in(db, T0, password="wrong-password-guess")
    logins.reset_password(db, login, "a-fresh-synthetic-one", now=T0, detail={"via": "cli"})
    assert (login.totp_secret, login.totp_pending_secret, login.locked_until) == (None, None, None)
    assert login.failed_count == 0
    assert sessions.resolve(db, token, T0) is None
    assert sign_in(db, T0, password="a-fresh-synthetic-one").kind == "ok"
    assert events(db)[-2] == "password_reset"


# --- the setup code ----------------------------------------------------------------------------


def test_the_setup_code_is_written_once_readable_only_by_the_owner(tmp_path: Path) -> None:
    setup_code.ensure(tmp_path)
    code = (tmp_path / "setup-code").read_text().strip()
    assert len(code) >= 20
    setup_code.ensure(tmp_path)  # idempotent: a waiting code is kept
    assert (tmp_path / "setup-code").read_text().strip() == code
    assert setup_code.matches(tmp_path, code)
    assert setup_code.matches(tmp_path, f"  {code}\n")
    assert not setup_code.matches(tmp_path, code + "x")
    assert not setup_code.matches(tmp_path, "")
    setup_code.remove(tmp_path)
    assert not setup_code.matches(tmp_path, code)


# --- the append-only trail ---------------------------------------------------------------------


def test_auth_events_cannot_be_updated_or_deleted(db: Session) -> None:
    logins.create_login(db, PASSWORD, now=T0)
    db.commit()
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute(update(AuthEvent).values(kind="login"))
    db.rollback()
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute(text("DELETE FROM auth_event"))
    db.rollback()
    assert events(db) == ["setup"]


def test_unknown_event_kinds_are_refused(db: Session) -> None:
    with pytest.raises(ValueError):
        record_event(db, "nonsense", now=T0)


def test_a_correct_current_password_resets_the_failure_counter(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    for _ in range(3):
        assert not logins.confirm_password(db, login, "wrong-password-guess", T0)
    assert login.failed_count == 3
    assert logins.confirm_password(db, login, PASSWORD, T0)
    assert (login.failed_count, login.locked_until) == (0, None)


def test_a_correct_confirmation_code_and_disable_reset_the_failure_counter(db: Session) -> None:
    login = logins.create_login(db, PASSWORD, now=T0)
    secret, _ = logins.totp_begin(db, login, T0)
    assert not logins.totp_confirm(db, login, "000000", T0)
    assert login.failed_count == 1
    assert logins.totp_confirm(db, login, code_at(secret, T0), T0)
    assert login.failed_count == 0
    later = T0 + timedelta(minutes=5)
    assert not logins.totp_disable(db, login, "wrong-password-guess", code_at(secret, later), later)
    assert login.failed_count == 1
    assert logins.totp_disable(db, login, PASSWORD, code_at(secret, later), later)
    assert login.failed_count == 0
