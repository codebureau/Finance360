"""Provider fetch runs: Prices and FX rates (ADR-0013 section 6).

A provider (``providers.py``) returns canonical candidates; **this module writes them**, with
provenance, and records each run in ``fetch_run`` (mirroring ``backup_run``). Nothing here knows
which provider it is talking to beyond the key it is handed.

Rules that hold for every run:

* **Idempotent.** A candidate for a day that already has a current row from the same source and
  the same value changes nothing; the write is ``INSERT ... ON CONFLICT DO NOTHING`` against the
  partial unique index, so a concurrent writer cannot duplicate a row either.
* **Append-only, manual first.** A revised provider value supersedes the old provider row (kept).
  A ``manual`` row is never read, touched or superseded here: it simply wins the lookup tie
  (``core.price``, ``core.fx``).
* **Nothing fabricated.** Non-trading days are absent, never filled; an invalid bar is dropped,
  never repaired; a currency different from the Instrument's rejects the whole series.
* **A failure never blocks anything.** Each Instrument (or currency) is fetched sequentially with
  a short delay; on an error we back off once, retry once, then record the error and move on. The
  run row carries the outcome; the caller never sees an exception for a failed provider.
* **Locked.** At most one run per dataset is ``running``, enforced by a partial unique index.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from finance360.capabilities.reference import providers
from finance360.capabilities.reference.providers import (
    FxProvider,
    FxQuote,
    PriceBar,
    PriceProvider,
    PriceSeries,
    ProviderError,
)
from finance360.capabilities.reference.trade_dates import earliest_trade_dates
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.db import make_session_factory
from finance360.core.errors import FieldError, ValidationError
from finance360.core.fx import format_rate, parse_rate
from finance360.core.models.account import Account
from finance360.core.models.fetch_run import FetchRun
from finance360.core.models.fx_rate import FxRate
from finance360.core.models.instrument import Instrument, InstrumentIdentifier
from finance360.core.models.price import Price
from finance360.core.models.transaction import Transaction
from finance360.core.models.valuation import Valuation
from finance360.core.money import BASE_CURRENCY, CURRENCY_EXPONENTS, MoneyError
from finance360.core.price import IMPORT_SOURCE, parse_price

log = logging.getLogger(__name__)

#: With no trade date and no stored Price, how far back the first fetch reaches.
FALLBACK_LOOKBACK_DAYS = 365
#: A ``running`` row older than this is a run that died; the next claim closes it.
RUNNING_TIMEOUT = timedelta(hours=1)
#: The scheduler's start-up catch-up: run if the last success is older than this (ADR-0013).
CATCH_UP_AFTER = timedelta(hours=24)
#: Pause between one Instrument's request and the next, to be polite to a free API.
DELAY_SECONDS = 1.0
#: The one back-off before the single retry of a failed request.
BACKOFF_SECONDS = 5.0
ERROR_MAX = 300
BUSY_MESSAGE = "A fetch of this data is already running."

DATASET_LABELS = {"prices": "Prices", "fx": "FX rates"}
#: A run that fetched at least something counts as a success for freshness (a single Instrument
#: with no provider coverage must not make every restart re-fetch). Its failures stay visible.
SUCCESS_STATUSES = ("success", "partial")
ERROR_STATUSES = ("failed", "partial")

Sleep = Callable[[float], object]


class FetchBusy(Exception):
    """Another run of the same dataset is live."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as every timestamp here is stored


def _tidy(text: str) -> str:
    """One line, at most ``ERROR_MAX`` characters: safe to store and show."""
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= ERROR_MAX else one_line[: ERROR_MAX - 1] + "…"


# --- the lock and the history ---------------------------------------------------------------


