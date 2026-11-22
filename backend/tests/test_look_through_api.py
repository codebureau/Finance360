"""The Account look-through through the real app on the synthetic household (integration).

The sample Account holds, on 2026-09-30, the Broad Market ETF (a profile totalling 99.5%), the
Mining share (100% Australian shares), the New Horizons ETF (created at commit, so no profile) and
cash. The figures are worked by hand in ``test_lookthrough_pure`` and checked here end to end.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from finance360.capabilities.imports import commit, service
from finance360.capabilities.reference import instruments as reference
from finance360.config import Settings
from finance360.core.audit import AuditContext
from finance360.core.models.account import Account
from finance360.main import create_app
from support import TestClient, owner_client

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
PI = "Sample Brokerage account"
ON = "2026-09-30"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _account(c: TestClient, name: str = PI) -> int:
    return next(a["id"] for a in c.get("/api/accounts").json() if a["name"] == name)


def _commit_history(c: TestClient) -> int:
    """Commit the sample trades and cash for the PI Account; return the SNHX Instrument id."""
    with c.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        pi = s.scalars(select(Account).where(Account.name == PI)).one()
        trades = "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
        staged = service.stage(s, CTX, (IMPORTS / trades).read_bytes(), trades, pi.id).import_
        service.create_for_reference(
            s,
            CTX,
            staged.id,
            "ticker",
            "SNHX",
            reference.InstrumentInput("Sample New Horizons ETF", "etf", "AUD"),
        )
        commit.commit(s, CTX, staged.id)
        cash = "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv"
        staged = service.stage(s, CTX, (IMPORTS / cash).read_bytes(), cash, pi.id)
        commit.commit(s, CTX, staged.import_.id)
        found = reference.resolve(s, "SNHX", "ticker")
        snhx = found.id  # type: ignore[union-attr]
    added = c.post(
        f"/api/instruments/{snhx}/prices",
        json={"as_at": ON, "value": "12.5", "provenance_note": "Invented price"},
    )
    assert added.status_code == 201, added.text
    return snhx


def _switch(c: TestClient, account_id: int, type_key: str) -> None:
    a = next(x for x in c.get("/api/accounts").json() if x["id"] == account_id)
    body = {
        **{k: a[k] for k in ("name", "category_key", "currency", "notes", "opened_on")},
        "account_type_key": type_key,
        "ownership": a["ownership"],
        "owners": [{"member_id": o["member_id"], "share_bp": o["share_bp"]} for o in a["owners"]],
        "tags": a["tags"],
    }
    assert c.put(f"/api/accounts/{account_id}", json=body).status_code == 200


@pytest.fixture
def held(client: TestClient) -> tuple[TestClient, int, int]:
    """The PI Account on holdings, with the sample history committed. -> (client, PI, SNHX)."""
    pi = _account(client)
    snhx = _commit_history(client)
    _switch(client, pi, "brokerage_holdings")
    return client, pi, snhx


def _view(c: TestClient, account: int, **params: str) -> dict[str, Any]:
    res = c.get(f"/api/accounts/{account}/look-through", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _rows(view: dict[str, Any]) -> list[tuple[str, str, str, str | None]]:
    return [(x["kind"], x["name"], x["amount"]["amount"], x["percent"]) for x in view["lines"]]


def test_golden_the_investment_account_shows_its_asset_class_split_including_no_profile(
    held: tuple[TestClient, int, int],
) -> None:
    client, pi, snhx = held
    view = _view(client, pi, on=ON)
    assert (view["applies"], view["status"], view["reason"]) == (True, "ok", None)
    assert (view["dimension_key"], view["as_at"]) == ("asset_class", ON)
    assert view["total"] == {"amount": "6645.13", "amount_minor": 664513, "currency": "AUD"}
    assert _rows(view) == [
        ("label", "Australian shares", "3263.05", "49.10"),
        ("label", "International shares", "1898.12", "28.56"),
        ("label", "Cash", "834.93", "12.56"),
        ("no_profile", "No profile: Sample New Horizons ETF", "625.00", "9.41"),
        ("not_classified", "Not classified", "24.03", "0.36"),
        ("unallocated", "Unallocated", "0.00", "0.00"),
    ]
    # the rows add exactly to the Account's value in the Position
    assert sum(x["amount"]["amount_minor"] for x in view["lines"]) == 664513
    position = client.get("/api/position", params={"on": ON}).json()
    value = next(
        a["value"]
        for cl in position["classes"]
        for cat in cl["categories"]
        for a in cat["accounts"]
        if a["name"] == PI
    )
    assert value == "6645.13"
    no_profile = next(x for x in view["lines"] if x["kind"] == "no_profile")
    assert no_profile["instrument_id"] == snhx and view["flagged"] is False
    etf = next(p for p in view["parts"] if p["name"] == "Sample Broad Market ETF")
    assert (etf["shortfall_percent"], etf["profile_as_at"], etf["dated_after"]) == (
        "0.5",
        "2026-06-30",
        False,
    )


def test_the_default_date_is_the_latest_one_the_coverage_reaches(
    held: tuple[TestClient, int, int],
) -> None:
    client, pi, _ = held
    view = _view(client, pi)  # no date: the same as the Derived value panel
    assert view["as_at"] == ON and view["status"] == "ok"
    assert view["total"]["amount"] == "6645.13"


def test_entering_a_profile_for_the_missing_instrument_removes_the_no_profile_line(
    held: tuple[TestClient, int, int],
) -> None:
    client, pi, snhx = held
    posted = client.post(
        f"/api/instruments/{snhx}/exposure-profiles",
        json={
            "as_at": "2026-06-30",
            "provenance_note": "Invented factsheet",
            "weights": [
                {
                    "dimension_key": "asset_class",
                    "label_key": "international_shares",
                    "percent": "100",
                }
            ],
        },
    )
    assert posted.status_code == 201, posted.text
    view = _view(client, pi, on=ON)
    assert [x["kind"] for x in view["lines"]] == [
        "label",
        "label",
        "label",
        "not_classified",
        "unallocated",
    ]
    assert view["lines"][1]["amount"]["amount"] == "2523.12"  # 1898.12 + 625.00
    assert sum(x["amount"]["amount_minor"] for x in view["lines"]) == 664513


def test_a_profile_dated_after_the_date_is_used_and_marked(
    held: tuple[TestClient, int, int],
) -> None:
    client, pi, snhx = held
    assert (
        client.post(
            f"/api/instruments/{snhx}/exposure-profiles",
            json={
                "as_at": "2026-12-31",
                "provenance_note": "Invented, later",
                "weights": [
                    {"dimension_key": "asset_class", "label_key": "cash", "percent": "100"}
                ],
            },
        ).status_code
        == 201
    )
    view = _view(client, pi, on=ON)
    part = next(p for p in view["parts"] if p["name"] == "Sample New Horizons ETF")
    assert (part["dated_after"], part["profile_as_at"]) == (True, "2026-12-31")
    assert not any(x["kind"] == "no_profile" for x in view["lines"])


def test_a_newer_recorded_valuation_leaves_a_flagged_unallocated_gap_never_scaled(
    held: tuple[TestClient, int, int],
) -> None:
    client, pi, _ = held
    client.post(
        f"/api/accounts/{pi}/valuations",
        json={
            "as_at": ON,
            "amount": "7000.00",
            "source": "statement",
            "provenance_note": "Invented statement",
        },
    )
    view = _view(client, pi, on=ON)
    assert view["total"]["amount"] == "7000.00"
    unallocated = view["lines"][-1]
    assert (unallocated["kind"], unallocated["amount"]["amount"]) == ("unallocated", "354.87")
    assert view["flagged"] is True and view["tolerance"]["amount"] == "70.00"
    assert view["lines"][0]["amount"]["amount"] == "3263.05"  # the parts keep their values
    assert sum(x["amount"]["amount_minor"] for x in view["lines"]) == 700000


def test_an_account_without_a_derived_value_says_why(client: TestClient) -> None:
    pi = _account(client)
    _switch(client, pi, "brokerage_holdings")  # holdings-based, but nothing imported yet
    view = _view(client, pi, on=ON)
    assert (view["applies"], view["status"], view["lines"], view["total"]) == (
        True,
        "not_analysed",
        [],
        None,
    )
    assert "Stream has no committed Import" in view["reason"]
    simple = _view(client, _account(client, "Everyday account (Sample Bank)"))
    assert (simple["applies"], simple["status"]) == (False, "not_analysed")


def test_an_unknown_account_or_dimension_is_refused(client: TestClient) -> None:
    assert client.get("/api/accounts/9999/look-through").status_code == 404
    res = client.get(f"/api/accounts/{_account(client)}/look-through", params={"dimension": "mood"})
    assert res.status_code == 422 and res.json()["errors"][0]["field"] == "dimension"


# --- profiles over the API -------------------------------------------------------------------


def _etf(c: TestClient) -> int:
    return next(i["id"] for i in c.get("/api/instruments").json() if i["kind"] == "etf")


def test_the_vocabulary_lists_asset_class_with_its_labels(client: TestClient) -> None:
    [dimension] = client.get("/api/exposure/vocabulary").json()
    assert dimension["key"] == "asset_class"
    assert [x["key"] for x in dimension["labels"]][0] == "australian_shares"
    assert dimension["labels"][-1] == {"key": "other", "name": "Other"}


def test_a_correction_supersedes_and_the_history_keeps_the_earlier_version(
    client: TestClient,
) -> None:
    etf = _etf(client)
    [fixture] = client.get(f"/api/instruments/{etf}/exposure-profiles").json()
    assert fixture["status"] == "current" and fixture["source"] == "manual"
    [r] = fixture["resolutions"]
    assert (
        r["total_percent"],
        r["difference_percent"],
        r["outcome"],
        r["not_classified_percent"],
    ) == (
        "99.5",
        "-0.5",
        "short",
        "0.5",
    )
    fixed = client.post(
        f"/api/instruments/{etf}/exposure-profiles",
        json={
            "as_at": fixture["as_at"],
            "provenance_note": "Corrected from the PDS",
            "weights": [
                {"dimension_key": "asset_class", "label_key": "australian_shares", "percent": "40"},
                {
                    "dimension_key": "asset_class",
                    "label_key": "international_shares",
                    "percent": "60.8",
                },
            ],
        },
    )
    assert fixed.status_code == 201, fixed.text
    assert fixed.json()["resolutions"][0]["outcome"] == "over"
    assert fixed.json()["resolutions"][0]["trimmed"] == [
        {"label_key": "international_shares", "percent": "0.8"}
    ]
    history = client.get(f"/api/instruments/{etf}/exposure-profiles").json()
    assert [(p["status"], p["superseded_by_id"]) for p in history] == [
        ("current", None),
        ("corrected", fixed.json()["id"]),
    ]
    assert history[1]["weights"][0]["percent"] == "60"  # the earlier version, unchanged
    trail = client.get("/api/audit", params={"entity_type": "exposure_profile"}).json()
    assert trail["items"][0]["summary"].startswith("Corrected manual Exposure profile of")


def test_a_total_beyond_the_tolerance_is_422_with_the_difference_in_the_shared_error_shape(
    client: TestClient,
) -> None:
    etf = _etf(client)
    res = client.post(
        f"/api/instruments/{etf}/exposure-profiles",
        json={
            "as_at": "2026-09-30",
            "provenance_note": "Typo",
            "weights": [
                {"dimension_key": "asset_class", "label_key": "australian_shares", "percent": "60"},
                {"dimension_key": "asset_class", "label_key": "cash", "percent": "37"},
            ],
        },
    )
    assert res.status_code == 422
    [error] = res.json()["errors"]
    assert error["field"] == "weights" and "3 points short of 100%" in error["message"]
    assert len(client.get(f"/api/instruments/{etf}/exposure-profiles").json()) == 1  # not written
