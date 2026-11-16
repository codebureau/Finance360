"""Derived Holdings and cash, and Opening-position detection and entry (ADR-0013 sections 3 and 7).

Unit and Hypothesis tests on the pure replay; integration tests against a real migrated temporary
SQLite and the synthetic household; one golden test against the committed sample history; and the
Opening-position endpoint's validation over HTTP. The real-server test is in
``test_opening_real_server.py``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.imports import commit, opening, service, transactions
from finance360.capabilities.reference import instruments as reference
from finance360.config import Settings
from finance360.core import transaction as core
from finance360.core.audit import AuditContext
from finance360.core.errors import ValidationError
from finance360.core.models.account import Account
from finance360.core.models.audit import AuditEvent
from finance360.core.models.imports import Import
from finance360.core.models.instrument import Instrument
from finance360.core.models.transaction import Transaction
from finance360.core.money import Money
from finance360.main import create_app
from support import TestClient, owner_client

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
PI = "Sample Brokerage account"
AUD = "AUD"
D = date


def _change(on: date, instrument: int | None, delta: str, ccy: str | None = None) -> core.Change:
    return core.Change(on, instrument, None if instrument else ccy, Decimal(delta))


# --- pure replay ---------------------------------------------------------------------------


def test_a_balance_that_ends_a_day_negative_is_reported_on_its_first_such_date() -> None:
    changes = [
        _change(D(2026, 1, 5), 1, "-3"),
        _change(D(2026, 1, 1), 1, "10"),
        _change(D(2026, 1, 9), 1, "-20"),
        _change(D(2026, 1, 20), 1, "50"),  # recovers, but the dip is still reported
    ]
    [found] = core.first_negatives(changes)
    assert (found.instrument_id, found.on, found.balance) == (1, D(2026, 1, 9), Decimal("-13"))


def test_a_buy_and_a_sell_on_one_day_net_out_because_their_order_is_unknown() -> None:
    changes = [_change(D(2026, 1, 5), 1, "-5"), _change(D(2026, 1, 5), 1, "5")]
    assert core.first_negatives(changes) == []


def test_cash_is_tracked_per_currency_apart_from_units() -> None:
    changes = [
        _change(D(2026, 1, 1), None, "10000", "AUD"),
        _change(D(2026, 1, 2), None, "-2500", "GBP"),
        _change(D(2026, 1, 2), 7, "5"),
    ]
    [found] = core.first_negatives(changes)
    assert (found.instrument_id, found.currency, found.balance) == (None, "GBP", Decimal("-2500"))


def test_derive_never_returns_a_negative_balance_as_a_value() -> None:
    changes = [
        _change(D(2026, 1, 1), 1, "10"),
        _change(D(2026, 1, 2), 2, "-4"),  # sold without a buy
        _change(D(2026, 1, 3), None, "-100", AUD),
    ]
    got = core.derive(changes)
    assert got.units == {1: Decimal("10")}
    assert got.cash == {}
    assert {(n.instrument_id, n.currency) for n in got.invalid} == {(2, None), (None, AUD)}


def test_derive_as_at_an_earlier_date_has_fewer_units_and_a_later_dip_is_not_yet_invalid() -> None:
    changes = [_change(D(2026, 1, 1), 1, "10"), _change(D(2026, 2, 1), 1, "-30")]
    assert core.derive(changes, D(2026, 1, 15)).units == {1: Decimal("10")}
    assert core.derive(changes, D(2026, 1, 15)).invalid == []
    assert core.derive(changes, D(2026, 2, 1)).invalid[0].on == D(2026, 2, 1)


def test_a_held_then_sold_out_instrument_is_not_listed() -> None:
    changes = [_change(D(2026, 1, 1), 1, "10"), _change(D(2026, 1, 2), 1, "-10")]
    assert core.derive(changes).units == {}


@pytest.mark.parametrize("text", ["abc", "", "1.0000001", "NaN", "inf"])
def test_parse_quantity_refuses_what_it_cannot_hold_exactly(text: str) -> None:
    with pytest.raises(ValueError):
        core.parse_quantity(text)


# --- Hypothesis: Holdings never go negative given a valid history --------------------------

_event = st.tuples(
    st.integers(0, 40),  # day offset
    st.integers(1, 3),  # Instrument
    st.integers(1, 500),  # units
    st.booleans(),  # True: a sell
    st.booleans(),  # True: cash instead of units
)


def _valid_history(events: list[tuple[int, int, int, bool, bool]]) -> list[core.Change]:
    """Replay random events in date order, clipping each sell to what is held, so every balance
    stays at or above zero at every point."""
    held: dict[tuple[int | None, str | None], Decimal] = {}
    out: list[core.Change] = []
    start = D(2026, 1, 1)
    for offset, instrument, units, sell, cash in sorted(events, key=lambda e: e[0]):
        key = (None, AUD) if cash else (instrument, None)
        delta = Decimal(units)
        if sell:
            delta = -min(delta, held.get(key, Decimal(0)))
            if delta == 0:
                continue
        held[key] = held.get(key, Decimal(0)) + delta
        out.append(core.Change(start + timedelta(days=offset), key[0], key[1], delta))
    return out


@given(events=st.lists(_event, max_size=40), as_at=st.integers(0, 45))
def test_a_valid_history_never_derives_a_negative_holding_or_cash(
    events: list[tuple[int, int, int, bool, bool]], as_at: int
) -> None:
    changes = _valid_history(events)
    on = D(2026, 1, 1) + timedelta(days=as_at)
    got = core.derive(changes, on)
    assert got.invalid == []
    assert all(q > 0 for q in got.units.values())
    assert all(m.amount_minor >= 0 for m in got.cash.values())
    # and the figures are the independent sums, exactly
    for instrument in {c.instrument_id for c in changes if c.instrument_id}:
        total = sum(
            (c.delta for c in changes if c.instrument_id == instrument and c.occurred_on <= on),
            Decimal(0),
        )
        assert got.units.get(instrument, Decimal(0)) == total


@given(events=st.lists(_event, min_size=1, max_size=30))
def test_every_dip_below_zero_is_flagged_never_hidden(
    events: list[tuple[int, int, int, bool, bool]],
) -> None:
    """Arbitrary (possibly invalid) history: a balance is flagged exactly when some end-of-day
    balance is below zero, and a flagged balance is never among the values."""
    start = D(2026, 1, 1)
    changes = [
        core.Change(
            start + timedelta(days=o),
            None if cash else i,
            AUD if cash else None,
            Decimal(-u if sell else u),
        )
        for o, i, u, sell, cash in events
    ]
    by_day: dict[tuple[int | None, str | None], dict[date, Decimal]] = {}
    for c in changes:
        key = (c.instrument_id, c.currency)
        by_day.setdefault(key, {}).setdefault(c.occurred_on, Decimal(0))
        by_day[key][c.occurred_on] += c.delta
    expected: set[tuple[int | None, str | None]] = set()
    for key, days in by_day.items():
        running = Decimal(0)
        for day in sorted(days):
            running += days[day]
            if running < 0:
                expected.add(key)
    got = core.derive(changes)
    assert {(n.instrument_id, n.currency) for n in got.invalid} == expected
    assert not ({i for i, _ in expected if i} & set(got.units))
    assert not ({c for i, c in expected if i is None} & set(got.cash))


# --- integration and golden ----------------------------------------------------------------


def _file(name: str) -> bytes:
    return (IMPORTS / name).read_bytes()


def _account(s: Session) -> Account:
    return s.scalars(select(Account).where(Account.name == PI)).one()


def _stage(s: Session, name: str) -> Import:
    return service.stage(s, CTX, _file(name), name, _account(s).id).import_


def _instrument(s: Session, ticker: str) -> Instrument:
    found = reference.resolve(s, ticker, "ticker")
    assert isinstance(found, Instrument)
    return found


def _commit_sample_history(s: Session) -> None:
    trades = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
    service.create_for_reference(
        s,
        CTX,
        trades.id,
        "ticker",
        "SNHX",
        reference.InstrumentInput("Sample New Horizons ETF", "etf", AUD),
    )
    commit.commit(s, CTX, trades.id)
    commit.commit(
        s, CTX, _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv").id
    )


def _named(s: Session, account_id: int, on: date | None) -> dict[str, Decimal]:
    view = transactions.holdings(s, account_id, on)
    return {view.names[i]: q for i, q in view.holdings.units.items()}


def test_golden_holdings_and_cash_of_the_committed_sample_history(
    sample_factory: sessionmaker[Session],
) -> None:
    """Known answers from the synthetic exports, worked out by hand from the files."""
    with sample_factory.begin() as s:
        _commit_sample_history(s)
        pi = _account(s).id
        latest = _named(s, pi, None)
        assert latest == {
            "Sample Broad Market ETF": Decimal("50"),  # 30 + 20
            "Sample Mining Ltd": Decimal("60"),  # 100 - 40
            "Sample New Horizons ETF": Decimal("50"),
        }
        assert transactions.holdings(s, pi, None).holdings.cash == {AUD: Money(83493, AUD)}
        assert transactions.holdings(s, pi, None).holdings.invalid == []

        # an earlier date: fewer units, and the cash at that date
        assert _named(s, pi, D(2026, 9, 1)) == {
            "Sample Broad Market ETF": Decimal("30"),
            "Sample Mining Ltd": Decimal("100"),
            "Sample New Horizons ETF": Decimal("50"),
        }
        assert transactions.holdings(s, pi, D(2026, 9, 1)).holdings.cash == {
            AUD: Money(242951, AUD)
        }
        assert _named(s, pi, D(2026, 8, 20)) == {
            "Sample Broad Market ETF": Decimal("30"),
            "Sample Mining Ltd": Decimal("100"),
        }
        assert _named(s, pi, D(2026, 7, 1)) == {}


def test_the_trade_fee_is_not_taken_from_cash_twice(sample_factory: sessionmaker[Session]) -> None:
    with sample_factory.begin() as s:
        _commit_sample_history(s)
        # cash at 2026-08-05: deposit 5000.00, SBME fee -9.50, SBME purchase -2966.87
        derived = transactions.holdings(s, _account(s).id, D(2026, 8, 5)).holdings
        assert derived.cash == {AUD: Money(202363, AUD)}


def _warnings(s: Session, imp: Import, code: str | None = None) -> list[str]:
    return [w.message for w in commit.preview(s, imp.id).warnings if code in (None, w.code)]


def test_staging_a_sell_with_no_earlier_buy_names_the_instrument_and_date(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s, "investment_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv")
        [warning] = _warnings(s, imp, "negative_holding")
        assert "2026-09-16" in warning and "Sample Broad Market ETF" in warning
        assert "-10 units" in warning and "Opening position" in warning

        # committing is still allowed: warnings never block, and the figure is then an invalid
        # state rather than a value
        commit.commit(s, CTX, imp.id)
        shown = transactions.holdings(s, _account(s).id, None).holdings
        assert shown.units == {} and [n.on for n in shown.invalid] == [D(2026, 9, 16)]


def test_an_earlier_opening_position_clears_the_warning(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        imp = _stage(s, "investment_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv")
        assert _warnings(s, imp, "negative_holding")
        sbme = _instrument(s, "SBME")
        txn = opening.record(
            s, CTX, pi.id, opening.OpeningInput("holding", D(2026, 8, 31), sbme.id, quantity="50")
        )
        assert (txn.kind, txn.stream, txn.import_id, txn.quantity) == (
            "opening_balance",
            "trade",
            None,
            Decimal("50"),
        )
        # no negative, and no coverage conflict (it is dated earlier); the unmatched cash remains
        assert _warnings(s, imp, "negative_holding") == []
        assert _warnings(s, imp, "coverage_past_opening") == []
        commit.commit(s, CTX, imp.id)
        assert _named(s, pi.id, None) == {"Sample Broad Market ETF": Decimal("40")}  # 50 - 10


def test_an_opening_position_is_audited_and_survives_a_later_import(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        txn = opening.record(
            s, CTX, pi.id, opening.OpeningInput("cash", D(2026, 6, 30), amount="1,000.50")
        )
        assert (txn.stream, txn.amount) == ("cash", Money(100050, AUD))
        event = s.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "transaction", AuditEvent.entity_id == txn.id
            )
        ).one()
        assert event.action == "create" and event.after is not None
        assert event.after["amount"] == {"minor": 100050, "ccy": AUD}
        assert "Opening position" in event.summary and "2026-06-30" in event.summary

        commit.commit(
            s, CTX, _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv").id
        )
        assert txn.superseded_at is None  # outside every Coverage: an Import never supersedes it
        # 1,000.50 opening plus the sample cash file's 834.93
        assert transactions.holdings(s, pi.id, None).holdings.cash == {AUD: Money(183543, AUD)}


def test_cash_that_would_go_negative_is_warned_with_the_date(
    sample_factory: sessionmaker[Session],
) -> None:
    csv = (
        b"Date,Type,Product Type,Product Name,Product ID,Units,Total\n"
        b"05-Sep-2026,Withdrawal,Cash account,Synthetic,CASH,,-40.00\n"
        b"02-Sep-2026,Deposit,Cash account,Synthetic,CASH,,10.00\n"
    )
    name = "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv"
    with sample_factory.begin() as s:
        pi = _account(s)
        imp = service.stage(s, CTX, csv, name, pi.id).import_
        [warning] = _warnings(s, imp, "negative_cash")
        assert "2026-09-05" in warning and "-30.00 AUD" in warning
        opening.record(s, CTX, pi.id, opening.OpeningInput("cash", D(2026, 8, 31), amount="100"))
        assert _warnings(s, imp, "negative_cash") == []


def test_a_coverage_reaching_back_past_an_opening_position_warns_of_double_counting(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        sbme = _instrument(s, "SBME")
        opening.record(
            s, CTX, pi.id, opening.OpeningInput("holding", D(2026, 9, 20), sbme.id, quantity="25")
        )
        imp = _stage(
            s, "investment_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv"
        )  # Coverage 2026-09-01 to 2026-09-30
        [warning] = _warnings(s, imp, "coverage_past_opening")
        assert "2026-09-01" in warning and "2026-09-20" in warning
        assert "25 units of Sample Broad Market ETF" in warning
        # and the cash Stream is unaffected by a trade Opening position
        cash = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
        assert _warnings(s, cash, "coverage_past_opening") == []


@pytest.mark.parametrize(
    ("data", "field"),
    [
        (opening.OpeningInput("holding", D(2026, 8, 1), None, quantity="5"), "instrument_id"),
        (opening.OpeningInput("holding", D(2026, 8, 1), 1, quantity="0"), "quantity"),
        (opening.OpeningInput("holding", D(2026, 8, 1), 1, quantity="-3"), "quantity"),
        (opening.OpeningInput("holding", D(2026, 8, 1), 1, quantity="1.0000001"), "quantity"),
        (opening.OpeningInput("holding", None, 1, quantity="5"), "occurred_on"),
        (opening.OpeningInput("cash", D(2026, 8, 1), amount="-5"), "amount"),
        (opening.OpeningInput("cash", D(2026, 8, 1), amount="1.234"), "amount"),
        (opening.OpeningInput("cash", D(2026, 8, 1), amount=""), "amount"),
        (opening.OpeningInput("other", D(2026, 8, 1)), "kind"),
    ],
)
def test_a_bad_opening_position_is_refused_naming_the_field(
    sample_factory: sessionmaker[Session], data: opening.OpeningInput, field: str
) -> None:
    with sample_factory.begin() as s:
        with pytest.raises(ValidationError) as caught:
            opening.record(s, CTX, _account(s).id, data)
        assert field in {e.field for e in caught.value.errors}
        assert s.scalars(select(Transaction)).first() is None  # nothing was written


# --- over HTTP -----------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _pi(c: TestClient) -> int:
    return next(a["id"] for a in c.get("/api/accounts").json() if a["name"] == PI)


def test_holdings_endpoint_lists_units_cash_and_an_invalid_balance_apart(
    client: TestClient,
) -> None:
    pi = _pi(client)
    sbme = next(i["id"] for i in client.get("/api/instruments").json() if "Broad" in i["name"])
    path = f"/api/accounts/{pi}"
    assert client.get(f"{path}/holdings").json() == {
        "as_at": None,
        "holdings": [],
        "cash": [],
        "invalid": [],
    }
    posted = client.post(
        f"{path}/opening-positions",
        json={
            "kind": "holding",
            "occurred_on": "2026-08-01",
            "instrument_id": sbme,
            "quantity": "12.5",
        },
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["kind"] == "opening_balance" and posted.json()["import_id"] is None
    cash = client.post(
        f"{path}/opening-positions",
        json={"kind": "cash", "occurred_on": "2026-08-01", "amount": "250.00"},
    )
    assert cash.status_code == 201, cash.text
    held = client.get(f"{path}/holdings", params={"as_at": "2026-08-15"}).json()
    assert [(h["instrument_name"], h["units"]) for h in held["holdings"]] == [
        ("Sample Broad Market ETF", "12.5")
    ]
    assert [(m["amount"], m["currency"]) for m in held["cash"]] == [("250.00", "AUD")]
    assert client.get(f"{path}/holdings", params={"as_at": "2026-07-31"}).json()["holdings"] == []

    bad = client.post(
        f"{path}/opening-positions",
        json={
            "kind": "holding",
            "occurred_on": "2026-08-01",
            "instrument_id": sbme,
            "quantity": "-1",
        },
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["field"] == "quantity"
    assert client.get("/api/accounts/9999/holdings").status_code == 404
