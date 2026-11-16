"""Alembic environment. The target is ``finance360.core.db.Base.metadata``.

Programmatic runs (``finance360.core.migrate``) pass ``config.attributes["data_dir"]``. The CLI
(``alembic upgrade head`` / ``alembic check``) falls back to ``FINANCE360_DATA_DIR`` like the app.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from alembic import context
from sqlalchemy import Engine

from finance360.config import Settings
from finance360.core.db import Base, db_path, make_engine

import_module("finance360.core.models")  # registers every table on Base.metadata


def _engine() -> Engine:
    data_dir = context.config.attributes.get("data_dir")
    directory = Path(data_dir) if data_dir else Settings.from_env().data_dir
    directory.mkdir(parents=True, exist_ok=True)
    return make_engine(db_path(directory))


def run_migrations_online() -> None:
    engine = _engine()
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                render_as_batch=True,  # SQLite cannot ALTER most things in place
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported.")
run_migrations_online()
