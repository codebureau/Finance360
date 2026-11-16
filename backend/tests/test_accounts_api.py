"""Accounts API end to end: HTTP -> service -> real temporary SQLite, including audit."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.config import Settings
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


def _audit(client: TestClient) -> list[tuple[str, str, str]]:
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        assert isinstance(s, Session)
        return [(e.entity_type, e.action, e.source) for e in s.scalars(select(AuditEvent))]


def _member(client: TestClient, name: str) -> int:
    res = client.post("/api/household/members", json={"name": name, "date_of_birth": "1990-01-01"})
    return int(res.json()["id"])


@pytest.fixture
def members(client: TestClient) -> tuple[int, int]:
    return _member(client, "Alex Sample"), _member(client, "Jordan Sample")


def _body(owner: int, **kw: Any) -> dict[str, Any]:
    return {
        "name": "Everyday",
        "category_key": "cash",
        "account_type_key": "transaction_account",
        "ownership": "sole",
        "owners": [{"member_id": owner}],
        **kw,
    }


def test_a_new_instance_has_no_accounts_or_tags(client: TestClient) -> None:
    assert client.get("/api/accounts").json() == []
    assert client.get("/api/tags").json() == []


def test_create_edit_and_close_through_the_api_and_audit_each(
    client: TestClient, members: tuple[int, int]
) -> None:
    alex, jordan = members
    created = client.post("/api/accounts", json=_body(alex, tags=["Everyday"], notes="hi"))
    assert created.status_code == 201
    account = created.json()
    assert (
        account["category_name"] == "Cash" and account["account_type_name"] == "Transaction account"
    )
    assert account["currency"] == "AUD" and account["closed_on"] is None
    assert account["owners"] == [
        {"member_id": alex, "member_name": "Alex Sample", "share_bp": 10000}
    ]
    assert account["tags"] == ["Everyday"]

    joint = {
        "ownership": "joint",
        "owners": [{"member_id": alex}, {"member_id": jordan}],
    }
    edited = client.put(f"/api/accounts/{account['id']}", json=_body(alex, **joint))
    assert edited.status_code == 200
    assert [o["share_bp"] for o in edited.json()["owners"]] == [5000, 5000]  # 50/50 default
    sixty_forty = {
        "ownership": "joint",
        "owners": [
            {"member_id": alex, "share_bp": 6000},
            {"member_id": jordan, "share_bp": 4000},
        ],
    }
    res = client.put(f"/api/accounts/{account['id']}", json=_body(alex, **sixty_forty))
    assert [o["share_bp"] for o in res.json()["owners"]] == [6000, 4000]
    assert client.get(f"/api/accounts/{account['id']}").json()["owners"][0]["share_bp"] == 6000

    closed = client.post(f"/api/accounts/{account['id']}/close", json={"closed_on": "2026-06-30"})
    assert closed.status_code == 200 and closed.json()["closed_on"] == "2026-06-30"
    assert _audit(client)[-6:] == [
        ("member", "create", "api"),
        ("account", "create", "api"),
        ("account", "update", "api"),
        ("account", "update", "api"),
        ("valuation", "create", "api"),
        ("account", "close", "api"),
    ]  # fmt: skip  (closing writes the zero Valuation first, in the same transaction)


def test_closed_accounts_are_hidden_unless_asked_for(
    client: TestClient, members: tuple[int, int]
) -> None:
    keep = client.post("/api/accounts", json=_body(members[0], name="Keep")).json()["id"]
    gone = client.post("/api/accounts", json=_body(members[0], name="Gone")).json()["id"]
    client.post(f"/api/accounts/{gone}/close", json={})  # defaults to today
    assert [a["id"] for a in client.get("/api/accounts").json()] == [keep]
    shown = client.get("/api/accounts", params={"include_closed": True}).json()
    assert {a["id"] for a in shown} == {keep, gone}
    assert next(a for a in shown if a["id"] == gone)["closed_on"] is not None


def test_shares_that_do_not_sum_to_100_percent_are_rejected_with_422(
    client: TestClient, members: tuple[int, int]
) -> None:
    alex, jordan = members
    fifty_sixty = {
        "ownership": "joint",
        "owners": [
            {"member_id": alex, "share_bp": 6000},
            {"member_id": jordan, "share_bp": 5000},
        ],
    }
    res = client.post("/api/accounts", json=_body(alex, **fifty_sixty))
    assert res.status_code == 422
    (error,) = res.json()["errors"]
    assert error["field"] == "owners" and "add up to 100%" in error["message"]
    assert client.get("/api/accounts").json() == []
    assert [a for a in _audit(client) if a[0] == "account"] == []


def test_invalid_input_returns_field_errors_and_malformed_input_is_422(
    client: TestClient, members: tuple[int, int]
) -> None:
    res = client.post(
        "/api/accounts",
        json=_body(members[0], name="", account_type_key="home_loan", currency="ZZZ"),
    )
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {"name", "account_type", "currency"}
    odd = client.post("/api/accounts", json=_body(members[0], opened_on="2020-13-45"))
    assert odd.status_code == 422
    assert odd.json()["errors"] == [
        {"field": "opened_on", "message": "Enter a valid date as YYYY-MM-DD."}
    ]


def test_unknown_account_is_404_and_double_close_is_422(
    client: TestClient, members: tuple[int, int]
) -> None:
    assert client.get("/api/accounts/99").status_code == 404
    assert client.put("/api/accounts/99", json=_body(members[0])).status_code == 404
    assert client.post("/api/accounts/99/close", json={}).status_code == 404
    account = client.post("/api/accounts", json=_body(members[0])).json()["id"]
    assert client.post(f"/api/accounts/{account}/close", json={}).status_code == 200
    again = client.post(f"/api/accounts/{account}/close", json={})
    assert again.status_code == 422 and again.json()["errors"][0]["field"] == "closed_on"


def test_tags_autocomplete_from_existing_tags_and_are_reused(
    client: TestClient, members: tuple[int, int]
) -> None:
    client.post("/api/accounts", json=_body(members[0], name="A", tags=["Retirement", "growth"]))
    client.post("/api/accounts", json=_body(members[1], name="B", tags=["retirement"]))
    assert client.get("/api/tags").json() == ["growth", "Retirement"]


def test_deleting_a_member_who_owns_an_account_is_blocked_with_409(
    client: TestClient, members: tuple[int, int]
) -> None:
    alex, jordan = members
    client.post("/api/accounts", json=_body(alex))
    blocked = client.delete(f"/api/household/members/{alex}")
    assert blocked.status_code == 409
    assert "Ownership of an Account" in blocked.json()["message"]
    assert client.delete(f"/api/household/members/{jordan}").status_code == 204


def test_the_review_fixture_loads_twelve_accounts_once(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    fixture = REPO / "fixtures" / "sample_household" / household.FIXTURE_FILENAME
    (data / household.FIXTURE_FILENAME).write_text(fixture.read_text(encoding="utf-8"))
    with owner_client(create_app(_settings(data))) as c:
        accounts = c.get("/api/accounts").json()
        assert len(accounts) == 12
        assert {a["category_key"] for a in accounts} == {
            "cash", "investments", "superannuation", "overseas_pensions", "property",
            "vehicles", "loans_credit",
        }  # fmt: skip
        (joint,) = [a for a in accounts if a["ownership"] == "joint"]
        assert [o["share_bp"] for o in joint["owners"]] == [5000, 5000]
        assert [a["currency"] for a in accounts if a["currency"] != "AUD"] == ["GBP"]
        assert c.get("/api/tags").json() == ["emergency fund", "everyday", "growth", "retirement"]
    with owner_client(create_app(_settings(data))) as c:  # a restart does not duplicate
        assert len(c.get("/api/accounts").json()) == 12


def test_a_failed_commit_is_an_error_response_not_a_2xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.orm import SessionTransaction

    with owner_client(create_app(_settings(tmp_path / "data")), raise_server_exceptions=False) as c:
        member = _member(c, "Alex Sample")
        real_commit = SessionTransaction.commit

        def failing(self: SessionTransaction, *args: Any, **kw: Any) -> None:
            real_commit(self, *args, **kw)
            raise RuntimeError("disk full")

        monkeypatch.setattr(SessionTransaction, "commit", failing)
        res = c.post("/api/accounts", json=_body(member))
        monkeypatch.undo()
        assert res.status_code == 500
