"""Provider fetch runs against a real temporary SQLite database (ADR-0013 section 6): Prices and
FX rates are written with provenance, idempotently, append-only, with a manual row always winning,
and every failure is recorded and contained. Providers are fakes; nothing reaches the network."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.position import valuations as position_valuations
from finance360.capabilities.reference import fetch, fx_rates, instruments, prices
from finance360.capabilities.reference.providers import (
    FxFetch,
    FxQuote,
    PriceBar,
    PriceSeries,
    ProviderError,
    ProviderThrottled,
)
from finance360.core.audit import AuditContext
from finance360.core.fx import FxPoint, NoRate
from finance360.core.models.audit import AuditEvent
from finance360.core.models.fetch_run import FetchRun
from finance360.core.models.fx_rate import FxRate
from finance360.core.models.price import Price
from finance360.core.price import PricePoint
from support import FakeFxProvider, FakePriceProvider, no_sleep

TODAY = date(2026, 10, 20)
CTX = AuditContext("owner", "login:1", "api")
ETF, MINING = "SBME.AX", "SMPM.AX"  # the sample household's two Yahoo symbols


@dataclass
class Env:
    factory: sessionmaker[Session]
    engine: Engine

    def instrument_id(self, symbol: str) -> int:
        with self.factory() as s:
            found = instruments.resolve(s, symbol, "yahoo")
            assert not isinstance(found, instruments.Ambiguous) and found is not None
            return found.id

    def current(self, symbol: str, source: str | None = None) -> dict[date, Price]:
        with self.factory() as s:
            query = select(Price).where(
                Price.instrument_id == self.instrument_id(symbol), Price.superseded_at.is_(None)
            )
            if source:
                query = query.where(Price.source == source)
            return {p.as_at: p for p in s.scalars(query)}

    def all_rows(self, model: type[Price] | type[FxRate]) -> list[tuple[object, ...]]:
        """Every row, superseded ones included, as plain tuples (to prove nothing changed)."""
        conn = sqlite3.connect(str(self.engine.url.database))
        try:
            query = f"SELECT * FROM {model.__tablename__} ORDER BY id"  # noqa: S608 - a constant
            return [tuple(r) for r in conn.execute(query)]
        finally:
            conn.close()

    def manual_fx(self) -> list[tuple[object, ...]]:
        with self.factory() as s:
            return [
                (r.id, r.as_at, r.rate, r.provenance_note, r.superseded_at)
                for r in s.scalars(
                    select(FxRate).where(FxRate.source == "manual").order_by(FxRate.id)
                )
            ]


@pytest.fixture
def env(sample_factory: sessionmaker[Session]) -> Env:
    engine = sample_factory.kw["bind"]
    return Env(sample_factory, engine)


def series(*pairs: tuple[str, str], ccy: str | None = "AUD") -> PriceSeries:
    return PriceSeries(ccy, tuple(PriceBar(date.fromisoformat(d), Decimal(v)) for d, v in pairs))


MINING_BARS = series(("2026-10-13", "6.3000"))


def run_prices(
    env: Env, provider: FakePriceProvider, today: date = TODAY, sleeps: list[float] | None = None
) -> fetch.RunOutcome:
    run_id = fetch.claim(env.engine, "prices", provider.key, "manual")
    pause = (lambda seconds: sleeps.append(seconds)) if sleeps is not None else no_sleep
    return fetch.execute_prices(env.engine, run_id, provider, today, sleep=pause)


def run_row(env: Env, run_id: int) -> FetchRun:
    with env.factory() as s:
        run = fetch.get_run(s, run_id)
        assert run is not None
        return run


# --- Prices: a first run -------------------------------------------------------------------


def test_a_first_run_stores_raw_closes_with_the_symbol_as_provenance_and_records_the_run(
    env: Env,
) -> None:
    provider = FakePriceProvider(
        {
            ETF: series(("2026-10-12", "98.1234"), ("2026-10-13", "98.5")),
            MINING: series(("2026-10-12", "6.3304")),
        }
    )
    outcome = run_prices(env, provider)

    assert (outcome.status, outcome.added, outcome.revised, outcome.error) == (
        "success",
        3,
        0,
        None,
    )
    etf = env.current(ETF, "yfinance")
    assert etf[date(2026, 10, 13)].value == Decimal("98.5")
    assert etf[date(2026, 10, 13)].ccy == "AUD"
    assert etf[date(2026, 10, 13)].provenance_note == "Fake Yahoo, symbol SBME.AX"
    run = run_row(env, outcome.run_id)
    assert (run.dataset, run.provider, run.trigger, run.status) == (
        "prices",
        "yfinance",
        "manual",
        "success",
    )
    assert run.finished_at is not None and (run.added, run.revised) == (3, 0)
    assert [(r["target"], r["outcome"], r["added"]) for r in run.detail] == [
        ("Sample Broad Market ETF", "ok", 2),
        ("Sample Mining Ltd", "ok", 1),
    ]


def test_only_instruments_with_the_providers_symbol_are_fetched_and_the_first_start_is_bounded(
    env: Env,
) -> None:
    provider = FakePriceProvider({ETF: series(), MINING: MINING_BARS})
    # No stored provider Price and no trade date yet: both start at the bounded look-back. (The
    # unlisted fund has no yahoo symbol, so it is never asked about.)
    with env.factory() as s:
        targets = fetch.price_targets(s, provider, TODAY)
    assert [(t.name, t.symbol, t.start) for t in targets] == [
        ("Sample Broad Market ETF", ETF, TODAY - timedelta(days=fetch.FALLBACK_LOOKBACK_DAYS)),
        ("Sample Mining Ltd", MINING, TODAY - timedelta(days=fetch.FALLBACK_LOOKBACK_DAYS)),
    ]


def test_requests_are_sequential_with_a_pause_between_instruments(env: Env) -> None:
    provider = FakePriceProvider(
        {ETF: series(("2026-10-13", "1")), MINING: series(("2026-10-13", "2"))}
    )
    sleeps: list[float] = []
    run_prices(env, provider, sleeps=sleeps)
    assert [c[0] for c in provider.calls] == [ETF, MINING]  # one range request per Instrument
    assert sleeps == [fetch.DELAY_SECONDS]  # a pause between the two, none before the first


# --- Prices: idempotent, append-only --------------------------------------------------------


def test_running_the_same_fetch_again_adds_nothing_and_asks_from_the_latest_stored_day(
    env: Env,
) -> None:
    bars = series(("2026-10-12", "98.1"), ("2026-10-13", "98.2"))
    run_prices(env, FakePriceProvider({ETF: bars, MINING: MINING_BARS}))
    before = env.all_rows(Price)

    second_provider = FakePriceProvider({ETF: bars, MINING: MINING_BARS})
    second = run_prices(env, second_provider)

    assert (second.status, second.added, second.revised) == ("success", 0, 0)
    assert env.all_rows(Price) == before  # not one row added, changed or superseded
    # Overlaps the last stored day (so a provisional last bar can be revised), never earlier.
    assert second_provider.calls[0] == (ETF, date(2026, 10, 13), TODAY)


def test_a_revised_close_supersedes_the_old_row_and_keeps_it(env: Env) -> None:
    run_prices(
        env, FakePriceProvider({ETF: series(("2026-10-13", "98.2000")), MINING: MINING_BARS})
    )
    first = env.current(ETF, "yfinance")[date(2026, 10, 13)]

    outcome = run_prices(
        env, FakePriceProvider({ETF: series(("2026-10-13", "98.9000")), MINING: MINING_BARS})
    )

    assert (outcome.added, outcome.revised) == (0, 1)
    now = env.current(ETF, "yfinance")[date(2026, 10, 13)]
    assert now.value == Decimal("98.9") and now.id != first.id
    with env.factory() as s:
        old = s.get(Price, first.id)
        assert old is not None
        assert old.value == Decimal("98.2") and old.superseded_by_id == now.id  # kept, linked
        assert old.superseded_at is not None


def test_a_manual_price_on_the_same_date_wins_and_is_never_touched(env: Env) -> None:
    etf = env.instrument_id(ETF)
    with env.factory.begin() as s:
        manual = prices.add_price(
            s, CTX, etf, prices.PriceInput(date(2026, 10, 13), "123.456789", "Statement")
        )
        manual_id = manual.id
    provider = FakePriceProvider(
        {ETF: series(("2026-10-13", "98.2000"), ("2026-10-14", "98.3000")), MINING: MINING_BARS}
    )
    run_prices(env, provider)

    with env.factory() as s:
        won = prices.price_for(s, etf, date(2026, 10, 13))
        assert isinstance(won, PricePoint)
        assert (won.source, won.value) == ("manual", Decimal("123.456789"))
        assert won.id == manual_id
        row = s.get(Price, manual_id)
        assert row is not None and row.superseded_at is None  # the fetch never touched it
        # the provider's own close for that date is stored, and simply loses the tie
        assert env.current(ETF, "yfinance")[date(2026, 10, 13)].value == Decimal("98.2")
        later = prices.price_for(s, etf, date(2026, 10, 14))
        assert isinstance(later, PricePoint) and later.source == "yfinance"
    # and re-fetching leaves the manual row exactly as it was
    run_prices(env, provider)
    with env.factory() as s:
        row = s.get(Price, manual_id)
        assert row is not None and row.superseded_at is None and row.value == Decimal("123.456789")


def test_each_stored_batch_is_audited_once_with_the_run_in_the_record(env: Env) -> None:
    outcome = run_prices(
        env,
        FakePriceProvider(
            {ETF: series(("2026-10-12", "1"), ("2026-10-13", "2")), MINING: MINING_BARS}
        ),
    )
    with env.factory() as s:
        events = list(s.scalars(select(AuditEvent).where(AuditEvent.entity_type == "price")))
    assert len(events) == 2  # one per Instrument's batch, not one per Price
    event = next(e for e in events if "Broad Market" in e.summary)
    assert (event.actor_kind, event.source, event.action) == ("system", "fetch:yfinance", "create")
    assert event.actor_ref == f"fetch_run:{outcome.run_id}"
    assert event.after is not None
    assert (event.after["added"], event.after["from"], event.after["to"]) == (
        2,
        "2026-10-12",
        "2026-10-13",
    )
    assert (
        event.summary
        == "Fetched 2 yfinance Price(s) for Sample Broad Market ETF, 2026-10-12 to 2026-10-13"
    )


# --- Prices: what the provider gets wrong ---------------------------------------------------


def test_a_close_in_another_currency_is_rejected_and_nothing_is_stored(env: Env) -> None:
    provider = FakePriceProvider(
        {ETF: series(("2026-10-13", "98.2"), ccy="GBp"), MINING: series(("2026-10-13", "6.3"))}
    )
    outcome = run_prices(env, provider)

    assert outcome.status == "partial"
    assert env.current(ETF, "yfinance") == {}
    assert outcome.error is not None and "quoted SBME.AX in GBp" in outcome.error
    assert "priced in AUD" in outcome.error and "Nothing was stored" in outcome.error
    assert env.current(MINING, "yfinance")  # the other Instrument was still fetched


def test_a_provider_that_names_no_currency_is_rejected(env: Env) -> None:
    run_prices(
        env, FakePriceProvider({ETF: series(("2026-10-13", "98.2"), ccy=None), MINING: MINING_BARS})
    )
    assert env.current(ETF, "yfinance") == {}


def test_a_failure_backs_off_once_retries_once_and_then_succeeds(env: Env) -> None:
    provider = FakePriceProvider(
        {
            ETF: [
                ProviderThrottled("Yahoo Finance is rate limiting requests."),
                series(("2026-10-13", "98.2")),
            ],
            MINING: MINING_BARS,
        }
    )
    sleeps: list[float] = []
    outcome = run_prices(env, provider, sleeps=sleeps)
    assert outcome.status == "success"
    assert [c[0] for c in provider.calls] == [ETF, ETF, MINING]
    assert sleeps[0] == fetch.BACKOFF_SECONDS  # exactly one back-off before the one retry
    assert env.current(ETF, "yfinance")


def test_a_failure_that_persists_is_recorded_and_changes_no_stored_price(env: Env) -> None:
    run_prices(
        env,
        FakePriceProvider(
            {ETF: series(("2026-10-13", "98.2")), MINING: series(("2026-10-13", "6.3"))}
        ),
    )
    before = env.all_rows(Price)

    down = ProviderThrottled("Yahoo Finance is rate limiting requests.")
    provider = FakePriceProvider({ETF: down, MINING: series(("2026-10-14", "6.4"))})
    sleeps: list[float] = []
    outcome = run_prices(env, provider, sleeps=sleeps)

    assert outcome.status == "partial"
    assert [c[0] for c in provider.calls] == [ETF, ETF, MINING]  # one retry, then stop that one
    assert outcome.error == (
        "1 of 2 Instruments failed. Sample Broad Market ETF: "
        "Yahoo Finance is rate limiting requests."
    )
    run = run_row(env, outcome.run_id)
    assert [(r["target"], r["outcome"]) for r in run.detail] == [
        ("Sample Broad Market ETF", "error"),
        ("Sample Mining Ltd", "ok"),
    ]
    after = env.all_rows(Price)
    assert after[: len(before)] == before  # every earlier row byte-identical
    assert len(after) == len(before) + 1  # only the other Instrument's new close


def test_when_the_provider_is_down_for_everything_the_run_is_failed_and_the_app_is_untouched(
    env: Env,
) -> None:
    before = env.all_rows(Price)
    provider = FakePriceProvider({ETF: ProviderError("down"), MINING: ProviderError("down")})
    outcome = run_prices(env, provider)
    assert (outcome.status, outcome.added) == ("failed", 0)
    assert env.all_rows(Price) == before
    assert (
        run_row(env, outcome.run_id).finished_at is not None
    )  # the run is closed, not left running


def test_a_plugin_bug_is_contained_and_reported_without_its_message(env: Env) -> None:
    provider = FakePriceProvider(
        {ETF: RuntimeError("secret internals /home/x"), MINING: series(("2026-10-13", "6.3"))}
    )
    outcome = run_prices(env, provider)
    assert outcome.status == "partial"
    assert outcome.error is not None and "Unexpected RuntimeError" in outcome.error
    assert "secret" not in outcome.error


def test_a_first_fetch_that_returns_nothing_is_an_error_but_a_quiet_day_is_not(env: Env) -> None:
    first = run_prices(
        env, FakePriceProvider({ETF: series(), MINING: series(("2026-10-13", "6.3"))})
    )
    assert first.status == "partial"
    assert first.error is not None and "returned no data for SBME.AX" in first.error

    run_prices(env, FakePriceProvider({ETF: series(("2026-10-13", "98.2")), MINING: series()}))
    # Weekend / holiday: the provider has no bar in the asked range, and the Instrument already
    # has provider Prices, so this is a success with nothing to add (and nothing invented).
    weekend = run_prices(env, FakePriceProvider({ETF: series(), MINING: series()}))
    assert (weekend.status, weekend.added, weekend.error) == ("success", 0, None)
    assert max(env.current(ETF, "yfinance")) == date(2026, 10, 13)  # not forward-filled


def test_unusable_bars_are_dropped_and_bars_outside_the_asked_range_are_ignored(env: Env) -> None:
    bad = PriceSeries(
        "AUD",
        (
            PriceBar(date(2026, 10, 12), Decimal("0")),  # not above zero
            PriceBar(date(2026, 10, 13), Decimal("1.1234567")),  # more than 6 places: not rounded
            PriceBar(date(2026, 10, 14), Decimal("NaN")),
            PriceBar(date(2026, 10, 15), Decimal("98.1")),  # fine
            PriceBar(TODAY + timedelta(days=1), Decimal("99")),  # the future
            PriceBar(TODAY - timedelta(days=fetch.FALLBACK_LOOKBACK_DAYS + 5), Decimal("50")),
        ),
    )
    outcome = run_prices(env, FakePriceProvider({ETF: bad, MINING: MINING_BARS}))
    assert set(env.current(ETF, "yfinance")) == {date(2026, 10, 15)}
    detail = run_row(env, outcome.run_id).detail
    assert detail[0]["message"] == "3 invalid close(s) dropped"


def test_an_unregistered_provider_key_fails_the_run_cleanly(env: Env) -> None:
    run_id = fetch.claim(env.engine, "prices", "nonesuch", "manual")
    outcome = fetch.execute(env.engine, run_id, "prices", "nonesuch", TODAY)
    assert outcome.status == "failed" and "No prices provider is registered" in str(outcome.error)


# --- the run lock and history ---------------------------------------------------------------


def test_only_one_run_per_dataset_can_be_live_and_a_dead_one_is_taken_over(env: Env) -> None:
    now = datetime(2026, 10, 20, 8, 0)
    with env.factory.begin() as s:
        first = fetch.claim_in(s, "prices", "yfinance", "manual", now)
    with env.factory.begin() as s:
        with pytest.raises(fetch.FetchBusy):
            fetch.claim_in(s, "prices", "yfinance", "scheduled", now + timedelta(minutes=5))
        other = fetch.claim_in(s, "fx", "rba", "manual", now)  # a different dataset is independent
    assert other != first
    with env.factory.begin() as s:  # the first never finished: an hour on, it is taken over
        again = fetch.claim_in(
            s, "prices", "yfinance", "scheduled", now + fetch.RUNNING_TIMEOUT * 2
        )
    with env.factory() as s:
        old = s.get(FetchRun, first)
        assert old is not None and old.status == "failed" and "never finished" in str(old.error)
        assert again != first


def test_two_threads_claiming_at_once_get_exactly_one_run(env: Env) -> None:
    results: list[object] = []
    barrier = threading.Barrier(2)

    def claim() -> None:
        barrier.wait()
        try:
            results.append(fetch.claim(env.engine, "prices", "yfinance", "manual"))
        except fetch.FetchBusy as busy:
            results.append(busy)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(isinstance(r, int) for r in results) == 1
    assert sum(isinstance(r, fetch.FetchBusy) for r in results) == 1
    with env.factory() as s:
        assert s.scalar(select(func.count()).select_from(FetchRun)) == 1


def test_a_restart_closes_runs_it_left_open(env: Env) -> None:
    with env.factory.begin() as s:
        run_id = fetch.claim_in(s, "fx", "rba", "scheduled")
    with env.factory.begin() as s:
        fetch.fail_interrupted(s)
    run = run_row(env, run_id)
    assert run.status == "failed" and run.error == "Interrupted: the app restarted during the run."


def test_catch_up_is_due_when_the_last_success_is_over_24_hours_old(env: Env) -> None:
    now = datetime(2026, 10, 20, 8, 0)
    with env.factory() as s:
        assert fetch.is_due(s, "prices", now)  # never succeeded
    with env.factory.begin() as s:
        run_id = fetch.claim_in(s, "prices", "yfinance", "manual", now - timedelta(hours=30))
        run = fetch.get_run(s, run_id)
        assert run is not None
        run.status, run.finished_at = "success", now - timedelta(hours=25)
    with env.factory() as s:
        assert fetch.is_due(s, "prices", now)
        assert not fetch.is_due(s, "prices", now - timedelta(hours=2))  # 23 hours old: fresh
        assert fetch.is_due(s, "fx", now)  # another dataset has its own history
    with env.factory.begin() as s:  # a live run means not due, whatever the age
        fetch.claim_in(s, "prices", "yfinance", "manual", now)
    with env.factory() as s:
        assert not fetch.is_due(s, "prices", now)


def test_a_partial_run_counts_as_a_success_and_a_failed_one_does_not(env: Env) -> None:
    run_prices(
        env, FakePriceProvider({ETF: ProviderError("x"), MINING: series(("2026-10-13", "6.3"))})
    )
    with env.factory() as s:
        state = fetch.dataset_state(s, "prices")
    assert state.last_success is not None and state.last_success.status == "partial"
    assert state.last_error is not None and state.last_error.id == state.last_success.id
    run_prices(env, FakePriceProvider({ETF: ProviderError("x"), MINING: ProviderError("x")}))
    with env.factory() as s:
        state = fetch.dataset_state(s, "prices")
    assert state.latest is not None and state.latest.status == "failed"
    assert state.last_success is not None and state.last_success.status == "partial"
    assert state.last_error is not None and state.last_error.id == state.latest.id


def test_stale_instruments_are_those_with_no_price_or_one_older_than_the_limit(env: Env) -> None:
    run_prices(
        env,
        FakePriceProvider(
            {ETF: series(("2026-10-19", "98.2")), MINING: series(("2026-10-01", "6.3"))}
        ),
    )
    with env.factory() as s:
        stale = fetch.stale_instruments(s, TODAY, 7)
    by_name = {x.name: x.latest_as_at for x in stale}
    assert by_name == {
        "Sample Mining Ltd": date(2026, 10, 1),
        "Sample Balanced Growth Fund": None,
        "Sample Global Equity ETF": None,  # no provider symbol, so nothing fetched
        "Sample Diversified Fund": None,  # only a manual Price, so the provider has none
        "Sample Super Growth Option": None,  # super options have no Price at all
        "Sample Super Balanced Option": None,
        "Sample Super High Growth Option": None,
    }


# --- concurrency: the unique index holds ---------------------------------------------------


def test_concurrent_writers_of_the_same_closes_leave_exactly_one_current_row_per_day(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the race: every writer reads "nothing stored yet" and only then do all of them write.
    # (``store_prices`` takes the time right after that read and right before its first insert.)
    meet = threading.Barrier(4)
    real_now = fetch._utcnow
    seen = threading.local()

    def rendezvous() -> datetime:
        if not getattr(seen, "met", False):
            seen.met = True
            meet.wait(timeout=10)
        return real_now()

    monkeypatch.setattr(fetch, "_utcnow", rendezvous)
    etf = env.instrument_id(ETF)
    days = [TODAY - timedelta(days=30 - n) for n in range(30)]
    bars = PriceSeries("AUD", tuple(PriceBar(d, Decimal("98.1234")) for d in days))
    with env.factory() as s:
        target = fetch.PriceTarget(etf, "Sample Broad Market ETF", ETF, "AUD", days[0])
    provider = FakePriceProvider({})
    added: list[int] = []
    barrier = threading.Barrier(4)

    def write() -> None:
        barrier.wait()
        with env.factory.begin() as s:
            added.append(fetch.store_prices(s, CTX, 1, provider, target, bars, TODAY).added)

    threads = [threading.Thread(target=write) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert len(added) == 4, "a writer crashed (a unique violation must be absorbed, not raised)"
    assert sum(added) == len(days)  # each day written once, by exactly one of the writers
    current = env.current(ETF, "yfinance")
    assert sorted(current) == days
    with env.factory() as s:
        total = s.scalar(
            select(func.count())
            .select_from(Price)
            .where(Price.instrument_id == etf, Price.source == "yfinance")
        )
    assert total == len(days)  # and no superseded duplicates either


# --- FX -------------------------------------------------------------------------------------


def quotes(ccy: str, *pairs: tuple[str, str], note: str = "published A$1 = 0.4902 GBP") -> FxFetch:
    return FxFetch(tuple(FxQuote(ccy, date.fromisoformat(d), Decimal(r), note) for d, r in pairs))


def run_fx(
    env: Env, provider: FakeFxProvider, sleeps: list[float] | None = None
) -> fetch.RunOutcome:
    run_id = fetch.claim(env.engine, "fx", provider.key, "manual")
    pause = (lambda seconds: sleeps.append(seconds)) if sleeps is not None else no_sleep
    return fetch.execute_fx(env.engine, run_id, provider, TODAY, sleep=pause)


def fx_current(env: Env, ccy: str = "GBP") -> dict[date, FxRate]:
    with env.factory() as s:
        query = select(FxRate).where(FxRate.from_ccy == ccy, FxRate.superseded_at.is_(None))
        return {r.as_at: r for r in s.scalars(query)}


def test_only_currencies_in_use_are_asked_for(env: Env) -> None:
    with env.factory() as s:
        assert fetch.fx_currencies(s) == ["GBP"]  # the sample UK pension; nothing else is foreign
    provider = FakeFxProvider(quotes("GBP", ("2026-10-19", "2.0400000000")))
    run_fx(env, provider)
    assert [c[0] for c in provider.calls] == [("GBP",)]  # USD, EUR, ... are not requested

    with env.factory.begin() as s:  # an Instrument priced in USD brings USD into use
        instruments.create_instrument(
            s, CTX, instruments.InstrumentInput("Sample US Fund", "fund", "USD", None, ())
        )
        assert fetch.fx_currencies(s) == ["GBP", "USD"]


def test_with_no_foreign_currency_in_use_the_provider_is_not_called_at_all(env: Env) -> None:
    with env.factory.begin() as s:
        from finance360.core.models.account import Account

        for account in s.scalars(select(Account).where(Account.currency != "AUD")):
            account.currency = "AUD"
    provider = FakeFxProvider(ProviderError("must not be called"))
    outcome = run_fx(env, provider)
    assert (outcome.status, outcome.added, provider.calls) == ("success", 0, [])


def test_rates_are_stored_foreign_to_aud_with_the_published_figure_as_provenance(env: Env) -> None:
    provider = FakeFxProvider(
        quotes("GBP", ("2026-10-19", "2.0399836801"), ("2026-10-20", "2.0408163265"))
    )
    outcome = run_fx(env, provider)
    assert (outcome.status, outcome.added) == ("success", 2)
    rate = fx_current(env)[date(2026, 10, 19)]
    assert (rate.from_ccy, rate.to_ccy, rate.source) == ("GBP", "AUD", "rba")
    assert rate.rate == Decimal("2.0399836801")
    assert rate.provenance_note == "published A$1 = 0.4902 GBP"
    with env.factory() as s:
        event = s.scalars(select(AuditEvent).where(AuditEvent.entity_type == "fx_rate")).all()[-1]
    assert (event.actor_kind, event.source) == ("system", "fetch:rba")


def test_the_first_fx_start_is_the_earliest_valuation_in_that_currency_then_the_latest_rate(
    env: Env,
) -> None:
    with env.factory.begin() as s:  # the sample UK pension's Valuations, in GBP
        position_valuations.load_fixture_valuations(s, Path(str(env.engine.url.database)).parent)
    provider = FakeFxProvider(quotes("GBP", ("2026-10-19", "2.04")))
    run_fx(env, provider)
    first_start = provider.calls[0][1]
    assert first_start < date(2025, 1, 1)  # the UK pension's valuations begin in 2024

    again = FakeFxProvider(quotes("GBP", ("2026-10-19", "2.04")))
    run_fx(env, again)
    assert again.calls[0][1] == date(2026, 10, 19)  # overlaps the latest stored rba rate only


def test_a_manual_rate_on_the_same_date_wins_and_a_newer_provider_rate_beats_an_older_manual_one(
    env: Env,
) -> None:
    with env.factory.begin() as s:
        fx_rates.add_fx_rate(
            s, CTX, fx_rates.FxRateInput("GBP", date(2026, 10, 19), "2.5000", "Bank statement")
        )
    before_manual = env.manual_fx()

    run_fx(env, FakeFxProvider(quotes("GBP", ("2026-10-19", "2.0400"), ("2026-10-20", "2.0500"))))

    with env.factory() as s:
        same_day = fx_rates.rate_for(s, "GBP", "AUD", date(2026, 10, 19))
        assert isinstance(same_day, FxPoint) and (same_day.source, same_day.rate) == (
            "manual",
            Decimal("2.5"),
        )
        newer = fx_rates.rate_for(s, "GBP", "AUD", date(2026, 10, 20))
        assert isinstance(newer, FxPoint) and (newer.source, newer.rate) == ("rba", Decimal("2.05"))
        converted = fx_rates.convert_on(s, _gbp("100.00"), "AUD", date(2026, 10, 20))
        assert not isinstance(converted, NoRate) and converted.rate_source == "rba"
    assert env.manual_fx() == before_manual  # untouched


def _gbp(amount: str):  # noqa: ANN202
    from finance360.core.money import parse_amount

    return parse_amount(amount, "GBP")


def test_rerunning_the_fx_fetch_adds_nothing_and_a_revised_rate_supersedes(env: Env) -> None:
    run_fx(env, FakeFxProvider(quotes("GBP", ("2026-10-19", "2.0400"))))
    before = env.all_rows(FxRate)
    again = run_fx(env, FakeFxProvider(quotes("GBP", ("2026-10-19", "2.0400"))))
    assert (again.added, again.revised) == (0, 0) and env.all_rows(FxRate) == before

    revised = run_fx(env, FakeFxProvider(quotes("GBP", ("2026-10-19", "2.0450"))))
    assert (revised.added, revised.revised) == (0, 1)
    assert fx_current(env)[date(2026, 10, 19)].rate == Decimal("2.045")
    with env.factory() as s:
        old = s.scalars(
            select(FxRate).where(FxRate.source == "rba", FxRate.rate == Decimal("2.04"))
        ).one()
        assert old.superseded_at is not None  # kept, marked superseded
        assert old.superseded_by_id == fx_current(env)[date(2026, 10, 19)].id


def test_a_currency_the_provider_does_not_publish_is_skipped_not_failed(env: Env) -> None:
    with env.factory.begin() as s:
        instruments.create_instrument(
            s, CTX, instruments.InstrumentInput("Sample Swiss Fund", "fund", "CHF", None, ())
        )
    provider = FakeFxProvider(
        FxFetch(quotes("GBP", ("2026-10-19", "2.04")).quotes, unsupported=("CHF",))
    )
    outcome = run_fx(env, provider)
    assert (outcome.status, outcome.added) == ("success", 1)
    detail = {r["target"]: r for r in run_row(env, outcome.run_id).detail}
    assert (
        detail["CHF"]["outcome"] == "skipped" and "does not publish CHF" in detail["CHF"]["message"]
    )


def test_a_provider_failure_is_retried_once_then_recorded_and_changes_no_rate(env: Env) -> None:
    before = env.all_rows(FxRate)
    provider = FakeFxProvider(ProviderError("The RBA could not be reached (URLError)."))
    sleeps: list[float] = []
    outcome = run_fx(env, provider, sleeps)
    assert outcome.status == "failed"
    assert outcome.error is not None and "The RBA could not be reached" in outcome.error
    assert len(provider.calls) == 2 and sleeps == [fetch.BACKOFF_SECONDS]
    assert env.all_rows(FxRate) == before


def test_an_invalid_published_rate_is_dropped_never_repaired(env: Env) -> None:
    bad = FxFetch(
        (
            FxQuote("GBP", date(2026, 10, 19), Decimal("0"), "x"),
            FxQuote("GBP", date(2026, 10, 20), Decimal("2.12345678901"), "x"),  # 11 places
            FxQuote("GBP", date(2026, 10, 18), Decimal("2.05"), "ok"),
        )
    )
    outcome = run_fx(env, FakeFxProvider(bad))
    assert outcome.added == 1 and set(fx_current(env)) == {date(2026, 10, 18)}


def test_a_fetched_rate_is_overridden_by_a_manual_one_never_edited_or_removed(env: Env) -> None:
    from finance360.core.errors import ValidationError

    run_fx(env, FakeFxProvider(quotes("GBP", ("2026-10-19", "2.0400"))))
    fetched = fx_current(env)[date(2026, 10, 19)]
    for attempt in (
        lambda s: fx_rates.correct_fx_rate(
            s, CTX, fetched.id, fx_rates.FxRateInput("GBP", date(2026, 10, 19), "2.1", "Mine")
        ),
        lambda s: fx_rates.remove_fx_rate(s, CTX, fetched.id),
    ):
        with env.factory.begin() as s, pytest.raises(ValidationError) as caught:
            attempt(s)
        assert "To override a rba rate, add a manual one" in caught.value.errors[0].message
    assert fx_current(env)[date(2026, 10, 19)].id == fetched.id  # still there, still current
    with env.factory.begin() as s:  # adding a manual rate for that date is the way, and it wins
        fx_rates.add_fx_rate(
            s, CTX, fx_rates.FxRateInput("GBP", date(2026, 10, 19), "2.1000", "Bank statement")
        )
    with env.factory() as s:
        found = fx_rates.rate_for(s, "GBP", "AUD", date(2026, 10, 19))
        assert isinstance(found, FxPoint) and found.source == "manual"


# --- the first fetch starts at the earliest current trade date ------------------------------


def _row(s: Session, instrument_id: int, on: date, *, superseded: bool = False, cash: bool = False):  # noqa: ANN202
    from finance360.core.models.account import Account
    from finance360.core.models.transaction import Transaction

    account = s.scalars(select(Account).where(Account.name == "Sample Brokerage account")).one()
    now = datetime(2026, 10, 1)
    row = Transaction(
        account_id=account.id,
        stream="cash" if cash else "trade",
        import_id=None,
        occurred_on=on,
        kind="deposit" if cash else "buy",
        instrument_id=None if cash else instrument_id,
        quantity=None if cash else Decimal("10"),
        amount_minor=100 if cash else None,
        amount_ccy="AUD" if cash else None,
        recorded_at=now,
        superseded_at=now if superseded else None,
    )
    s.add(row)
    s.flush()
    return row


def _starts(env: Env) -> dict[str, date]:
    provider = FakePriceProvider({ETF: series(), MINING: MINING_BARS})
    with env.factory() as s:
        return {t.symbol: t.start for t in fetch.price_targets(s, provider, TODAY)}


def test_a_first_fetch_starts_on_the_instruments_earliest_current_trade_date(env: Env) -> None:
    etf = env.instrument_id(ETF)
    with env.factory.begin() as s:
        _row(s, etf, date(2026, 3, 9))
        _row(s, etf, date(2026, 5, 1))
    starts = _starts(env)
    assert starts[ETF] == date(2026, 3, 9)  # its earliest trade
    assert starts[MINING] == TODAY - timedelta(days=fetch.FALLBACK_LOOKBACK_DAYS)  # no trades


def test_superseded_trades_and_cash_rows_do_not_set_the_start(env: Env) -> None:
    etf = env.instrument_id(ETF)
    with env.factory.begin() as s:
        _row(s, etf, date(2026, 1, 5), superseded=True)  # replaced by a later Import
        _row(s, etf, date(2026, 4, 2))
        _row(s, etf, date(2026, 2, 2), cash=True)  # a cash row names no Instrument
    assert _starts(env)[ETF] == date(2026, 4, 2)


def test_a_stored_provider_price_still_beats_the_trade_date_for_later_runs(env: Env) -> None:
    etf = env.instrument_id(ETF)
    with env.factory.begin() as s:
        _row(s, etf, date(2026, 3, 9))
    run_prices(env, FakePriceProvider({ETF: series(("2026-10-13", "98.2")), MINING: MINING_BARS}))
    assert _starts(env)[ETF] == date(2026, 10, 13)
