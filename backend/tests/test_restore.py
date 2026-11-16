"""One-command restore (ADR-0011 "Serving restore").

Two layers, like ``test_backup.py``. The always-on tests cover the manifest rules, the path
mapping, the data directory lock (against a real app process), the move-in and its undo, and the
CLI. The ``needs_restic`` tests drive real restic and rclone (a local-folder repo and an rclone
``alias`` remote) over the synthetic household: back up, mutate or destroy, restore, and compare
the Position and every table; and each failure (wrong key, missing Backup, newer schema, damaged
repo, damaged database, no repo) leaves the target exactly as it was. CI installs both tools; in
the Docker image they are on PATH too.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time as clock
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine

from finance360 import backup as backup_cli
from finance360.config import Settings
from finance360.core import backup
from finance360.core.db import db_path
from finance360.core.migrate import alembic_config, head_revision, is_known_revision
from finance360.main import create_app
from support import TestClient, owner_client

KEY = "synthetic-restore-passphrase-NOT-A-REAL-KEY-4Qp8"
WRONG_KEY = "a-different-synthetic-passphrase-0000"
FIXTURE = Path(__file__).parents[2] / "fixtures" / "sample_household"
DOC = "SYNTHETIC-DOCUMENT-CONTENT"
#: Tables the app itself writes at startup or that record the Backup run itself, not data.
NOT_COMPARED = {"backup_run"}

needs_restic = pytest.mark.skipif(
    shutil.which("restic") is None or shutil.which("rclone") is None,
    reason="needs the restic and rclone binaries (they ship in the Docker image and CI)",
)


@pytest.fixture(autouse=True)
def no_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs are driven by hand here; the app's nightly loop would race them."""

    async def idle(settings: Settings, engine: Engine) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("finance360.main.backup_loop", idle)


def _settings(tmp_path: Path, data: str = "data", key: str = KEY) -> Settings:
    key_file = tmp_path / "secrets" / f"key-{abs(hash(key))}"
    key_file.parent.mkdir(exist_ok=True)
    key_file.write_text(key + "\n", encoding="utf-8")
    return Settings(
        data_dir=tmp_path / data,
        port=8000,
        git_sha="restore-test",
        static_dir=None,
        backup_key_file=key_file,
    )


def _tree(folder: Path) -> dict[str, bytes | None]:
    """Every path under ``folder`` with its bytes (None for a folder), bar the lock file."""
    if not folder.exists():
        return {}
    return {
        p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(folder.rglob("*"))
        if p.name != backup.LOCK_FILENAME
    }


def _household(settings: Settings) -> None:
    """The synthetic household, loaded by the app on first start, plus one document."""
    settings.data_dir.mkdir(parents=True)
    for src in FIXTURE.iterdir():
        if src.name in {"README.md", ".gitkeep"}:
            continue
        if src.is_dir():  # as review.py does: the sample exports under imports/
            shutil.copytree(src, settings.data_dir / src.name)
        else:
            shutil.copy2(src, settings.data_dir / src.name)
    docs = settings.data_dir / "documents" / "2026"
    docs.mkdir(parents=True)
    (docs / "statement.txt").write_text(DOC, encoding="utf-8")


