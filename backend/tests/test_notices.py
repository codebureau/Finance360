"""Notices through the service and the API against a real temporary SQLite database (ADR-0033):
the write and its Notice are one transaction, a failing capture never blocks the write, no
identifier leaks into a Notice, expiry and dismissal."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.imports import account_identifiers
from finance360.capabilities.notices import service as notices
from finance360.capabilities.position import accounts, valuations
from finance360.config import Settings
from finance360.core import due as core_due
from finance360.core import questions as core_questions
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.notice import Notice
from finance360.core.models.valuation import Valuation
from finance360.main import create_app, register_due_rules, register_questions
from support import TestClient, owner_client

CTX = AuditContext("owner", "login:1", "api")
RECENT = date.today() - timedelta(days=2)  # fresh, and never ahead of the Household's today


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def registered() -> Iterator[None]:
    """The real Due rules and Questions, registered as ``main.py`` does."""
    rules = dict(core_due._RULES)
    questions, inputs = dict(core_questions._QUESTIONS), dict(core_questions._INPUTS)
    core_due._RULES.clear()
    core_questions._QUESTIONS.clear()
    core_questions._INPUTS.clear()
    register_due_rules()
    register_questions()
    yield
    core_due._RULES.clear()
    core_due._RULES.update(rules)
    core_questions._QUESTIONS.clear()
    core_questions._QUESTIONS.update(questions)
    core_questions._INPUTS.clear()
    core_questions._INPUTS.update(inputs)


def _input(amount: str, as_at: date = RECENT) -> valuations.ValuationInput:
    return valuations.ValuationInput(as_at, amount, "statement", "Invented statement")


class Db:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        with factory.begin() as s:
            household.ensure_household(s)
            seed_catalogue(s)
            self.member = household.add_member(s, CTX, "Alex Sample", date(1984, 3, 12)).id

    def account(self, name: str = "Sample Savings") -> int:
        with self.factory.begin() as s:
            data = accounts.AccountInput(
                name=name,
                category_key="cash",
                account_type_key="savings_account",
                owners=[accounts.OwnerInput(self.member)],
            )
            return accounts.create_account(s, CTX, data).id

    def valued(self, amount: str, name: str = "Sample Savings") -> tuple[int, int]:
        account_id = self.account(name)
        with self.factory.begin() as s:
            v = valuations.add_valuation(s, CTX, account_id, _input(amount))
            return account_id, v.id

    def notices(self) -> list[Notice]:
        with self.factory() as s:
            return list(s.scalars(select(Notice).order_by(Notice.id)))

    def valuation_count(self) -> int:
        with self.factory() as s:
            return s.scalar(select(func.count()).select_from(Valuation)) or 0


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Db:
    return Db(session_factory)


def test_the_migration_creates_the_notice_table(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as s:
        columns = {c["name"] for c in inspect(s.get_bind()).get_columns("notice")}
    assert {"created_at", "kind", "headline", "effect", "delta_minor", "delta_currency"} <= columns
    assert {"link_path", "dismissed_at"} <= columns


def test_adding_a_valuation_records_a_notice_with_the_exact_net_change(db: Db) -> None:
    account_id = db.account()
    with db.factory.begin() as s:
        _, notice = notices.add_valuation(s, CTX, account_id, _input("1200.50"))
        assert notice is not None
    [stored] = db.notices()
    assert stored.kind == "valuation.added"
    assert stored.headline == "You added a Valuation for Sample Savings."
    assert (stored.delta_minor, stored.delta_currency) == (120_050, "AUD")
    assert stored.effect is not None and "Net position moved by $1,200.50 (up)." in stored.effect
    assert stored.link_path == "/position"
    assert stored.dismissed_at is None


def test_correcting_a_valuation_records_the_difference(db: Db) -> None:
    _, valuation_id = db.valued("1000.00")
    with db.factory.begin() as s:
        _, notice = notices.correct_valuation(s, CTX, valuation_id, _input("400.00"))
        assert notice is not None
    stored = db.notices()[-1]
    assert stored.kind == "valuation.corrected"
    assert stored.headline == "You corrected a Valuation for Sample Savings."
    assert (stored.delta_minor, stored.delta_currency) == (-60_000, "AUD")
    assert stored.effect is not None and "(down)" in stored.effect


def test_a_write_that_changes_nothing_makes_no_notice(db: Db) -> None:
    _, valuation_id = db.valued("1000.00")
    before = len(db.notices())
    with db.factory.begin() as s:
        _, notice = notices.correct_valuation(s, CTX, valuation_id, _input("1000.00"))
        assert notice is None
    assert len(db.notices()) == before


def test_a_change_below_the_tolerance_makes_no_notice(db: Db) -> None:
    _, valuation_id = db.valued("1000.00")
    before = len(db.notices())
    with db.factory.begin() as s:
        _, notice = notices.correct_valuation(s, CTX, valuation_id, _input("1000.99"))
        assert notice is None
    assert len(db.notices()) == before


def test_a_readiness_change_alone_makes_a_notice_with_no_figure(db: Db) -> None:
    db.valued("1000.00", name="Counted")
    never_valued = db.account("Never valued")  # leaves "own and owe" Partly ready
    with db.factory.begin() as s:
        _, notice = notices.add_valuation(s, CTX, never_valued, _input("0.00"))
        assert notice is not None
    stored = db.notices()[-1]
    assert (stored.delta_minor, stored.delta_currency) == (None, None)
    assert stored.effect is not None
    assert "What do we own and owe? went from Partly ready to Ready." in stored.effect
    assert "Net position" not in stored.effect


def test_no_account_number_or_provider_reference_is_stored(db: Db) -> None:
    account_id, valuation_id = db.valued("100.00")
    number, provider = "00998877665544", "Invented Provider Pty Ltd"
    ident = account_identifiers.IdentifierInput("member_number", number, provider)
    with db.factory.begin() as s:
        account_identifiers.add_identifier(s, CTX, account_id, ident)
    with db.factory.begin() as s:
        notices.correct_valuation(s, CTX, valuation_id, _input("800.00"))
    stored = db.notices()
    assert len(stored) >= 1
    for notice in stored:
        for field in (
            notice.kind,
            notice.headline,
            notice.effect,
            notice.link_path,
            notice.delta_currency,
        ):
            assert number not in (field or "")
            assert provider not in (field or "")
            assert number[-4:] not in (field or "")


def test_a_failed_write_leaves_no_notice_and_no_valuation(db: Db) -> None:
    account_id = db.account()
    future = date.today() + timedelta(days=30)
    with pytest.raises(ValidationError), db.factory.begin() as s:
        notices.add_valuation(s, CTX, account_id, _input("10.00", as_at=future))
    assert db.notices() == []
    assert db.valuation_count() == 0


def test_the_notice_and_the_write_are_one_transaction(db: Db) -> None:
    """The caller's commit decides both: a later failure in the request loses both."""
    account_id = db.account()

    class Boom(Exception):
        pass

    with pytest.raises(Boom), db.factory.begin() as s:
        _, notice = notices.add_valuation(s, CTX, account_id, _input("500.00"))
        assert notice is not None  # made in the session...
        raise Boom  # ...and rolled back with the write
    assert db.notices() == []
    assert db.valuation_count() == 0


