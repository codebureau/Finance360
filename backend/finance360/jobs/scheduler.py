"""The in-app scheduler (ADR-0011, ADR-0013): one asyncio task per daily job.

Deliberately not a job framework: a loop per job, sharing the clock arithmetic.

* The nightly Backup runs a catch-up at startup if the last success is over 24 hours old, then
  sleeps until ``FINANCE360_BACKUP_TIME`` in the Household's local time zone, takes the Backup and
  runs the weekly prune and check when they are due.
* The daily fetch of Prices and FX rates (``FINANCE360_FETCH_TIME``, default 18:30) does the same
  with its own 24-hour catch-up. It only exists when a provider is configured, and it calls only
  the reference service.

Every run is a thread (restic and the providers block), and nothing a run does can end a loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household.service import find_household, household_today
from finance360.capabilities.reference import fetch as reference_fetch
from finance360.config import Settings
from finance360.core import backup
from finance360.core.db import make_session_factory
from finance360.core.localtime import DEFAULT_TIME_ZONE, parse_time_zone

log = logging.getLogger(__name__)

#: Re-check the clock this often while waiting, so a host that slept through 02:00 still runs.
_TICK = 900.0


def next_run_at(after: datetime, at: time, zone: ZoneInfo) -> datetime:
    """The next ``at`` (local wall-clock time in ``zone``) strictly after ``after``, as aware UTC.

    ``after`` is aware. A wall time skipped by a DST jump runs just after the gap; a repeated one
    runs once, at its first occurrence.
    """
    local = after.astimezone(zone)
    day = local.date()
    for _ in range(3):
        candidate = datetime.combine(day, at, tzinfo=zone)
        if candidate.astimezone(UTC) > after:
            return candidate.astimezone(UTC)
        day += timedelta(days=1)
    raise AssertionError("unreachable: some day within three is after `after`")


def _zone(session_factory: sessionmaker[Session]) -> ZoneInfo:
    try:
        with session_factory() as session:
            household = find_household(session)
            return parse_time_zone(household.time_zone if household else DEFAULT_TIME_ZONE)
    except Exception:  # noqa: BLE001 - a bad zone must not stop Backups
        return parse_time_zone(DEFAULT_TIME_ZONE)


def _run_due(settings: Settings, engine: Engine, *, nightly: bool) -> None:
    """One wake-up: the nightly Backup (or a catch-up), then maintenance if it is due."""
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        reason = "nightly" if nightly else backup.due_reason(session)
    while reason is not None:
        try:
            result = backup.run_backup(reason, settings, engine)
        except backup.BackupBusy:
            log.warning("Scheduled %s skipped: another run is live.", reason)
            return
        except backup.BackupNotConfigured:
            return
        if not result.ok or reason == "maintenance":
            return
        with session_factory() as session:
            reason = backup.maintenance_due(session)


def _guarded(settings: Settings, engine: Engine, nightly: bool) -> None:
    try:
        _run_due(settings, engine, nightly=nightly)
    except Exception:
        log.exception("The scheduled backup run crashed; it will be retried at the next wake-up.")


async def backup_loop(
    settings: Settings,
    engine: Engine,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> None:
    """Run until cancelled. ``clock`` and ``sleep`` are seams for tests."""
    session_factory = make_session_factory(engine)
    await asyncio.to_thread(_guarded, settings, engine, False)
    while True:
        wake = next_run_at(clock(), settings.backup_time, _zone(session_factory))
        while (remaining := (wake - clock()).total_seconds()) > 0:
            await sleep(min(remaining, _TICK))
        await asyncio.to_thread(_guarded, settings, engine, True)


# --- the daily fetch of Prices and FX rates -----------------------------------------------------


def fetch_datasets(settings: Settings) -> list[tuple[str, str]]:
    """``(dataset, provider key)`` for each dataset that has a provider configured."""
    pairs = (("prices", settings.price_provider), ("fx", settings.fx_provider))
    return [(dataset, key) for dataset, key in pairs if key]


def _run_fetch(settings: Settings, engine: Engine, trigger: str) -> None:
    """One wake-up: fetch each configured dataset (at startup, only if its catch-up is due)."""
    session_factory = make_session_factory(engine)
    for dataset, key in fetch_datasets(settings):
        with session_factory() as session:
            if trigger == "catch-up" and not reference_fetch.is_due(session, dataset):
                continue
            today = household_today(session)
        try:
            run_id = reference_fetch.claim(engine, dataset, key, trigger)
        except reference_fetch.FetchBusy:
            log.warning("Scheduled %s fetch skipped: another run is live.", dataset)
            continue
        reference_fetch.execute(engine, run_id, dataset, key, today)


def _fetch_guarded(settings: Settings, engine: Engine, trigger: str) -> None:
    try:
        _run_fetch(settings, engine, trigger)
    except Exception:
        log.exception("The scheduled fetch crashed; it will be retried at the next wake-up.")


async def fetch_loop(
    settings: Settings,
    engine: Engine,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> None:
    """Run until cancelled. The start-up catch-up runs in a thread, so it never delays start-up
    or the app; a provider that is down only produces a failed run row."""
    session_factory = make_session_factory(engine)
    await asyncio.to_thread(_fetch_guarded, settings, engine, "catch-up")
    while True:
        wake = next_run_at(clock(), settings.fetch_time, _zone(session_factory))
        while (remaining := (wake - clock()).total_seconds()) > 0:
            await sleep(min(remaining, _TICK))
        await asyncio.to_thread(_fetch_guarded, settings, engine, "scheduled")
