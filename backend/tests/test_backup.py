"""Encrypted Backups (ADR-0011).

Two layers. Tests that need only Python (the lock, the status rules, the scheduler's clock, the
CLI exit codes, the manifest) always run. Tests that drive the real restic and rclone use a
local-folder restic repo and a local rclone remote (an ``alias`` to a temp folder) over a synthetic
data dir; they skip when either binary is missing and run in the Docker image in CI-equivalent
form (see the PR notes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
import subprocess
import threading
import time as clock
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from finance360 import backup as backup_cli
from finance360.config import Settings
from finance360.core import backup
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import (
    BACKUP_DIRNAME,
    head_revision,
    migrate,
    prune_pre_migration_copies,
)
from finance360.core.models.backup import BackupRun
from finance360.jobs import scheduler
from finance360.main import create_app
from support import owner_client

#: Obviously fake, and unique enough that finding it anywhere is a leak.
KEY = "synthetic-test-passphrase-NOT-A-REAL-KEY-7Zk3"
SENTINEL_DB = "SENTINEL-PLAINTEXT-IN-THE-DATABASE"
SENTINEL_DOC = "SENTINEL-PLAINTEXT-IN-A-DOCUMENT"

needs_restic = pytest.mark.skipif(
    shutil.which("restic") is None or shutil.which("rclone") is None,
    reason="needs the restic and rclone binaries (they ship in the Docker image)",
)


@pytest.fixture(autouse=True)
def no_scheduler(monkeypatch: pytest.MonkeyPatch) -> list[Settings]:
    """The app's lifespan starts the nightly loop when a key is configured; these tests drive runs
    by hand, so swap it for one that only records that it was started."""
    started: list[Settings] = []

    async def idle(settings: Settings, engine: Engine) -> None:
        started.append(settings)
        await asyncio.Event().wait()

    monkeypatch.setattr("finance360.main.backup_loop", idle)
    return started


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    key_file = tmp_path / "secrets" / "backup-key"
    key_file.parent.mkdir(exist_ok=True)
    key_file.write_text(KEY + "\n", encoding="utf-8")
    base = Settings(
        data_dir=tmp_path / "data",
        port=8000,
        git_sha="abc1234",
        static_dir=None,
        backup_key_file=key_file,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """A synthetic, migrated data dir with a row, a document, a key file and a local repo only."""
    s = _settings(tmp_path)
    migrate(s.data_dir)
    conn = sqlite3.connect(db_path(s.data_dir))
    with conn:
        conn.execute("CREATE TABLE scratch (id INTEGER PRIMARY KEY, grp INTEGER, note TEXT)")
        conn.execute("INSERT INTO scratch (grp, note) VALUES (0, ?)", (SENTINEL_DB,))
    conn.close()
    docs = s.data_dir / "documents" / "ab"
    docs.mkdir(parents=True)
    (docs / "statement.txt").write_text(SENTINEL_DOC, encoding="utf-8")
    return s


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    e = make_engine(db_path(settings.data_dir))
    yield e
    e.dispose()


@pytest.fixture
def remote(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Settings, Path]:
    """Add a local rclone remote (an alias to a folder) as the off-site repo."""
    folder = tmp_path / "offsite"
    folder.mkdir()
    conf = tmp_path / "rclone.conf"
    conf.write_text(f"[offsite]\ntype = alias\nremote = {folder.as_posix()}\n", encoding="utf-8")
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    return replace(settings, backup_remote="rclone:offsite:repo"), folder


def _runs(engine: Engine) -> list[BackupRun]:
    with make_session_factory(engine)() as session:
        return list(session.scalars(select(BackupRun).order_by(BackupRun.id)))


def _restic(
    settings: Settings, repo: str, *args: str, key_file: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = backup.restic_env(settings)
    if key_file is not None:
        env["RESTIC_PASSWORD_FILE"] = str(key_file)
    return subprocess.run(  # noqa: S603
        ["restic", "-r", repo, *args],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _snapshots(settings: Settings, repo: str, tag: str | None = None) -> list[dict[str, object]]:
    extra = ["--tag", tag] if tag else []
    done = _restic(settings, repo, "snapshots", "--json", *extra)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _local(settings: Settings) -> str:
    return str(backup.local_repo(settings.data_dir))


def _bytes_under(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file())


# --- always-on: the lock, status, clock, CLI -------------------------------------------------


def test_only_one_run_holds_the_lock(engine: Engine) -> None:
    first = backup.claim(engine, "manual")
    with pytest.raises(backup.BackupBusy):
        backup.claim(engine, "nightly")
    backup._finish(engine, first, status="success")
    backup.claim(engine, "nightly")  # free again once the first finished


def test_a_crashed_run_does_not_hold_the_lock_forever(engine: Engine) -> None:
    long_ago = (
        datetime.now(UTC).replace(tzinfo=None) - backup.RUNNING_TIMEOUT - timedelta(minutes=1)
    )
    stale = backup.claim(engine, "nightly", now=long_ago)
    fresh = backup.claim(engine, "manual")
    runs = {r.id: r for r in _runs(engine)}
    assert runs[stale].status == "failed" and "Interrupted" in (runs[stale].error or "")
    assert runs[fresh].status == "running"


def test_an_app_restart_closes_an_open_run(engine: Engine) -> None:
    run_id = backup.claim(engine, "manual")
    with make_session_factory(engine).begin() as session:
        backup.fail_interrupted(session)
    (run,) = _runs(engine)
    assert run.id == run_id and run.status == "failed" and run.finished_at is not None


def test_sanitise_blanks_secrets_and_is_short() -> None:
    text = backup.sanitise(f"boom {KEY}\n\n  with   noise " + "x" * 1000, [KEY])
    assert KEY not in text and "[redacted]" in text and "\n" not in text
    assert len(text) <= 300


def test_no_key_file_means_not_configured(tmp_path: Path) -> None:
    s = replace(_settings(tmp_path), backup_key_file=None)
    with pytest.raises(backup.BackupNotConfigured):
        backup.run_backup("manual", s)
    assert not backup.status(_empty_session(tmp_path), s).configured


def _empty_session(tmp_path: Path) -> Session:
    migrate(tmp_path / "empty")
    return make_session_factory(make_engine(db_path(tmp_path / "empty")))()


def test_a_missing_key_file_is_a_recorded_failure_not_a_crash(
    settings: Settings, engine: Engine
) -> None:
    broken = replace(settings, backup_key_file=settings.data_dir / "no-such-key")
    result = backup.run_backup("manual", broken, engine)
    assert not result.ok and "key file" in (result.error or "")
    (run,) = _runs(engine)
    assert run.status == "failed" and run.finished_at is not None
    assert not (settings.data_dir / BACKUP_DIRNAME / "staging").exists()


def test_status_goes_stale_after_36_hours(settings: Settings, engine: Engine) -> None:
    now = datetime(2026, 3, 10, 12, 0)
    with make_session_factory(engine).begin() as session:
        assert backup.status(session, settings, now).stale  # set up, never succeeded
        session.add(
            BackupRun(
                reason="nightly",
                started_at=now - timedelta(hours=35),
                finished_at=now - timedelta(hours=35),
                status="success",
                shipped=False,
            )
        )
    with make_session_factory(engine)() as session:
        assert not backup.status(session, settings, now).stale
        assert backup.status(session, settings, now + timedelta(hours=2)).stale


def test_maintenance_is_kept_apart_from_backups_in_the_status(
    settings: Settings, engine: Engine
) -> None:
    now = datetime(2026, 3, 10, 12, 0)
    with make_session_factory(engine).begin() as session:
        for reason, status in (("nightly", "success"), ("maintenance", "failed")):
            session.add(
                BackupRun(
                    reason=reason, started_at=now, finished_at=now, status=status, shipped=False
                )
            )
    with make_session_factory(engine)() as session:
        state = backup.status(session, settings, now)
        assert state.latest is not None and state.latest.reason == "nightly"
        assert state.maintenance is not None and state.maintenance.status == "failed"
        assert backup.due_reason(session, now) == "maintenance"  # backup fresh, prune never done
        assert backup.due_reason(session, now + timedelta(hours=25)) == "nightly"  # catch up first


def test_pre_migration_copies_are_capped_at_the_latest_five(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    names = [f"finance360-2026010{i}T000000000000Z-0001.sqlite" for i in range(1, 9)]
    for name in names:
        (folder / name).write_bytes(b"x")
    (folder / "unrelated.txt").write_text("keep")
    prune_pre_migration_copies(folder)
    assert sorted(p.name for p in folder.glob("finance360-*.sqlite")) == names[3:]
    assert (folder / "unrelated.txt").exists()


def test_settings_read_the_backup_environment(tmp_path: Path) -> None:
    s = Settings.from_env(
        {
            "FINANCE360_DATA_DIR": str(tmp_path),
            "FINANCE360_BACKUP_KEY_FILE": "/run/secrets/k",
            "FINANCE360_BACKUP_TIME": "03:30",
            "FINANCE360_BACKUP_RCLONE_REMOTE": " rclone:onedrive:Finance360 ",
        }
    )
    assert s.backup_key_file == Path("/run/secrets/k")
    assert s.backup_time == time(3, 30)
    assert s.backup_remote == "rclone:onedrive:Finance360"
    default = Settings.from_env({"FINANCE360_DATA_DIR": str(tmp_path)})
    assert default.backup_key_file is None and default.backup_time == time(2, 0)
    with pytest.raises(ValueError, match="HH:MM"):
        Settings.from_env({"FINANCE360_BACKUP_TIME": "teatime"})


def test_the_manifest_has_what_restore_needs_and_no_secret(settings: Settings) -> None:
    now = datetime(2026, 3, 10, 12, 0)
    paths = backup._stage(settings, "pre-deploy", now)
    manifest = json.loads((backup._staging(settings.data_dir) / "manifest.json").read_text())
    assert manifest["schema_revision"] == head_revision()
    assert manifest["app_version"] == "abc1234"
    assert manifest["created_at"] == "2026-03-10T12:00:00Z"
    assert manifest["reason"] == "pre-deploy"
    assert manifest["contents"] == ["finance360.sqlite", "manifest.json", "documents/"]
    assert [p.name for p in paths] == ["finance360.sqlite", "manifest.json", "documents"]
    text = json.dumps(manifest)
    assert KEY not in text and str(settings.backup_key_file) not in text
    # The staged DB copy is one self-contained file, readable on its own.
    staged = sqlite3.connect(backup._staging(settings.data_dir) / "finance360.sqlite")
    assert staged.execute("SELECT note FROM scratch").fetchone() == (SENTINEL_DB,)
    staged.close()


def test_the_cli_reports_each_outcome_in_its_exit_code(
    settings: Settings,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake(result: backup.BackupResult | Exception) -> None:
        def run(reason: str) -> backup.BackupResult:
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(backup, "run_backup", run)

    ok = backup.BackupResult(1, "success", "deadbeef", True, None)
    fake(ok)
    assert backup_cli.main(["run", "--reason", "pre-deploy"]) == 0
    assert "pre-deploy succeeded" in capsys.readouterr().out
    fake(backup.BackupResult(1, "failed", None, False, "boom"))
    assert backup_cli.main(["run", "--reason", "pre-deploy"]) == 1
    fake(backup.BackupNotConfigured("not set up"))
    assert backup_cli.main(["run", "--reason", "pre-deploy"]) == 2
    fake(backup.BackupBusy("busy"))
    assert backup_cli.main(["run", "--reason", "pre-deploy"]) == 3


# --- always-on: the scheduler's clock --------------------------------------------------------


def test_next_run_is_the_next_local_wall_time() -> None:
    melbourne = ZoneInfo("Australia/Melbourne")
    at = time(2, 0)
    # 2026-07-01 15:00 UTC is 01:00 Melbourne (AEST, +10): 02:00 is an hour away.
    after = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
    assert scheduler.next_run_at(after, at, melbourne) == datetime(2026, 7, 1, 16, 0, tzinfo=UTC)
    # Just past 02:00 local: tomorrow's.
    after = datetime(2026, 7, 1, 16, 0, 1, tzinfo=UTC)
    assert scheduler.next_run_at(after, at, melbourne) == datetime(2026, 7, 2, 16, 0, tzinfo=UTC)
    # The same wall time in daylight saving (+11) is an hour earlier in UTC.
    after = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert scheduler.next_run_at(after, at, melbourne) == datetime(2026, 1, 1, 15, 0, tzinfo=UTC)


def test_a_wall_time_skipped_by_daylight_saving_still_runs_once() -> None:
    melbourne = ZoneInfo("Australia/Melbourne")
    # 2026-10-04 02:00 does not exist in Melbourne (clocks go 02:00 -> 03:00).
    after = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    run = scheduler.next_run_at(after, time(2, 30), melbourne)
    assert run > after and run.astimezone(melbourne).date().isoformat() == "2026-10-04"


def test_the_loop_survives_a_crashing_run(
    settings: Settings,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    def boom(reason: str, *_: object) -> backup.BackupResult:
        calls.append(reason)
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(backup, "run_backup", boom)
    now = datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
    ticks = 0

    async def sleep(seconds: float) -> None:
        nonlocal now, ticks
        ticks += 1
        if ticks > 150:  # a day and a half of ticks: one nightly wake-up has passed
            raise asyncio.CancelledError
        now += timedelta(seconds=seconds)

    with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.backup_loop(settings, engine, clock=lambda: now, sleep=sleep))
    # Catch-up at start (nothing ever succeeded), then at least one nightly: the loop kept going.
    assert calls[0] == "nightly" and len(calls) >= 2
    assert any("will be retried" in r.message for r in caplog.records)
    assert KEY not in caplog.text


# --- always-on: the API ----------------------------------------------------------------------


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


def test_an_unconfigured_instance_says_so_and_cannot_start_a_backup(tmp_path: Path) -> None:
    s = replace(_settings(tmp_path), backup_key_file=None)
    with owner_client(create_app(s)) as client:
        status = client.get("/api/backup/status").json()
        assert status["configured"] is False and status["stale"] is False
        assert status["latest"] is None
        refused = client.post("/api/backup/run")
        assert refused.status_code == 409 and "not set up" in refused.json()["message"]


def test_a_failed_backup_shows_in_the_status_and_the_app_carries_on(app: FastAPI) -> None:
    app.state.settings = replace(
        app.state.settings, backup_key_file=app.state.settings.data_dir / "missing-key"
    )
    with owner_client(app) as client:
        started = client.post("/api/backup/run")
        assert started.status_code == 202 and started.json()["status"] == "running"
        status = client.get("/api/backup/status").json()
        assert status["latest"]["status"] == "failed"
        assert "key file" in status["latest"]["error"]
        assert status["last_success"] is None and status["stale"] is True
        assert client.get("/health").json()["status"] == "healthy"


def test_only_the_owner_may_see_or_start_backups(app: FastAPI) -> None:
    from finance360.capabilities.auth.types import Principal, Role
    from support import as_owner

    with owner_client(app) as client:
        as_owner(app, Principal(login_id=2, role=Role.VIEWER))
        assert client.get("/api/backup/status").status_code == 403
        assert client.post("/api/backup/run").status_code == 403


def test_the_app_starts_the_nightly_loop_only_when_backups_are_set_up(
    settings: Settings, tmp_path: Path, no_scheduler: list[Settings]
) -> None:
    with owner_client(create_app(replace(settings, backup_key_file=None))):
        assert no_scheduler == []
    with owner_client(create_app(settings)):
        assert no_scheduler == [settings]


def test_run_now_works_after_the_session_was_idle_over_a_real_server(
    settings: Settings, tmp_path: Path
) -> None:
    """Regression: auth touches ``last_seen_at`` (at most once a minute) and holds SQLite's write
    lock in the request session; claiming on a second connection then hung 5 s and gave a 500.
    Real login, real uvicorn, the real auth dependency (no override)."""
    import socket

    import httpx
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    password = "synthetic-owner-pass-1"
    live = replace(settings, port=port, cookie_secure=False, owner_password=password)
    server = uvicorn.Server(
        uvicorn.Config(create_app(live), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = clock.monotonic() + 20
    while not server.started:
        assert clock.monotonic() < deadline, "server did not start"
        clock.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=url, timeout=30, headers={"Origin": url}) as http:
            login = http.post("/api/auth/login", json={"password": password})
            assert login.status_code == 200
            assert http.get("/api/backup/status").status_code == 200
            # The Session has been idle for ten minutes: the next request will touch it.
            conn = sqlite3.connect(db_path(live.data_dir))
            with conn:
                conn.execute(
                    "UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')"
                )
            conn.close()
            started = clock.monotonic()
            response = http.post("/api/backup/run")
            elapsed = clock.monotonic() - started
        assert response.status_code == 202, response.text
        assert elapsed < 3, f"took {elapsed:.1f}s: the claim waited on a lock"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_a_locked_database_during_claim_is_busy_not_a_crash(engine: Engine, tmp_path: Path) -> None:
    holder = sqlite3.connect(db_path(tmp_path / "data"), timeout=0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # a writer holds the lock
    try:
        with make_session_factory(engine)() as session:
            session.connection().exec_driver_sql("PRAGMA busy_timeout=100")
            with pytest.raises(backup.BackupBusy):
                backup.claim_in(session, "manual")
    finally:
        holder.rollback()
        holder.close()


def test_a_second_run_is_refused_while_one_is_live(app: FastAPI) -> None:
    with owner_client(app) as client:
        backup.claim(app.state.engine, "nightly")
        refused = client.post("/api/backup/run")
        assert refused.status_code == 409 and "already running" in refused.json()["message"]


# --- real restic and rclone ------------------------------------------------------------------


@pytest.mark.platform
@needs_restic
def test_a_backup_is_encrypted_and_shipped_to_the_remote(
    settings: Settings,
    engine: Engine,
    remote: tuple[Settings, Path],
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    s, offsite = remote
    with caplog.at_level(logging.DEBUG):
        result = backup.run_backup("manual", s, engine)
    assert result.ok and result.shipped and result.snapshot_id
    (run,) = _runs(engine)
    assert run.status == "success" and run.shipped and (run.size_bytes or 0) > 0
    assert run.snapshot_id == result.snapshot_id and run.error is None

    # Both repos hold the snapshot, under the fixed host and the reason tag.
    for repo in (_local(s), s.backup_remote or ""):
        (snap,) = _snapshots(s, repo)
        assert snap["hostname"] == "finance360" and snap["tags"] == ["manual"]
    assert (offsite / "repo" / "config").is_file()  # the remote is a real restic repo

    # Plaintext data, and the key itself, appear nowhere in either repo as stored.
    for stored in (_bytes_under(backup.local_repo(s.data_dir)), _bytes_under(offsite)):
        assert SENTINEL_DB.encode() not in stored and SENTINEL_DOC.encode() not in stored
        assert KEY.encode() not in stored
    # The staging copy of the DB is gone once the run ends.
    assert not (s.data_dir / BACKUP_DIRNAME / "staging").exists()
    # No key in the logs, the process output or the recorded history.
    seen = caplog.text + "".join(capfd.readouterr())
    assert KEY not in seen


@pytest.mark.platform
@needs_restic
def test_the_backup_cannot_be_read_without_the_key(
    settings: Settings, engine: Engine, remote: tuple[Settings, Path], tmp_path: Path
) -> None:
    s, _ = remote
    assert backup.run_backup("manual", s, engine).ok
    wrong = tmp_path / "wrong-key"
    wrong.write_text("a-different-passphrase-entirely-0000\n", encoding="utf-8")
    for repo in (_local(s), s.backup_remote or ""):
        refused = _restic(s, repo, "snapshots", key_file=wrong)
        assert refused.returncode != 0
        assert "wrong password" in refused.stderr.lower() or "no key" in refused.stderr.lower()
        assert _restic(s, repo, "snapshots").returncode == 0  # the right key reads it


@pytest.mark.platform
@needs_restic
def test_the_snapshot_restores_to_a_consistent_database_with_documents_and_manifest(
    settings: Settings, engine: Engine, tmp_path: Path
) -> None:
    assert backup.run_backup("pre-deploy", settings, engine).ok
    target = tmp_path / "restored"
    done = _restic(settings, _local(settings), "restore", "latest", "--target", str(target))
    assert done.returncode == 0, done.stderr
    staged = target / str(backup._staging(settings.data_dir).relative_to(settings.data_dir.anchor))
    manifest = json.loads((staged / "manifest.json").read_text())
    assert manifest["schema_revision"] == head_revision()
    assert manifest["reason"] == "pre-deploy"
    assert KEY not in json.dumps(manifest)
    restored = sqlite3.connect(staged / "finance360.sqlite")
    assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert restored.execute("SELECT note FROM scratch").fetchone() == (SENTINEL_DB,)
    restored.close()
    docs = target / str((settings.data_dir / "documents").relative_to(settings.data_dir.anchor))
    assert (docs / "ab" / "statement.txt").read_text() == SENTINEL_DOC
    # The allowlist: no backups/ recursion, no key file, no setup code.
    names = {p.name for p in target.rglob("*") if p.is_file()}
    assert names == {"finance360.sqlite", "manifest.json", "statement.txt"}


@pytest.mark.platform
@needs_restic
def test_a_backup_taken_during_writes_is_consistent(
    settings: Settings, engine: Engine, tmp_path: Path
) -> None:
    """Each writer transaction inserts two rows of one group; a torn copy would split a group."""
    stop = threading.Event()
    written = 0

    def writer() -> None:
        nonlocal written
        conn = sqlite3.connect(db_path(settings.data_dir), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        group = 1
        while not stop.is_set():
            with conn:
                conn.execute("INSERT INTO scratch (grp, note) VALUES (?, 'a')", (group,))
                conn.execute("INSERT INTO scratch (grp, note) VALUES (?, 'b')", (group,))
            written = group  # groups fully committed so far
            group += 1
            clock.sleep(0.002)  # a busy writer, not a starving one
        conn.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        while written < 20:
            pass
        results = [backup.run_backup("manual", settings, engine) for _ in range(2)]
    finally:
        stop.set()
        thread.join()
    assert all(r.ok for r in results)
    for snap in _snapshots(settings, _local(settings)):
        target = tmp_path / f"r-{snap['short_id']}"
        done = _restic(
            settings, _local(settings), "restore", str(snap["id"]), "--target", str(target)
        )
        assert done.returncode == 0, done.stderr
        db = next(target.rglob("finance360.sqlite"))
        conn = sqlite3.connect(db)
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        sizes = conn.execute("SELECT count(*) FROM scratch WHERE grp > 0 GROUP BY grp").fetchall()
        assert len(sizes) >= 20 and all(n == 2 for (n,) in sizes)
        conn.close()


@pytest.mark.platform
@needs_restic
def test_retention_keeps_the_latest_five_manual_snapshots(
    settings: Settings, engine: Engine
) -> None:
    assert backup.KEEP_LATEST_TAGGED == 5  # ADR-0011, resolved question 3
    for _ in range(6):
        assert backup.run_backup("manual", settings, engine).ok
    assert len(_snapshots(settings, _local(settings), "manual")) == 5


@pytest.mark.platform
@needs_restic
def test_nightly_retention_keeps_a_rolling_window_not_everything(
    settings: Settings, tmp_path: Path
) -> None:
    """Twenty nightly snapshots, one per day, back-dated: the newest 14 days all survive and the
    older ones are thinned to the weekly/monthly keepers."""
    repo = _local(settings)
    (tmp_path / "one.txt").write_text("synthetic")
    (settings.data_dir / "backups" / "cache").mkdir(parents=True, exist_ok=True)
    assert _restic(settings, repo, "init").returncode == 0
    newest = datetime(2026, 3, 20, 2, 0)
    for age in range(20):
        when = (newest - timedelta(days=age)).strftime("%Y-%m-%d %H:%M:%S")
        done = _restic(
            settings, repo, "backup", "--host", backup.HOST, "--tag", "nightly",
            "--time", when, str(tmp_path / "one.txt"),
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
    backup.apply_retention(repo, backup.restic_env(settings))
    kept = {str(snap["time"])[:10] for snap in _snapshots(settings, repo, "nightly")}
    assert {(newest - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14)} <= kept
    assert 14 < len(kept) < 20


@pytest.mark.platform
@needs_restic
def test_retention_caps_pre_deploy_apart_from_manual_on_both_repos(
    settings: Settings,
    engine: Engine,
    remote: tuple[Settings, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same rule with a smaller cap, to keep the test quick: each tag is trimmed on its own."""
    s, _ = remote
    monkeypatch.setattr(backup, "KEEP_LATEST_TAGGED", 2)
    for reason in ("pre-deploy", "manual", "pre-deploy", "pre-deploy", "manual"):
        assert backup.run_backup(reason, s, engine).ok
    for repo in (_local(s), s.backup_remote or ""):
        assert len(_snapshots(s, repo, "pre-deploy")) == 2
        assert len(_snapshots(s, repo, "manual")) == 2


