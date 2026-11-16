"""Household and Member services: validation (unit, Hypothesis) and behaviour with audit
(integration, real migrated SQLite)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service
from finance360.capabilities.household.service import (
    EARLIEST_DATE_OF_BIRTH,
    MemberInUseError,
    NotFoundError,
    ValidationError,
)
from finance360.core.audit import AuditContext
from finance360.core.models.audit import AuditEvent
from finance360.core.models.household import Member

CTX = AuditContext("owner", "owner", "api")
TODAY = date(2026, 10, 17)
PAST = date(1990, 5, 1)


def _events(factory: sessionmaker[Session]) -> list[AuditEvent]:
    with factory() as s:
        return list(s.scalars(select(AuditEvent).order_by(AuditEvent.id)))


@pytest.fixture
def factory(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with session_factory.begin() as s:
        service.ensure_household(s)
    return session_factory


@pytest.fixture(autouse=True)
def _no_leaked_guards() -> Iterator[None]:
    before = list(service._member_delete_guards)  # noqa: SLF001
    yield
    service._member_delete_guards[:] = before  # noqa: SLF001


# --- pure validation -----------------------------------------------------------------------


def test_blank_and_overlong_names_are_rejected() -> None:
    assert service.validate_name("   ")[1][0].message == "Name is required."
    assert "at most 200" in service.validate_name("x" * 201)[1][0].message
    assert service.validate_name("  Alex Sample ") == ("Alex Sample", [])


@given(st.dates(min_value=EARLIEST_DATE_OF_BIRTH, max_value=TODAY - timedelta(days=1)))
def test_every_past_date_is_accepted(dob: date) -> None:
    assert service.validate_date_of_birth(dob, TODAY) == []


@given(st.dates(min_value=TODAY, max_value=date(9999, 12, 31)))
def test_today_and_future_dates_are_rejected(dob: date) -> None:
    (error,) = service.validate_date_of_birth(dob, TODAY)
    assert error.field == "date_of_birth"
    assert "past" in error.message


def test_missing_and_implausibly_old_dates_are_rejected() -> None:
    assert "required" in service.validate_date_of_birth(None, TODAY)[0].message
    assert service.validate_date_of_birth(date(1899, 12, 31), TODAY) != []


@given(st.text().filter(lambda t: not t.strip()))
def test_whitespace_only_names_are_always_rejected(name: str) -> None:
    assert service.validate_name(name)[1] != []


# --- Household -----------------------------------------------------------------------------


def test_first_run_creates_one_household_without_an_audit_record(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(NotFoundError), session_factory() as s:
        service.get_household(s)
    with session_factory.begin() as s:
        first = service.ensure_household(s)
    with session_factory.begin() as s:
        again = service.ensure_household(s)
    assert first.id == again.id
    assert _events(session_factory) == []


def test_a_second_household_row_is_refused_by_the_database(factory: sessionmaker[Session]) -> None:
    from finance360.core.models.household import Household

    with pytest.raises(IntegrityError), factory.begin() as s:
        s.add(Household(id=2, name="Another"))


def test_renaming_the_household_is_audited(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        service.rename_household(s, CTX, "  The Examples  ")
    (event,) = _events(factory)
    assert (event.entity_type, event.action) == ("household", "update")
    assert event.before == {"name": "My household"}
    assert event.after == {"name": "The Examples"}


def test_renaming_to_the_same_name_or_blank(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        service.rename_household(s, CTX, "My household")
    assert _events(factory) == []
    with pytest.raises(ValidationError), factory.begin() as s:
        service.rename_household(s, CTX, " ")


# --- Members -------------------------------------------------------------------------------


def test_add_edit_delete_each_write_one_audit_record(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        member = service.add_member(s, CTX, " Alex Sample ", PAST, today=TODAY)
        member_id = member.id
    with factory.begin() as s:
        service.update_member(s, CTX, member_id, "Alex S. Sample", date(1991, 1, 2), today=TODAY)
    with factory.begin() as s:
        service.delete_member(s, CTX, member_id)

    create, update, delete = _events(factory)
    assert [e.action for e in (create, update, delete)] == ["create", "update", "delete"]
    assert all(e.entity_type == "member" and e.entity_id == member_id for e in (create, update))
    assert create.before is None
    assert create.after == {"name": "Alex Sample", "date_of_birth": "1990-05-01"}
    assert update.before == create.after
    assert update.after == {"name": "Alex S. Sample", "date_of_birth": "1991-01-02"}
    assert delete.before == update.after and delete.after is None
    assert (create.actor_kind, create.source) == ("owner", "api")
    with factory() as s:
        assert service.list_members(s) == []


def test_an_unchanged_edit_writes_no_audit_record(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        member_id = service.add_member(s, CTX, "Alex Sample", PAST, today=TODAY).id
    with factory.begin() as s:
        service.update_member(s, CTX, member_id, "Alex Sample", PAST, today=TODAY)
    assert len(_events(factory)) == 1


def test_invalid_input_is_rejected_with_every_error_and_nothing_is_written(
    factory: sessionmaker[Session],
) -> None:
    with pytest.raises(ValidationError) as caught, factory.begin() as s:
        service.add_member(s, CTX, "  ", TODAY, today=TODAY)
    assert {e.field for e in caught.value.errors} == {"name", "date_of_birth"}
    with factory() as s:
        assert service.list_members(s) == []
    assert _events(factory) == []


def test_editing_with_invalid_input_leaves_the_member_unchanged(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        member_id = service.add_member(s, CTX, "Alex Sample", PAST, today=TODAY).id
    with pytest.raises(ValidationError), factory.begin() as s:
        service.update_member(s, CTX, member_id, "", PAST, today=TODAY)
    with factory() as s:
        assert service.get_member(s, member_id).name == "Alex Sample"


def test_unknown_member_is_not_found(factory: sessionmaker[Session]) -> None:
    for action in (
        lambda s: service.update_member(s, CTX, 99, "A", PAST),
        lambda s: service.delete_member(s, CTX, 99),
    ):
        with pytest.raises(NotFoundError), factory.begin() as s:
            action(s)


def test_a_failed_audit_rolls_the_change_back(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(service.audit, "record", broken)
    with pytest.raises(RuntimeError), factory.begin() as s:
        service.add_member(s, CTX, "Alex Sample", PAST, today=TODAY)
    with factory() as s:
        assert s.scalars(select(Member)).first() is None


# --- delete guard (the account_owner rule, ADR-0009 section 2) -----------------------------


def test_a_registered_guard_blocks_deleting_a_member_and_nothing_is_written(
    factory: sessionmaker[Session],
) -> None:
    owners = {1}  # stands in for account_owner.member_id until Accounts exist

    def guard(_: Session, member_id: int) -> str | None:
        return "they own an Account" if member_id in owners else None

    service.register_member_delete_guard(guard)
    with factory.begin() as s:
        owner_id = service.add_member(s, CTX, "Alex Sample", PAST, today=TODAY).id
        other_id = service.add_member(s, CTX, "Jordan Sample", PAST, today=TODAY).id
    assert owner_id in owners
    with pytest.raises(MemberInUseError, match="they own an Account"), factory.begin() as s:
        service.delete_member(s, CTX, owner_id)
    with factory.begin() as s:
        service.delete_member(s, CTX, other_id)  # unreferenced Members can still go
    with factory() as s:
        assert [m.id for m in service.list_members(s)] == [owner_id]
    assert [e.action for e in _events(factory)] == ["create", "create", "delete"]


# --- first run / fixture -------------------------------------------------------------------


def test_bootstrap_without_a_fixture_creates_an_empty_household(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    with session_factory.begin() as s:
        service.bootstrap(s, tmp_path / "nowhere")
    with session_factory() as s:
        assert service.get_household(s).name == "My household"
        assert service.list_members(s) == []


def test_bootstrap_loads_the_fixture_through_the_service_once(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    (tmp_path / "household.json").write_text(
        json.dumps(
            {
                "household": {"name": "Fixture Family"},
                "members": [
                    {"name": "Alex Sample", "date_of_birth": "1984-03-12"},
                    {"name": "Jordan Sample", "date_of_birth": "1986-09-30"},
                ],
            }
        )
    )
    with session_factory.begin() as s:
        service.bootstrap(s, tmp_path)
    with session_factory.begin() as s:
        service.bootstrap(s, tmp_path)  # second start: no duplicates
    with session_factory() as s:
        assert service.get_household(s).name == "Fixture Family"
        assert [m.name for m in service.list_members(s)] == ["Alex Sample", "Jordan Sample"]
    events = _events(session_factory)
    assert [(e.entity_type, e.action) for e in events] == [("member", "create")] * 2
    assert {(e.actor_kind, e.source) for e in events} == {("system", "fixture")}


def test_an_invalid_fixture_fails_loudly(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    (tmp_path / "household.json").write_text(
        json.dumps({"members": [{"name": "", "date_of_birth": "1984-03-12"}]})
    )
    with pytest.raises(ValidationError), session_factory.begin() as s:
        service.bootstrap(s, tmp_path)