def claim_in(
    session: Session, dataset: str, provider: str, trigger: str, now: datetime | None = None
) -> int:
    """Take the dataset's lock on the caller's own session; ``FetchBusy`` if a run is live.

    A request must use this, not ``claim``: its auth step may already hold SQLite's write lock in
    the request session, and a second connection would wait out ``busy_timeout`` and fail.
    """
    now = now or _utcnow()
    try:
        with session.begin_nested():
            session.execute(
                update(FetchRun)
                .where(
                    FetchRun.dataset == dataset,
                    FetchRun.status == "running",
                    FetchRun.started_at < now - RUNNING_TIMEOUT,
                )
                .values(
                    status="failed", finished_at=now, error="Interrupted: the run never finished."
                )
            )
            run = FetchRun(
                dataset=dataset,
                provider=provider,
                trigger=trigger,
                started_at=now,
                status="running",
                added=0,
                revised=0,
                detail=[],
            )
            session.add(run)
            session.flush()
            return run.id
    except (IntegrityError, OperationalError):  # a live run, or the database is write-locked
        raise FetchBusy(BUSY_MESSAGE) from None


def claim(engine: Engine, dataset: str, provider: str, trigger: str) -> int:
    """``claim_in`` on its own short transaction (the scheduler)."""
    with make_session_factory(engine).begin() as session:
        return claim_in(session, dataset, provider, trigger)


def fail_interrupted(session: Session, now: datetime | None = None) -> None:
    """At app start nothing can still be running: close rows a restart left open."""
    session.execute(
        update(FetchRun)
        .where(FetchRun.status == "running")
        .values(
            status="failed",
            finished_at=now or _utcnow(),
            error="Interrupted: the app restarted during the run.",
        )
    )


def get_run(session: Session, run_id: int) -> FetchRun | None:
    return session.get(FetchRun, run_id)


def list_runs(session: Session, dataset: str | None = None, limit: int = 30) -> list[FetchRun]:
    """Newest first."""
    query = select(FetchRun).order_by(FetchRun.id.desc()).limit(limit)
    if dataset is not None:
        query = query.where(FetchRun.dataset == dataset)
    return list(session.scalars(query))


@dataclass(frozen=True)
class DatasetState:
    latest: FetchRun | None
    #: The latest run that fetched something (``success`` or ``partial``).
    last_success: FetchRun | None
    #: The latest run that recorded an error (``failed`` or ``partial``).
    last_error: FetchRun | None


def _latest(
    session: Session, dataset: str, statuses: Sequence[str] | None = None
) -> FetchRun | None:
    query = select(FetchRun).where(FetchRun.dataset == dataset)
    if statuses is not None:
        query = query.where(FetchRun.status.in_(statuses))
    return session.scalars(query.order_by(FetchRun.id.desc()).limit(1)).first()


def dataset_state(session: Session, dataset: str) -> DatasetState:
    return DatasetState(
        latest=_latest(session, dataset),
        last_success=_latest(session, dataset, SUCCESS_STATUSES),
        last_error=_latest(session, dataset, ERROR_STATUSES),
    )


def is_due(session: Session, dataset: str, now: datetime | None = None) -> bool:
    """True when a start-up catch-up should run: nothing is running and the last success (or its
    absence) is older than 24 hours."""
    now = now or _utcnow()
    if _latest(session, dataset, ("running",)) is not None:
        return False
    last = _latest(session, dataset, SUCCESS_STATUSES)
    return last is None or last.finished_at is None or now - last.finished_at > CATCH_UP_AFTER


# --- targets --------------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceTarget:
    instrument_id: int
    name: str
    symbol: str
    currency: str
    #: First day to ask for (inclusive).
    start: date