@pytest.mark.parametrize("failing_call", [1, 2])
def test_a_failing_capture_is_logged_and_never_blocks_the_write(
    db: Db, monkeypatch: pytest.MonkeyPatch, failing_call: int
) -> None:
    account_id = db.account()
    real = notices._capture
    calls = {"n": 0}

    def flaky(session: Session) -> object:
        calls["n"] += 1
        if calls["n"] == failing_call:
            raise RuntimeError("capture failed")
        return real(session)

    monkeypatch.setattr(notices, "_capture", flaky)
    with db.factory.begin() as s:
        valuation, notice = notices.add_valuation(s, CTX, account_id, _input("900.00"))
        assert notice is None
        assert valuation.id is not None
    assert db.valuation_count() == 1  # the write was kept
    assert db.notices() == []


def test_expired_notices_are_deleted_by_the_next_write_and_never_listed(db: Db) -> None:
    account_id = db.account()
    old = _now() - timedelta(days=31)
    with db.factory.begin() as s:
        s.add(
            Notice(created_at=old, kind="valuation.added", headline="Old.", link_path="/position")
        )
    with db.factory() as s:
        assert notices.list_notices(s, include_dismissed=True) == []  # filtered before deletion
    assert len(db.notices()) == 1  # still stored: expiry is lazy
    with db.factory.begin() as s:
        notices.add_valuation(s, CTX, account_id, _input("300.00"))
    assert [n.headline for n in db.notices()] == ["You added a Valuation for Sample Savings."]


def test_a_notice_inside_the_expiry_is_listed(db: Db) -> None:
    with db.factory.begin() as s:
        s.add(
            Notice(
                created_at=_now() - timedelta(days=29),
                kind="valuation.added",
                headline="Recent.",
                link_path="/position",
            )
        )
    with db.factory() as s:
        assert [n.headline for n in notices.list_notices(s)] == ["Recent."]


