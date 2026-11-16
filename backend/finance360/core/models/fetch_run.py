"""Fetch run history (ADR-0013 sections 2 and 6). Services:
``capabilities/reference/fetch.py``.

One row per provider run per dataset (``prices`` or ``fx``), mirroring ``backup_run``. At most one
row per dataset can be ``running``: a partial unique index makes that the cross-process lock, so
the daily job and "Fetch now" can never overlap. Plain operational history, not audited Household
data: what a run wrote is audited through the Prices and FX rates themselves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

DATASETS = ("prices", "fx")
TRIGGERS = ("scheduled", "catch-up", "manual")
#: ``partial``: some targets (Instruments or currencies) fetched, some failed.
STATUSES = ("running", "success", "partial", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class FetchRun(Base):
    __tablename__ = "fetch_run"
    __table_args__ = (
        CheckConstraint(_in("dataset", DATASETS), name="dataset_known"),
        CheckConstraint(_in("trigger", TRIGGERS), name="trigger_known"),
        CheckConstraint(_in("status", STATUSES), name="status_known"),
        Index(
            "uq_fetch_run_running",
            "dataset",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset: Mapped[str] = mapped_column(String(10))
    #: The provider's registry key, e.g. ``yfinance`` or ``rba``.
    provider: Mapped[str] = mapped_column(String(20))
    trigger: Mapped[str] = mapped_column(String(10))
    #: UTC, stored naive like every timestamp here.
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10))
    #: Rows written this run (new Prices or FX rates).
    added: Mapped[int] = mapped_column(default=0)
    #: Rows whose value the provider revised (the old row is superseded, never altered).
    revised: Mapped[int] = mapped_column(default=0)
    #: A short, sanitised summary of what failed. Never more than 300 characters.
    error: Mapped[str | None] = mapped_column(String(300))
    #: Per-target results: a list of ``{target, symbol, outcome, added, revised, message}``.
    detail: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
