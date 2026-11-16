"""Encrypted Backups (ADR-0011).

``run_backup(reason)`` is the single entry point: the in-app scheduler, the owner-only
"run now" route and the ``python -m finance360.backup`` CLI (used by ``deploy``) all call it.

One run:

1. claim the lock (a ``backup_run`` row in status ``running``; a partial unique index lets only
   one exist, across processes);
2. stage an allowlist: an online-backup copy of the DB and a ``manifest.json``;
3. ``restic backup`` the staged files and ``documents/`` into the local repo, then
   ``restic copy`` the new snapshot to the off-site repo when one is configured;
4. apply retention to both repos;
5. record the outcome. Any failure is caught and recorded; nothing propagates into the app.

The Backup key is read by restic itself, from the file named by ``FINANCE360_BACKUP_KEY_FILE``,
through ``RESTIC_PASSWORD_FILE`` in the subprocess environment. It is never read into this
module's data, logged, put in the manifest or written beside a Backup. Error text recorded for the
status is sanitised and short.

``restore(settings, ...)`` reads a Backup back into a data directory, using the
snapshot layout above as its contract: everything is verified and migrated in a work directory
before the target's database and documents are moved aside into a dated safety copy and replaced.
``data_dir_lock`` keeps it from running while the app serves the same data directory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import cast

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from finance360.config import Settings
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import (
    BACKUP_DIRNAME,
    backup_database,
    head_revision,
    is_known_revision,
    migrate,
)
from finance360.core.models.backup import BackupRun

log = logging.getLogger(__name__)

#: Every snapshot is made under this host name: the container's own hostname changes.
HOST = "finance360"
TAGS = ("nightly", "manual", "pre-deploy")
MANIFEST_VERSION = 1

#: Retention, per repo, after each successful backup (ADR-0011). Constants, not settings.
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY, KEEP_YEARLY = 14, 8, 12, 3
KEEP_LATEST_TAGGED = 5  # pre-deploy and manual snapshots

STALE_AFTER = timedelta(hours=36)
CATCH_UP_AFTER = timedelta(hours=24)
MAINTENANCE_EVERY = timedelta(days=7)
#: A ``running`` row older than this is a crashed run, not a live one.
RUNNING_TIMEOUT = timedelta(hours=6)

#: restic's exit code for "there is no repository at this location".
RESTIC_NO_REPO = 10
_ERROR_MAX = 300
_BACKUP_TIMEOUT = 3600  # seconds per restic call
_REMOTE_TIMEOUT = 4 * 3600  # copy, prune and check over the network are slow


class BackupError(Exception):
    """A step failed. The message is safe to record and show (it never carries the key)."""


BUSY_MESSAGE = "A backup or maintenance run is already running."


class BackupBusy(Exception):
    """Another backup or maintenance run holds the lock."""


class BackupNotConfigured(Exception):
    """No Backup key file is configured, so no Backup can be made."""


@dataclass(frozen=True)
class BackupResult:
    run_id: int
    status: str
    snapshot_id: str | None
    shipped: bool
    error: str | None

    @property
    def ok(self) -> bool:
        return self.status == "success"


def configured(settings: Settings) -> bool:
    return settings.backup_key_file is not None


def key_file(settings: Settings) -> Path:
    if settings.backup_key_file is None:
        raise BackupNotConfigured("Backups are not set up: FINANCE360_BACKUP_KEY_FILE is not set.")
    return settings.backup_key_file


def local_repo(data_dir: Path) -> Path:
    return data_dir / BACKUP_DIRNAME / "restic"


def _staging(data_dir: Path) -> Path:
    return data_dir / BACKUP_DIRNAME / "staging"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- restic ----------------------------------------------------------------------------------


def restic_env(settings: Settings, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The subprocess environment. The key is passed as a file path only, never as a value."""
    env = {k: v for k, v in (os.environ if base is None else base).items()}
    for name in [k for k in env if k.startswith("RESTIC_")]:
        del env[name]  # nothing inherited may redirect or re-key restic
    key = str(key_file(settings))
    env["RESTIC_PASSWORD_FILE"] = key
    env["RESTIC_FROM_PASSWORD_FILE"] = key  # the source repo of `copy` / `init --from-repo`
    env["RESTIC_CACHE_DIR"] = str(settings.data_dir / BACKUP_DIRNAME / "cache")
    return env


