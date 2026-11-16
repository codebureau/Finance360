"""SQLAlchemy 2 setup: declarative base, engine and sessions on a WAL-mode SQLite file."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

#: Stable constraint names so Alembic can alter them (SQLite needs batch mode for that).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

DB_FILENAME = "finance360.sqlite"


class Base(DeclarativeBase):
    """Base for every table model in ``core`` (the metadata Alembic compares against)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def db_path(data_dir: Path) -> Path:
    return data_dir / DB_FILENAME


def _configure_connection(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(path: Path) -> Engine:
    """Engine for the SQLite file at ``path`` (created if missing), WAL mode, foreign keys on."""
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", _configure_connection)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
