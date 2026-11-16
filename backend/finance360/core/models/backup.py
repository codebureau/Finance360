"""Backup run history (ADR-0011). Services live in ``core/backup.py``.

One row per backup (or weekly maintenance) attempt. At most one row can be ``running`` at a time:
a partial unique index makes that the cross-process lock, so the in-app scheduler, the on-demand
route and the ``deploy`` CLI can never overlap. Plain history, not audited data: it is operational
state, not Household figures.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

REASONS = ("nightly", "manual", "pre-deploy", "maintenance")
STATUSES = ("running", "success", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class BackupRun(Base):
    __tablename__ = "backup_run"
    __table_args__ = (
        CheckConstraint(_in("reason", REASONS), name="reason_known"),
        CheckConstraint(_in("status", STATUSES), name="status_known"),
        Index(
            "uq_backup_run_running", "status", unique=True, sqlite_where=text("status = 'running'")
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reason: Mapped[str] = mapped_column(String(20))
    #: UTC, stored naive like every timestamp here.
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10))
    #: The restic snapshot id in the local repo (None for maintenance and for early failures).
    snapshot_id: Mapped[str | None] = mapped_column(String(64))
    #: Bytes read from the source for the snapshot (an integer count, never a float).
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    #: True once the snapshot was copied to the off-site repo.
    shipped: Mapped[bool] = mapped_column(Boolean, default=False)
    #: A short, sanitised message. Never a key, token or full command line.
    error: Mapped[str | None] = mapped_column(String(300))
