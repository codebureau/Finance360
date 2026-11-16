"""The commit, diff and Transactions endpoints over HTTP -> service -> real temporary SQLite with
the synthetic household. The real-server tests (a real sign-in, a stale
session, concurrent commits) are in ``test_imports_commit_real_server.py``."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
MASKED_CASH_NAME = "cash_transactions_acc••• 0001_01-Jul-2026_30-Sep-2026.csv"
OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _pi(c: TestClient) -> int:
    return next(
        a["id"] for a in c.get("/api/accounts").json() if a["name"] == "Sample Brokerage account"
    )


def _stage(c: TestClient, name: str) -> dict[str, Any]:
    response = c.post(
        "/api/imports",
        content=(IMPORTS / name).read_bytes(),
        headers=OCTET,
        params={"account_id": _pi(c), "file_name": name},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _match_snhx(c: TestClient, import_id: int) -> None:
    made = c.post(
        f"/api/imports/{import_id}/references/create",
        json={
            "scheme": "ticker",
            "ref": "SNHX",
            "name": "Sample New Horizons ETF",
            "kind": "etf",
            "currency": "AUD",
        },
    )
    assert made.status_code == 200, made.text


def _transactions(c: TestClient, stream: str, **params: Any) -> dict[str, Any]:
    response = c.get(f"/api/accounts/{_pi(c)}/transactions", params={"stream": stream, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_diff_of_a_first_import_is_all_added_and_staged_rows_say_so(client: TestClient) -> None:
    imp = _stage(client, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
    diff = client.get(f"/api/imports/{imp['id']}/diff").json()
    assert (diff["added"], diff["removed"], diff["unchanged"]) == (14, 0, 0)
    assert (diff["scope_from"], diff["scope_to"]) == ("2026-07-01", "2026-09-30")
    assert diff["commit_blocked"] is False
    rows = client.get(f"/api/imports/{imp['id']}/rows", params={"limit": 50}).json()["items"]
    assert {r["change"] for r in rows} == {"added"}


def test_commit_makes_transactions_visible_per_stream_and_the_import_committed(
    client: TestClient,
) -> None:
    imp = _stage(client, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
    committed = client.post(f"/api/imports/{imp['id']}/commit")
    assert committed.status_code == 200, committed.text
    body = committed.json()
    assert body["status"] == "committed" and body["commit_blocked"] is False
    assert (
        body["committed"]["added"],
        body["committed"]["replaced"],
        body["committed"]["unchanged"],
    ) == (
        14,
        0,
        0,
    )

    cash = _transactions(client, "cash", limit=50)
    assert (cash["total"], cash["current_cash"], cash["current_trade"]) == (14, 14, 0)
    newest = cash["items"][0]
    assert newest["occurred_on"] == "2026-09-30"  # newest first
    buy = next(
        t
        for t in cash["items"]
        if t["kind"] == "trade_settlement" and t["occurred_on"] == "2026-09-30"
    )
    assert buy["amount"] == {"amount": "-1922.15", "amount_minor": -192215, "currency": "AUD"}
    assert (buy["import_id"], buy["file_name"], buy["superseded_at"]) == (
        imp["id"],
        MASKED_CASH_NAME,  # the account number in the file name is masked when stored
        None,
    )
    assert _transactions(client, "trade")["total"] == 0

    # a committed Import has no diff left to show, and its rows no longer carry a change
    assert client.get(f"/api/imports/{imp['id']}/diff").status_code == 422
    rows = client.get(f"/api/imports/{imp['id']}/rows").json()["items"]
    assert {r["change"] for r in rows} == {None}
    trail = client.get(
        "/api/audit", params={"entity_type": "import", "entity_id": imp["id"]}
    ).json()
    latest = trail["items"][0]
    assert latest["actor"].startswith("Import (") and latest["source"] == f"import:{imp['id']}"
    assert (
        latest["summary"]
        == f"Committed cash file {MASKED_CASH_NAME}: 14 added, 0 removed, 0 unchanged"
    )


def test_commit_is_refused_while_a_reference_is_unmatched_and_then_allowed(
    client: TestClient,
) -> None:
    imp = _stage(client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
    refused = client.post(f"/api/imports/{imp['id']}/commit")
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["field"] == "references"
    assert _transactions(client, "trade")["total"] == 0
    assert client.get(f"/api/imports/{imp['id']}/diff").json()["commit_blocked"] is True
    _match_snhx(client, imp["id"])
    assert client.post(f"/api/imports/{imp['id']}/commit").status_code == 200
    trades = _transactions(client, "trade")["items"]
    assert len(trades) == 5
    sell = next(t for t in trades if t["kind"] == "sell")
    assert (sell["quantity"], sell["unit_price"], sell["unit_price_ccy"]) == ("-40", "6.40", "AUD")
    assert sell["fee"]["amount"] == "9.50" and sell["amount"] is None  # informational only


def test_committing_twice_or_a_missing_import_is_a_clear_error(client: TestClient) -> None:
    imp = _stage(client, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
    assert client.post(f"/api/imports/{imp['id']}/commit").status_code == 200
    again = client.post(f"/api/imports/{imp['id']}/commit")
    assert again.status_code == 422 and again.json()["errors"][0]["field"] == "status"
    assert _transactions(client, "cash")["total"] == 14
    assert client.post("/api/imports/9999/commit").status_code == 404
    assert client.get("/api/imports/9999/diff").status_code == 404


def test_the_overlapping_later_file_shows_added_and_unchanged_and_supersedes_nothing_it_keeps(
    client: TestClient,
) -> None:
    trades = _stage(client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
    _match_snhx(client, trades["id"])
    client.post(f"/api/imports/{trades['id']}/commit")
    first = _stage(client, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
    client.post(f"/api/imports/{first['id']}/commit")

    later = _stage(client, "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_05-Oct-2026.csv")
    diff = client.get(f"/api/imports/{later['id']}/diff").json()
    assert (diff["added"], diff["removed"], diff["unchanged"]) == (1, 0, 7)
    assert (diff["scope_from"], diff["scope_to"]) == ("2026-09-01", "2026-10-05")
    rows = client.get(f"/api/imports/{later['id']}/rows", params={"limit": 50}).json()["items"]
    assert [r["change"] for r in rows].count("added") == 1
    new = next(r for r in rows if r["change"] == "added")
    assert (new["occurred_on"], new["amount"]["amount"]) == ("2026-10-05", "1.02")
    assert client.get(f"/api/imports/{later['id']}/diff/removed").json() == {
        "total": 0,
        "items": [],
    }

    done = client.post(f"/api/imports/{later['id']}/commit").json()
    assert done["committed"] == {
        "committed_at": done["committed"]["committed_at"],
        "added": 1,
        "replaced": 0,
        "unchanged": 7,
        "instruments_without_price": 0,  # every Instrument it names already has a Price
        "offer_fetch": False,
    }
    assert _transactions(client, "cash")["current_cash"] == 15
    assert _transactions(client, "trade")["current_trade"] == 5  # the trade Stream is untouched


def test_removed_rows_are_listed_and_superseded_rows_stay_retrievable(client: TestClient) -> None:
    first = _stage(client, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
    client.post(f"/api/imports/{first['id']}/commit")
    pi = _pi(client)
    shorter = (
        (IMPORTS / "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
        .read_text()
        .splitlines()
    )
    # drop the 1000.00 deposit of 01-Sep-2026 from the same period
    trimmed = "\n".join(
        line for line in shorter if "Deposit from linked bank,CASH,,1000.00" not in line
    )
    staged = client.post(
        "/api/imports",
        content=trimmed.encode(),
        headers=OCTET,
        # The stated period (and so Coverage) comes from the name; this one matches the original.
        params={
            "account_id": pi,
            "file_name": "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026 (1).csv",
        },
    ).json()
    removed = client.get(f"/api/imports/{staged['id']}/diff/removed").json()
    assert removed["total"] == 1 and removed["items"][0]["amount"]["amount"] == "1000.00"
    assert removed["items"][0]["superseded_at"] is None  # not yet: it is only what would go
    client.post(f"/api/imports/{staged['id']}/commit")

    assert _transactions(client, "cash")["total"] == 13
    with_superseded = _transactions(client, "cash", include_superseded=True, limit=50)
    assert with_superseded["total"] == 14
    gone = [t for t in with_superseded["items"] if t["superseded_at"] is not None]
    assert [(t["amount"]["amount"], t["import_id"]) for t in gone] == [("1000.00", first["id"])]
    # Like added_at and committed_at, superseded_at is sent as UTC, not as a naive time.
    assert datetime.fromisoformat(gone[0]["superseded_at"]).utcoffset() == timedelta(0)


def test_transactions_paginate_and_validate_their_parameters(client: TestClient) -> None:
    staged = _stage(client, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
    client.post(f"/api/imports/{staged['id']}/commit")
    page = _transactions(client, "cash", limit=5, offset=10)
    assert page["total"] == 14 and len(page["items"]) == 4
    assert (
        client.get(
            f"/api/accounts/{_pi(client)}/transactions", params={"stream": "nope"}
        ).status_code
        == 422
    )
    assert client.get("/api/accounts/9999/transactions").status_code == 404
    bad = client.get(f"/api/accounts/{_pi(client)}/transactions", params={"limit": 0})
    assert bad.status_code == 422 and "errors" in bad.json()
