"""The audit trail read API: HTTP -> service -> real temporary SQLite, with edits made through the
real write endpoints (so the records are exactly what the services write)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from finance360.capabilities.auth import logins
from finance360.capabilities.auth.types import Principal, Role
from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, as_owner, owner_client

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 17, 1, 0, 0)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _audit(client: TestClient, **params: Any) -> dict[str, Any]:
    res = client.get("/api/audit", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _edit_one_of_each(client: TestClient) -> dict[str, int]:
    """One edit each of Member, Account, Valuation and FX rate (the issue's How to test)."""
    member = client.get("/api/household").json()["members"][0]
    assert client.put(
        f"/api/household/members/{member['id']}",
        json={"name": "Alex Renamed", "date_of_birth": member["date_of_birth"]},
    ).is_success
    account = client.get("/api/accounts").json()[0]
    assert client.put(
        f"/api/accounts/{account['id']}",
        json={
            "name": "Everyday renamed",
            "category_key": account["category_key"],
            "account_type_key": account["account_type_key"],
            "ownership": account["ownership"],
            "owners": [
                {"member_id": o["member_id"], "share_bp": o["share_bp"]} for o in account["owners"]
            ],
            "currency": account["currency"],
            "notes": account["notes"],
            "opened_on": account["opened_on"],
            "tags": account["tags"],
        },
    ).is_success
    added = client.post(
        f"/api/accounts/{account['id']}/valuations",
        json={
            "as_at": "2026-10-01",
            "amount": "1234.56",
            "source": "manual",
            "provenance_note": "x",
        },
    )
    assert added.is_success, added.text
    rate = client.post(
        "/api/fx-rates",
        json={"from_ccy": "NZD", "as_at": "2026-10-01", "rate": "1.0850", "provenance_note": "x"},
    )
    assert rate.is_success, rate.text
    return {"member": member["id"], "account": account["id"], "valuation": added.json()["id"]}


def test_one_edit_of_each_kind_shows_four_entries_with_entity_time_and_summary(
    client: TestClient,
) -> None:
    ids = _edit_one_of_each(client)
    page = _audit(client, limit=4)
    assert page["total"] > 4  # the fixture's own records are in the trail too
    kinds = [e["entity_type"] for e in page["items"]]
    assert kinds == ["fx_rate", "valuation", "account", "member"]  # newest first
    fx, valuation, account, member = page["items"]
    assert valuation["entity_id"] == ids["valuation"]
    assert member["action"] == "update"
    assert "Alex Renamed" in member["summary"]
    assert member["source"] == "api"
    assert member["occurred_at"].endswith("Z") or "+" in member["occurred_at"]
    assert member["actor"] == "Login 1"  # no such Login row in this test: a plain fallback label
    assert {c["field"]: (c["before"], c["after"]) for c in member["changes"]} == {
        "Name": ("Alex Sample", "Alex Renamed")
    }
    assert {c["field"]: c["after"] for c in account["changes"]} == {"Name": "Everyday renamed"}
    assert valuation["action"] == "create"
    changes = {c["field"]: (c["before"], c["after"]) for c in valuation["changes"]}
    assert changes["Amount"] == (None, "AUD 1234.56")
    assert changes["As at"] == (None, "2026-10-01")
    assert {c["field"]: c["after"] for c in fx["changes"]}["Rate"] == "1.085"


def test_filters_by_entity_type_entity_and_account(client: TestClient) -> None:
    ids = _edit_one_of_each(client)
    only_accounts = _audit(client, entity_type="account")
    assert {e["entity_type"] for e in only_accounts["items"]} == {"account"}
    one = _audit(client, entity_type="account", entity_id=ids["account"])
    assert one["total"] >= 2  # created by the fixture, then edited
    assert all(e["entity_id"] == ids["account"] for e in one["items"])
    # An Account's trail is the Account's own records plus those of its Valuations.
    trail = _audit(client, account_id=ids["account"])
    assert {e["entity_type"] for e in trail["items"]} == {"account", "valuation"}
    assert any(e["entity_id"] == ids["valuation"] for e in trail["items"])
    other = [a["id"] for a in client.get("/api/accounts").json() if a["id"] != ids["account"]][0]
    assert all(
        e["entity_id"] != ids["valuation"] for e in _audit(client, account_id=other)["items"]
    )


def test_date_range_uses_the_household_time_zone(client: TestClient) -> None:
    _edit_one_of_each(client)
    engine = client.app.state.session_factory  # type: ignore[attr-defined]
    with engine.begin() as s:
        # Move one record to 23:30 UTC on 1 Jan: 10:30 on 2 Jan in the Household's Melbourne zone.
        s.execute(text("DROP TRIGGER IF EXISTS audit_event_no_update"))
        s.execute(text("UPDATE audit_event SET occurred_at = '2026-01-01 23:30:00' WHERE id = 1"))
    jan2 = _audit(client, date_from="2026-01-02", date_to="2026-01-02")
    assert [e["id"] for e in jan2["items"]] == [1]
    assert _audit(client, date_from="2026-01-01", date_to="2026-01-01")["total"] == 0
    assert _audit(client, date_from="2026-01-03", date_to="2026-01-02")["total"] == 0


def test_pagination_newest_first(client: TestClient) -> None:
    _edit_one_of_each(client)
    first = _audit(client, limit=3)
    second = _audit(client, limit=3, offset=3)
    ids = [e["id"] for e in first["items"] + second["items"]]
    assert ids == sorted(ids, reverse=True)
    assert len(set(ids)) == 6
    assert client.get("/api/audit", params={"limit": 0}).status_code == 422
    assert client.get("/api/audit", params={"limit": 1000}).status_code == 422


def test_actor_is_a_readable_label_for_a_real_login(client: TestClient, fast_hash: None) -> None:
    with client.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        logins.create_login(s, "a-long-sample-passphrase", now=NOW, event=None)
        login_id = logins.get_login(s).id  # type: ignore[union-attr]
    as_owner(client.app, Principal(login_id, Role.OWNER))  # type: ignore[arg-type]
    client.put("/api/household", json={"name": "Renamed Household"})
    newest = _audit(client, limit=1)["items"][0]
    assert newest["actor"] == "Owner"
    assert newest["actor_ref"] == f"login:{login_id}"
    fixture = _audit(client, limit=200)["items"][-1]
    assert fixture["actor"] == "System (fixture)"
    assert fixture["source"] == "fixture"


def test_auth_events_are_listed_without_secrets_and_for_the_owner_only(
    client: TestClient, fast_hash: None
) -> None:
    with client.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        login = logins.create_login(s, "a-long-sample-passphrase", now=NOW, event=None)
        logins.record_failure(s, login, NOW, 30, {"client": "10.0.0.1", "user_agent": "Sample"})
        logins.change_password(s, login, "another-long-sample-passphrase", now=NOW, keep_token=None)
    res = client.get("/api/audit/auth-events")
    assert res.status_code == 200
    page = res.json()
    kinds = [e["kind"] for e in page["items"]]
    assert kinds == ["password_changed", "lockout", "login_failed"]
    failed = page["items"][2]
    assert failed["label"] == "Sign-in failed"
    assert failed["detail"] == {"client": "10.0.0.1", "user_agent": "Sample"}
    assert _only(client, kind="lockout") == ["lockout"]
    assert "hash" not in res.text.lower()
    as_owner(client.app, Principal(1, Role.VIEWER))  # type: ignore[arg-type]
    assert client.get("/api/audit/auth-events").status_code == 403
    assert client.get("/api/audit").status_code == 200  # the data trail is readable by a viewer


def _only(client: TestClient, **params: Any) -> list[str]:
    res = client.get("/api/audit/auth-events", params=params)
    return [e["kind"] for e in res.json()["items"]]


def test_unknown_detail_keys_never_reach_the_response(client: TestClient, fast_hash: None) -> None:
    from finance360.capabilities.auth.events import record_event

    with client.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        record_event(
            s,
            "login",
            now=datetime.now(UTC).replace(tzinfo=None),
            detail={"client": "10.0.0.1", "totp_secret": "ABC", "code": "123456"},
        )
    (event,) = client.get("/api/audit/auth-events").json()["items"]
    assert event["detail"] == {"client": "10.0.0.1"}
