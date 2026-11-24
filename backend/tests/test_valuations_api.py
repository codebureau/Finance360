"""Valuations and the Household time zone through the API: HTTP -> service -> real temporary
SQLite, including audit, the close confirmation and the shipped sample household."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.config import Settings
from finance360.core.migrate import alembic_config, migrate
from finance360.core.models.audit import AuditEvent
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]


def _settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir, port=8000, git_sha="dev", static_dir=None)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with owner_client(create_app(_settings(tmp_path / "data"))) as c:
        yield c


def _account(client: TestClient, currency: str = "AUD", name: str = "Everyday") -> int:
    member = client.post(
        "/api/household/members", json={"name": f"Owner of {name}", "date_of_birth": "1990-01-01"}
    ).json()["id"]
    res = client.post(
        "/api/accounts",
        json={
            "name": name,
            "category_key": "cash",
            "account_type_key": "transaction_account",
            "currency": currency,
            "owners": [{"member_id": member}],
        },
    )
    return int(res.json()["id"])


def _body(
    as_at: str | None, amount: str, source: str = "manual", note: str = "Checked"
) -> dict[str, Any]:
    return {"as_at": as_at, "amount": amount, "source": source, "provenance_note": note}


def _audit(client: TestClient) -> list[tuple[str, str]]:
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        assert isinstance(s, Session)
        return [(e.entity_type, e.action) for e in s.scalars(select(AuditEvent))]


def test_add_list_correct_and_remove_through_the_api(client: TestClient) -> None:
    account = _account(client)
    assert client.get(f"/api/accounts/{account}/valuations").json() == []

    first = client.post(f"/api/accounts/{account}/valuations", json=_body("2026-03-31", "100.50"))
    assert first.status_code == 201
    body = first.json()
    assert (body["amount"], body["amount_minor"], body["currency"]) == ("100.50", 10_050, "AUD")
    assert (body["source"], body["provenance_note"], body["status"]) == (
        "manual",
        "Checked",
        "current",
    )
    client.post(
        f"/api/accounts/{account}/valuations", json=_body("2026-01-31", "90", "statement", "Jan")
    )
    middle = client.post(
        f"/api/accounts/{account}/valuations", json=_body("2026-02-28", "95", "estimate", "Guess")
    )

    listed = client.get(f"/api/accounts/{account}/valuations").json()
    assert [v["as_at"] for v in listed] == ["2026-03-31", "2026-02-28", "2026-01-31"]  # by date

    corrected = client.put(
        f"/api/valuations/{middle.json()['id']}",
        json=_body("2026-02-28", "96.25", "estimate", "Better guess"),
    )
    assert corrected.status_code == 200 and corrected.json()["amount"] == "96.25"
    listed = client.get(f"/api/accounts/{account}/valuations").json()
    feb = [v for v in listed if v["as_at"] == "2026-02-28"]
    assert [(v["amount"], v["status"]) for v in feb] == [
        ("96.25", "current"),
        ("95.00", "corrected"),
    ]
    assert feb[1]["superseded_by_id"] == feb[0]["id"] and feb[1]["superseded_at"]

    removed = client.delete(f"/api/valuations/{first.json()['id']}")
    assert removed.status_code == 204
    gone = [
        v
        for v in client.get(f"/api/accounts/{account}/valuations").json()
        if v["as_at"] == "2026-03-31"
    ]
    assert [(v["status"], v["superseded_by_id"]) for v in gone] == [("removed", None)]

    kinds = [a for a in _audit(client) if a[0] == "valuation"]
    assert kinds == [("valuation", "create")] * 3 + [("valuation", "supersede")] * 2


def test_foreign_currency_amounts_are_stored_and_returned_in_the_account_currency(
    client: TestClient,
) -> None:
    gbp = _account(client, "GBP", "UK Pension")
    res = client.post(f"/api/accounts/{gbp}/valuations", json=_body("2026-03-31", "250000.99"))
    assert (res.json()["amount"], res.json()["currency"]) == ("250000.99", "GBP")
    wrong = client.post(
        f"/api/accounts/{gbp}/valuations", json={**_body("2026-03-30", "1"), "currency": "AUD"}
    )
    assert wrong.status_code == 422 and wrong.json()["errors"][0]["field"] == "currency"


def test_validation_errors_are_422_with_field_messages(client: TestClient) -> None:
    account = _account(client)
    res = client.post(
        f"/api/accounts/{account}/valuations", json=_body("2999-01-01", "abc", "guess", " ")
    )
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {
        "as_at",
        "amount",
        "source",
        "provenance_note",
    }
    malformed = client.post(
        f"/api/accounts/{account}/valuations", json={"as_at": "2026-13-45", "amount": "1"}
    )
    assert malformed.status_code == 422 and malformed.json()["errors"][0]["field"] == "as_at"
    ok = client.post(f"/api/accounts/{account}/valuations", json=_body("2026-03-31", "1"))
    dup = client.post(f"/api/accounts/{account}/valuations", json=_body("2026-03-31", "2"))
    assert ok.status_code == 201 and dup.status_code == 422


def test_a_float_amount_is_refused(client: TestClient) -> None:
    account = _account(client)
    res = client.post(
        f"/api/accounts/{account}/valuations", json={**_body("2026-03-31", "1"), "amount": 12.5}
    )
    assert res.status_code == 422


def test_unknown_account_or_valuation_is_404(client: TestClient) -> None:
    assert client.get("/api/accounts/999/valuations").status_code == 404
    assert (
        client.post("/api/accounts/999/valuations", json=_body("2026-03-31", "1")).status_code
        == 404
    )
    assert client.put("/api/valuations/999", json=_body("2026-03-31", "1")).status_code == 404
    assert client.delete("/api/valuations/999").status_code == 404


def test_closing_over_a_non_zero_balance_is_409_until_confirmed(client: TestClient) -> None:
    account = _account(client)
    client.post(
        f"/api/accounts/{account}/valuations",
        json=_body("2026-03-31", "1500.25", "statement", "Mar"),
    )
    refused = client.post(f"/api/accounts/{account}/close", json={"closed_on": "2026-06-30"})
    assert refused.status_code == 409 and "AUD 1500.25" in refused.json()["message"]
    assert client.get(f"/api/accounts/{account}").json()["closed_on"] is None
    assert len(client.get(f"/api/accounts/{account}/valuations").json()) == 1

    ok = client.post(
        f"/api/accounts/{account}/close", json={"closed_on": "2026-06-30", "confirm_non_zero": True}
    )
    assert ok.status_code == 200 and ok.json()["closed_on"] == "2026-06-30"
    history = client.get(f"/api/accounts/{account}/valuations").json()
    assert [(v["as_at"], v["amount"], v["provenance_note"]) for v in history][0] == (
        "2026-06-30",
        "0.00",
        "Account closed",
    )
    assert len(history) == 2


def test_the_household_time_zone_can_be_read_and_changed(client: TestClient) -> None:
    assert client.get("/api/household").json()["time_zone"] == "Australia/Melbourne"
    res = client.put(
        "/api/household", json={"name": "The Sample Household", "time_zone": "Pacific/Auckland"}
    )
    assert res.status_code == 200 and res.json()["time_zone"] == "Pacific/Auckland"
    assert client.get("/api/household").json()["time_zone"] == "Pacific/Auckland"
    renamed = client.put("/api/household", json={"name": "Renamed"})  # omitted: unchanged
    assert renamed.json()["time_zone"] == "Pacific/Auckland"
    bad = client.put("/api/household", json={"name": "Renamed", "time_zone": "Mars/Base"})
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "time_zone"
    assert client.get("/api/household").json()["time_zone"] == "Pacific/Auckland"


# --- the migration and the shipped sample household -----------------------------------------


def test_migration_0005_keeps_an_existing_household_and_defaults_its_time_zone(
    tmp_path: Path,
) -> None:
    import sqlite3

    from alembic import command

    command.upgrade(alembic_config(tmp_path), "0004")
    db = tmp_path / "finance360.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO household (id, name) VALUES (1, 'Old household')")
    migrate(tmp_path)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT name, time_zone FROM household").fetchall() == [
            ("Old household", "Australia/Melbourne")
        ]
        assert con.execute("SELECT count(*) FROM valuation").fetchone() == (0,)


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    with owner_client(create_app(_settings(data))) as c:
        yield c


def test_the_sample_household_has_about_two_years_of_valuations_with_gaps_per_account(
    sample: TestClient,
) -> None:
    accounts = sample.get("/api/accounts").json()
    assert len(accounts) == 13
    for account in accounts:
        history = sample.get(f"/api/accounts/{account['id']}/valuations").json()
        dates = sorted(date.fromisoformat(v["as_at"]) for v in history)
        if account["name"].startswith("Sample Term Deposit"):
            # Deliberately short: opened Jan 2025, first valued Mar 2025, so earlier month-ends
            # show an incomplete (flagged) point in the history chart.
            assert [d.isoformat() for d in dates][0] == "2025-03-15" and len(dates) == 3
            continue
        if account["name"].startswith(("Dormant", "Old trailer")):
            # Left untouched on purpose (zero balance): What's due asks for a new figure. The
            # dormant super fund's one other figure is its Super statement's zero at 30 June 2025.
            wanted = ["2024-10-01"]
            if account["name"].startswith("Dormant super"):
                wanted.append("2025-06-30")
            assert [d.isoformat() for d in dates] == wanted
            continue
        if account["name"].startswith("Joint investment account"):
            # Opened empty (#87): one opening 0.00; its value comes from imports, not Valuations.
            assert [d.isoformat() for d in dates] == ["2026-07-02"]
            continue
        assert 12 <= len(history) <= 30, account["name"]
        assert (dates[-1] - dates[0]).days >= 600, account["name"]  # about 24 months
        months = {(d.year, d.month) for d in dates}
        span = (dates[-1].year - dates[0].year) * 12 + dates[-1].month - dates[0].month + 1
        assert len(months) < span, f"{account['name']} should have gaps"
        assert {v["currency"] for v in history} == {account["currency"]}
        assert all(v["provenance_note"] for v in history)
        assert all(v["status"] == "current" for v in history)
    assert {"GBP"} <= {a["currency"] for a in accounts}


def test_sample_valuations_are_audited_as_fixture_changes(sample: TestClient) -> None:
    with sample.app.state.session_factory() as s:  # type: ignore[attr-defined]
        sources = {
            e.source
            for e in s.scalars(select(AuditEvent).where(AuditEvent.entity_type == "valuation"))
        }
    assert sources == {"fixture"}


def test_non_ascii_digits_and_ancient_dates_are_422(client: TestClient) -> None:
    account = _account(client)
    url = f"/api/accounts/{account}/valuations"
    arabic = client.post(url, json=_body("2026-03-31", "١٢٣"))
    assert arabic.status_code == 422 and arabic.json()["errors"][0]["field"] == "amount"
    old = client.post(url, json=_body("1899-12-31", "1"))
    assert old.status_code == 422 and old.json()["errors"][0]["field"] == "as_at"
    assert client.post(url, json=_body("1900-01-01", "1")).status_code == 201
    assert len(client.get(url).json()) == 1


def test_closing_before_a_current_valuation_is_422_and_writes_nothing(client: TestClient) -> None:
    account = _account(client)
    url = f"/api/accounts/{account}/valuations"
    client.post(url, json=_body("2026-03-31", "0", "statement", "Mar"))
    refused = client.post(f"/api/accounts/{account}/close", json={"closed_on": "2026-02-28"})
    assert refused.status_code == 422
    error = refused.json()["errors"][0]
    assert error["field"] == "closed_on"
    assert error["message"] == "This Account has Valuations after that date."
    assert client.get(f"/api/accounts/{account}").json()["closed_on"] is None
    assert len(client.get(url).json()) == 1
    # A removed (superseded) later Valuation no longer blocks the close.
    only = client.get(url).json()[0]["id"]
    client.delete(f"/api/valuations/{only}")
    ok = client.post(f"/api/accounts/{account}/close", json={"closed_on": "2026-02-28"})
    assert ok.status_code == 200
