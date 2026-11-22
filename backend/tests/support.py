"""Test helpers for the signed-in API (ADR-0010).

API tests replace the ``current_principal`` dependency in code, as the ADR says. There is no
configuration switch that turns authentication off, so this cannot leak into a real run. Tests of
the sign-in flow itself (``test_auth_*``) do not use these: they sign in for real.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient as _TestClient

from finance360.api.deps import current_principal
from finance360.capabilities.auth.types import Principal, Role
from finance360.capabilities.reference.providers import FxFetch, PriceSeries

OWNER = Principal(login_id=1, role=Role.OWNER)
#: What the origin check expects from a browser talking to the tests' host. ``localhost`` is on
#: the default allowed-hosts list (ADR-0012), unlike Starlette's default ``testserver``.
ORIGIN = {"Origin": "http://localhost"}


class TestClient(_TestClient):
    """Starlette's client, talking to ``localhost`` so the allowed-hosts check passes."""

    __test__ = False

    def __init__(self, *args: Any, base_url: str = "http://localhost", **kwargs: Any) -> None:
        super().__init__(*args, base_url=base_url, **kwargs)


def as_owner(app: FastAPI, principal: Principal = OWNER) -> FastAPI:
    app.dependency_overrides[current_principal] = lambda: principal
    return app


def owner_client(app: FastAPI, **kwargs: Any) -> TestClient:
    """A TestClient that is signed in as the owner and sends a same-origin ``Origin``."""
    return TestClient(as_owner(app), headers=ORIGIN, **kwargs)


def without_depreciating_assets(client: TestClient) -> TestClient:
    """The synthetic household's Tax records without its depreciating assets.

    A diminishing value asset reads the Rule pack figure of the year it started in. Tests that swap
    in the invented pack for a fictional year have no such figure for the fixture's real start
    dates, so an estimate that includes the asset's Member refuses; those tests use this instead.
    """
    for record in client.get("/api/tax/records").json():
        if record["record_type"] == "depreciating_asset":
            assert client.delete(f"/api/tax/records/{record['id']}").status_code == 204
    return client


def without_home_office_hours(client: TestClient) -> TestClient:
    """The synthetic household's Tax records without its home office hours and car kilometres.

    Each reads Rule pack figures (the rate per hour; the rate per kilometre and the limit) for its
    year. Tests that swap in an invented pack to isolate something else (the depreciating assets)
    have no such figures, so these records would be left out of the totals or refuse an estimate;
    those tests use this instead. That holds for good: an invented pack never has them.
    """
    for record in client.get("/api/tax/records").json():
        if record["record_type"] in ("home_office_hours", "car_kilometres"):
            assert client.delete(f"/api/tax/records/{record['id']}").status_code == 204
    return client


def without_income_streams(client: TestClient) -> TestClient:
    """The synthetic household as it was before income streams.

    The fixture household now has a salary for each Member and a working-years spending phase
    (150,000.00 from the projection start; 165,000.00 in the custom Scenario's own list). Tests of
    something else (the drawdown order, saved runs, the Rule pack's ages) keep the household they
    were written against: no salaries, and spending only from the last retirement (70,000.00, then
    55,000.00 from ten years on; 85,000.00 in the custom Scenario). Their figures are then exactly
    the pre-B7 ones, which also shows that a household with no stream projects as it did.
    """
    for stream in client.get("/api/income-streams").json():
        assert client.delete(f"/api/income-streams/{stream['id']}").status_code == 204

    def retire(offset: int, amount: str) -> dict[str, object]:
        return {"anchor": "last_retirement", "offset_years": offset, "annual_amount": amount}

    put = client.put(
        "/api/spending-phases", json={"phases": [retire(0, "70000.00"), retire(10, "55000.00")]}
    )
    assert put.status_code == 200, put.text
    early = next(
        (s for s in client.get("/api/scenarios").json() if s["name"].startswith("Retire early")),
        None,
    )
    if early is not None:
        own = client.put(
            "/api/spending-phases",
            json={"scenario_id": early["id"], "phases": [retire(0, "85000.00")]},
        )
        assert own.status_code == 200, own.text
    return client


# --- stand-in providers: tests never reach the network ------------------------------------------
Responder = Callable[[date, date], PriceSeries]
PriceOutcome = PriceSeries | Exception | Responder
FxOutcome = FxFetch | Exception


class FakePriceProvider:
    """A price provider that answers from a table. An entry is a ``PriceSeries``, an exception to
    raise, a function of ``(start, end)`` returning a series, or a list of those consumed one per
    call (the last repeats), to model a flaky provider.
    Every call is recorded in ``calls`` as ``(symbol, start, end)``."""

    identifier_scheme = "yahoo"

    def __init__(
        self,
        responses: Mapping[str, PriceOutcome | list[PriceOutcome]],
        key: str = "yfinance",
        name: str = "Fake Yahoo",
    ) -> None:
        self.key = key
        self.name = name
        self.responses = {k: list(v) if isinstance(v, list) else [v] for k, v in responses.items()}
        self.calls: list[tuple[str, date, date]] = []

    def fetch_closes(self, symbol: str, start: date, end: date) -> PriceSeries:
        self.calls.append((symbol, start, end))
        queue = self.responses[symbol]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(start, end) if callable(outcome) else outcome


class FakeFxProvider:
    """An FX provider that answers from one outcome (or a list consumed one per call)."""

    def __init__(
        self, outcome: FxOutcome | list[FxOutcome], key: str = "rba", name: str = "Fake RBA"
    ) -> None:
        self.key = key
        self.name = name
        self.queue = list(outcome) if isinstance(outcome, list) else [outcome]
        self.calls: list[tuple[tuple[str, ...], date, date]] = []

    def fetch_rates(self, currencies: Sequence[str], start: date, end: date) -> FxFetch:
        self.calls.append((tuple(currencies), start, end))
        outcome = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def no_sleep(_seconds: float) -> None:
    """Passed as ``sleep`` so back-off and delays cost nothing in tests."""


def register_income_statement_target() -> None:
    """The ``tax.income_statement`` Fill target, as ``create_app`` registers it, for a test of the
    Document service that builds no app (the profile validates against it on register)."""
    from finance360.capabilities.documents import registry as document_registry
    from finance360.capabilities.tax import fill_target

    document_registry.register_target(
        document_registry.FillTarget(
            key=fill_target.KEY,
            title=fill_target.TITLE,
            fields=tuple(document_registry.TargetField(k, t) for k, t in fill_target.FIELDS),
            has_period=fill_target.HAS_PERIOD,
            link_kind=fill_target.LINK_KIND,
        )
    )