def _tables(data_dir: Path) -> dict[str, list[tuple[object, ...]]]:
    conn = sqlite3.connect(db_path(data_dir))
    try:
        names = [
            n
            for (n,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            if n not in NOT_COMPARED
        ]
        return {n: conn.execute(f'SELECT * FROM "{n}" ORDER BY rowid').fetchall() for n in names}  # noqa: S608
    finally:
        conn.close()


def _position(client: TestClient) -> tuple[object, object]:
    now = client.get("/api/position")
    history = client.get("/api/position/history")
    assert now.status_code == 200 and history.status_code == 200
    return now.json(), history.json()


# --- always-on -------------------------------------------------------------------------------


def test_snapshot_paths_map_under_the_restore_target(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    assert backup.restored_path(root, "/data/backups/staging/finance360.sqlite") == (
        root / "data" / "backups" / "staging" / "finance360.sqlite"
    )
    assert backup.restored_path(root, "C:\\data\\documents") == root / "C" / "data" / "documents"


def _manifest(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "format": 1,
        "schema_revision": head_revision(),
        "app_version": "abc1234",
        "created_at": "2026-03-10T12:00:00Z",
        "reason": "nightly",
        "contents": ["finance360.sqlite", "manifest.json", "documents/"],
    }
    return base | overrides


def test_the_manifest_must_be_known_and_match_the_backup() -> None:
    assert backup.check_manifest(_manifest(), has_documents=True)["reason"] == "nightly"
    with pytest.raises(backup.RestoreError, match="newer version"):
        backup.check_manifest(_manifest(schema_revision="9999"), has_documents=True)
    with pytest.raises(backup.RestoreError, match="format"):
        backup.check_manifest(_manifest(format=2), has_documents=True)
    with pytest.raises(backup.RestoreError, match="do not match"):
        backup.check_manifest(_manifest(), has_documents=False)  # documents listed, none there
    with pytest.raises(backup.RestoreError, match="no schema revision"):
        backup.check_manifest(_manifest(schema_revision=None), has_documents=True)
    with pytest.raises(backup.RestoreError, match="not a JSON object"):
        backup.check_manifest(["a list"], has_documents=True)


def test_only_exact_known_revisions_count() -> None:
    assert is_known_revision(head_revision()) and is_known_revision("0001")
    assert not is_known_revision("000")  # a prefix of several, never a partial match
    assert not is_known_revision("9999") and not is_known_revision("ffffffffffff")


def test_the_data_dir_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    with backup.data_dir_lock(tmp_path):
        with pytest.raises(backup.DataDirBusy, match="in use"):
            with backup.data_dir_lock(tmp_path):
                pass
    with backup.data_dir_lock(tmp_path):  # free again
        pass


def test_the_app_will_not_start_while_a_restore_holds_the_data_dir(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    with backup.data_dir_lock(s.data_dir):
        with pytest.raises(backup.DataDirBusy):
            with owner_client(create_app(s)):
                pass
    with owner_client(create_app(s)) as client:  # and starts once it is released
        assert client.get("/health").status_code == 200


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def serving(tmp_path: Path) -> Iterator[Settings]:
    """The real app, in its own process (``python -m finance360.main``), serving a data dir."""
    s = _settings(tmp_path)
    _household(s)
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("FINANCE360_")}
    env |= {"FINANCE360_DATA_DIR": str(s.data_dir), "FINANCE360_PORT": str(port)}
    app = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "finance360.main"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = clock.monotonic() + 60
        while True:
            assert app.poll() is None, "the app exited"
            assert clock.monotonic() < deadline, "the app did not start"
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2):  # noqa: S310
                    break
            except OSError:
                clock.sleep(0.2)
        yield s
    finally:
        app.terminate()
        app.wait(timeout=30)


def test_restore_refuses_while_the_app_serves_the_target(
    serving: Settings, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _tree(serving.data_dir)
    with pytest.raises(backup.DataDirBusy):
        backup.restore(serving, source="local")
    assert _tree(serving.data_dir) == before
    # The CLI says what to do, with its own exit code.
    monkeypatch.setenv("FINANCE360_DATA_DIR", str(serving.data_dir))
    monkeypatch.setenv("FINANCE360_BACKUP_KEY_FILE", str(serving.backup_key_file))
    assert backup_cli.main(["restore"]) == 3
    assert "Stop the app first" in capsys.readouterr().err
    assert _tree(serving.data_dir) == before


def test_bad_arguments_fail_before_anything_is_created(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    with pytest.raises(backup.RestoreError, match="Not a Backup id"):
        backup.restore(s, snapshot="--delete")
    with pytest.raises(backup.RestoreError, match="key file"):
        backup.restore(replace(s, backup_key_file=tmp_path / "no-such-key"))
    with pytest.raises(backup.RestoreError, match="No off-site repo"):
        backup.restore(s, source="remote")
    with pytest.raises(backup.BackupNotConfigured):
        backup.restore(replace(s, backup_key_file=None))
    assert not s.data_dir.exists()


def _restored(work: Path) -> backup._Restored:
    staged = work / "data"
    (staged / "documents").mkdir(parents=True)
    (staged / "finance360.sqlite").write_bytes(b"restored-db")
    (staged / "documents" / "new.txt").write_text("restored-doc")
    return backup._Restored(
        db=staged / "finance360.sqlite",
        documents=staged / "documents",
        snapshot_id="0" * 64,
        manifest=_manifest(),
        from_revision=head_revision(),
        to_revision=head_revision(),
    )


def test_moving_in_keeps_the_previous_data_as_a_safety_copy(tmp_path: Path) -> None:
    target = tmp_path / "target"
    (target / "documents").mkdir(parents=True)
    (target / "finance360.sqlite").write_bytes(b"old-db")
    (target / "finance360.sqlite-wal").write_bytes(b"old-wal")
    (target / "documents" / "old.txt").write_text("old-doc")
    (target / "household.json").write_text("{}")  # not part of a Backup: left alone
    safety = backup._swap(target, _restored(tmp_path / "work"), "20260310T120000Z")
    assert safety is not None and safety == target / "backups" / "pre-restore-20260310T120000Z"
    assert (target / "finance360.sqlite").read_bytes() == b"restored-db"
    assert not (target / "finance360.sqlite-wal").exists()  # the old WAL went with its database
    assert [p.name for p in (target / "documents").iterdir()] == ["new.txt"]
    assert (safety / "finance360.sqlite").read_bytes() == b"old-db"
    assert (safety / "finance360.sqlite-wal").read_bytes() == b"old-wal"
    assert (safety / "documents" / "old.txt").read_text() == "old-doc"
    assert (target / "household.json").read_text() == "{}"


def test_a_failed_move_in_is_undone_and_the_target_is_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    (target / "documents").mkdir(parents=True)
    (target / "finance360.sqlite").write_bytes(b"old-db")
    (target / "documents" / "old.txt").write_text("old-doc")
    (target / "backups").mkdir()  # restore() always has its work folder in here
    before = _tree(target)
    restored = _restored(tmp_path / "work")
    real_replace = os.replace

    def failing(src: object, dst: object) -> None:
        if Path(str(src)) == restored.documents:  # the very last move fails
            raise OSError("disk full (simulated)")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(OSError, match="simulated"):
        backup._swap(target, restored, "20260310T120000Z")
    monkeypatch.undo()
    assert _tree(target) == before  # the old data is back in place, no empty safety folder left
    assert restored.db.read_bytes() == b"restored-db"


def test_the_restore_cli_reports_each_outcome_in_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FINANCE360_DATA_DIR", str(tmp_path / "data"))
    seen: dict[str, object] = {}

    def fake(outcome: backup.RestoreResult | Exception) -> None:
        def run(settings: Settings, **kwargs: object) -> backup.RestoreResult:
            seen.update(kwargs)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(backup, "restore", run)

    ok = backup.RestoreResult(
        snapshot_id="abcdef0123456789",
        created_at="2026-03-10T12:00:00Z",
        reason="nightly",
        app_version="abc1234",
        from_revision="0008",
        to_revision="0009",
        target=tmp_path / "data",
        safety_copy=tmp_path / "data" / "backups" / "pre-restore-x",
    )
    fake(ok)
    args = ["restore", "--from", "remote", "--backup", "abcdef01", "--target", str(tmp_path)]
    assert backup_cli.main(args) == 0
    out = capsys.readouterr().out
    assert "restored Backup abcdef01" in out and "migrated to 0009" in out
    assert "pre-restore-x" in out
    assert seen == {"source": "remote", "snapshot": "abcdef01", "target": tmp_path}
    fake(backup.RestoreError("wrong key"))
    assert backup_cli.main(["restore"]) == 1
    assert "FAILED: wrong key" in capsys.readouterr().err
    assert seen["source"] == "local" and seen["snapshot"] == "latest" and seen["target"] is None
    fake(backup.BackupNotConfigured("not set up"))
    assert backup_cli.main(["restore"]) == 2
    fake(backup.DataDirBusy("in use."))
    assert backup_cli.main(["restore"]) == 3


# --- real restic and rclone ------------------------------------------------------------------


@pytest.fixture
def household(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """The synthetic household, started once by the app, with a local rclone remote off-site."""
    folder = tmp_path / "offsite"
    folder.mkdir()
    conf = tmp_path / "rclone.conf"
    conf.write_text(f"[offsite]\ntype = alias\nremote = {folder.as_posix()}\n", encoding="utf-8")
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    s = replace(_settings(tmp_path), backup_remote="rclone:offsite:repo")
    _household(s)
    with owner_client(create_app(s)) as client:
        assert len(client.get("/api/accounts").json()) > 0  # the fixture loaded
    return s


def _backup(settings: Settings) -> str:
    result = backup.run_backup("manual", settings)
    assert result.ok and result.shipped and result.snapshot_id, result.error
    return result.snapshot_id


def _add_valuation(settings: Settings) -> None:
    with owner_client(create_app(settings)) as client:
        account = client.get("/api/accounts").json()[0]
        added = client.post(
            f"/api/accounts/{account['id']}/valuations",
            json={"amount": "123456.78", "provenance_note": "after the Backup"},
        )
        assert added.status_code == 201, added.text


@pytest.mark.platform
@needs_restic
def test_restore_after_a_bad_change_gives_back_the_same_position(household: Settings) -> None:
    with owner_client(create_app(household)) as client:
        position = _position(client)
    tables = _tables(household.data_dir)
    assert tables["valuation"] and tables["audit_event"]
    _backup(household)

    _add_valuation(household)  # a change after the Backup, which the restore must undo
    changed = db_path(household.data_dir).read_bytes()
    with owner_client(create_app(household)) as client:
        assert _position(client) != position
    (household.data_dir / "documents" / "2026" / "statement.txt").write_text("overwritten")

    result = backup.restore(household, source="local")
    assert result.from_revision == result.to_revision == head_revision()
    assert result.reason == "manual" and result.app_version == "restore-test"
    assert _tables(household.data_dir) == tables  # Valuations, audit rows, everything
    with owner_client(create_app(household)) as client:
        assert _position(client) == position
        assert client.get("/health").json()["status"] == "healthy"
    statement = household.data_dir / "documents" / "2026" / "statement.txt"
    assert statement.read_text() == DOC
    # The changed database is kept, never deleted, and nothing is left in the work folder.
    assert result.safety_copy is not None
    assert (result.safety_copy / "finance360.sqlite").read_bytes() == changed
    assert (result.safety_copy / "documents" / "2026" / "statement.txt").read_text() == (
        "overwritten"
    )
    leftovers = [p.name for p in (household.data_dir / "backups").iterdir()]
    assert not [n for n in leftovers if n.startswith("restore-work-")]


@pytest.mark.platform
@needs_restic
def test_a_fresh_host_restores_from_the_offsite_repo(household: Settings, tmp_path: Path) -> None:
    """Disaster recovery: the whole data dir is gone, local repo included."""
    with owner_client(create_app(household)) as client:
        position = _position(client)
    tables = _tables(household.data_dir)
    _backup(household)
    shutil.rmtree(household.data_dir)

    fresh = replace(household, data_dir=tmp_path / "fresh-host")
    with pytest.raises(backup.RestoreError, match="no Backup repository"):
        backup.restore(fresh, source="local")  # there is no local repo on a fresh host
    assert not fresh.data_dir.exists()  # and the failed attempt left nothing behind

    result = backup.restore(fresh, source="remote")
    assert result.safety_copy is None  # nothing was there to keep
    assert _tables(fresh.data_dir) == tables
    assert (fresh.data_dir / "documents" / "2026" / "statement.txt").read_text() == DOC
    with owner_client(create_app(fresh)) as client:
        assert _position(client) == position


@pytest.mark.platform
@needs_restic
def test_a_named_backup_restores_that_backup(household: Settings) -> None:
    first_tables = _tables(household.data_dir)
    first = _backup(household)
    _add_valuation(household)
    second = _backup(household)
    assert first != second
    # The id recorded by the run (and shown in the status) is the local repo's: `restic copy`
    # gives the off-site copy an id of its own, so ids are looked up per repo.
    result = backup.restore(household, source="local", snapshot=first[:8])
    assert result.snapshot_id == first
    assert _tables(household.data_dir) == first_tables


def _unchanged_after_failure(settings: Settings, match: str, **kwargs: object) -> None:
    before = _tree(settings.data_dir)
    with pytest.raises(backup.RestoreError, match=match) as failure:
        backup.restore(settings, **kwargs)  # type: ignore[arg-type]
    assert _tree(settings.data_dir) == before, "the target changed"
    assert KEY not in str(failure.value) and WRONG_KEY not in str(failure.value)


@pytest.mark.platform
@needs_restic
def test_a_wrong_key_fails_before_the_target_is_touched(
    household: Settings, tmp_path: Path
) -> None:
    _backup(household)
    _add_valuation(household)
    wrong = replace(household, backup_key_file=_settings(tmp_path, key=WRONG_KEY).backup_key_file)
    _unchanged_after_failure(wrong, "Wrong Backup key", source="local")
    _unchanged_after_failure(wrong, "Wrong Backup key", source="remote")


@pytest.mark.platform
@needs_restic
def test_a_missing_backup_fails_before_the_target_is_touched(household: Settings) -> None:
    _backup(household)
    _unchanged_after_failure(household, "No Backup 'deadbeef'", snapshot="deadbeef")


def _corrupt(folder: Path) -> None:
    """Overwrite bytes in the middle of every pack file (restic makes them read-only)."""
    for pack in folder.rglob("*"):
        if pack.is_file():
            pack.chmod(0o600)
            data = bytearray(pack.read_bytes())
            data[len(data) // 2 : len(data) // 2 + 16] = b"\xff" * 16
            pack.write_bytes(bytes(data))


@pytest.mark.platform
@needs_restic
def test_a_damaged_repo_fails_before_the_target_is_touched(household: Settings) -> None:
    _backup(household)
    _corrupt(backup.local_repo(household.data_dir) / "data")
    _unchanged_after_failure(household, "failed", source="local")


def _craft_backup(settings: Settings, db: Path, manifest: dict[str, object]) -> None:
    """A hand-made Backup in the same layout as ``run_backup``'s, for cases it would refuse to
    make (a damaged database) or cannot make (an older or newer schema)."""
    staging = settings.data_dir.parent / "craft" / "backups" / "staging"
    staging.mkdir(parents=True)
    shutil.copy2(db, staging / "finance360.sqlite")
    (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    env = backup.restic_env(settings)
    repo = str(backup.local_repo(settings.data_dir))
    files = [str(staging / "finance360.sqlite"), str(staging / "manifest.json")]
    for args in (["init"], ["backup", "--host", backup.HOST, "--tag", "manual", *files]):
        done = subprocess.run(  # noqa: S603
            ["restic", "-r", repo, *args],  # noqa: S607
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0 or "already" in done.stderr, done.stderr
    shutil.rmtree(staging.parent.parent)


def _db_at(tmp_path: Path, revision: str) -> Path:
    folder = tmp_path / f"db-{revision}"
    folder.mkdir()
    command.upgrade(alembic_config(folder), revision)
    conn = sqlite3.connect(db_path(folder))
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    return db_path(folder)


@pytest.mark.platform
@needs_restic
def test_a_backup_from_a_newer_version_fails_before_the_target_is_touched(
    household: Settings, tmp_path: Path
) -> None:
    db = _db_at(tmp_path, head_revision())
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("UPDATE alembic_version SET version_num = '0099'")
    conn.close()
    contents = ["finance360.sqlite", "manifest.json"]
    _craft_backup(household, db, _manifest(schema_revision="0099", contents=contents))
    _unchanged_after_failure(household, "newer version")


@pytest.mark.platform
@needs_restic
def test_a_damaged_database_in_a_backup_fails_before_the_target_is_touched(
    household: Settings, tmp_path: Path
) -> None:
    db = _db_at(tmp_path, head_revision())
    data = bytearray(db.read_bytes())
    data[4096 : 4096 + 64] = b"\x00\xff" * 32  # trash the second page
    db.write_bytes(bytes(data))
    contents = ["finance360.sqlite", "manifest.json"]
    _craft_backup(household, db, _manifest(contents=contents))
    _unchanged_after_failure(household, "damaged|integrity")


@pytest.mark.platform
@needs_restic
def test_a_backup_from_an_older_version_is_migrated_forward(
    household: Settings, tmp_path: Path
) -> None:
    db = _db_at(tmp_path, "0008")
    contents = ["finance360.sqlite", "manifest.json"]
    _craft_backup(household, db, _manifest(schema_revision="0008", contents=contents))
    result = backup.restore(household)
    assert (result.from_revision, result.to_revision) == ("0008", head_revision())
    conn = sqlite3.connect(db_path(household.data_dir))
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (head_revision(),)
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    conn.close()
    assert not (household.data_dir / "documents").exists()  # that Backup had none
    with owner_client(create_app(household)) as client:
        assert client.get("/health").status_code == 200


@pytest.mark.platform
@needs_restic
def test_the_restore_cli_end_to_end(
    household: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tables = _tables(household.data_dir)
    _backup(household)
    db_path(household.data_dir).unlink()  # the database is lost
    monkeypatch.setenv("FINANCE360_DATA_DIR", str(household.data_dir))
    monkeypatch.setenv("FINANCE360_BACKUP_KEY_FILE", str(household.backup_key_file))
    monkeypatch.setenv("FINANCE360_BACKUP_RCLONE_REMOTE", household.backup_remote or "")
    assert backup_cli.main(["restore", "--from", "remote"]) == 0
    out = capsys.readouterr()
    assert "restored Backup" in out.out and KEY not in out.out + out.err
    assert _tables(household.data_dir) == tables
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    (safety,) = (household.data_dir / "backups").glob(f"pre-restore-{stamp}*")
    assert (safety / "documents" / "2026" / "statement.txt").read_text() == DOC
