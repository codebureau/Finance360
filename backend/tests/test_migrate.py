"""Migrations and the pre-migration backup, against real temporary SQLite databases."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.util.exc import CommandError
from sqlalchemy import Column, Integer, Table, inspect, text

from finance360.core.db import Base, db_path, make_engine
from finance360.core.migrate import (
    MIGRATIONS_DIR,
    MigrationError,
    alembic_config,
    head_revision,
    migrate,
    schema_version,
)

REPO = Path(__file__).resolve().parents[2]

SECOND_MIGRATION = '''"""add a scratch table"""

from alembic import op
import sqlalchemy as sa

revision = "9001"
down_revision = "__HEAD__"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("scratch", sa.Column("id", sa.Integer, primary_key=True))


def downgrade() -> None:
    op.drop_table("scratch")
'''


@pytest.fixture
def scripts_with_second(tmp_path: Path) -> Path:
    """A copy of the real migrations plus a 9001, standing in for 'a new migration arrives'."""
    scripts = tmp_path / "scripts"
    shutil.copytree(MIGRATIONS_DIR, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "9001_scratch.py").write_text(
        SECOND_MIGRATION.replace("__HEAD__", head_revision())
    )
    return scripts


def _version(data_dir: Path) -> str | None:
    engine = make_engine(db_path(data_dir))
    try:
        return schema_version(engine)
    finally:
        engine.dispose()


def test_fresh_data_dir_creates_the_database_at_head_without_a_backup(tmp_path: Path) -> None:
    data = tmp_path / "data"
    result = migrate(data)
    assert db_path(data).is_file()
    assert result.from_revision is None
    assert result.to_revision == head_revision()
    assert result.backup is None
    assert not (data / "backups").exists()
    assert _version(data) == head_revision()


def test_database_is_in_wal_mode(tmp_path: Path) -> None:
    migrate(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as con:
        assert con.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_rerunning_at_head_is_a_no_op_with_no_backup(tmp_path: Path) -> None:
    migrate(tmp_path)
    again = migrate(tmp_path)
    assert not again.migrated
    assert again.backup is None
    assert not (tmp_path / "backups").exists()


def test_existing_database_is_backed_up_before_a_new_migration(
    tmp_path: Path, scripts_with_second: Path
) -> None:
    real = tmp_path / "real"
    migrate(real)  # at the real head
    with sqlite3.connect(db_path(real)) as con:  # data that must survive in the backup
        con.execute("CREATE TABLE precious (n INTEGER)")
        con.execute("INSERT INTO precious VALUES (42)")

    # Reuse the same data dir, but now with the second migration available.
    result = migrate(real, scripts_with_second)

    assert result.from_revision == head_revision()
    assert result.to_revision == "9001"
    assert result.backup is not None
    assert result.backup.parent == real / "backups"
    assert _version(real) == "9001"
    with sqlite3.connect(result.backup) as con:
        assert con.execute("SELECT n FROM precious").fetchall() == [(42,)]
        assert con.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (head_revision(),)
        ]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "scratch" not in tables  # the backup is the state before the migration
    with sqlite3.connect(db_path(real)) as con:
        assert con.execute("SELECT n FROM precious").fetchall() == [(42,)]


def test_each_migration_gets_its_own_backup_file(tmp_path: Path, scripts_with_second: Path) -> None:
    migrate(tmp_path)
    first = migrate(tmp_path, scripts_with_second)
    assert first.backup is not None and first.backup.is_file()
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_database_from_a_newer_version_is_refused_and_untouched(
    tmp_path: Path, scripts_with_second: Path
) -> None:
    migrate(tmp_path, scripts_with_second)  # now at 9001, which the real scripts do not know
    before = db_path(tmp_path).read_bytes()
    with pytest.raises(MigrationError, match="9001"):
        migrate(tmp_path)  # real scripts do not know 9001
    assert not (tmp_path / "backups").exists()
    assert db_path(tmp_path).read_bytes() == before


# ---- alembic check: the CI "up to date" guard ---------------------------------------------------


def test_models_match_migrations_at_head(tmp_path: Path) -> None:
    migrate(tmp_path)
    command.check(alembic_config(tmp_path))  # raises if autogenerate would emit anything


def test_alembic_check_fails_when_a_model_lacks_a_migration(tmp_path: Path) -> None:
    migrate(tmp_path)
    unmigrated = Table("unmigrated", Base.metadata, Column("id", Integer, primary_key=True))
    try:
        with pytest.raises(CommandError, match="New upgrade operations detected"):
            command.check(alembic_config(tmp_path))
    finally:
        Base.metadata.remove(unmigrated)
    command.check(alembic_config(tmp_path))  # and passes again once the model is gone


def test_alembic_cli_upgrades_and_checks_using_alembic_ini(tmp_path: Path) -> None:
    """The exact commands CI runs, driven by alembic.ini and FINANCE360_DATA_DIR."""
    env = {**os.environ, "FINANCE360_DATA_DIR": str(tmp_path / "cli")}
    for args in (["upgrade", "head"], ["check"]):
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
    assert _version(tmp_path / "cli") == head_revision()
    engine = make_engine(db_path(tmp_path / "cli"))
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
        with engine.connect() as c:
            assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    finally:
        engine.dispose()


def test_module_entry_point_migrates_and_reports(tmp_path: Path) -> None:
    env = {**os.environ, "FINANCE360_DATA_DIR": str(tmp_path / "mod")}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "finance360.core.migrate"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "(new) -> " in proc.stdout
    assert _version(tmp_path / "mod") == head_revision()
