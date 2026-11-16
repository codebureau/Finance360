"""Startup migration runner with an automatic pre-migration backup (ADR-0009).

``migrate(data_dir)`` brings ``FINANCE360_DATA_DIR/finance360.sqlite`` to the latest Alembic
revision. If the database already exists and a migration is pending, it is copied to
``<data_dir>/backups/`` first, using SQLite's online backup API so a WAL-mode database is copied
consistently. A fresh data directory is not backed up: there is nothing to lose.

Also runnable as ``python -m finance360.core.migrate`` (the review slot uses this, so the CLI and
the app share one code path).
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import Engine

from finance360.core.db import db_path, make_engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
BACKUP_DIRNAME = "backups"
#: Pre-migration copies kept (ADR-0011); older ones are deleted after a new one is made.
KEEP_PRE_MIGRATION_COPIES = 5


class MigrationError(RuntimeError):
    """The database cannot be migrated by this version of the app."""


@dataclass(frozen=True)
class MigrationResult:
    from_revision: str | None
    to_revision: str
    backup: Path | None

    @property
    def migrated(self) -> bool:
        return self.from_revision != self.to_revision


def alembic_config(data_dir: Path, script_location: Path | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(script_location or MIGRATIONS_DIR))
    config.attributes["data_dir"] = str(data_dir)
    return config


def head_revision(script_location: Path | None = None) -> str:
    script = ScriptDirectory(str(script_location or MIGRATIONS_DIR))
    head = script.get_current_head()
    if head is None:
        raise MigrationError("No migrations found.")
    return head


def is_known_revision(revision: str, script_location: Path | None = None) -> bool:
    """True if this version of the app has a migration with exactly that revision id."""
    try:
        found = ScriptDirectory(str(script_location or MIGRATIONS_DIR)).get_revision(revision)
    except CommandError:
        return False
    return found.revision == revision  # no partial-id matches


def schema_version(engine: Engine) -> str | None:
    """The revision the database is at, or None if it has never been migrated."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def backup_database(source: Path, destination: Path) -> None:
    """Consistent copy of a SQLite file (safe in WAL mode, unlike a plain file copy)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def prune_pre_migration_copies(backup_dir: Path, keep: int = KEEP_PRE_MIGRATION_COPIES) -> None:
    """Keep only the latest ``keep`` pre-migration copies (names start with a UTC timestamp)."""
    copies = sorted(p for p in backup_dir.glob("finance360-*.sqlite") if p.is_file())
    for old in copies[: max(len(copies) - keep, 0)]:
        old.unlink(missing_ok=True)


def migrate(data_dir: Path, script_location: Path | None = None) -> MigrationResult:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = db_path(data_dir)
    existed = path.exists() and path.stat().st_size > 0
    head = head_revision(script_location)

    current: str | None = None
    if existed:
        engine = make_engine(path)
        try:
            current = schema_version(engine)
        finally:
            engine.dispose()
    if current == head:
        return MigrationResult(current, head, None)

    if current is not None and not is_known_revision(current, script_location):
        raise MigrationError(
            f"The database is at revision {current}, which this version of Finance360 does "
            "not know. It was probably migrated by a newer version; refusing to touch it."
        )

    backup: Path | None = None
    if existed:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = data_dir / BACKUP_DIRNAME / f"finance360-{stamp}-{current or 'unversioned'}.sqlite"
        backup_database(path, backup)
        prune_pre_migration_copies(backup.parent)

    command.upgrade(alembic_config(data_dir, script_location), "head")
    return MigrationResult(current, head, backup)


def main() -> int:
    from finance360.config import Settings

    result = migrate(Settings.from_env().data_dir)
    if not result.migrated:
        print(f"Database already at schema version {result.to_revision}.")
    else:
        print(f"Migrated database {result.from_revision or '(new)'} -> {result.to_revision}.")
    if result.backup:
        print(f"Backed up the previous database to {result.backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