def price_targets(session: Session, provider: PriceProvider, today: date) -> list[PriceTarget]:
    """Instruments that carry this provider's symbol, with where each one's fetch starts.

    * Later runs start **on** the latest stored Price from this source, not the day after: the
      provider's last bar can be provisional, and a revised value supersedes (an unchanged one is
      a no-op), so overlapping by a day keeps the newest Close honest at no cost.
    * The first run starts at the Instrument's earliest current trade date, else a bounded
      look-back (``FALLBACK_LOOKBACK_DAYS``).
    """
    latest = {
        instrument_id: as_at
        for instrument_id, as_at in session.execute(
            select(Price.instrument_id, func.max(Price.as_at))
            .where(Price.source == provider.key, Price.superseded_at.is_(None))
            .group_by(Price.instrument_id)
        )
    }
    trades = earliest_trade_dates(session)
    fallback = today - timedelta(days=FALLBACK_LOOKBACK_DAYS)
    rows = session.execute(
        select(Instrument, InstrumentIdentifier.value)
        .join(InstrumentIdentifier, InstrumentIdentifier.instrument_id == Instrument.id)
        .where(InstrumentIdentifier.scheme == provider.identifier_scheme)
        .order_by(func.lower(Instrument.name), Instrument.id, InstrumentIdentifier.id)
    )
    targets: list[PriceTarget] = []
    seen: set[int] = set()
    for instrument, symbol in rows:
        if instrument.id in seen:  # one symbol per Instrument: the first one entered
            continue
        seen.add(instrument.id)
        start = latest.get(instrument.id) or trades.get(instrument.id) or fallback
        targets.append(
            PriceTarget(instrument.id, instrument.name, symbol, instrument.currency, start)
        )
    return targets


def fx_currencies(session: Session) -> list[str]:
    """Foreign currencies in use by an Account or an Instrument: the only ones we fetch."""
    used = set(session.scalars(select(Account.currency).distinct()))
    used |= set(session.scalars(select(Instrument.currency).distinct()))
    return sorted(c for c in used if c != BASE_CURRENCY and c in CURRENCY_EXPONENTS)


def _fx_start(session: Session, ccy: str, source: str, today: date) -> date:
    """Latest stored rate from this source (overlapping by a day), else the earliest Valuation in
    that currency, else the bounded look-back."""
    latest = session.scalar(
        select(func.max(FxRate.as_at)).where(
            FxRate.from_ccy == ccy,
            FxRate.to_ccy == BASE_CURRENCY,
            FxRate.source == source,
            FxRate.superseded_at.is_(None),
        )
    )
    if latest is not None:
        return latest
    earliest = session.scalar(
        select(func.min(Valuation.as_at)).where(
            Valuation.amount_ccy == ccy, Valuation.superseded_at.is_(None)
        )
    )
    return earliest or today - timedelta(days=FALLBACK_LOOKBACK_DAYS)


def instruments_without_price(session: Session, import_id: int) -> int:
    """How many Instruments an Import's Transactions name that have no current market Price yet:
    the cue to offer "Fetch now" after a commit that brought in new Instruments. Unit prices the
    Import itself recorded (source ``import``) don't count: they are dealing prices, not the
    market series Fetch now brings in."""
    named = select(Transaction.instrument_id).where(
        Transaction.import_id == import_id, Transaction.instrument_id.is_not(None)
    )
    priced = select(Price.instrument_id).where(
        Price.superseded_at.is_(None), Price.source != IMPORT_SOURCE
    )
    return (
        session.scalar(
            select(func.count(func.distinct(Transaction.instrument_id))).where(
                Transaction.instrument_id.in_(named), Transaction.instrument_id.not_in(priced)
            )
        )
        or 0
    )


@dataclass(frozen=True)
class StaleInstrument:
    instrument_id: int
    name: str
    #: The date of its newest current Price from any source; ``None`` if it has none.
    latest_as_at: date | None


