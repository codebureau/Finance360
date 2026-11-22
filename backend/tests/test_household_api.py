"""Household and Member API end to end: HTTP -> service -> real temporary SQLite."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service
from finance360.config import Settings
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
    from finance360.core.models.audit import AuditEvent

    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        assert isinstance(s, Session)
        return [(e.entity_type, e.action, e.source) for e in s.scalars(select(AuditEvent))]


def test_a_new_instance_has_an_empty_household(client: TestClient) -> None:
    res = client.get("/api/household")
    assert res.status_code == 200
    body = res.json()
    facts = body.pop("facts")
    assert body == {
        "name": "My household",
        "time_zone": "Australia/Melbourne",
        "effort_level": "light",  # until the user chooses (ADR-0020)
        "members": [],
    }
    # No Members: nothing to default the couple status from, so the Household is not modelled.
    assert facts["state"] == "not_modelled" and facts["property_accounts"] == []


def test_add_edit_delete_through_the_api_and_audit_each(client: TestClient) -> None:
    created = client.post(
        "/api/household/members", json={"name": "Casey Sample", "date_of_birth": "1995-02-03"}
    )
    assert created.status_code == 201
    member = created.json()
    assert member["name"] == "Casey Sample" and member["date_of_birth"] == "1995-02-03"

    edited = client.put(
        f"/api/household/members/{member['id']}",
        json={"name": "Casey Example", "date_of_birth": "1995-02-03"},
    )
    assert edited.status_code == 200 and edited.json()["name"] == "Casey Example"
    assert [m["name"] for m in client.get("/api/household").json()["members"]] == ["Casey Example"]

    assert client.delete(f"/api/household/members/{member['id']}").status_code == 204
    assert client.get("/api/household").json()["members"] == []
    assert _audit(client) == [("member", "create", "api"), ("member", "update", "api"),
                              ("member", "delete", "api")]  # fmt: skip


def test_renaming_the_household(client: TestClient) -> None:
    res = client.put("/api/household", json={"name": "The Examples"})
    assert res.status_code == 200 and res.json()["name"] == "The Examples"
    assert _audit(client) == [("household", "update", "api")]


def test_invalid_input_returns_clear_field_errors(client: TestClient) -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    res = client.post("/api/household/members", json={"name": " ", "date_of_birth": tomorrow})
    assert res.status_code == 422
    assert res.json() == {
        "errors": [
            {"field": "name", "message": "Name is required."},
            {"field": "date_of_birth", "message": "Date of birth must be in the past."},
        ]
    }
    missing = client.post("/api/household/members", json={})
    assert {e["field"] for e in missing.json()["errors"]} == {"name", "date_of_birth"}
    assert client.put("/api/household", json={"name": ""}).status_code == 422
    assert _audit(client) == []


def test_a_malformed_date_is_a_field_error_not_a_crash(client: TestClient) -> None:
    res = client.post(
        "/api/household/members", json={"name": "Casey Sample", "date_of_birth": "2020-13-45"}
    )
    assert res.status_code == 422
    assert res.json() == {
        "errors": [{"field": "date_of_birth", "message": "Enter a valid date as YYYY-MM-DD."}]
    }


def test_unknown_member_is_404(client: TestClient) -> None:
    body = {"name": "A", "date_of_birth": "1990-01-01"}
    assert client.put("/api/household/members/99", json=body).status_code == 404
    assert client.delete("/api/household/members/99").status_code == 404


def test_deleting_a_referenced_member_is_blocked_with_409(client: TestClient) -> None:
    member = client.post(
        "/api/household/members", json={"name": "Casey Sample", "date_of_birth": "1995-02-03"}
    ).json()

    def guard(_: Session, member_id: int) -> str | None:
        return "they own an Account" if member_id == member["id"] else None

    service.register_member_delete_guard(guard)
    try:
        res = client.delete(f"/api/household/members/{member['id']}")
    finally:
        service.unregister_member_delete_guard(guard)
    assert res.status_code == 409
    assert "they own an Account" in res.json()["message"]
    assert len(client.get("/api/household").json()["members"]) == 1


def test_the_review_fixture_is_loaded_on_first_start(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    fixture = REPO / "fixtures" / "sample_household" / service.FIXTURE_FILENAME
    (data / service.FIXTURE_FILENAME).write_text(fixture.read_text(encoding="utf-8"))
    with owner_client(create_app(_settings(data))) as c:
        body = c.get("/api/household").json()
        assert len(body["members"]) == 2
        assert all(m["name"].endswith("Sample") for m in body["members"])
        audit = _audit(c)
        assert (
            audit[:14]
            == [("member", "create", "fixture")] * 2 + [("account", "create", "fixture")] * 12
        )
        assert audit[14] == ("household", "update", "fixture")  # the principal home
        assert audit[15] == ("account_identifier", "create", "fixture")  # the sample PI Account
        assert {a for a in audit[16:]} == {
            ("valuation", "create", "fixture"),
            ("fx_rate", "create", "fixture"),
            ("instrument", "create", "fixture"),
            ("price", "create", "fixture"),
            ("scenario", "create", "fixture"),
            ("scenario", "update", "fixture"),
            ("assumption", "create", "fixture"),
            ("spending_phase", "create", "fixture"),
            ("income_stream", "create", "fixture"),
            ("tax_record", "create", "fixture"),
            ("rule_capture", "create", "fixture"),
            ("super_statement", "create", "fixture"),
        }
    json.loads(fixture.read_text(encoding="utf-8"))  # it is valid JSON
    with owner_client(create_app(_settings(data))) as c:  # a restart does not duplicate
        assert len(c.get("/api/household").json()["members"]) == 2