def test_dismissal_is_idempotent_and_keeps_the_notice_for_the_page(db: Db) -> None:
    account_id = db.account()
    with db.factory.begin() as s:
        _, notice = notices.add_valuation(s, CTX, account_id, _input("300.00"))
        assert notice is not None
        notice_id = notice.id
    with db.factory.begin() as s:
        notices.dismiss(s, notice_id)
    first = db.notices()[0].dismissed_at
    assert first is not None
    with db.factory.begin() as s:
        notices.dismiss(s, notice_id)
    assert db.notices()[0].dismissed_at == first  # the second call changes nothing
    with db.factory() as s:
        assert notices.list_notices(s) == []
        assert len(notices.list_notices(s, include_dismissed=True)) == 1
    with db.factory.begin() as s, pytest.raises(NotFoundError):
        notices.dismiss(s, 9999)


def test_the_list_is_newest_first_and_capped(db: Db) -> None:
    base = _now() - timedelta(days=1)
    with db.factory.begin() as s:
        for i in range(12):
            s.add(
                Notice(
                    created_at=base + timedelta(minutes=i),
                    kind="valuation.added",
                    headline=f"N{i}.",
                    link_path="/position",
                )
            )
    with db.factory() as s:
        assert [n.headline for n in notices.list_notices(s)] == [f"N{i}." for i in range(11, 1, -1)]
        assert len(notices.list_notices(s, limit=500)) == 12


# --- through the API ----------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / "data", port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _api_account(client: TestClient) -> int:
    member = client.post(
        "/api/household/members", json={"name": "Alex Sample", "date_of_birth": "1990-01-01"}
    ).json()["id"]
    res = client.post(
        "/api/accounts",
        json={
            "name": "Sample Savings",
            "category_key": "cash",
            "account_type_key": "savings_account",
            "currency": "AUD",
            "owners": [{"member_id": member}],
        },
    )
    return int(res.json()["id"])


def test_the_valuation_routes_return_the_notice_and_the_list_shows_it(client: TestClient) -> None:
    account = _api_account(client)
    body = {
        "as_at": RECENT.isoformat(),
        "amount": "2500.00",
        "source": "manual",
        "provenance_note": "Checked",
    }
    added = client.post(f"/api/accounts/{account}/valuations", json=body)
    assert added.status_code == 201
    notice = added.json()["notice"]
    assert notice["headline"] == "You added a Valuation for Sample Savings."
    assert notice["delta"] == {"minor": 250_000, "currency": "AUD", "amount": "2500.00"}
    assert notice["direction"] == "up"
    assert notice["link_path"] == "/position"
    assert notice["dismissed"] is False

    corrected = client.put(
        f"/api/valuations/{added.json()['id']}", json={**body, "amount": "2000.00"}
    )
    assert corrected.json()["notice"]["delta"]["minor"] == -50_000
    assert corrected.json()["notice"]["direction"] == "down"
    unchanged = client.put(
        f"/api/valuations/{corrected.json()['id']}", json={**body, "amount": "2000.00"}
    )
    assert unchanged.json()["notice"] is None

    listed = client.get("/api/notices").json()["items"]
    assert [n["kind"] for n in listed] == ["valuation.corrected", "valuation.added"]
    assert client.get("/api/notices?limit=1").json()["items"][0]["id"] == listed[0]["id"]
    assert client.get("/api/notices?limit=51").status_code == 422


def test_dismiss_returns_204_twice_and_hides_from_home(client: TestClient) -> None:
    account = _api_account(client)
    created = client.post(
        f"/api/accounts/{account}/valuations",
        json={
            "as_at": RECENT.isoformat(),
            "amount": "10.00",
            "source": "manual",
            "provenance_note": "Checked",
        },
    ).json()["notice"]
    assert client.post(f"/api/notices/{created['id']}/dismiss").status_code == 204
    assert client.post(f"/api/notices/{created['id']}/dismiss").status_code == 204
    assert client.get("/api/notices").json()["items"] == []
    page = client.get("/api/notices?include_dismissed=true").json()["items"]
    assert [n["dismissed"] for n in page] == [True]
    assert client.post("/api/notices/9999/dismiss").status_code == 404


def test_a_rejected_valuation_makes_no_notice(client: TestClient) -> None:
    account = _api_account(client)
    res = client.post(
        f"/api/accounts/{account}/valuations", json={"as_at": RECENT.isoformat(), "amount": "abc"}
    )
    assert res.status_code == 422
    assert res.json()["errors"]
    assert client.get("/api/notices?include_dismissed=true").json()["items"] == []