def stale_instruments(session: Session, today: date, stale_days: int) -> list[StaleInstrument]:
    """Instruments whose newest Price is older than ``stale_days`` (or that have none)."""
    latest = {
        instrument_id: as_at
        for instrument_id, as_at in session.execute(
            select(Price.instrument_id, func.max(Price.as_at))
            .where(Price.superseded_at.is_(None))
            .group_by(Price.instrument_id)
        )
    }
    cutoff = today - timedelta(days=stale_days)
    found: list[StaleInstrument] = []
    for instrument in session.scalars(
        select(Instrument).order_by(func.lower(Instrument.name), Instrument.id)
    ):
        as_at = latest.get(instrument.id)
        if as_at is None or as_at < cutoff:
            found.append(StaleInstrument(instrument.id, instrument.name, as_at))
    return found


# --- writing (the only place provider rows enter the database) --------------------------------


@dataclass(frozen=True)
class Stored:
    added: int
    revised: int
    #: Candidates dropped as invalid (non-positive, too precise, out of range of the column).
    dropped: int


def _clean_prices(
    bars: Sequence[PriceBar], start: date, end: date
) -> tuple[dict[date, Decimal], int]:
    cleaned: dict[date, Decimal] = {}
    dropped = 0
    for bar in bars:
        if not start <= bar.as_at <= end:
            continue  # outside what was asked for (or in the future): not ours to store
        try:
            cleaned[bar.as_at] = parse_price(format(bar.close, "f"))
        except (MoneyError, ArithmeticError, ValueError):
            dropped += 1
    return cleaned, dropped


def store_prices(
    session: Session,
    context: AuditContext,
    run_id: int,
    provider: PriceProvider,
    target: PriceTarget,
    series: PriceSeries,
    today: date,
) -> Stored:
    """Write one Instrument's fetched Closes. Raises ``ValidationError`` (nothing written) if the
    provider's currency differs from the Instrument's. One audit event for the batch."""
    if series.bars and (series.currency is None or series.currency.strip() != target.currency):
        raise ValidationError(
            [
                FieldError(
                    "currency",
                    f"{provider.name} quoted {target.symbol} in {series.currency or 'no currency'}"
                    f" but {target.name} is priced in {target.currency}. Nothing was stored.",
                )
            ]
        )
    cleaned, dropped = _clean_prices(series.bars, target.start, today)
    if not cleaned:
        return Stored(0, 0, dropped)
    existing = {
        row.as_at: row
        for row in session.scalars(
            select(Price).where(
                Price.instrument_id == target.instrument_id,
                Price.source == provider.key,
                Price.superseded_at.is_(None),
                Price.as_at.between(min(cleaned), max(cleaned)),
            )
        )
    }
    note = f"{provider.name}, symbol {target.symbol}"
    now = _utcnow()
    added = revised = 0
    for as_at in sorted(cleaned):
        value = cleaned[as_at]
        old = existing.get(as_at)
        if old is not None and old.value == value:
            continue  # unchanged: re-running a fetch is a no-op
        if old is not None:  # the provider revised this Close: supersede, keep the old row
            old.superseded_at = now
            session.flush()
        result = session.execute(
            insert(Price)
            .values(
                instrument_id=target.instrument_id,
                as_at=as_at,
                value=value,
                ccy=target.currency,
                source=provider.key,
                provenance_note=note,
                recorded_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["instrument_id", "as_at", "source"],
                index_where=Price.superseded_at.is_(None),
            )
        )
        inserted = result.rowcount == 1  # type: ignore[attr-defined]
        if old is not None and inserted:
            new_id = session.scalar(
                select(Price.id).where(
                    Price.instrument_id == target.instrument_id,
                    Price.as_at == as_at,
                    Price.source == provider.key,
                    Price.superseded_at.is_(None),
                )
            )
            old.superseded_by_id = new_id
            session.flush()
            revised += 1
        elif inserted:
            added += 1
    if added or revised:
        last_id = session.scalar(
            select(func.max(Price.id)).where(
                Price.instrument_id == target.instrument_id, Price.source == provider.key
            )
        )
        first, last = min(cleaned), max(cleaned)
        revised_text = f", {revised} revised" if revised else ""
        audit.record(
            session,
            context,
            entity_type="price",
            entity_id=last_id or 0,
            action="create",
            after={
                "instrument_id": target.instrument_id,
                "source": provider.key,
                "symbol": target.symbol,
                "added": added,
                "revised": revised,
                "from": first.isoformat(),
                "to": last.isoformat(),
                "fetch_run_id": run_id,
            },
            summary=(
                f"Fetched {added} {provider.key} Price(s) for {target.name}, "
                f"{first} to {last}{revised_text}"
            ),
        )
    return Stored(added, revised, dropped)


