"""The daily fetch job (ADR-0013 section 6): off unless configured, a catch-up at start-up when the
last success is over 24 hours old, the scheduled run, and no failure ever blocks the app. Providers
are fakes registered by key; the job is driven with a fake clock where it loops."""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time as clock
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.reference import fetch, providers
from finance360.capabilities.reference.providers import (
    FxFetch,
    PriceBar,
    PriceSeries,
    ProviderError,
)
from finance360.config import Settings
from finance360.core.models.fetch_run import FetchRun
from finance360.jobs import scheduler
from finance360.main import create_app
from support import FakeFxProvider, FakePriceProvider, owner_client

REPO = Path(__file__).resolve().parents[2]


def _settings(data_dir: Path, **extra: object) -> Settings:
    return Settings(data_dir=data_dir, port=8000, git_sha="dev", static_dir=None, **extra)  # type: ignore[arg-type]


def _ok(start: date, end: date) -> PriceSeries:
    return PriceSeries("AUD", (PriceBar(end, Decimal("10.5000")),))


def register(price: FakePriceProvider | None = None, fx: FakeFxProvider | None = None) -> None:
    providers.register_price_provider(
        price or FakePriceProvider({"SBME.AX": _ok, "SMPM.AX": _ok})  # type: ignore[dict-item]
    )
    providers.register_fx_provider(fx or FakeFxProvider(FxFetch(())))


@pytest.fixture
def engine(sample_factory: sessionmaker[Session]) -> Engine:
    return sample_factory.kw["bind"]


def job_settings(engine: Engine) -> Settings:
    return _settings(
        Path(str(engine.url.database)).parent, price_provider="yfinance", fx_provider="rba"
    )


def runs(engine: Engine) -> list[tuple[str, str, str]]:
    with Session(engine) as s:
        rows = s.scalars(select(FetchRun).order_by(FetchRun.id))
        return [(r.dataset, r.trigger, r.status) for r in rows]


def finished_ago(engine: Engine, dataset: str, hours: float) -> None:
    """Record a finished success for ``dataset`` that ended ``hours`` ago."""
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(engine) as s, s.begin():
        run_id = fetch.claim_in(s, dataset, "x", "scheduled", now - timedelta(hours=hours + 1))
        run = fetch.get_run(s, run_id)
        assert run is not None
        run.status, run.finished_at = "success", now - timedelta(hours=hours)


# --- off by default -------------------------------------------------------------------------


def test_nothing_is_scheduled_unless_a_provider_is_configured() -> None:
    assert scheduler.fetch_datasets(_settings(Path("."))) == []
    both = _settings(Path("."), price_provider="yfinance", fx_provider="rba")
    assert scheduler.fetch_datasets(both) == [("prices", "yfinance"), ("fx", "rba")]
    only_fx = _settings(Path("."), fx_provider="rba")
    assert scheduler.fetch_datasets(only_fx) == [("fx", "rba")]


def test_the_app_starts_the_fetch_loop_only_when_a_provider_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[Settings] = []

    async def idle(settings: Settings, engine: Engine) -> None:
        started.append(settings)
        await asyncio.Event().wait()

    monkeypatch.setattr("finance360.main.fetch_loop", idle)
    with owner_client(create_app(_settings(tmp_path / "a"))):
        assert started == []  # the default: the instance never touches the network for data
    configured = _settings(tmp_path / "b", fx_provider="rba", provider_transport="sample")
    with owner_client(create_app(configured)):
        assert started == [configured]


def test_a_default_instance_never_calls_a_provider(tmp_path: Path) -> None:
    """The autouse network guard (conftest) raises if any live transport is reached, so simply
    starting a default instance and using the fetch API proves nothing was fetched."""
    with owner_client(create_app(_settings(tmp_path / "data"))) as client:
        assert client.get("/api/fetch/status").status_code == 200
        assert client.post("/api/fetch/run").status_code == 409
        assert client.get("/api/fetch/runs").json() == []


# --- catch-up and the scheduled run ----------------------------------------------------------


def test_start_up_catches_up_when_nothing_has_ever_succeeded(engine: Engine) -> None:
    register()
    scheduler._run_fetch(job_settings(engine), engine, "catch-up")
    assert runs(engine) == [("prices", "catch-up", "success"), ("fx", "catch-up", "success")]


def test_start_up_catches_up_when_the_last_success_is_over_24_hours_old(engine: Engine) -> None:
    register()
    finished_ago(engine, "prices", hours=25)
    finished_ago(engine, "fx", hours=2)  # fresh: this one is skipped
    scheduler._run_fetch(job_settings(engine), engine, "catch-up")
    assert [r for r in runs(engine) if r[1] == "catch-up"] == [("prices", "catch-up", "success")]


def test_start_up_does_not_fetch_when_the_last_success_is_recent(engine: Engine) -> None:
    price = FakePriceProvider({"SBME.AX": _ok, "SMPM.AX": _ok})  # type: ignore[dict-item]
    fx = FakeFxProvider(FxFetch(()))
    register(price, fx)
    finished_ago(engine, "prices", hours=23)
    finished_ago(engine, "fx", hours=1)
    scheduler._run_fetch(job_settings(engine), engine, "catch-up")
    assert price.calls == [] and fx.calls == []
    assert all(r[1] != "catch-up" for r in runs(engine))


