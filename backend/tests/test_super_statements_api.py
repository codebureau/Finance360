"""Super statements through the API: HTTP -> service -> real temporary SQLite. The shared 422 error
shape, warn-and-tick, the Valuation kept in step, and the shipped sample household."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

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


def _account(
    client: TestClient, category: str = "superannuation", kind: str = "super_fund_account"
) -> int:
    member = client.post(
        "/api/household/members", json={"name": "Owner Sample", "date_of_birth": "1990-01-01"}
    ).json()["id"]
    res = client.post(
        "/api/accounts",
        json={
            "name": "Sample Super",
            "category_key": category,
            "account_type_key": kind,
            "owners": [{"member_id": member}],
        },
    )
    return int(res.json()["id"])


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "period_start": "2025-07-01",
        "period_end": "2026-06-30",
        "opening": "1000.00",
        "contributions_in": "200.00",
        "investment_returns": "50.00",
        "withdrawals": "30.00",
        "closing": "1220.00",
    }
    body.update(over)
    return body


def _fields(res: Any) -> dict[str, str]:
    assert res.status_code == 422
    return {e["field"]: e["message"] for e in res.json()["errors"]}


def test_add_list_correct_and_remove_keep_the_valuation_in_step(client: TestClient) -> None:
    account = _account(client)
    assert client.get(f"/api/accounts/{account}/super-statements").json() == []

    rich = _body(
        contributions_tax="20.00",
        fees_before_rebate="15.00",
        rebate="5.00",
        fees_inside_investment="9.00",
        insurance_premiums="0.00",
        concessional_received="150.00",
        options=[
            {"name": "Growth", "value": "700.00", "units": "100.5", "unit_price": "6.965174"},
            {"name": "Balanced", "value": "520.01"},
        ],
    )
    created = client.post(f"/api/accounts/{account}/super-statements", json=rich)
    assert created.status_code == 201, created.text
    statement = created.json()
    assert statement["financial_year"] == 2026
    assert statement["closing"] == "1220.00"
    assert statement["other_withdrawals"] is None  # not stated
    assert statement["insurance_premiums"] == "0.00"  # stated zero
    assert statement["options"][0] == {
        "name": "Growth",
        "units": "100.500000",
        "unit_price": "6.965174",
        "value": "700.00",
    }
    assert statement["options"][1]["units"] is None
    assert [c["status"] for c in statement["checks"]] == ["ok", "ok", "ok"]
    assert statement["ticked_checks"] == []

    valuations = client.get(f"/api/accounts/{account}/valuations").json()
    assert [(v["as_at"], v["amount"], v["source"], v["status"]) for v in valuations] == [
        ("2026-06-30", "1220.00", "statement", "current")
    ]
    assert valuations[0]["id"] == statement["valuation_id"]

    corrected = client.put(
        f"/api/super-statements/{statement['id']}",
        json={**rich, "opening": "1010.00", "closing": "1230.00", "options": []},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["id"] != statement["id"]
    assert corrected.json()["options"] == []
    listed = client.get(f"/api/accounts/{account}/super-statements").json()
    assert [s["id"] for s in listed] == [corrected.json()["id"]]  # only the current one
    valuations = client.get(f"/api/accounts/{account}/valuations").json()
    assert sorted((v["amount"], v["status"]) for v in valuations) == [
        ("1220.00", "corrected"),
        ("1230.00", "current"),
    ]

    assert client.delete(f"/api/super-statements/{corrected.json()['id']}").status_code == 204
    assert client.get(f"/api/accounts/{account}/super-statements").json() == []
    valuations = client.get(f"/api/accounts/{account}/valuations").json()
    assert {v["status"] for v in valuations} == {"corrected", "removed"}
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        assert isinstance(s, Session)
        actions = [
            e.action
            for e in s.scalars(
                select(AuditEvent).where(AuditEvent.entity_type == "super_statement")
            )
        ]
    assert actions == ["create", "supersede", "supersede"]


def test_a_failed_check_is_a_422_naming_the_check_until_it_is_ticked(client: TestClient) -> None:
    account = _account(client)
    off = _body(closing="1225.00")
    fields = _fields(client.post(f"/api/accounts/{account}/super-statements", json=off))
    assert list(fields) == ["checks.roll"]
    assert "1220.00" in fields["checks.roll"]
    assert client.get(f"/api/accounts/{account}/valuations").json() == []

    saved = client.post(
        f"/api/accounts/{account}/super-statements", json={**off, "ticked_checks": ["roll"]}
    )
    assert saved.status_code == 201
    assert saved.json()["ticked_checks"] == ["roll"]
    assert [c["status"] for c in saved.json()["checks"]] == [
        "warn",
        "not_applicable",
        "not_applicable",
    ]


def test_the_accounts_audit_trail_includes_its_super_statements(client: TestClient) -> None:
    account = _account(client)
    client.post(f"/api/accounts/{account}/super-statements", json=_body())
    trail = client.get("/api/audit", params={"account_id": account}).json()["items"]
    kinds = {e["entity_type"] for e in trail}
    assert {"super_statement", "valuation", "account"} <= kinds
    statement = next(e for e in trail if e["entity_type"] == "super_statement")
    assert "Super statement for 'Sample Super', 2025-26" in statement["summary"]
    assert {"field": "Closing", "before": None, "after": "AUD 1220.00"} in statement["changes"]


def test_the_check_endpoint_reports_without_saving(client: TestClient) -> None:
    account = _account(client)
    res = client.post(
        f"/api/accounts/{account}/super-statements/checks",
        json=_body(closing="1225.00", withdrawals="35.00", rebate="1.00"),
    )
    assert res.status_code == 200
    by_name = {c["name"]: c for c in res.json()}
    assert by_name["roll"]["status"] == "warn"
    assert by_name["outflows"]["status"] == "warn"
    assert by_name["options"]["status"] == "not_applicable"
    assert client.get(f"/api/accounts/{account}/super-statements").json() == []
    assert client.get(f"/api/accounts/{account}/valuations").json() == []


def test_the_check_endpoint_also_answers_for_a_year_that_already_has_a_statement(
    client: TestClient,
) -> None:
    """The form checks as figures are typed, including when correcting an existing year: the
    one-statement-a-year rule belongs to saving, not to the live check."""
    account = _account(client)
    assert client.post(f"/api/accounts/{account}/super-statements", json=_body()).status_code == 201
    res = client.post(
        f"/api/accounts/{account}/super-statements/checks", json=_body(closing="1.00")
    )
    assert res.status_code == 200
    assert {c["name"]: c["status"] for c in res.json()}["roll"] == "warn"


def test_malformed_input_is_a_422_in_the_shared_shape(client: TestClient) -> None:
    account = _account(client)
    res = client.post(
        f"/api/accounts/{account}/super-statements",
        json=_body(
            closing="abc",
            period_end="2026-05-31",
            ticked_checks=["roll"],
            options=[{"name": "", "value": "1.00", "units": "-3"}],
        ),
    )
    assert res.status_code == 422
    assert set(res.json()) == {"errors"}
    assert set(_fields(res)) == {"closing", "period_start", "options.0.name", "options.0.units"}

    duplicate = client.post(f"/api/accounts/{account}/super-statements", json=_body())
    assert duplicate.status_code == 201
    again = _fields(client.post(f"/api/accounts/{account}/super-statements", json=_body()))
    assert "Correct that one instead" in again["period_start"]


def test_a_non_super_account_and_a_missing_one(client: TestClient) -> None:
    cash = _account(client, "cash", "transaction_account")
    assert "account_id" in _fields(
        client.post(f"/api/accounts/{cash}/super-statements", json=_body())
    )
    assert client.get("/api/accounts/9999/super-statements").status_code == 404
    assert client.put("/api/super-statements/9999", json=_body()).status_code == 404
    assert client.delete("/api/super-statements/9999").status_code == 404


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    with owner_client(create_app(_settings(data))) as c:
        yield c


def test_the_sample_household_has_a_rich_and_a_roll_only_statement_that_agree(
    sample: TestClient,
) -> None:
    accounts = {a["name"]: a["id"] for a in sample.get("/api/accounts").json()}
    rich = sample.get(f"/api/accounts/{accounts['Sample Super Fund']}/super-statements").json()
    assert len(rich) == 1
    assert rich[0]["financial_year"] == 2026
    assert [o["name"] for o in rich[0]["options"]] == ["Growth", "Balanced", "High Growth"]
    assert [c["status"] for c in rich[0]["checks"]] == ["ok", "ok", "ok"]
    assert rich[0]["ticked_checks"] == []
    options_check = rich[0]["checks"][2]
    assert "within rounding" in options_check["message"]  # the one-cent difference
    assert rich[0]["other_withdrawals"] is None and rich[0]["division_293_released"] == "0.00"

    basic = sample.get(
        f"/api/accounts/{accounts['Dormant super fund (Sample Super)']}/super-statements"
    ).json()
    assert len(basic) == 1
    assert basic[0]["financial_year"] == 2025
    assert basic[0]["options"] == [] and basic[0]["contributions_tax"] is None
    assert [c["status"] for c in basic[0]["checks"]] == ["ok", "not_applicable", "not_applicable"]

    # Each statement recorded its 30 June Valuation.
    for statement, account, day in (
        (rich[0], "Sample Super Fund", "2026-06-30"),
        (basic[0], "Dormant super fund (Sample Super)", "2025-06-30"),
    ):
        history = sample.get(f"/api/accounts/{accounts[account]}/valuations").json()
        match = [v for v in history if v["id"] == statement["valuation_id"]]
        assert match and match[0]["as_at"] == day and match[0]["amount"] == statement["closing"]