def store_fx_rates(
    session: Session,
    context: AuditContext,
    run_id: int,
    provider: FxProvider,
    from_ccy: str,
    quotes: Sequence[FxQuote],
    start: date,
    today: date,
) -> Stored:
    """Write one currency's fetched foreign to AUD rates; same rules as ``store_prices``."""
    if from_ccy == BASE_CURRENCY or from_ccy not in CURRENCY_EXPONENTS:
        raise ValidationError([FieldError("from_ccy", f"{from_ccy} is not a foreign currency.")])
    cleaned: dict[date, tuple[Decimal, str]] = {}
    dropped = 0
    for quote in quotes:
        if quote.from_ccy != from_ccy or not start <= quote.as_at <= today:
            continue
        try:
            cleaned[quote.as_at] = (parse_rate(format(quote.rate, "f")), quote.note)
        except (MoneyError, ArithmeticError, ValueError):
            dropped += 1
    if not cleaned:
        return Stored(0, 0, dropped)
    existing = {
        row.as_at: row
        for row in session.scalars(
            select(FxRate).where(
                FxRate.from_ccy == from_ccy,
                FxRate.to_ccy == BASE_CURRENCY,
                FxRate.source == provider.key,
                FxRate.superseded_at.is_(None),
                FxRate.as_at.between(min(cleaned), max(cleaned)),
            )
        )
    }
    now = _utcnow()
    added = revised = 0
    for as_at in sorted(cleaned):
        rate, note = cleaned[as_at]
        old = existing.get(as_at)
        if old is not None and old.rate == rate:
            continue
        if old is not None:
            old.superseded_at = now
            session.flush()
        result = session.execute(
            insert(FxRate)
            .values(
                from_ccy=from_ccy,
                to_ccy=BASE_CURRENCY,
                as_at=as_at,
                rate=rate,
                source=provider.key,
                provenance_note=note,
                recorded_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["from_ccy", "to_ccy", "as_at", "source"],
                index_where=FxRate.superseded_at.is_(None),
            )
        )
        inserted = result.rowcount == 1  # type: ignore[attr-defined]
        if old is not None and inserted:
            old.superseded_by_id = session.scalar(
                select(FxRate.id).where(
                    FxRate.from_ccy == from_ccy,
                    FxRate.to_ccy == BASE_CURRENCY,
                    FxRate.as_at == as_at,
                    FxRate.source == provider.key,
                    FxRate.superseded_at.is_(None),
                )
            )
            session.flush()
            revised += 1
        elif inserted:
            added += 1
    if added or revised:
        last_id = session.scalar(
            select(func.max(FxRate.id)).where(
                FxRate.from_ccy == from_ccy, FxRate.source == provider.key
            )
        )
        first, last = min(cleaned), max(cleaned)
        newest = format_rate(cleaned[last][0])
        audit.record(
            session,
            context,
            entity_type="fx_rate",
            entity_id=last_id or 0,
            action="create",
            after={
                "from_ccy": from_ccy,
                "to_ccy": BASE_CURRENCY,
                "source": provider.key,
                "added": added,
                "revised": revised,
                "from": first.isoformat(),
                "to": last.isoformat(),
                "fetch_run_id": run_id,
            },
            summary=(
                f"Fetched {added} {provider.key} FX rate(s) {from_ccy}->{BASE_CURRENCY}, "
                f"{first} to {last} (latest {newest})" + (f", {revised} revised" if revised else "")
            ),
        )
    return Stored(added, revised, dropped)