def test_the_scheduled_run_fetches_even_when_the_last_success_is_recent(engine: Engine) -> None:
    register()
    finished_ago(engine, "prices", hours=1)
    scheduler._run_fetch(job_settings(engine), engine, "scheduled")
    assert ("prices", "scheduled", "success") in runs(engine)


def test_a_dataset_that_is_already_running_is_skipped_without_error(engine: Engine) -> None:
    register()
    with Session(engine) as s, s.begin():
        fetch.claim_in(s, "prices", "yfinance", "manual")
    scheduler._run_fetch(job_settings(engine), engine, "scheduled")
    assert ("prices", "scheduled", "success") not in runs(engine)  # skipped, not stacked
    assert ("fx", "scheduled", "success") in runs(engine)  # the other dataset still ran


# --- failures never block --------------------------------------------------------------------


def test_a_provider_that_is_down_is_a_failed_run_not_an_exception(engine: Engine) -> None:
    register(
        FakePriceProvider({"SBME.AX": ProviderError("down"), "SMPM.AX": ProviderError("down")}),
        FakeFxProvider(ProviderError("down")),
    )
    scheduler._fetch_guarded(job_settings(engine), engine, "scheduled")  # must not raise
    assert runs(engine) == [("prices", "scheduled", "failed"), ("fx", "scheduled", "failed")]


def test_a_crash_in_the_job_is_logged_and_does_not_escape(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(fetch, "execute", boom)
    register()
    with caplog.at_level(logging.ERROR):
        scheduler._fetch_guarded(job_settings(engine), engine, "scheduled")
    assert any("will be retried" in r.message for r in caplog.records)


def test_the_loop_catches_up_then_runs_at_the_scheduled_time_and_survives_a_crash(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def record(_settings: Settings, _engine: Engine, trigger: str) -> None:
        calls.append(trigger)
        if trigger == "scheduled":
            raise RuntimeError("crash")  # _fetch_guarded must contain it

    monkeypatch.setattr(scheduler, "_run_fetch", record)
    now = datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
    ticks = 0

    async def sleep(seconds: float) -> None:
        nonlocal now, ticks
        ticks += 1
        if ticks > 150:
            raise asyncio.CancelledError
        now += timedelta(seconds=seconds)

    settings = _settings(Path("."), fx_provider="rba", fetch_time=time(18, 30))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.fetch_loop(settings, engine, clock=lambda: now, sleep=sleep))
    assert calls[0] == "catch-up"
    assert (
        calls.count("scheduled") >= 1
    )  # it ran at the scheduled time, and the crash did not end it
    assert len(calls) >= 2


def test_the_loop_waits_for_the_configured_local_time(engine: Engine) -> None:
    settings = _settings(Path("."), fx_provider="rba", fetch_time=time(18, 30))
    zone = scheduler._zone(sessionmaker(engine))
    after = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)  # 11:00 in Melbourne (UTC+11)
    wake = scheduler.next_run_at(after, settings.fetch_time, zone)
    assert wake == datetime(2026, 10, 20, 7, 30, tzinfo=UTC)  # 18:30 local the same day


# --- the app is never held up -----------------------------------------------------------------


def test_a_provider_that_hangs_at_start_up_does_not_hold_the_app_up(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    app = create_app(_settings(data, price_provider="yfinance", provider_transport="sample"))
    release = threading.Event()
    entered = threading.Event()

    def hang(start: date, end: date) -> PriceSeries:
        entered.set()
        assert release.wait(30)
        return _ok(start, end)

    providers.register_price_provider(
        FakePriceProvider({"SBME.AX": hang, "SMPM.AX": hang})  # type: ignore[dict-item]
    )
    try:
        with owner_client(app) as client:
            assert entered.wait(15), "the start-up catch-up never started"
            began = clock.monotonic()
            assert client.get("/health").status_code == 200  # the app answers while it is stuck
            assert client.get("/api/fetch/status").json()["datasets"][0]["latest"]["status"] == (
                "running"
            )
            assert clock.monotonic() - began < 5
            release.set()
            deadline = clock.monotonic() + 20
            while client.get("/api/fetch/status").json()["datasets"][0]["latest"]["status"] == (
                "running"
            ):
                assert clock.monotonic() < deadline, "the catch-up never finished"
                clock.sleep(0.1)
            latest = client.get("/api/fetch/status").json()["datasets"][0]["latest"]
            assert (latest["trigger"], latest["status"]) == ("catch-up", "success")
    finally:
        release.set()


def test_a_provider_that_is_down_at_start_up_leaves_the_app_working_and_the_error_visible(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    app = create_app(_settings(data, price_provider="yfinance", provider_transport="sample"))
    down = ProviderError("Yahoo Finance could not be read (ConnectionError).")
    providers.register_price_provider(FakePriceProvider({"SBME.AX": down, "SMPM.AX": down}))
    with owner_client(app) as client:
        deadline = clock.monotonic() + 30
        while True:
            latest = client.get("/api/fetch/status").json()["datasets"][0]["latest"]
            if latest and latest["status"] != "running":
                break
            assert clock.monotonic() < deadline, "the catch-up never finished"
            clock.sleep(0.1)
        assert latest["status"] == "failed" and "ConnectionError" in latest["error"]
        assert client.get("/health").status_code == 200
        assert client.get("/api/position").status_code == 200
