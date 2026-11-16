"""Invariants of storing provider closes, for any series a provider might return (Hypothesis, on a
real migrated SQLite database): re-running is a no-op, the stored rows are exactly the valid bars,
nothing outside the asked range is stored, and a manual Price on a date always wins."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.reference import fetch, instruments, prices
from finance360.capabilities.reference.providers import PriceBar, PriceSeries
from finance360.core.audit import AuditContext
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import migrate
from finance360.core.models.price import Price
from finance360.core.price import PricePoint
from support import FakePriceProvider

CTX = AuditContext("owner", "login:1", "api")
TODAY = date(2026, 10, 20)
START = TODAY - timedelta(days=40)
PROVIDER = FakePriceProvider({})


@pytest.fixture(scope="module")
def factory(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sessionmaker[Session]]:
    data = tmp_path_factory.mktemp("props")
    migrate(Path(data))
    engine = make_engine(db_path(Path(data)))
    yield make_session_factory(engine)
    engine.dispose()


bar_dates = st.integers(-10, 45).map(lambda n: START + timedelta(days=n))  # some out of range
#: Valid at scale 6, plus the awkward ones a provider could send: zero, negative, 7 places.
values = st.one_of(
    st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("5000"), places=4),
    st.sampled_from([Decimal(0), Decimal("-1.5"), Decimal("1.2345678"), Decimal("NaN")]),
)
bars = st.lists(st.builds(PriceBar, bar_dates, values), max_size=25)


def _new_instrument(factory: sessionmaker[Session], n: int) -> fetch.PriceTarget:
    with factory.begin() as s:
        inst = instruments.create_instrument(
            s, CTX, instruments.InstrumentInput(f"Property Fund {n}", "etf", "AUD", None, ())
        )
        return fetch.PriceTarget(inst.id, inst.name, f"P{n}.AX", "AUD", START)


def _expected(series_bars: list[PriceBar]) -> dict[date, Decimal]:
    """The last valid bar per in-range date: what a correct store must leave behind."""
    out: dict[date, Decimal] = {}
    for bar in series_bars:
        if not START <= bar.as_at <= TODAY:
            continue
        if bar.close.is_nan() or bar.close <= 0 or -bar.close.as_tuple().exponent > 6:  # type: ignore[operator]
            continue
        out[bar.as_at] = bar.close
    return out


def _stored(factory: sessionmaker[Session], target: fetch.PriceTarget) -> dict[date, Decimal]:
    with factory() as s:
        rows = s.scalars(
            select(Price).where(
                Price.instrument_id == target.instrument_id, Price.superseded_at.is_(None)
            )
        )
        return {p.as_at: p.value for p in rows}


_counter = iter(range(1, 10**6))


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(series_bars=bars)
def test_storing_any_series_leaves_exactly_the_valid_in_range_bars_and_rerunning_is_a_no_op(
    factory: sessionmaker[Session], series_bars: list[PriceBar]
) -> None:
    target = _new_instrument(factory, next(_counter))
    series = PriceSeries("AUD", tuple(series_bars))

    with factory.begin() as s:
        first = fetch.store_prices(s, CTX, 1, PROVIDER, target, series, TODAY)
    expected = _expected(series_bars)
    assert _stored(factory, target) == expected
    assert first.added == len(expected) and first.revised == 0

    with factory.begin() as s:
        again = fetch.store_prices(s, CTX, 1, PROVIDER, target, series, TODAY)
    assert (again.added, again.revised) == (0, 0)  # idempotent
    assert _stored(factory, target) == expected


@settings(
    max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(series_bars=bars, manual_offset=st.integers(0, 40), manual_value=st.integers(1, 10**6))
def test_a_manual_price_always_wins_its_date_whatever_the_provider_sends(
    factory: sessionmaker[Session],
    series_bars: list[PriceBar],
    manual_offset: int,
    manual_value: int,
) -> None:
    target = _new_instrument(factory, next(_counter))
    day = START + timedelta(days=manual_offset)
    value = f"{manual_value}.123456"
    with factory.begin() as s:
        prices.add_price(s, CTX, target.instrument_id, prices.PriceInput(day, value, "Checked"))
        fetch.store_prices(
            s, CTX, 1, PROVIDER, target, PriceSeries("AUD", tuple(series_bars)), TODAY
        )
        fetch.store_prices(
            s, CTX, 1, PROVIDER, target, PriceSeries("AUD", tuple(series_bars)), TODAY
        )
    with factory() as s:
        found = prices.price_for(s, target.instrument_id, day)
    assert isinstance(found, PricePoint)
    assert (found.source, found.as_at, found.value) == ("manual", day, Decimal(value))
