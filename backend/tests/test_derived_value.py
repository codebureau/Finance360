"""The holdings-based Behaviour and derived Position points (ADR-0013 sections 3 and 4).

Unit and Hypothesis tests on the pure ``derive_value``; integration tests through the real app on a
migrated temporary SQLite and the synthetic household with its committed sample history; one golden
test with the exact derived total worked out by hand. No new write route, so no real-server test.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from finance360.behaviours.holdings_based import derive_value
from finance360.capabilities.imports import commit, service
from finance360.capabilities.reference import instruments as reference
from finance360.config import Settings
from finance360.core.audit import AuditContext
from finance360.core.derived import (
    DerivedValue,
    DeriveInputs,
    NoDerivedValue,
    stream_issue,
    stream_issues,
)
from finance360.core.fx import FxPoint
from finance360.core.models.account import Account
from finance360.core.models.audit import AuditEvent
from finance360.core.money import Money
from finance360.core.price import PricePoint
from finance360.core.transaction import Change
from finance360.main import create_app
from support import TestClient, owner_client

D = date
AUD, GBP = "AUD", "GBP"
CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
PI = "Sample Brokerage account"
ON = D(2026, 9, 30)
FULL = {"trade": D(2026, 9, 30), "cash": D(2026, 9, 30)}


# --- the pure derivation -------------------------------------------------------------------


def _price(instrument: int, as_at: date, value: str, source: str = "manual") -> PricePoint:
    return PricePoint(instrument * 1000 + as_at.day, instrument, as_at, Decimal(value), source)


def _inputs(
    changes: list[Change],
    prices: list[PricePoint],
    *,
    currencies: dict[int, str] | None = None,
    fx: list[FxPoint] | None = None,
    coverage: dict[str, date] | None = None,
    currency: str = AUD,
    stale_days: int = 7,
) -> DeriveInputs:
    ids = {c.instrument_id for c in changes if c.instrument_id is not None}
    return DeriveInputs(
        account_currency=currency,
        changes=changes,
        instrument_currencies=currencies or {i: AUD for i in ids},
        instrument_names={i: f"Instrument {i}" for i in ids},
        prices=prices,
        fx=fx or [],
        coverage=FULL if coverage is None else coverage,
        stale_days=stale_days,
    )


def _units(on: date, instrument: int, units: str) -> Change:
    return Change(on, instrument, None, Decimal(units))


def _cash(on: date, minor: int, ccy: str = AUD) -> Change:
    return Change(on, None, ccy, Decimal(minor))


def _derived(result: DerivedValue | NoDerivedValue) -> DerivedValue:
    assert isinstance(result, DerivedValue), result
    return result


def test_units_times_price_plus_cash_rounded_once_per_line() -> None:
    held = [
        _units(D(2026, 8, 3), 1, "50"),
        _units(D(2026, 8, 3), 2, "60"),
        _cash(D(2026, 8, 3), 83493),
    ]
    prices = [_price(1, ON, "96.1075"), _price(2, ON, "6.3304")]
    got = _derived(derive_value(_inputs(held, prices), ON))
    # 50 x 96.1075 = 4805.375: a tie, so half-even gives 4805.38; 60 x 6.3304 = 379.824
    assert [(line.kind, line.value) for line in got.lines] == [
        ("holding", Money(480538, AUD)),
        ("holding", Money(37982, AUD)),
        ("cash", Money(83493, AUD)),
    ]
    assert got.total == Money(480538 + 37982 + 83493, AUD)
    assert not got.stale


@pytest.mark.parametrize(
    ("units", "price", "minor"),
    [
        ("1", "0.125", 12),  # 12.5 minor units: ties go to the even neighbour, down ...
        ("1", "0.135", 14),  # ... 13.5 goes up to 14 ...
        ("1", "0.145", 14),  # ... 14.5 goes down to 14 ...
        ("2", "0.0625", 12),  # ... and a tie arrived at through the units
        ("1", "0.124999", 12),
        ("1", "0.125001", 13),
    ],
)
def test_a_tie_rounds_half_to_even_never_half_up(units: str, price: str, minor: int) -> None:
    held = [_units(D(2026, 8, 3), 1, units)]
    got = _derived(derive_value(_inputs(held, [_price(1, ON, price)]), ON))
    assert got.lines[0].value == Money(minor, AUD)


def test_the_latest_price_on_or_before_the_date_is_used_and_a_later_one_never() -> None:
    held = [_units(D(2026, 8, 3), 1, "10"), _cash(D(2026, 8, 3), 0)]
    prices = [_price(1, D(2026, 9, 28), "2.00"), _price(1, D(2026, 9, 29), "3.00")]
    prices.append(_price(1, D(2026, 10, 1), "9.00"))
    got = _derived(derive_value(_inputs(held, prices), ON))
    assert got.total == Money(3000, AUD)
    assert got.lines[0].price is not None and got.lines[0].price.as_at == D(2026, 9, 29)


def test_a_price_older_than_the_limit_is_shown_stale_not_refused() -> None:
    held = [_units(D(2026, 8, 3), 1, "10")]
    old = [_price(1, ON - timedelta(days=8), "2.00")]
    got = _derived(derive_value(_inputs(held, old), ON))
    assert got.stale and got.lines[0].stale and got.total == Money(2000, AUD)
    edge = [_price(1, ON - timedelta(days=7), "2.00")]
    assert not _derived(derive_value(_inputs(held, edge), ON)).stale
    # configurable
    assert _derived(derive_value(_inputs(held, edge, stale_days=3), ON)).stale


def test_a_foreign_instrument_is_converted_at_the_exact_pair_rate_once() -> None:
    held = [_units(D(2026, 8, 3), 1, "3"), _cash(D(2026, 8, 3), 1000, GBP)]
    prices = [_price(1, ON, "10.00")]
    rates = [
        FxPoint(1, GBP, AUD, D(2026, 9, 1), Decimal("1.9000000000"), "rba"),
        FxPoint(2, GBP, AUD, D(2026, 9, 20), Decimal("2.0143000000"), "rba"),
    ]
    ins = _inputs(held, prices, currencies={1: GBP}, fx=rates)
    got = _derived(derive_value(ins, ON))
    # 3 x 10.00 x 2.0143 = 60.429 -> 60.43; the 10.00 GBP of cash is 20.143 -> 20.14
    assert [line.value for line in got.lines] == [Money(6043, AUD), Money(2014, AUD)]
    assert all(line.fx is not None and line.fx.as_at == D(2026, 9, 20) for line in got.lines)
    assert got.total == Money(8057, AUD)


@pytest.mark.parametrize(
    ("what", "inputs", "reason"),
    [
        ("no Price", _inputs([_units(D(2026, 8, 3), 1, "1")], []), "no Price"),
        (
            "only a later Price",
            _inputs([_units(D(2026, 8, 3), 1, "1")], [_price(1, D(2026, 10, 1), "1.00")]),
            "no Price",
        ),
        (
            "no FX rate",
            _inputs([_units(D(2026, 8, 3), 1, "1")], [_price(1, ON, "1.00")], currencies={1: GBP}),
            "No GBP to AUD rate",
        ),
        (
            "an inverse FX rate only",
            _inputs(
                [_units(D(2026, 8, 3), 1, "1")],
                [_price(1, ON, "1.00")],
                currencies={1: GBP},
                fx=[FxPoint(1, AUD, GBP, ON, Decimal("0.5"), "rba")],
            ),
            "No GBP to AUD rate",
        ),
        (
            "no cash Stream",
            _inputs([_units(D(2026, 8, 3), 1, "1")], [_price(1, ON, "1")], coverage={"trade": ON}),
            "cash Stream has no committed Import",
        ),
        (
            "no trade Stream",
            _inputs([_cash(D(2026, 8, 3), 5)], [], coverage={"cash": ON}),
            "trade Stream has no committed Import",
        ),
        (
            "a stale Stream",
            _inputs([_cash(D(2026, 8, 3), 5)], [], coverage={**FULL, "trade": D(2026, 9, 29)}),
            "trade Stream's Coverage ends 2026-09-29",
        ),
        ("nothing recorded yet", _inputs([_cash(D(2026, 10, 2), 5)], []), "Nothing is recorded"),
        (
            "a negative balance",
            _inputs([_units(D(2026, 9, 1), 1, "-5")], [_price(1, ON, "1")]),
            "went negative",
        ),
    ],
)
def test_no_derived_value_says_why_and_is_never_a_partial_total(
    what: str, inputs: DeriveInputs, reason: str
) -> None:
    got = derive_value(inputs, ON)
    assert isinstance(got, NoDerivedValue), what
    assert reason in got.reason, got.reason


def test_stream_issues_put_a_missing_stream_before_a_stale_one_and_the_prose_follows() -> None:
    """The look-through dead end led with the stale trade Stream and buried the cash Stream that
    had no Import at all. Missing now comes first, and the prose is built from the same list."""
    coverage = {"trade": D(2026, 3, 31)}  # trade is stale, cash has nothing
    issues = stream_issues(coverage, ON)
    assert [(i.stream, i.kind) for i in issues] == [("cash", "missing"), ("trade", "stale")]
    assert stream_issues(FULL, ON) == ()
    got = derive_value(_inputs([_cash(D(2026, 8, 3), 5)], [], coverage=coverage), ON)
    assert isinstance(got, NoDerivedValue)
    assert got.issues == issues
    assert got.reason == " ".join(i.message for i in issues)
    assert got.reason.startswith("The cash Stream has no committed Import.")
    assert stream_issue("trade", coverage, ON) == issues[1].message  # the per-Stream form agrees


def test_one_missing_price_refuses_the_whole_value() -> None:
    held = [_units(D(2026, 8, 3), 1, "1"), _units(D(2026, 8, 3), 2, "1"), _cash(D(2026, 8, 3), 5)]
    got = derive_value(_inputs(held, [_price(1, ON, "1.00")]), ON)
    assert isinstance(got, NoDerivedValue) and "Instrument 2" in got.reason


_units_st = st.decimals(min_value=Decimal("0.000001"), max_value=Decimal("100000"), places=6)
_price_st = st.decimals(min_value=Decimal("0.000001"), max_value=Decimal("9999"), places=6)
_rate_st = st.decimals(min_value=Decimal("0.0000000001"), max_value=Decimal("9"), places=10)


@given(
    lots=st.lists(st.tuples(_units_st, _price_st), min_size=1, max_size=6),
    rate=_rate_st,
    cash=st.integers(min_value=0, max_value=10**9),
)
@settings(max_examples=150, deadline=None)
def test_lines_sum_exactly_to_the_total_and_each_line_rounds_half_even_once(
    lots: list[tuple[Decimal, Decimal]], rate: Decimal, cash: int
) -> None:
    changes = [_units(D(2026, 8, 3), i, str(u)) for i, (u, _) in enumerate(lots, 1)]
    changes.append(_cash(D(2026, 8, 3), cash))
    prices = [_price(i, ON, str(p)) for i, (_, p) in enumerate(lots, 1)]
    ins = _inputs(
        changes,
        prices,
        currencies={i: GBP for i in range(1, len(lots) + 1)},
        fx=[FxPoint(1, GBP, AUD, ON, rate, "manual")],
    )
    got = _derived(derive_value(ins, ON))
    exact = Context(prec=80, rounding=ROUND_HALF_EVEN)
    holdings = [line for line in got.lines if line.kind == "holding"]
    assert len(holdings) == len(lots)
    for line, (units, price) in zip(holdings, lots, strict=True):
        product = exact.multiply(exact.multiply(units, price), rate)
        assert line.value.amount_minor == int(
            product.quantize(Decimal("0.01"), ROUND_HALF_EVEN) * 100
        )
    assert sum(line.value.amount_minor for line in got.lines) == got.total.amount_minor


# --- through the real app ------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    app_settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(app_settings)) as c:
        yield c


def _pi(c: TestClient) -> int:
    return next(a["id"] for a in c.get("/api/accounts").json() if a["name"] == PI)


def _commit_history(
    c: TestClient, *, cash: bool = True, snhx_price_on: str | None = "2026-09-30"
) -> None:
    """Commit the sample trades (and cash) for the PI Account, through the services."""
    with c.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        pi = s.scalars(select(Account).where(Account.name == PI)).one()
        trades = service.stage(
            s,
            CTX,
            (
                IMPORTS / "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
            ).read_bytes(),
            "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv",
            pi.id,
        ).import_
        service.create_for_reference(
            s,
            CTX,
            trades.id,
            "ticker",
            "SNHX",
            reference.InstrumentInput("Sample New Horizons ETF", "etf", AUD),
        )
        commit.commit(s, CTX, trades.id)
        if cash:
            staged = service.stage(
                s,
                CTX,
                (
                    IMPORTS / "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv"
                ).read_bytes(),
                "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv",
                pi.id,
            )
            commit.commit(s, CTX, staged.import_.id)
        instrument = reference.resolve(s, "SNHX", "ticker")
        assert instrument is not None
        instrument_id = instrument.id  # type: ignore[union-attr]
    if snhx_price_on:
        added = c.post(
            f"/api/instruments/{instrument_id}/prices",
            json={"as_at": snhx_price_on, "value": "12.5", "provenance_note": "Invented price"},
        )
        assert added.status_code == 201, added.text


def _switch(c: TestClient, account_id: int, type_key: str) -> dict[str, Any]:
    account = next(a for a in c.get("/api/accounts").json() if a["id"] == account_id)
    body = {
        "name": account["name"],
        "category_key": account["category_key"],
        "account_type_key": type_key,
        "currency": account["currency"],
        "notes": account["notes"],
        "opened_on": account["opened_on"],
        "ownership": account["ownership"],
        "owners": [
            {"member_id": o["member_id"], "share_bp": o["share_bp"]} for o in account["owners"]
        ],
        "tags": account["tags"],
    }
    res = c.put(f"/api/accounts/{account_id}", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _line(c: TestClient, on: str, name: str = PI) -> dict[str, Any]:
    position = c.get("/api/position", params={"on": on}).json()
    lines = [
        a
        for cl in position["classes"]
        for cat in cl["categories"]
        for a in cat["accounts"]
        if a["name"] == name
    ]
    assert len(lines) == 1
    return lines[0]


def test_the_new_account_type_is_seeded_and_the_plain_brokerage_type_stays_simple_balance(
    client: TestClient,
) -> None:
    types = {
        t["key"]: t
        for cl in client.get("/api/catalogue").json()["classes"]
        for cat in cl["categories"]
        for t in cat["account_types"]
    }
    assert types["brokerage_holdings"]["name"] == "Brokerage account (holdings)"
    assert types["brokerage_holdings"]["behaviour_key"] == "holdings_based"
    assert types["brokerage_account"]["behaviour_key"] == "simple_balance"


def test_golden_derived_total_of_the_committed_sample_history(client: TestClient) -> None:
    """Worked by hand from the files: 50 x 96.1075 (a half-even tie, 4805.38) + 60 x 6.3304
    (379.82) + 50 x 12.50 (625.00) + cash 834.93 = 6645.13 on 2026-09-30."""
    pi = _pi(client)
    _commit_history(client)
    _switch(client, pi, "brokerage_holdings")
    view = client.get(f"/api/accounts/{pi}/derived-value", params={"as_at": "2026-09-30"}).json()
    assert view["applies"] and view["reason"] is None and not view["stale"]
    assert view["total"] == {"amount": "6645.13", "amount_minor": 664513, "currency": "AUD"}
    by_name = {line["instrument_name"] or "cash": line for line in view["lines"]}
    assert by_name["Sample Broad Market ETF"]["units"] == "50"
    assert by_name["Sample Broad Market ETF"]["price"] == "96.1075"
    assert by_name["Sample Broad Market ETF"]["price_as_at"] == "2026-09-30"
    assert by_name["Sample Broad Market ETF"]["value"]["amount"] == "4805.38"
    assert by_name["Sample Mining Ltd"]["value"]["amount"] == "379.82"
    assert by_name["Sample New Horizons ETF"]["value"]["amount"] == "625.00"
    assert by_name["cash"]["value"]["amount"] == "834.93"
    assert sum(line["value"]["amount_minor"] for line in view["lines"]) == 664513
    assert [(s["stream"], s["status"]) for s in view["streams"]] == [
        ("trade", "ok"),
        ("cash", "ok"),
    ]
    # the same figure is what the Position takes for the Account on that date
    line = _line(client, "2026-09-30")
    assert (line["source"], line["value"], line["valuation_as_at"]) == (
        "derived",
        "6645.13",
        "2026-09-30",
    )


def test_a_derived_point_beats_an_earlier_manual_valuation_and_a_manual_one_on_the_date_beats_it(
    client: TestClient,
) -> None:
    pi = _pi(client)
    _commit_history(client)
    before = _line(client, "2026-09-30")  # simple balance: the 2026-09-04 manual Valuation
    assert (before["source"], before["valuation_as_at"]) == ("manual", "2026-09-04")
    _switch(client, pi, "brokerage_holdings")

    derived = _line(client, "2026-09-30")
    assert (derived["source"], derived["value"]) == ("derived", "6645.13")

    for source, note in (("manual", "Read from the invented app"), ("statement", "Invented")):
        added = client.post(
            f"/api/accounts/{pi}/valuations",
            json={
                "as_at": "2026-09-30",
                "amount": "7000.00",
                "source": source,
                "provenance_note": note,
            },
        )
        assert added.status_code == 201, added.text
        won = _line(client, "2026-09-30")
        assert (won["source"], won["value"]) == (source, "7000.00")
        # and the difference is reconciliation, shown on read
        view = client.get(
            f"/api/accounts/{pi}/derived-value", params={"as_at": "2026-09-30"}
        ).json()
        assert view["total"]["amount"] == "6645.13"
        assert (
            view["observed"]["source"] == source
            and view["observed"]["amount"]["amount"] == "7000.00"
        )
        assert view["difference"]["amount"] == "354.87"
        client.delete(f"/api/valuations/{added.json()['id']}")

    # the day after the manual Valuation, a derived point is later again and wins
    assert _line(client, "2026-09-30")["source"] == "derived"


DATES = ("2026-06-30", "2026-07-31", "2026-08-31", "2026-09-04", "2026-09-30", "2026-10-22")


def _series(c: TestClient, *, only_pi: bool | None = None) -> dict[str, Any]:
    """Every Account's Position line on each date (``only_pi``: just the PI, or all the others),
    plus the month-end history, which is what a person sees drawn."""
    out: dict[str, Any] = {}
    for on in DATES:
        position = c.get("/api/position", params={"on": on}).json()
        out[on] = [
            a
            for cl in position["classes"]
            for cat in cl["categories"]
            for a in cat["accounts"]
            if only_pi is None or (a["name"] == PI) is only_pi
        ]
    return out


def test_switching_back_restores_the_manual_only_series_and_touches_no_other_account(
    client: TestClient,
) -> None:
    pi = _pi(client)
    _commit_history(client)
    original = _series(client)
    history = client.get("/api/position/history", params={"to": "2026-10-22"}).json()
    valuations = client.get(f"/api/accounts/{pi}/valuations").json()

    _switch(client, pi, "brokerage_holdings")
    switched = _series(client)
    assert switched != original
    assert _series(client, only_pi=False) == {
        on: [a for a in lines if a["name"] != PI] for on, lines in original.items()
    }  # every manual-only Account is exactly as it was
    sources = {
        on: [a["source"] for a in lines if a["name"] == PI] for on, lines in switched.items()
    }
    # derived from the first Transaction to the end of Coverage; before and after, the recorded one
    assert sources == {
        "2026-06-30": ["manual"],  # before the first Transaction (the 07-02 deposit)
        "2026-07-31": ["derived"],  # cash only, before the first trade
        "2026-08-31": ["derived"],  # Mining's 08-12 import Price is the latest on or before
        "2026-09-04": ["manual"],  # a manual Valuation exactly on the date beats derived
        "2026-09-30": ["derived"],  # later than the 09-04 manual Valuation, so it wins
        "2026-10-22": ["manual"],  # Coverage ends 09-30: stale, so no derived point
    }

    _switch(client, pi, "brokerage_account")
    assert _series(client) == original
    assert client.get("/api/position/history", params={"to": "2026-10-22"}).json() == history
    assert client.get(f"/api/accounts/{pi}/valuations").json() == valuations  # nothing rewritten


def test_a_holdings_account_with_no_imported_streams_behaves_as_simple_balance(
    client: TestClient,
) -> None:
    pi = _pi(client)
    original = client.get("/api/position/history", params={"to": "2026-10-22"}).json()
    _switch(client, pi, "brokerage_holdings")
    assert client.get("/api/position/history", params={"to": "2026-10-22"}).json() == original
    view = client.get(f"/api/accounts/{pi}/derived-value").json()
    assert view["applies"] and view["total"] is None and view["lines"] == []
    assert [s["status"] for s in view["streams"]] == ["missing", "missing"]
    assert "no committed Import" in view["reason"]


def test_a_missing_cash_stream_gives_no_derived_value_and_names_the_stream(
    client: TestClient,
) -> None:
    pi = _pi(client)
    _commit_history(client, cash=False)
    _switch(client, pi, "brokerage_holdings")
    view = client.get(f"/api/accounts/{pi}/derived-value", params={"as_at": "2026-09-30"}).json()
    assert view["total"] is None
    assert [(s["stream"], s["status"]) for s in view["streams"]] == [
        ("trade", "ok"),
        ("cash", "missing"),
    ]
    assert _line(client, "2026-09-30")["source"] == "manual"  # the Position never takes a partial


def test_a_missing_price_is_named_and_the_position_keeps_the_recorded_valuation(
    client: TestClient,
) -> None:
    pi = _pi(client)
    _commit_history(client)
    # Every traded Instrument now has an import Price from its trade, so the unpriced one is
    # held through an Opening position, which carries no unit price.
    unpriced = client.post(
        "/api/instruments",
        json={"name": "Sample Unpriced Fund", "kind": "fund", "currency": "AUD", "notes": ""},
    ).json()["id"]
    opened = client.post(
        f"/api/accounts/{pi}/opening-positions",
        json={
            "kind": "holding",
            "occurred_on": "2026-07-01",
            "instrument_id": unpriced,
            "quantity": "10",
        },
    )
    assert opened.status_code == 201, opened.text
    _switch(client, pi, "brokerage_holdings")
    view = client.get(f"/api/accounts/{pi}/derived-value", params={"as_at": "2026-09-30"}).json()
    assert view["total"] is None and "Sample Unpriced Fund has no Price" in view["reason"]
    assert _line(client, "2026-09-30")["source"] == "manual"


def test_the_default_date_is_the_latest_both_streams_reach_and_a_later_date_is_stale(
    client: TestClient,
) -> None:
    pi = _pi(client)
    _commit_history(client)
    _switch(client, pi, "brokerage_holdings")
    assert client.get(f"/api/accounts/{pi}/derived-value").json()["as_at"] == "2026-09-30"
    later = client.get(f"/api/accounts/{pi}/derived-value", params={"as_at": "2026-10-22"}).json()
    assert later["total"] is None
    assert [(s["stream"], s["status"]) for s in later["streams"]] == [
        ("trade", "stale"),
        ("cash", "stale"),
    ]
    assert "2026-09-30" in later["streams"][0]["message"]


def test_a_price_older_than_the_limit_is_flagged_stale_in_the_view(client: TestClient) -> None:
    pi = _pi(client)
    _commit_history(client, snhx_price_on="2026-09-20")  # 10 days before the value date
    _switch(client, pi, "brokerage_holdings")
    view = client.get(f"/api/accounts/{pi}/derived-value", params={"as_at": "2026-09-30"}).json()
    assert view["stale"] and view["stale_after_days"] == 7
    assert view["total"]["amount"] == "6645.13"  # shown, not refused
    flags = {
        line["instrument_name"]: line["stale"]
        for line in view["lines"]
        if line["kind"] == "holding"
    }
    assert flags == {
        "Sample Broad Market ETF": False,
        "Sample Mining Ltd": False,
        "Sample New Horizons ETF": True,
    }
    stale_line = next(x for x in view["lines"] if x["stale"])
    assert stale_line["price_as_at"] == "2026-09-20"


def test_a_simple_balance_account_does_not_apply(client: TestClient) -> None:
    other = next(a["id"] for a in client.get("/api/accounts").json() if a["name"] != PI)
    view = client.get(f"/api/accounts/{other}/derived-value").json()
    assert view["applies"] is False and view["total"] is None and view["lines"] == []
    assert client.get("/api/accounts/9999/derived-value").status_code == 404


def test_switching_is_an_audited_account_edit(client: TestClient) -> None:
    pi = _pi(client)
    _switch(client, pi, "brokerage_holdings")
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        events = list(
            s.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "account",
                    AuditEvent.entity_id == pi,
                    AuditEvent.action == "update",
                )
            )
        )
    assert len(events) == 1
    assert "brokerage_holdings" in str(events[0].after) and "brokerage_account" in str(
        events[0].before
    )