@pytest.mark.platform
@needs_restic
def test_weekly_maintenance_prunes_and_checks_both_repos(
    settings: Settings, engine: Engine, remote: tuple[Settings, Path]
) -> None:
    s, _ = remote
    assert backup.run_backup("nightly", s, engine).ok
    result = backup.run_backup("maintenance", s, engine)
    assert result.ok and result.snapshot_id is None
    with make_session_factory(engine)() as session:
        state = backup.status(session, s)
        assert state.maintenance is not None and state.maintenance.status == "success"
        assert state.latest is not None and state.latest.reason == "nightly"
        assert backup.due_reason(session) is None  # fresh backup, fresh maintenance


@pytest.mark.platform
@needs_restic
def test_an_unreachable_remote_fails_visibly_but_keeps_the_local_snapshot(
    settings: Settings, engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_conf = tmp_path / "empty.conf"
    empty_conf.write_text("", encoding="utf-8")
    monkeypatch.setenv("RCLONE_CONFIG", str(empty_conf))
    s = replace(settings, backup_remote="rclone:gone:repo")
    result = backup.run_backup("nightly", s, engine)
    assert not result.ok and not result.shipped and result.snapshot_id  # local snapshot exists
    assert result.error and KEY not in result.error and len(result.error) <= 300
    assert len(_snapshots(s, _local(s))) == 1
    app = create_app(s)
    with owner_client(app) as client:
        latest = client.get("/api/backup/status").json()["latest"]
        assert latest["status"] == "failed" and latest["shipped"] is False
        assert client.get("/health").json()["status"] == "healthy"


@pytest.mark.platform
@needs_restic
def test_a_wrong_key_for_an_existing_repo_is_a_clean_failure(
    settings: Settings, engine: Engine, tmp_path: Path
) -> None:
    assert backup.run_backup("manual", settings, engine).ok
    wrong = tmp_path / "wrong-key"
    wrong.write_text("a-different-passphrase-entirely-0000\n", encoding="utf-8")
    result = backup.run_backup("manual", replace(settings, backup_key_file=wrong), engine)
    assert not result.ok and result.error
    assert "wrong password" in result.error.lower()  # the real cause, not "config already exists"
    assert "already exists" not in result.error
    assert KEY not in result.error and "a-different-passphrase" not in result.error
    assert [r.status for r in _runs(engine)] == ["success", "failed"]


@pytest.mark.platform
@needs_restic
def test_the_scheduler_catches_up_a_missing_backup_then_runs_maintenance(
    settings: Settings, engine: Engine
) -> None:
    scheduler._run_due(settings, engine, nightly=False)
    runs = _runs(engine)
    assert [(r.reason, r.status) for r in runs] == [
        ("nightly", "success"),
        ("maintenance", "success"),
    ]
    scheduler._run_due(settings, engine, nightly=False)  # nothing is due now
    assert len(_runs(engine)) == 2


@pytest.mark.platform
@needs_restic
def test_run_now_over_the_api_makes_a_backup_and_reports_success(
    settings: Settings, remote: tuple[Settings, Path]
) -> None:
    s, _ = remote
    with owner_client(create_app(s)) as client:
        started = client.post("/api/backup/run")
        assert started.status_code == 202
        status = client.get("/api/backup/status").json()
        assert status["configured"] and status["remote_configured"] and not status["stale"]
        assert status["last_success"]["status"] == "success" and status["last_success"]["shipped"]
        assert status["last_success"]["reason"] == "manual"
        assert KEY not in json.dumps(status)