# --- running ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetResult:
    """One Instrument's or currency's outcome, kept in ``fetch_run.detail``."""

    target: str
    symbol: str | None
    #: ``ok``, ``error`` (counts against the run) or ``skipped`` (nothing to fetch; not a failure).
    outcome: str
    added: int = 0
    revised: int = 0
    message: str | None = None


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    status: str
    added: int
    revised: int
    error: str | None


def _summarise(results: Sequence[TargetResult], noun: str) -> tuple[str, str | None]:
    attempted = [r for r in results if r.outcome != "skipped"]
    failed = [r for r in attempted if r.outcome == "error"]
    if not failed:
        return "success", None
    status = "failed" if len(failed) == len(attempted) else "partial"
    first = failed[0]
    error = _tidy(
        f"{len(failed)} of {len(attempted)} {noun} failed. {first.target}: {first.message}"
    )
    return status, error


def _finish(
    engine: Engine, run_id: int, status: str, results: Sequence[TargetResult], error: str | None
) -> RunOutcome:
    added = sum(r.added for r in results)
    revised = sum(r.revised for r in results)
    with make_session_factory(engine).begin() as session:
        run = session.get(FetchRun, run_id)
        if run is not None:
            run.status = status
            run.finished_at = _utcnow()
            run.added = added
            run.revised = revised
            run.error = error
            run.detail = [asdict(r) for r in results]
    return RunOutcome(run_id, status, added, revised, error)


def _attempt[T](call: Callable[[], T], sleep: Sleep, backoff: float) -> tuple[T | None, str | None]:
    """Run ``call``; on any error back off once and retry once. (result, None) or (None, why)."""
    message = ""
    for attempt in (1, 2):
        try:
            return call(), None
        except ProviderError as error:
            message = _tidy(str(error)) or "The provider returned an error."
        except Exception as error:  # noqa: BLE001 - a plug-in bug must not stop the run
            log.exception("A provider call failed unexpectedly.")
            message = f"Unexpected {type(error).__name__} from the provider."
        if attempt == 1:
            sleep(backoff)
    return None, message


def _context(provider_key: str, run_id: int) -> AuditContext:
    return AuditContext("system", f"fetch_run:{run_id}", f"fetch:{provider_key}")


def execute_prices(
    engine: Engine,
    run_id: int,
    provider: PriceProvider,
    today: date,
    *,
    sleep: Sleep = time.sleep,
    delay: float = DELAY_SECONDS,
    backoff: float = BACKOFF_SECONDS,
) -> RunOutcome:
    """Fetch and store Closes for every Instrument carrying the provider's symbol, then record the
    outcome on the claimed run. Never raises for a failed provider."""
    factory = make_session_factory(engine)
    results: list[TargetResult] = []
    try:
        with factory() as session:
            targets = price_targets(session, provider, today)
        for index, target in enumerate(targets):
            if index:
                sleep(delay)
            series, why = _attempt(
                lambda t=target: provider.fetch_closes(t.symbol, t.start, today), sleep, backoff
            )
            if series is None:
                results.append(TargetResult(target.name, target.symbol, "error", message=why))
                continue
            try:
                with factory.begin() as session:
                    stored = store_prices(
                        session,
                        _context(provider.key, run_id),
                        run_id,
                        provider,
                        target,
                        series,
                        today,
                    )
            except ValidationError as rejected:
                results.append(
                    TargetResult(
                        target.name,
                        target.symbol,
                        "error",
                        message=_tidy(rejected.errors[0].message),
                    )
                )
                continue
            if not series.bars and not _has_provider_price(factory, target, provider.key):
                results.append(
                    TargetResult(
                        target.name,
                        target.symbol,
                        "error",
                        message=f"{provider.name} returned no data for {target.symbol}."
                        " Check the symbol.",
                    )
                )
                continue
            note = f"{stored.dropped} invalid close(s) dropped" if stored.dropped else None
            results.append(
                TargetResult(target.name, target.symbol, "ok", stored.added, stored.revised, note)
            )
        status, error = _summarise(results, "Instruments")
        return _finish(engine, run_id, status, results, error)
    except Exception as error:  # noqa: BLE001 - recorded, not propagated
        log.exception("Price fetch run %s crashed.", run_id)
        return _finish(
            engine, run_id, "failed", results, _tidy(f"Unexpected {type(error).__name__}.")
        )
    except BaseException:
        _finish(engine, run_id, "failed", results, "Interrupted.")
        raise


