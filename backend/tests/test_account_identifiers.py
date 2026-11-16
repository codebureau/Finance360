"""Account identifiers: masking, the explicit reveal, uniqueness among current rows, supersession,
the audit trail and the database's own guards. Integration against a real migrated
temporary SQLite; the API cases drive the real app."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.auth.types import Principal, Role
from finance360.capabilities.imports import account_identifiers as svc
from finance360.capabilities.imports.account_identifiers import IdentifierInput
from finance360.config import Settings
from finance360.core.account_identifier import mask_value
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.account import Account
from finance360.core.models.account_identifier import AccountIdentifier
from finance360.core.models.audit import AuditEvent
from finance360.main import create_app
from support import TestClient, as_owner, owner_client

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
SECRET = "SAMPLE-PI-0001"
PI = "Sample Brokerage account"
EVERYDAY = "Everyday account (Sample Bank)"


def _id(s: Session, name: str) -> int:
    return s.scalars(select(Account).where(Account.name == name)).one().id


def _fields(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


# --- masking -------------------------------------------------------------------------------


def test_mask_shows_only_the_last_four_characters() -> None:
    assert mask_value("SAMPLE-PI-0001") == "••• 0001"
    assert mask_value("062 000 12345678") == "••• 5678"
    assert mask_value("1234") == "••••"  # four or fewer: nothing shown
    assert mask_value("12") == "••••"


@given(st.text(min_size=5, max_size=40).filter(lambda t: len("".join(t.split())) > 4))
def test_a_mask_never_contains_more_than_the_last_four_characters(value: str) -> None:
    masked = mask_value(value)
    clean = "".join(value.split())
    assert masked.endswith(clean[-4:]) and len(masked) == 3 + 1 + 4


# --- the service ---------------------------------------------------------------------------


def test_the_sample_pi_account_holds_the_synthetic_placeholder(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory() as s:
        idents = svc.list_identifiers(s, _id(s, PI))
        assert [(i.kind, i.value, i.institution) for i in idents] == [
            ("vendor_account_id", SECRET, "Vanguard PI")
        ]
        assert svc.list_identifiers(s, _id(s, EVERYDAY)) == []


def test_audit_snapshots_and_summaries_store_the_masked_value_only(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        account = _id(s, EVERYDAY)
        added = svc.add_identifier(
            s, CTX, account, IdentifierInput("bsb_account", "062-000 99887766")
        )
        svc.remove_identifier(s, CTX, account, added.id)
    with sample_factory() as s:
        events = list(
            s.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_type == "account_identifier")
                .order_by(AuditEvent.id)
            )
        )
        assert [e.action for e in events] == ["create", "create", "delete"]  # fixture, add, remove
        text = repr([(e.summary, e.before, e.after) for e in events])
        for real in (SECRET, "99887766", "062-000"):
            assert real not in text
        assert events[1].after is not None
        assert events[1].after["value"] == "••• 7766"
        assert events[2].before is not None and events[2].before["value"].endswith("7766")


def test_errors_never_echo_a_value(sample_factory: sessionmaker[Session]) -> None:
    with sample_factory.begin() as s:
        everyday = _id(s, EVERYDAY)
    with sample_factory.begin() as s, pytest.raises(ValidationError) as clash:
        svc.add_identifier(
            s, CTX, everyday, IdentifierInput("vendor_account_id", SECRET.lower(), "vanguard pi")
        )
    assert SECRET.lower() not in str(clash.value).lower()
    assert PI in clash.value.errors[0].message  # says which Account holds it, not the value
    with sample_factory.begin() as s, pytest.raises(ValidationError) as bad:
        svc.add_identifier(s, CTX, everyday, IdentifierInput("passport", ""))
    assert set(_fields(bad)) == {"kind", "value"}
    with sample_factory.begin() as s, pytest.raises(NotFoundError) as nope:
        svc.reveal(s, everyday, 9999)
    assert SECRET not in str(nope.value)


def test_a_clash_among_current_rows_is_rejected_whatever_the_case_spacing_or_institution_case(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _id(s, PI)
        for spelling in (SECRET, "sample-pi-0001", " SAMPLE-PI- 0001 "):
            with pytest.raises(ValidationError), s.begin_nested():
                svc.add_identifier(
                    s, CTX, pi, IdentifierInput("vendor_account_id", spelling, "VANGUARD PI")
                )
        # A different institution, a different kind: not a clash.
        svc.add_identifier(s, CTX, pi, IdentifierInput("vendor_account_id", SECRET, "Other Broker"))
        svc.add_identifier(s, CTX, pi, IdentifierInput("member_number", SECRET))


def test_no_institution_is_one_value_so_a_second_blank_institution_clashes(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        a, b = _id(s, PI), _id(s, EVERYDAY)
        svc.add_identifier(s, CTX, a, IdentifierInput("last4", "4321"))
        with pytest.raises(ValidationError):
            svc.add_identifier(s, CTX, b, IdentifierInput("last4", "4321"))
    # And the database says the same on its own, with NULL institutions (SQLite treats NULLs in a
    # plain unique index as distinct, so this is what the institution_norm column is for).
    with sample_factory.begin() as s, pytest.raises(IntegrityError):
        s.add(
            AccountIdentifier(
                account_id=b,
                kind="last4",
                value="4321",
                value_norm="4321",
                institution=None,
                institution_norm="",
                recorded_at=datetime(2026, 1, 1),
            )
        )
        s.flush()


def test_removing_an_identifier_supersedes_it_so_the_value_can_be_used_again(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi, everyday = _id(s, PI), _id(s, EVERYDAY)
        ident = svc.list_identifiers(s, pi)[0]
        svc.remove_identifier(s, CTX, pi, ident.id)
        assert svc.list_identifiers(s, pi) == []
        with pytest.raises(NotFoundError):
            svc.reveal(s, pi, ident.id)  # no longer current
        with pytest.raises(NotFoundError):
            svc.remove_identifier(s, CTX, pi, ident.id)
        moved = svc.add_identifier(
            s, CTX, everyday, IdentifierInput("vendor_account_id", SECRET, "Vanguard PI")
        )
        assert moved.id != ident.id
    with sample_factory() as s:
        old = s.get(AccountIdentifier, ident.id)
        assert old is not None and old.superseded_at is not None  # the row stays: append-only
        assert old.value == SECRET


def test_reveal_returns_the_value_only_for_the_owning_account(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory() as s:
        pi, everyday = _id(s, PI), _id(s, EVERYDAY)
        ident = svc.list_identifiers(s, pi)[0]
        assert svc.reveal(s, pi, ident.id) == SECRET
        with pytest.raises(NotFoundError):
            svc.reveal(s, everyday, ident.id)
        with pytest.raises(NotFoundError):
            svc.list_identifiers(s, 9999)


def test_the_table_is_append_only_in_the_database(tmp_path: Path) -> None:
    from finance360.core.db import db_path
    from finance360.core.migrate import migrate

    migrate(tmp_path)
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO account_identifier (account_id, kind, value, value_norm, institution_norm,"
            " recorded_at) VALUES (1, 'last4', '1111', '1111', '', '2026-01-01')"
        )
        for statement in (
            "UPDATE account_identifier SET value = '9999'",
            "UPDATE account_identifier SET account_id = 2",
            "DELETE FROM account_identifier",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(statement)
        conn.execute("UPDATE account_identifier SET superseded_at = '2026-02-01'")  # allowed
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE account_identifier SET superseded_at = '2026-03-01'")
    finally:
        conn.close()


# --- the API -------------------------------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    import shutil

    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _pi(client: TestClient) -> int:
    accounts = client.get("/api/accounts").json()
    return next(a["id"] for a in accounts if a["name"] == PI)


def test_the_api_returns_the_identifier_masked_and_never_the_value(sample: TestClient) -> None:
    pi = _pi(sample)
    listed = sample.get(f"/api/accounts/{pi}/identifiers")
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "id": listed.json()[0]["id"],
            "kind": "vendor_account_id",
            "masked_value": "••• 0001",
            "institution": "Vanguard PI",
        }
    ]
    assert SECRET not in listed.text
    # Nothing else that touches Accounts or the trail carries it either.
    for path in (f"/api/accounts/{pi}", "/api/accounts", "/api/audit?limit=200"):
        assert SECRET not in sample.get(path).text, path


def test_only_the_explicit_reveal_call_returns_the_value_and_only_to_the_owner(
    sample: TestClient,
) -> None:
    pi = _pi(sample)
    ident = sample.get(f"/api/accounts/{pi}/identifiers").json()[0]["id"]
    revealed = sample.post(f"/api/accounts/{pi}/identifiers/{ident}/reveal")
    assert revealed.status_code == 200 and revealed.json() == {"value": SECRET}
    assert sample.get(f"/api/accounts/{pi}/identifiers/{ident}/reveal").status_code in (404, 405)
    as_owner(sample.app, Principal(login_id=2, role=Role.VIEWER))  # type: ignore[arg-type]
    assert sample.post(f"/api/accounts/{pi}/identifiers/{ident}/reveal").status_code == 403
    assert sample.get(f"/api/accounts/{pi}/identifiers").status_code == 200  # masked list is fine


def test_adding_clashing_and_removing_through_the_api(sample: TestClient) -> None:
    accounts = {a["name"]: a["id"] for a in sample.get("/api/accounts").json()}
    everyday, pi = accounts[EVERYDAY], accounts[PI]
    created = sample.post(
        f"/api/accounts/{everyday}/identifiers",
        json={"kind": "bsb_account", "value": "062000 99887766", "institution": "Sample Bank"},
    )
    assert created.status_code == 201
    assert created.json()["masked_value"] == "••• 7766"
    assert "99887766" not in created.text
    clash = sample.post(
        f"/api/accounts/{pi}/identifiers",
        json={"kind": "bsb_account", "value": "062000 99887766", "institution": "Sample Bank"},
    )
    assert clash.status_code == 422
    assert clash.json()["errors"][0]["field"] == "value" and "99887766" not in clash.text
    bad = sample.post(f"/api/accounts/{pi}/identifiers", json={"kind": "x", "value": ""})
    assert bad.status_code == 422 and {e["field"] for e in bad.json()["errors"]} == {
        "kind",
        "value",
    }
    gone = sample.delete(f"/api/accounts/{everyday}/identifiers/{created.json()['id']}")
    assert gone.status_code == 204
    assert sample.get(f"/api/accounts/{everyday}/identifiers").json() == []
    assert (
        sample.delete(f"/api/accounts/{everyday}/identifiers/{created.json()['id']}").status_code
        == 404
    )
    assert sample.get("/api/accounts/9999/identifiers").status_code == 404


def test_identifier_changes_appear_in_the_accounts_audit_trail_without_the_value(
    sample: TestClient,
) -> None:
    everyday = next(a["id"] for a in sample.get("/api/accounts").json() if a["name"] == EVERYDAY)
    sample.post(
        f"/api/accounts/{everyday}/identifiers",
        json={"kind": "last4", "value": "7788"},
    )
    trail = sample.get("/api/audit", params={"account_id": everyday}).json()
    summaries = [i["summary"] for i in trail["items"]]
    assert any(s.startswith("Added last4") for s in summaries)
    assert "7788" not in sample.get("/api/audit", params={"account_id": everyday}).text
