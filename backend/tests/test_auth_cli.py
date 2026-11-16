"""The host-side recovery CLI: ``python -m finance360.auth reset`` (ADR-0010)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import finance360.auth as auth_cli
from finance360.capabilities.auth import logins, passwords, sessions
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import migrate
from finance360.core.models.auth import AuthEvent

pytestmark = pytest.mark.usefixtures("fast_hash")

OLD = "synthetic-old-passphrase"
NEW = "synthetic-new-passphrase"
T0 = datetime(2026, 1, 5, 9, 0, 0)


def seeded(tmp_path: Path) -> sessionmaker[Session]:
    migrate(tmp_path)
    factory = make_session_factory(make_engine(db_path(tmp_path)))
    with factory.begin() as s:
        login = logins.create_login(s, OLD, now=T0)
        login.totp_secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
        sessions.issue(s, login.id, trusted=True, now=T0)
    return factory


def test_reset_sets_the_password_clears_totp_and_revokes_sessions(tmp_path: Path) -> None:
    factory = seeded(tmp_path)
    auth_cli.reset_password(tmp_path, NEW)
    with factory() as s:
        login = logins.get_login(s)
        assert login is not None
        assert passwords.verify_password(login.password_hash, NEW)
        assert login.totp_secret is None
        assert sessions.count_for(s, login.id) == 0
        reset = s.scalars(select(AuthEvent).where(AuthEvent.kind == "password_reset")).one()
        assert reset.detail == {"via": "cli"}
        assert NEW not in str(reset.detail)


def test_reset_refuses_a_weak_password_and_changes_nothing(tmp_path: Path) -> None:
    factory = seeded(tmp_path)
    with pytest.raises(auth_cli.ResetError, match="at least 12"):
        auth_cli.reset_password(tmp_path, "short")
    with factory() as s:
        login = logins.get_login(s)
        assert login is not None
        assert passwords.verify_password(login.password_hash, OLD)


def test_reset_with_no_database_or_no_login_explains(tmp_path: Path) -> None:
    with pytest.raises(auth_cli.ResetError, match="Start the app"):
        auth_cli.reset_password(tmp_path, NEW)
    migrate(tmp_path)
    with pytest.raises(auth_cli.ResetError, match="setup"):
        auth_cli.reset_password(tmp_path, NEW)


def test_main_prompts_twice_and_resets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    factory = seeded(tmp_path)
    monkeypatch.setenv("FINANCE360_DATA_DIR", str(tmp_path))
    answers = iter([NEW, NEW])
    monkeypatch.setattr(auth_cli.getpass, "getpass", lambda _prompt: next(answers))
    assert auth_cli.main(["reset"]) == 0
    assert "Password reset" in capsys.readouterr().out
    with factory() as s:
        login = logins.get_login(s)
        assert login is not None
        assert passwords.verify_password(login.password_hash, NEW)


def test_main_rejects_mismatched_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded(tmp_path)
    monkeypatch.setenv("FINANCE360_DATA_DIR", str(tmp_path))
    answers = iter([NEW, NEW + "x"])
    monkeypatch.setattr(auth_cli.getpass, "getpass", lambda _prompt: next(answers))
    assert auth_cli.main(["reset"]) == 1
    assert "differ" in capsys.readouterr().err