def _run_restic(
    args: Sequence[str], env: Mapping[str, str], *, remote: bool = False
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell, no user-supplied strings
            ["restic", *args],  # noqa: S607 - restic is installed in the image
            env=dict(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_REMOTE_TIMEOUT if remote else _BACKUP_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise BackupError("restic is not installed.") from None
    except subprocess.TimeoutExpired:
        raise BackupError(f"restic {args[2] if len(args) > 2 else ''} timed out.") from None


def _restic(step: str, args: Sequence[str], env: Mapping[str, str], *, remote: bool = False) -> str:
    """Run restic, return stdout; on failure raise a BackupError naming the step."""
    done = _run_restic(args, env, remote=remote)
    if done.returncode != 0:
        raise BackupError(f"{step} failed: {done.stderr or done.stdout}")
    return done.stdout


def _ensure_repo(repo: str, env: Mapping[str, str], *, source: str | None = None) -> None:
    """Create the repo if it does not exist. A copy target shares the source's chunker params."""
    probe = _run_restic(["-r", repo, "cat", "config"], env, remote=source is not None)
    if probe.returncode == 0:
        return
    if probe.returncode != RESTIC_NO_REPO:
        # A wrong key, an unreachable remote or a damaged repo: say so, never init over it.
        raise BackupError(f"repository check failed: {probe.stderr or probe.stdout}")
    args = ["-r", repo, "init"]
    if source is not None:
        args += ["--from-repo", source, "--copy-chunker-params"]
    _restic("init", args, env, remote=source is not None)


def apply_retention(repo: str, env: Mapping[str, str], *, remote: bool = False) -> None:
    """The ADR-0011 policy: a rolling window for nightly, the latest few of the others."""
    common = ["-r", repo, "forget", "--host", HOST, "--group-by", "host,tags"]
    _restic(
        "forget",
        [
            *common,
            "--tag",
            "nightly",
            f"--keep-daily={KEEP_DAILY}",
            f"--keep-weekly={KEEP_WEEKLY}",
            f"--keep-monthly={KEEP_MONTHLY}",
            f"--keep-yearly={KEEP_YEARLY}",
        ],
        env,
        remote=remote,
    )
    for tag in ("pre-deploy", "manual"):
        _restic(
            "forget",
            [*common, "--tag", tag, f"--keep-last={KEEP_LATEST_TAGGED}"],
            env,
            remote=remote,
        )


def _repos(settings: Settings) -> list[tuple[str, bool]]:
    repos = [(str(local_repo(settings.data_dir)), False)]
    if settings.backup_remote:
        repos.append((settings.backup_remote, True))
    return repos


# --- staging ---------------------------------------------------------------------------------


def _stage(settings: Settings, reason: str, now: datetime) -> list[Path]:
    """Build the allowlist: the DB copy, the manifest and (if present) the documents folder."""
    data_dir = settings.data_dir
    staging = _staging(data_dir)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    copy = staging / "finance360.sqlite"
    backup_database(db_path(data_dir), copy)
    conn = sqlite3.connect(copy)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, no -wal sidecar
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise BackupError(
                "The database copy failed its integrity check; nothing was backed up."
            )
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    documents = data_dir / "documents"
    has_documents = documents.is_dir()
    manifest = {
        "format": MANIFEST_VERSION,
        "schema_revision": row[0] if row else None,
        "app_version": settings.git_sha,
        "created_at": now.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        "reason": reason,
        "contents": [
            "finance360.sqlite",
            "manifest.json",
            *(["documents/"] if has_documents else []),
        ],
    }
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return [copy, staging / "manifest.json", *([documents] if has_documents else [])]


def _snapshot_summary(stdout: str) -> tuple[str, int]:
    """(snapshot id, bytes processed) from `restic backup --json`."""
    for line in reversed(stdout.splitlines()):
        try:
            message: object = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            fields = cast("dict[str, object]", message)
            snapshot_id = fields.get("snapshot_id")
            processed = fields.get("total_bytes_processed", 0)
            if (
                fields.get("message_type") == "summary"
                and isinstance(snapshot_id, str)
                and isinstance(processed, int)
            ):
                return snapshot_id, processed
    raise BackupError("restic backup gave no snapshot id.")


# --- the lock and the history ----------------------------------------------------------------


def claim_in(session: Session, reason: str, now: datetime | None = None) -> int:
    """Take the lock on the caller's own session; raise BackupBusy if a run is already live.

    A request must use this, not ``claim``: its auth step may already hold SQLite's write lock in
    the request session, and a second connection would wait out ``busy_timeout`` and fail.
    """
    now = now or _utcnow()
    try:
        with session.begin_nested():
            session.execute(
                update(BackupRun)
                .where(BackupRun.status == "running", BackupRun.started_at < now - RUNNING_TIMEOUT)
                .values(
                    status="failed", finished_at=now, error="Interrupted: the run never finished."
                )
            )
            run = BackupRun(reason=reason, started_at=now, status="running", shipped=False)
            session.add(run)
            session.flush()
            return run.id
    except (IntegrityError, OperationalError):  # a live run, or the database is write-locked
        raise BackupBusy(BUSY_MESSAGE) from None


def claim(engine: Engine, reason: str, now: datetime | None = None) -> int:
    """``claim_in`` on its own short transaction (CLI and scheduler)."""
    with make_session_factory(engine).begin() as session:
        return claim_in(session, reason, now)


def fail_interrupted(session: Session, now: datetime | None = None) -> None:
    """At app start nothing can still be running: close rows a restart left open."""
    session.execute(
        update(BackupRun)
        .where(BackupRun.status == "running")
        .values(
            status="failed",
            finished_at=now or _utcnow(),
            error="Interrupted: the app restarted during the run.",
        )
    )


def _finish(
    engine: Engine,
    run_id: int,
    *,
    status: str,
    snapshot_id: str | None = None,
    size_bytes: int | None = None,
    shipped: bool = False,
    error: str | None = None,
) -> None:
    with make_session_factory(engine).begin() as session:
        run = session.get(BackupRun, run_id)
        if run is None:  # pragma: no cover - the row was claimed by this run
            return
        run.status = status
        run.finished_at = _utcnow()
        run.snapshot_id = snapshot_id
        run.size_bytes = size_bytes
        run.shipped = shipped
        run.error = error


def sanitise(text: str, secrets: Sequence[str] = ()) -> str:
    """One short line safe to store and show: secrets blanked, whitespace collapsed, truncated."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    line = re.sub(r"\s+", " ", text).strip()
    return line[:_ERROR_MAX]


def _key_text(settings: Settings) -> str:
    try:
        return key_file(settings).read_text(encoding="utf-8").strip()
    except (OSError, BackupNotConfigured):
        return ""


def _error_text(error: BaseException, settings: Settings) -> str:
    key = _key_text(settings)
    message = str(error) if isinstance(error, BackupError) else f"{type(error).__name__}: {error}"
    secrets = [key] if len(key) >= 8 else []
    return sanitise(message, secrets) or type(error).__name__


# --- the run ---------------------------------------------------------------------------------


@dataclass
class _Progress:
    """What a run achieved so far, so a late failure still records the snapshot it made."""

    snapshot_id: str | None = None
    size: int | None = None
    shipped: bool = False


def _do_backup(settings: Settings, reason: str, progress: _Progress) -> None:
    now = _utcnow()
    if not key_file(settings).is_file():
        raise BackupError("The Backup key file is missing or unreadable.")
    env = restic_env(settings)
    (settings.data_dir / BACKUP_DIRNAME / "cache").mkdir(parents=True, exist_ok=True)
    local = str(local_repo(settings.data_dir))
    paths = _stage(settings, reason, now)
    try:
        _ensure_repo(local, env)
        out = _restic(
            "backup",
            ["-r", local, "backup", "--host", HOST, "--tag", reason, "--json", *map(str, paths)],
            env,
        )
        progress.snapshot_id, progress.size = _snapshot_summary(out)
    finally:
        shutil.rmtree(_staging(settings.data_dir), ignore_errors=True)  # no stray plaintext copy
    if settings.backup_remote:
        _ensure_repo(settings.backup_remote, env, source=local)
        _restic(
            "copy",
            ["-r", settings.backup_remote, "copy", "--from-repo", local, progress.snapshot_id],
            env,
            remote=True,
        )
        progress.shipped = True
    for repo, is_remote in _repos(settings):
        apply_retention(repo, env, remote=is_remote)


def _do_maintenance(settings: Settings) -> None:
    if not key_file(settings).is_file():
        raise BackupError("The Backup key file is missing or unreadable.")
    env = restic_env(settings)
    (settings.data_dir / BACKUP_DIRNAME / "cache").mkdir(parents=True, exist_ok=True)
    for repo, is_remote in _repos(settings):
        _restic("prune", ["-r", repo, "prune"], env, remote=is_remote)
        _restic("check", ["-r", repo, "check"], env, remote=is_remote)


def execute(settings: Settings, engine: Engine, run_id: int, reason: str) -> BackupResult:
    """Do the work for a claimed run and record the outcome. Never raises for a failed run."""
    progress = _Progress()
    try:
        if reason == "maintenance":
            _do_maintenance(settings)
        else:
            _do_backup(settings, reason, progress)
    except Exception as error:  # noqa: BLE001 - a failed backup is recorded, not propagated
        message = _error_text(error, settings)
        log.error("Backup run %s (%s) failed: %s", run_id, reason, message)
        _finish(
            engine,
            run_id,
            status="failed",
            snapshot_id=progress.snapshot_id,
            size_bytes=progress.size,
            shipped=progress.shipped,
            error=message,
        )
        return BackupResult(run_id, "failed", progress.snapshot_id, progress.shipped, message)
    except BaseException:
        _finish(engine, run_id, status="failed", error="Interrupted.")
        raise
    _finish(
        engine,
        run_id,
        status="success",
        snapshot_id=progress.snapshot_id,
        size_bytes=progress.size,
        shipped=progress.shipped,
    )
    log.info("Backup run %s (%s) succeeded.", run_id, reason)
    return BackupResult(run_id, "success", progress.snapshot_id, progress.shipped, None)


def run_backup(
    reason: str, settings: Settings | None = None, engine: Engine | None = None
) -> BackupResult:
    """Take a Backup now (or, for reason ``maintenance``, prune and check the repos).

    Raises BackupNotConfigured (no key file) or BackupBusy (another run is live); a failure of the
    run itself is returned as a ``failed`` result and recorded, not raised.
    """
    settings = settings or Settings.from_env()
    if not configured(settings):
        raise BackupNotConfigured("Backups are not set up: FINANCE360_BACKUP_KEY_FILE is not set.")
    own_engine = engine is None
    engine = engine or make_engine(db_path(settings.data_dir))
    try:
        run_id = claim(engine, reason)
        return execute(settings, engine, run_id, reason)
    finally:
        if own_engine:
            engine.dispose()


# --- status ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupStatus:
    configured: bool
    remote_configured: bool
    #: True when Backups are set up but none succeeded in the last 36 hours.
    stale: bool
    latest: BackupRun | None
    last_success: BackupRun | None
    maintenance: BackupRun | None


def _latest(session: Session, *conditions: object, maintenance: bool = False) -> BackupRun | None:
    is_maintenance = BackupRun.reason == "maintenance"
    return session.scalars(
        select(BackupRun)
        .where(is_maintenance if maintenance else ~is_maintenance, *conditions)  # type: ignore[arg-type]
        .order_by(BackupRun.id.desc())
        .limit(1)
    ).first()


def status(session: Session, settings: Settings, now: datetime | None = None) -> BackupStatus:
    now = now or _utcnow()
    last_success = _latest(session, BackupRun.status == "success")
    is_configured = configured(settings)
    fresh = (
        last_success is not None
        and last_success.finished_at is not None
        and now - last_success.finished_at <= STALE_AFTER
    )
    return BackupStatus(
        configured=is_configured,
        remote_configured=settings.backup_remote is not None,
        stale=is_configured and not fresh,
        latest=_latest(session),
        last_success=last_success,
        maintenance=_latest(session, maintenance=True),
    )


def due_reason(session: Session, now: datetime | None = None) -> str | None:
    """What the scheduler should run on waking or at startup: a catch-up Backup, else weekly
    maintenance if it is due, else nothing."""
    now = now or _utcnow()
    last = _latest(session, BackupRun.status == "success")
    if last is None or last.finished_at is None or now - last.finished_at > CATCH_UP_AFTER:
        return "nightly"
    return maintenance_due(session, now)


def maintenance_due(session: Session, now: datetime | None = None) -> str | None:
    now = now or _utcnow()
    last = _latest(session, BackupRun.status == "success", maintenance=True)
    if last is None or last.finished_at is None or now - last.finished_at > MAINTENANCE_EVERY:
        return "maintenance"
    return None


# --- the data directory lock -----------------------------------------------------------------

#: Held by the app for as long as it serves a data directory, and by restore while it replaces
#: one, so a restore can never swap the database out from under a running app.
LOCK_FILENAME = "finance360.lock"


class DataDirBusy(Exception):
    """Another process (the app, or another restore) holds the data directory lock."""


def _try_lock(fd: int) -> None:
    """Take an exclusive OS lock on ``fd`` without waiting; BlockingIOError if it is held.

    An OS lock, not a pid file: the OS drops it when the holder exits, however it exits, so it
    can never be left stale.
    """
    if sys.platform == "win32":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)  # msvcrt locks from the current position
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError(str(error)) from None
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def data_dir_lock(data_dir: Path) -> Generator[None]:
    """Hold ``<data_dir>/finance360.lock`` exclusively for the duration; raise DataDirBusy if
    another process holds it. Where the filesystem cannot lock at all, warn and carry on: a
    missing lock must never stop the app starting or a disaster-recovery restore."""
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(data_dir / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            _try_lock(fd)
        except BlockingIOError:
            raise DataDirBusy(
                f"{data_dir} is in use: the app is serving it, or a restore is running."
            ) from None
        except OSError as error:
            log.warning("Could not lock %s (%s); carrying on without it.", data_dir, error)
        yield
    finally:
        _unlock(fd)
        os.close(fd)


def _unlock(fd: int) -> None:
    """Release the lock explicitly (closing the descriptor would, too, but later on Windows)."""
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # it was never taken (busy, or an unlockable filesystem)


# --- restore ---------------------------------------------------------------------

#: restic's exit code for "wrong password or no key found".
RESTIC_WRONG_KEY = 12
SAFETY_COPY_PREFIX = "pre-restore-"
_WORK_PREFIX = "restore-work-"
_SNAPSHOT_ID = re.compile(r"[0-9a-f]{8,64}")
_DB_NAME = "finance360.sqlite"
_DOCUMENTS = "documents"
_MANIFEST = "manifest.json"


class RestoreError(Exception):
    """Restore stopped. The message is safe to print (it never carries the key). Unless it says
    otherwise, the target data directory was not touched."""


@dataclass(frozen=True)
class RestoreResult:
    snapshot_id: str
    #: From the manifest: when the Backup was made, why, and by which app version.
    created_at: str
    reason: str
    app_version: str
    #: The schema revision in the Backup, and the one after migrating forward (the same if none).
    from_revision: str
    to_revision: str
    target: Path
    #: Where the target's previous database and documents were moved; None if it had none.
    safety_copy: Path | None


@dataclass(frozen=True)
class _Restored:
    """A verified, migrated restore laid out in the work directory, ready to move in."""

    db: Path
    documents: Path | None
    snapshot_id: str
    manifest: dict[str, object]
    from_revision: str
    to_revision: str


def source_repo(settings: Settings, source: str) -> str:
    """``local`` (this data dir's repo), ``remote`` (the off-site repo) or an explicit restic
    repo, e.g. a copy of the desktop repo mounted into the container."""
    if source == "local":
        return str(local_repo(settings.data_dir))
    if source == "remote":
        if not settings.backup_remote:
            raise RestoreError(
                "No off-site repo is configured: set FINANCE360_BACKUP_RCLONE_REMOTE (and the "
                "rclone config) first, or name the repo with --from."
            )
        return settings.backup_remote
    if not source or source.startswith("-"):
        raise RestoreError(f"Not a repository: {source!r}.")
    return source


def _restic_failure(step: str, done: subprocess.CompletedProcess[str], repo: str) -> RestoreError:
    if done.returncode == RESTIC_WRONG_KEY:
        return RestoreError(
            f"Wrong Backup key for {repo}: restic could not open it with the key file given."
        )
    if done.returncode == RESTIC_NO_REPO:
        return RestoreError(
            f"There is no Backup repository at {repo}. On a fresh host, restore from the "
            "off-site repo (--from remote) or a copy of the desktop repo (--from <path>)."
        )
    return RestoreError(f"{step} failed: {sanitise(done.stderr or done.stdout)}")


def _restic_or_fail(
    step: str, args: Sequence[str], env: Mapping[str, str], repo: str, *, remote: bool
) -> str:
    try:
        done = _run_restic(args, env, remote=remote)
    except BackupError as error:
        raise RestoreError(str(error)) from None
    if done.returncode != 0:
        raise _restic_failure(step, done, repo)
    return done.stdout


def _find_snapshot(
    repo: str, snapshot: str, env: Mapping[str, str], *, remote: bool
) -> dict[str, object]:
    which = ["--host", HOST, "latest"] if snapshot == "latest" else [snapshot]
    out = _restic_or_fail(
        "Listing Backups", ["-r", repo, "snapshots", "--json", *which], env, repo, remote=remote
    )
    try:
        found: object = json.loads(out or "[]")
    except ValueError:
        raise RestoreError("restic gave an unreadable list of Backups.") from None
    if not isinstance(found, list) or not found:
        raise RestoreError(f"No Backup {snapshot!r} in {repo}.")
    first = cast("list[object]", found)[0]
    if not isinstance(first, dict):
        raise RestoreError("restic gave an unreadable list of Backups.")
    return cast("dict[str, object]", first)


def restored_path(root: Path, snapshot_path: str) -> Path:
    """Where ``restic restore --target root`` puts an absolute path from a snapshot."""
    parts = [p for p in snapshot_path.replace("\\", "/").split("/") if p]
    return root.joinpath(*(p.rstrip(":") for p in parts))  # a Windows drive "C:" becomes "C"


def check_manifest(manifest: object, *, has_documents: bool) -> dict[str, object]:
    """The manifest restore relies on: a known format, a schema revision this app knows (never a
    newer one) and contents that match what the Backup holds."""
    if not isinstance(manifest, dict):
        raise RestoreError("The Backup's manifest is not a JSON object.")
    fields = cast("dict[str, object]", manifest)
    if fields.get("format") != MANIFEST_VERSION:
        raise RestoreError(f"Unknown Backup manifest format {fields.get('format')!r}.")
    revision = fields.get("schema_revision")
    if not isinstance(revision, str) or not revision:
        raise RestoreError("The Backup's manifest has no schema revision.")
    if not is_known_revision(revision):
        raise RestoreError(
            f"The Backup is at schema revision {revision}, which this version of Finance360 does "
            f"not know (it knows up to {head_revision()}). It was made by a newer version: "
            "restore it with that version or later."
        )
    contents = fields.get("contents")
    if not isinstance(contents, list):
        raise RestoreError("The Backup's manifest has no contents list.")
    listed = cast("list[object]", contents)
    if _DB_NAME not in listed or (f"{_DOCUMENTS}/" in listed) != has_documents:
        raise RestoreError("The Backup's contents do not match its manifest.")
    return fields


def _check_database(path: Path, revision: str) -> None:
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchall()
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as error:
        raise RestoreError(f"The Backup's database is damaged: {error}") from None
    if integrity != [("ok",)]:
        raise RestoreError("The Backup's database failed its integrity check.")
    if row is None or row[0] != revision:
        raise RestoreError("The Backup's database and manifest disagree on the schema revision.")


def _fetch_and_verify(
    repo: str, snapshot: str, env: Mapping[str, str], work: Path, *, remote: bool
) -> _Restored:
    """Everything that can fail, done in ``work`` only: find the Backup, check the repo, restore
    and verify it, then migrate it forward. The target's data is not touched."""
    found = _find_snapshot(repo, snapshot, env, remote=remote)
    snapshot_id = found.get("id")
    paths = found.get("paths")
    if not isinstance(snapshot_id, str) or not isinstance(paths, list):
        raise RestoreError("restic gave an unreadable Backup record.")
    _restic_or_fail("Checking the repository", ["-r", repo, "check"], env, repo, remote=remote)
    raw = work / "raw"
    _restic_or_fail(
        "Restoring",
        ["-r", repo, "restore", snapshot_id, "--target", str(raw), "--verify"],
        env,
        repo,
        remote=remote,
    )
    by_name = {
        PurePosixPath(p.replace("\\", "/")).name: restored_path(raw, p)
        for p in cast("list[object]", paths)
        if isinstance(p, str)
    }
    db, manifest_path = by_name.get(_DB_NAME), by_name.get(_MANIFEST)
    if db is None or manifest_path is None or not db.is_file() or not manifest_path.is_file():
        raise RestoreError("Not a Finance360 Backup: it has no database or no manifest.")
    documents = by_name.get(_DOCUMENTS)
    if documents is not None and not documents.is_dir():
        documents = None
    try:
        manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError:
        raise RestoreError("The Backup's manifest is not valid JSON.") from None
    fields = check_manifest(manifest, has_documents=documents is not None)
    from_revision = cast("str", fields["schema_revision"])
    _check_database(db, from_revision)

    # Lay it out as a data directory and migrate it forward there, before the target is touched.
    staged = work / "data"
    staged.mkdir()
    os.replace(db, db_path(staged))
    if documents is not None:
        os.replace(documents, staged / _DOCUMENTS)
    try:
        migrated = migrate(staged)
    except Exception as error:  # noqa: BLE001 - any migration failure stops the restore
        raise RestoreError(f"Migrating the Backup forward failed: {error}") from None
    conn = sqlite3.connect(db_path(staged))
    try:
        conn.execute("PRAGMA journal_mode=DELETE")  # one self-contained file to move
    finally:
        conn.close()
    return _Restored(
        db=db_path(staged),
        documents=staged / _DOCUMENTS if documents is not None else None,
        snapshot_id=snapshot_id,
        manifest=fields,
        from_revision=from_revision,
        to_revision=migrated.to_revision,
    )


def _swap(target: Path, restored: _Restored, stamp: str) -> Path | None:
    """Move the target's database and documents aside into a dated safety copy (never deleted),
    then move the restored ones in. Every step is a rename within one filesystem; if one fails,
    the steps already done are undone in reverse."""
    db = db_path(target)
    current = [
        p
        for p in (db, db.with_name(f"{_DB_NAME}-wal"), db.with_name(f"{_DB_NAME}-shm"))
        if p.exists()
    ]
    if (target / _DOCUMENTS).exists():
        current.append(target / _DOCUMENTS)
    safety = target / BACKUP_DIRNAME / f"{SAFETY_COPY_PREFIX}{stamp}" if current else None
    done: list[tuple[Path, Path]] = []
    try:
        if safety is not None:
            safety.mkdir(parents=True)
            for path in current:
                os.replace(path, safety / path.name)
                done.append((path, safety / path.name))
        os.replace(restored.db, db)
        done.append((restored.db, db))
        if restored.documents is not None:
            os.replace(restored.documents, target / _DOCUMENTS)
            done.append((restored.documents, target / _DOCUMENTS))
    except BaseException as error:
        try:
            for source, moved_to in reversed(done):
                os.replace(moved_to, source)
        except OSError:
            raise RestoreError(
                f"Restore failed while moving files ({error}) and could not undo it. The previous "
                f"data is in {safety}; move it back by hand."
            ) from None
        if safety is not None and safety.is_dir() and not any(safety.iterdir()):
            safety.rmdir()
        raise
    return safety


def restore(
    settings: Settings,
    *,
    source: str = "local",
    snapshot: str = "latest",
    target: Path | None = None,
) -> RestoreResult:
    """Rebuild a data directory from a Backup (ADR-0011, "Serving restore").

    Restore into a work directory, verify it (``restic check``; restic's own decryption checks
    plus ``--verify``; ``PRAGMA integrity_check``; the manifest and its schema revision), migrate
    it forward, and only then move the database and documents into ``target``, keeping what was
    there as a dated safety copy. A wrong key, a missing or damaged Backup or a newer schema fails
    before the target's data is touched. Refuses (DataDirBusy) while the app or another restore
    holds the target.
    """
    target = (target or settings.data_dir).resolve()
    if snapshot != "latest" and not _SNAPSHOT_ID.fullmatch(snapshot):
        raise RestoreError(f"Not a Backup id: {snapshot!r} (use 'latest' or a restic id).")
    if not key_file(settings).is_file():
        raise RestoreError("The Backup key file is missing or unreadable.")
    repo = source_repo(settings, source)
    remote = repo.startswith("rclone:") or repo == settings.backup_remote
    made_target = not target.exists()
    try:
        with data_dir_lock(target):
            backups = target / BACKUP_DIRNAME
            made_backups = not backups.exists()
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            work = backups / f"{_WORK_PREFIX}{stamp}"
            work.mkdir(parents=True)
            env = restic_env(settings)
            env["RESTIC_CACHE_DIR"] = str(work / "cache")  # nothing left behind in the target
            try:
                restored = _fetch_and_verify(repo, snapshot, env, work, remote=remote)
                safety = _swap(target, restored, stamp)
            finally:
                shutil.rmtree(work, ignore_errors=True)
                if made_backups and backups.is_dir() and not any(backups.iterdir()):
                    backups.rmdir()
    except BaseException:
        if made_target:
            shutil.rmtree(target, ignore_errors=True)  # made by this call: nothing else is in it
        raise
    manifest = restored.manifest
    return RestoreResult(
        snapshot_id=restored.snapshot_id,
        created_at=str(manifest.get("created_at", "")),
        reason=str(manifest.get("reason", "")),
        app_version=str(manifest.get("app_version", "")),
        from_revision=restored.from_revision,
        to_revision=restored.to_revision,
        target=target,
        safety_copy=safety,
    )
