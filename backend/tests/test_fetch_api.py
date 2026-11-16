"""The fetch API (status, run history, "Fetch now") through HTTP -> service -> real temporary
SQLite, with fake providers registered over the app's own. Background work runs inside the test
client call, so a run is finished by the time ``post`` returns."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finance360.capabilities.auth.types import Principal, Role
from finance360.capabilities.reference import providers
from finance360.capabilities.reference.providers import (
    FxFetch,
    FxQuote,
    PriceBar,
    PriceSeries,
    ProviderError,
)
from finance360.config import Settings
from finance360.core.localtime import DEFAULT_TIME_ZONE, local_today
from finance360.main import create_app
from support import (
    OWNER,
    FakeFxProvider,
    FakePriceProvider,
    TestClient,
    as_owner,
    owner_client,
)

REPO = Path(__file__).resolve().parents[2]
TODAY = local_today(DEFAULT_TIME_ZONE)


def _settings(data_dir: Path, **extra: object) -> Settings:
    return Settings(data_dir=data_dir, port=8000, git_sha="dev", static_dir=None, **extra)  # type: ignore[arg-type]


def _app(tmp_path: Path, **extra: object):  # noqa: ANN202
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    return create_app(_settings(data, **extra))


def closes(start: date, end: date) -> PriceSeries:
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    return PriceSeries(
        "AUD", tuple(PriceBar(d, Decimal("50.1234")) for d in days if d.weekday() < 5)
    )


def enable(client: TestClient) -> None:
    """Turn the providers on after start-up. Configuring them in ``Settings`` would also start the
    daily job (and its start-up catch-up), which these tests are not about."""
    app = client.app
    app.state.settings = replace(  # type: ignore[attr-defined]
        app.state.settings,  # type: ignore[attr-defined]
        price_provider="yfinance",
        fx_provider="rba",
    )


@pytest.fixture
def configured(tmp_path: Path) -> Iterator[TestClient]:
    with owner_client(_app(tmp_path)) as client:
        enable(client)
        # Fakes replace the app's own providers (the registry is replaced by key).
        providers.register_price_provider(
            FakePriceProvider({"SBME.AX": closes, "SMPM.AX": closes})  # type: ignore[dict-item]
        )
        quote = FxQuote(
            "GBP", TODAY - timedelta(days=1), Decimal("2.0399836801"), "A$1 = 0.4902 GBP"
        )
        providers.register_fx_provider(FakeFxProvider(FxFetch((quote,))))
        yield client


def test_nothing_is_configured_by_default_and_fetch_now_says_so(tmp_path: Path) -> None:
    with owner_client(_app(tmp_path)) as client:
        status = client.get("/api/fetch/status").json()
        assert [(d["dataset"], d["configured"], d["provider"]) for d in status["datasets"]] == [
            ("prices", False, None),
            ("fx", False, None),
        ]
        assert status["fetch_time"] == "18:30" and status["sample_data"] is False
        assert status["stale_after_days"] == 7
        assert client.get("/api/fetch/runs").json() == []
        refused = client.post("/api/fetch/run")
        assert refused.status_code == 409
        assert refused.json() == {
            "message": "No price or FX provider is configured on this instance."
        }


def test_fetch_now_runs_both_datasets_and_the_status_shows_the_outcome(
    configured: TestClient,
) -> None:
    started = configured.post("/api/fetch/run")
    assert started.status_code == 202
    assert [(r["dataset"], r["provider"], r["trigger"], r["status"]) for r in started.json()] == [
        ("prices", "yfinance", "manual", "running"),
        ("fx", "rba", "manual", "running"),
    ]

    status = configured.get("/api/fetch/status").json()
    prices_state, fx_state = status["datasets"]
    assert prices_state["latest"]["status"] == "success" and prices_state["latest"]["added"] > 0
    assert prices_state["last_success"]["id"] == prices_state["latest"]["id"]
    assert prices_state["last_error"] is None
    assert fx_state["latest"]["status"] == "success" and fx_state["latest"]["added"] == 1

    # the fetched Price is in the Instrument's history with a provider source and the symbol
    etf = next(i for i in configured.get("/api/instruments").json() if i["kind"] == "etf")
    fetched = [
        p
        for p in configured.get(f"/api/instruments/{etf['id']}/prices").json()
        if p["source"] == "yfinance" and p["as_at"] == (TODAY - timedelta(days=1)).isoformat()
    ]
    assert fetched or TODAY.weekday() in (0, 6)  # the fake serves weekdays only
    # the FX rate is listed with source rba, and the GBP Account's Position line shows it
    rates = configured.get("/api/fx-rates").json()
    assert [r["source"] for r in rates if r["from_ccy"] == "GBP"].count("rba") == 1
    position = configured.get("/api/position", params={"on": TODAY.isoformat()}).json()
    lines = [
        a
        for c in position["classes"]
        for cat in c["categories"]
        for a in cat["accounts"]
        if a["currency"] == "GBP"
    ]
    assert [(a["rate_source"], a["status"]) for a in lines] == [("rba", "ok")]


def test_a_failing_provider_is_a_recorded_error_and_the_app_carries_on(tmp_path: Path) -> None:
    with owner_client(_app(tmp_path)) as client:
        enable(client)
        providers.register_price_provider(
            FakePriceProvider({"SBME.AX": ProviderError("down"), "SMPM.AX": ProviderError("down")})
        )
        providers.register_fx_provider(
            FakeFxProvider(ProviderError("The RBA could not be reached."))
        )
        before = client.get("/api/instruments").json()

        assert client.post("/api/fetch/run").status_code == 202

        status = client.get("/api/fetch/status").json()
        prices_state, fx_state = status["datasets"]
        assert prices_state["latest"]["status"] == "failed"
        assert prices_state["last_success"] is None
        assert "2 of 2 Instruments failed" in prices_state["last_error"]["error"]
        assert (
            fx_state["last_error"]["error"]
            == "1 of 1 currencies failed. GBP: The RBA could not be reached."
        )
        assert client.get("/api/instruments").json() == before  # the rest of the app is fine
        assert client.get("/health").status_code == 200


def test_run_history_and_one_run_in_detail(configured: TestClient) -> None:
    configured.post("/api/fetch/run")
    runs = configured.get("/api/fetch/runs").json()
    assert [r["dataset"] for r in runs] == ["fx", "prices"]  # newest first
    only_prices = configured.get("/api/fetch/runs", params={"dataset": "prices"}).json()
    assert [r["dataset"] for r in only_prices] == ["prices"]

    run = configured.get(f"/api/fetch/runs/{only_prices[0]['id']}").json()
    assert [(r["target"], r["symbol"], r["outcome"]) for r in run["results"]] == [
        ("Sample Broad Market ETF", "SBME.AX", "ok"),
        ("Sample Mining Ltd", "SMPM.AX", "ok"),
    ]
    assert configured.get("/api/fetch/runs/9999").status_code == 404
    assert configured.get("/api/fetch/runs", params={"dataset": "bogus"}).status_code == 422


def test_a_second_fetch_now_while_one_is_running_is_refused_and_claims_nothing(
    configured: TestClient,
) -> None:
    from finance360.capabilities.reference import fetch

    app_factory = configured.app.state.session_factory  # type: ignore[attr-defined]
    with app_factory.begin() as s:
        fetch.claim_in(s, "fx", "rba", "scheduled")  # a live FX run (as the daily job would)
    busy = configured.post("/api/fetch/run")
    assert busy.status_code == 409 and busy.json() == {"message": fetch.BUSY_MESSAGE}
    runs = configured.get("/api/fetch/runs").json()
    assert [(r["dataset"], r["status"]) for r in runs] == [("fx", "running")]  # prices not claimed


def test_only_the_owner_can_start_a_fetch_but_a_viewer_can_read_the_status(tmp_path: Path) -> None:
    app = _app(tmp_path)
    viewer = Principal(login_id=2, role=Role.VIEWER)
    with owner_client(app) as client:
        enable(client)
        as_owner(app, viewer)
        assert client.get("/api/fetch/status").status_code == 200
        assert client.post("/api/fetch/run").status_code == 403
        as_owner(app, OWNER)


def _commit_the_trade_export(client: TestClient) -> dict:  # type: ignore[type-arg]
    """Stage the sample trades, create their one new Instrument (SNHX), commit; return detail."""
    pi = next(a["id"] for a in client.get("/api/accounts").json() if "Brokerage" in a["name"])
    name = "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
    content = (REPO / "fixtures/sample_household/imports" / name).read_bytes()
    staged = client.post(
        "/api/imports",
        content=content,
        headers={"Content-Type": "application/octet-stream"},
        params={"account_id": pi, "file_name": name},
    ).json()
    made = client.post(
        f"/api/imports/{staged['id']}/references/create",
        json={
            "scheme": "ticker",
            "ref": "SNHX",
            "name": "Sample New Horizons ETF",
            "kind": "etf",
            "currency": "AUD",
        },
    )
    assert made.status_code == 200
    committed = client.post(f"/api/imports/{staged['id']}/commit")
    assert committed.status_code == 200
    return committed.json()


def test_a_commit_that_brings_in_an_unpriced_instrument_offers_fetch_now(tmp_path: Path) -> None:
    with owner_client(_app(tmp_path)) as client:
        enable(client)
        done = _commit_the_trade_export(client)
        assert done["committed"]["instruments_without_price"] == 1  # SNHX; the others have Prices
        assert done["committed"]["offer_fetch"] is True
        again = client.get(f"/api/imports/{done['id']}").json()
        assert again["committed"]["offer_fetch"] is True  # the page reloads to the same answer

        snhx = next(i for i in client.get("/api/instruments").json() if "New Horizons" in i["name"])
        client.post(
            f"/api/instruments/{snhx['id']}/prices",
            json={"as_at": "2026-09-30", "value": "12.5", "provenance_note": "Checked"},
        )
        priced = client.get(f"/api/imports/{done['id']}").json()["committed"]
        assert (priced["instruments_without_price"], priced["offer_fetch"]) == (0, False)


def test_no_fetch_now_is_offered_when_no_price_provider_is_configured(tmp_path: Path) -> None:
    with owner_client(_app(tmp_path)) as client:  # providers off, the default
        done = _commit_the_trade_export(client)
        assert done["committed"]["instruments_without_price"] == 1
        assert done["committed"]["offer_fetch"] is False


def test_an_unknown_provider_key_stops_the_app_starting(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FINANCE360_PRICE_PROVIDER: unknown provider 'nonesuch'"):
        _app(tmp_path, price_provider="nonesuch")