def _has_provider_price(factory: Callable[[], Session], target: PriceTarget, source: str) -> bool:
    with factory() as session:
        return (
            session.scalar(
                select(Price.id)
                .where(
                    Price.instrument_id == target.instrument_id,
                    Price.source == source,
                    Price.superseded_at.is_(None),
                )
                .limit(1)
            )
            is not None
        )


def execute_fx(
    engine: Engine,
    run_id: int,
    provider: FxProvider,
    today: date,
    *,
    sleep: Sleep = time.sleep,
    backoff: float = BACKOFF_SECONDS,
) -> RunOutcome:
    """Fetch foreign to AUD rates for the currencies in use (and only those), store them, and
    record the outcome on the claimed run. With no foreign currency in use the provider is not
    called at all."""
    factory = make_session_factory(engine)
    results: list[TargetResult] = []
    try:
        with factory() as session:
            starts = {
                ccy: _fx_start(session, ccy, provider.key, today) for ccy in fx_currencies(session)
            }
        if starts:
            fetched, why = _attempt(
                lambda: provider.fetch_rates(sorted(starts), min(starts.values()), today),
                sleep,
                backoff,
            )
            for ccy in sorted(starts):
                if fetched is None:
                    results.append(TargetResult(ccy, None, "error", message=why))
                elif ccy in fetched.unsupported:
                    results.append(
                        TargetResult(
                            ccy,
                            None,
                            "skipped",
                            message=(
                                f"{provider.name} does not publish {ccy}. Enter rates manually."
                            ),
                        )
                    )
                else:
                    with factory.begin() as session:
                        stored = store_fx_rates(
                            session,
                            _context(provider.key, run_id),
                            run_id,
                            provider,
                            ccy,
                            fetched.quotes,
                            starts[ccy],
                            today,
                        )
                    note = f"{stored.dropped} invalid rate(s) dropped" if stored.dropped else None
                    results.append(
                        TargetResult(ccy, None, "ok", stored.added, stored.revised, note)
                    )
        status, error = _summarise(results, "currencies")
        return _finish(engine, run_id, status, results, error)
    except Exception as error:  # noqa: BLE001 - recorded, not propagated
        log.exception("FX fetch run %s crashed.", run_id)
        return _finish(
            engine, run_id, "failed", results, _tidy(f"Unexpected {type(error).__name__}.")
        )
    except BaseException:
        _finish(engine, run_id, "failed", results, "Interrupted.")
        raise


def execute(
    engine: Engine,
    run_id: int,
    dataset: str,
    provider_key: str,
    today: date,
    *,
    sleep: Sleep = time.sleep,
) -> RunOutcome:
    """Run a claimed run with the registered provider for ``provider_key``. The scheduler and
    "Fetch now" both come through here, so they cannot drift apart."""
    if dataset == "prices" and (price := providers.price_provider(provider_key)) is not None:
        return execute_prices(engine, run_id, price, today, sleep=sleep)
    if dataset == "fx" and (fx := providers.fx_provider(provider_key)) is not None:
        return execute_fx(engine, run_id, fx, today, sleep=sleep)
    return _finish(
        engine, run_id, "failed", [], f"No {dataset} provider is registered as {provider_key!r}."
    )
