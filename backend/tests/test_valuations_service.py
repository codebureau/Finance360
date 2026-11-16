"""Valuation services against a real migrated temporary SQLite: provenance, supersession, audit,
the closing rule and the database's own guards (integration), plus a Hypothesis state machine."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.household.service import NotFoundError, ValidationError
from finance360.capabilities.position import accounts as accounts_svc
from finance360.capabilities.position import valuations as svc
from finance360.capabilities.position.accounts import (
    AccountInput,
    CloseNeedsConfirmationError,
    OwnerInput,
)
from finance360.capabilities.position.valuations import ValuationInput
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import migrate
from finance360.core.models.account import Account
from finance360.core.models.audit import AuditEvent
from finance360.core.models.valuation import Valuation
from finance360.core.money import Money
from finance360.core.valuation import NoValuation

CTX = AuditContext("owner", "owner", "api")
TODAY = date(2026, 10, 17)


def _in(
    as_at: str | None, amount: str, source: str = "manual", note: str | None = "Checked"
) -> ValuationInput:
    return ValuationInput(date.fromisoformat(as_at) if as_at else None, amount, source, note)


def _fields(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


def _new_account(s: Session, name: str = "Everyday", currency: str = "AUD") -> int:
    member = s.scalar(select(household.Member)) or household.add_member(
        s, CTX, "Alex Sample", date(1984, 3, 12), today=TODAY
    )
    return accounts_svc.create_account(
        s,
        CTX,
        AccountInput(
            name=name,
            category_key="cash",
            account_type_key="transaction_account",
            owners=[OwnerInput(member.id)],
            currency=currency,
        ),
    ).id


@pytest.fixture
def factory(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with session_factory.begin() as s:
        household.ensure_household(s)
        seed_catalogue(s)
    return session_factory


@pytest.fixture
def account_id(factory: sessionmaker[Session]) -> int:
    with factory.begin() as s:
        return _new_account(s)


def _events(factory: sessionmaker[Session], entity: str = "valuation") -> list[AuditEvent]:
    with factory() as s:
        q = select(AuditEvent).where(AuditEvent.entity_type == entity).order_by(AuditEvent.id)
        return list(s.scalars(q))


# --- adding ---------------------------------------------------------------------------------


def test_an_added_valuation_appears_in_the_history_with_source_and_provenance(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        svc.add_valuation(
            s,
            CTX,
            account_id,
            _in("2026-09-30", "1234.56", "statement", "Sept statement"),
            today=TODAY,
        )
    with factory() as s:
        [v] = svc.list_valuations(s, account_id)
        assert (v.as_at, v.amount, v.source, v.provenance_note) == (
            date(2026, 9, 30),
            Money(123_456, "AUD"),
            "statement",
            "Sept statement",
        )
        assert v.superseded_at is None and v.superseded_by_id is None and v.import_id is None
    [event] = _events(factory)
    assert (event.action, event.before) == ("create", None)
    assert event.after == {
        "id": v.id,
        "account_id": account_id,
        "as_at": "2026-09-30",
        "amount": {"minor": 123_456, "ccy": "AUD"},
        "source": "statement",
        "provenance_note": "Sept statement",
    }
    assert "1234.56" in event.summary


def test_history_is_ordered_by_date_whatever_order_it_was_entered(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        for day in ("2026-03-31", "2026-01-31", "2026-02-28"):
            svc.add_valuation(s, CTX, account_id, _in(day, "10"), today=TODAY)
    with factory() as s:
        assert [v.as_at.isoformat() for v in svc.list_valuations(s, account_id)] == [
            "2026-03-31",
            "2026-02-28",
            "2026-01-31",
        ]


def test_amounts_in_a_foreign_account_are_stored_in_its_currency(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        gbp = _new_account(s, "UK Pension", "GBP")
        svc.add_valuation(s, CTX, gbp, _in("2026-09-30", "250000.99"), today=TODAY)
    with factory() as s:
        [v] = svc.list_valuations(s, gbp)
        assert v.amount == Money(25_000_099, "GBP")
        assert (v.amount_minor, v.amount_ccy) == (25_000_099, "GBP")


def test_a_valuation_in_another_currency_is_rejected(
    factory: sessionmaker[Session], account_id: int
) -> None:
    entry = ValuationInput(date(2026, 9, 30), "10", "manual", "x", currency="USD")
    with factory.begin() as s, pytest.raises(ValidationError) as exc:
        svc.add_valuation(s, CTX, account_id, entry, today=TODAY)
    assert "AUD" in _fields(exc)["currency"]
    ok = ValuationInput(date(2026, 9, 30), "10", "manual", "x", currency="aud")
    with factory.begin() as s:
        svc.add_valuation(s, CTX, account_id, ok, today=TODAY)


def test_a_provenance_note_is_required_for_manual_statement_and_estimate(
    factory: sessionmaker[Session], account_id: int
) -> None:
    for source in ("manual", "statement", "estimate"):
        for note in (None, "", "   "):
            with factory.begin() as s, pytest.raises(ValidationError) as exc:
                svc.add_valuation(
                    s, CTX, account_id, _in("2026-09-30", "1", source, note), today=TODAY
                )
            assert "provenance_note" in _fields(exc)
    with factory() as s:
        assert svc.list_valuations(s, account_id) == []


def test_derived_valuations_cannot_be_entered_and_unknown_sources_are_rejected(
    factory: sessionmaker[Session], account_id: int
) -> None:
    for source in ("derived", "guess", ""):
        with factory.begin() as s, pytest.raises(ValidationError) as exc:
            svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "1", source), today=TODAY)
        assert "source" in _fields(exc)


def test_bad_amounts_are_rejected_with_a_message_and_nothing_is_written(
    factory: sessionmaker[Session], account_id: int
) -> None:
    for amount in ("", "abc", "12.345", "1e3", "NaN"):
        with factory.begin() as s, pytest.raises(ValidationError) as exc:
            svc.add_valuation(s, CTX, account_id, _in("2026-09-30", amount), today=TODAY)
        assert "amount" in _fields(exc)
    with factory() as s:
        assert svc.list_valuations(s, account_id) == []


def test_negative_amounts_are_allowed_for_an_overdrawn_account(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        v = svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "-12.50"), today=TODAY)
        assert v.amount == Money(-1250, "AUD")


def test_dates_after_today_are_rejected_and_today_and_the_default_are_fine(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s, pytest.raises(ValidationError) as exc:
        svc.add_valuation(s, CTX, account_id, _in("2026-10-18", "1"), today=TODAY)
    assert "as_at" in _fields(exc)
    with factory.begin() as s:
        assert svc.add_valuation(s, CTX, account_id, _in("2026-10-17", "1"), today=TODAY)
        assert (
            svc.add_valuation(s, CTX, account_id, _in(None, "2", "statement"), today=TODAY).as_at
            == TODAY
        )


def test_one_current_valuation_per_date_and_source_but_sources_may_share_a_date(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "1"), today=TODAY)
        svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "2", "statement"), today=TODAY)
    with factory.begin() as s, pytest.raises(ValidationError) as exc:
        svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "3"), today=TODAY)
    assert "Correct that one instead" in _fields(exc)["as_at"]


def test_adding_to_an_unknown_account_is_not_found(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s, pytest.raises(NotFoundError):
        svc.add_valuation(s, CTX, 999, _in("2026-09-30", "1"), today=TODAY)
    with factory() as s, pytest.raises(NotFoundError):
        svc.list_valuations(s, 999)


def test_a_closed_account_takes_no_valuation_after_its_close_date(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        accounts_svc.close_account(s, CTX, account_id, date(2026, 6, 30))
    with factory.begin() as s, pytest.raises(ValidationError) as exc:
        svc.add_valuation(s, CTX, account_id, _in("2026-07-01", "1"), today=TODAY)
    assert "closed" in _fields(exc)["as_at"]
    with factory.begin() as s:  # history up to the close date can still be filled in
        svc.add_valuation(s, CTX, account_id, _in("2026-05-31", "9"), today=TODAY)


# --- correcting and removing ----------------------------------------------------------------


def test_correcting_keeps_the_original_retrievable_and_audits_it(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        old_id = svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "100.00"), today=TODAY).id
    with factory.begin() as s:
        new = svc.correct_valuation(
            s, CTX, old_id, _in("2026-09-30", "110.00", note="Fixed typo"), today=TODAY
        )
        new_id = new.id
    assert new_id != old_id
    with factory() as s:
        old = svc.get_valuation(s, old_id)
        assert old.amount == Money(10_000, "AUD")  # untouched
        assert old.superseded_at is not None and old.superseded_by_id == new_id
        assert [v.id for v in svc.current_valuations(s, account_id)] == [new_id]
        assert [(v.id, v.amount.amount_minor) for v in svc.list_valuations(s, account_id)] == [
            (new_id, 11_000),
            (old_id, 10_000),
        ]
        best = svc.best_valuation(s, account_id, TODAY)
        assert not isinstance(best, NoValuation) and best.id == new_id
    created, corrected = _events(factory)
    assert corrected.action == "supersede" and corrected.entity_id == new_id
    assert corrected.before is not None and corrected.before["amount"] == {
        "minor": 10_000,
        "ccy": "AUD",
    }
    assert corrected.after is not None and corrected.after["amount"] == {
        "minor": 11_000,
        "ccy": "AUD",
    }


def test_a_correction_may_move_the_date_and_change_the_source(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        old_id = svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "100"), today=TODAY).id
        svc.add_valuation(s, CTX, account_id, _in("2026-08-31", "90"), today=TODAY)
    with factory.begin() as s:
        svc.correct_valuation(
            s, CTX, old_id, _in("2026-08-31", "95", "statement", "Aug statement"), today=TODAY
        )
    with factory() as s:
        # manual 31 Aug (90) and statement 31 Aug (95) coexist; statement wins that date
        best = svc.best_valuation(s, account_id, date(2026, 9, 30))
        assert not isinstance(best, NoValuation) and best.amount.amount_minor == 9_500


def test_correcting_into_an_occupied_date_and_source_is_rejected_and_changes_nothing(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        a = svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "100"), today=TODAY).id
        svc.add_valuation(s, CTX, account_id, _in("2026-08-31", "90"), today=TODAY)
    with factory.begin() as s, pytest.raises(ValidationError):
        svc.correct_valuation(s, CTX, a, _in("2026-08-31", "100"), today=TODAY)
    with factory() as s:
        assert svc.get_valuation(s, a).superseded_at is None


def test_an_unchanged_correction_writes_nothing(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        v = svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "100"), today=TODAY)
        same = svc.correct_valuation(s, CTX, v.id, _in("2026-09-30", "100.00"), today=TODAY)
        assert same.id == v.id
    assert len(_events(factory)) == 1
    with factory() as s:
        assert len(svc.list_valuations(s, account_id)) == 1


def test_only_current_valuations_can_be_corrected_or_removed(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        old = svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "100"), today=TODAY).id
        svc.correct_valuation(s, CTX, old, _in("2026-09-30", "101"), today=TODAY)
    with factory.begin() as s, pytest.raises(ValidationError):
        svc.correct_valuation(s, CTX, old, _in("2026-09-30", "102"), today=TODAY)
    with factory.begin() as s, pytest.raises(ValidationError):
        svc.remove_valuation(s, CTX, old)
    with factory.begin() as s, pytest.raises(NotFoundError):
        svc.remove_valuation(s, CTX, 999)


def test_removing_supersedes_with_no_replacement_and_falls_back_to_the_previous(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        svc.add_valuation(s, CTX, account_id, _in("2026-08-31", "90"), today=TODAY)
        gone = svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "100"), today=TODAY).id
    with factory.begin() as s:
        svc.remove_valuation(s, CTX, gone)
    with factory() as s:
        row = svc.get_valuation(s, gone)
        assert row.superseded_at is not None and row.superseded_by_id is None
        assert len(svc.list_valuations(s, account_id)) == 2  # never deleted
        best = svc.best_valuation(s, account_id, TODAY)
        assert not isinstance(best, NoValuation) and best.amount.amount_minor == 9_000
    removed = _events(factory)[-1]
    assert removed.action == "supersede" and removed.after is None and removed.before is not None
    assert removed.summary.startswith("Removed")


def test_removing_the_only_valuation_gives_no_valuation_not_zero(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        only = svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "100"), today=TODAY).id
        svc.remove_valuation(s, CTX, only)
    with factory() as s:
        assert svc.best_valuation(s, account_id, TODAY) == NoValuation(TODAY)


def test_a_failed_audit_rolls_back_the_valuation(
    factory: sessionmaker[Session], account_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(svc.audit, "record", boom)
    with pytest.raises(RuntimeError), factory.begin() as s:
        svc.add_valuation(s, CTX, account_id, _in("2026-09-30", "1"), today=TODAY)
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(Valuation)) == 0


# --- the database's own guards --------------------------------------------------------------


def _row(account_id: int, **kw: object) -> Valuation:
    base: dict[str, object] = {
        "account_id": account_id,
        "as_at": date(2026, 1, 1),
        "amount": Money(1, "AUD"),
        "source": "manual",
        "provenance_note": "x",
        "recorded_at": svc._now(),  # noqa: SLF001
    }
    return Valuation(**{**base, **kw})


def test_the_partial_unique_index_allows_superseded_duplicates_but_not_current_ones(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        s.add(_row(account_id, superseded_at=svc._now()))  # noqa: SLF001
        s.add(_row(account_id, superseded_at=svc._now()))  # noqa: SLF001
        s.add(_row(account_id))
    with pytest.raises(IntegrityError), factory.begin() as s:
        s.add(_row(account_id))


def test_the_database_enforces_source_note_and_currency_length(
    factory: sessionmaker[Session], account_id: int
) -> None:
    for bad in (
        {"source": "guess"},
        {"provenance_note": None},
        {"provenance_note": "   "},
        {"source": "estimate", "provenance_note": ""},
    ):
        with pytest.raises(IntegrityError), factory.begin() as s:
            s.add(_row(account_id, **bad))
    with factory.begin() as s:  # a derived row may have no note
        s.add(_row(account_id, source="derived", provenance_note=None))


def test_import_id_has_no_foreign_key(factory: sessionmaker[Session], account_id: int) -> None:
    with factory.begin() as s:
        s.add(_row(account_id, import_id=12345))


# --- closing (ADR-0009 section 2) -----------------------------------------------------------


def test_closing_with_no_valuation_writes_a_zero_valuation_in_the_same_transaction(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        accounts_svc.close_account(s, CTX, account_id, date(2026, 6, 30))
    with factory() as s:
        [v] = svc.list_valuations(s, account_id)
        assert (v.as_at, v.amount, v.source, v.provenance_note) == (
            date(2026, 6, 30),
            Money.zero("AUD"),
            "manual",
            "Account closed",
        )
        assert s.get(Account, account_id).closed_on == date(2026, 6, 30)  # type: ignore[union-attr]
    assert [e.entity_type for e in _events(factory, "valuation")] == ["valuation"]
    assert [e.action for e in _events(factory, "account")][-1] == "close"


def test_closing_over_a_non_zero_balance_needs_confirmation_and_changes_nothing(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        svc.add_valuation(
            s, CTX, account_id, _in("2026-05-31", "1500.25", "statement", "May"), today=TODAY
        )
    with pytest.raises(CloseNeedsConfirmationError, match=r"AUD 1500\.25"), factory.begin() as s:
        accounts_svc.close_account(s, CTX, account_id, date(2026, 6, 30))
    with factory() as s:
        assert s.get(Account, account_id).closed_on is None  # type: ignore[union-attr]
        assert len(svc.list_valuations(s, account_id)) == 1


def test_confirmed_closing_records_zero_after_the_balance_and_keeps_the_history(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        svc.add_valuation(
            s, CTX, account_id, _in("2026-05-31", "1500.25", "statement", "May"), today=TODAY
        )
    with factory.begin() as s:
        accounts_svc.close_account(s, CTX, account_id, date(2026, 6, 30), confirm_non_zero=True)
    with factory() as s:
        before = svc.best_valuation(s, account_id, date(2026, 6, 29))
        on_close = svc.best_valuation(s, account_id, date(2026, 6, 30))
        assert not isinstance(before, NoValuation) and before.amount.amount_minor == 150_025
        assert not isinstance(on_close, NoValuation) and on_close.amount.amount_minor == 0


def test_a_zero_balance_closes_without_confirmation(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        svc.add_valuation(s, CTX, account_id, _in("2026-05-31", "0.00"), today=TODAY)
    with factory.begin() as s:
        accounts_svc.close_account(s, CTX, account_id, date(2026, 6, 30))


def test_closing_replaces_a_manual_valuation_already_on_the_close_date(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        svc.add_valuation(s, CTX, account_id, _in("2026-06-30", "5.00"), today=TODAY)
    with factory.begin() as s:
        accounts_svc.close_account(s, CTX, account_id, date(2026, 6, 30), confirm_non_zero=True)
    with factory() as s:
        rows = svc.list_valuations(s, account_id)
        assert [(r.amount.amount_minor, r.superseded_at is None) for r in rows] == [
            (0, True),
            (500, False),
        ]


def test_an_open_account_with_valuations_cannot_change_currency(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory.begin() as s:
        svc.add_valuation(s, CTX, account_id, _in("2026-05-31", "1"), today=TODAY)
    with factory() as s:
        account = s.get(Account, account_id)
        assert account is not None
        data = AccountInput(
            name=account.name,
            category_key="cash",
            account_type_key="transaction_account",
            owners=[OwnerInput(account.owners[0].member_id)],
            currency="USD",
        )
    with factory.begin() as s, pytest.raises(ValidationError) as exc:
        accounts_svc.update_account(s, CTX, account_id, data)
    assert "currency" in _fields(exc)


def test_currency_can_change_while_there_are_no_valuations(
    factory: sessionmaker[Session], account_id: int
) -> None:
    with factory() as s:
        account = s.get(Account, account_id)
        assert account is not None
        data = AccountInput(
            name=account.name,
            category_key="cash",
            account_type_key="transaction_account",
            owners=[OwnerInput(account.owners[0].member_id)],
            currency="USD",
        )
    with factory.begin() as s:
        assert accounts_svc.update_account(s, CTX, account_id, data).currency == "USD"


# --- fixture loading ------------------------------------------------------------------------


def test_fixture_valuations_load_once_through_the_service(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    with factory.begin() as s:
        _new_account(s, "Sample Cash")
    (tmp_path / "household.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "name": "Sample Cash",
                        "valuations": [
                            {
                                "as_at": "2025-01-31",
                                "amount": "10.00",
                                "source": "statement",
                                "note": "n",
                            },
                            {
                                "as_at": "2025-03-31",
                                "amount": "12.00",
                                "source": "estimate",
                                "note": "n",
                            },
                        ],
                    },
                    {
                        "name": "Not in database",
                        "valuations": [
                            {"as_at": "2025-01-31", "amount": "1", "source": "manual", "note": "n"}
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    with factory.begin() as s:
        assert svc.load_fixture_valuations(s, tmp_path) == 2
    with factory.begin() as s:
        assert svc.load_fixture_valuations(s, tmp_path) == 0  # only while there are none
    assert {e.source for e in _events(factory)} == {"fixture"}


# --- Hypothesis: any sequence of adds, corrections and removals ------------------------------

DAYS = [date(2026, 1, 1 + n) for n in range(10)]
SOURCES = ["manual", "statement", "estimate"]


class Op(NamedTuple):
    kind: str  # add | correct | remove
    pick: int  # which current row a correction or removal targets
    day: date
    source: str
    minor: int


op_strategy = st.lists(
    st.builds(
        Op,
        st.sampled_from(["add", "correct", "remove"]),
        st.integers(0, 50),
        st.sampled_from(DAYS),
        st.sampled_from(SOURCES),
        st.integers(-5000, 5000),
    ),
    max_size=30,
)


@pytest.fixture(scope="module")
def template_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A migrated, seeded database, copied for each Hypothesis example."""
    path = tmp_path_factory.mktemp("template")
    migrate(path)
    engine = make_engine(db_path(path))
    with make_session_factory(engine).begin() as s:
        household.ensure_household(s)
        seed_catalogue(s)
    engine.dispose()
    return path


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(op_strategy, st.sampled_from([*DAYS, date(2026, 2, 1)]))
def test_any_sequence_of_edits_never_loses_a_row_and_keeps_best_on_or_before_correct(
    template_dir: Path,
    tmp_path_factory: pytest.TempPathFactory,
    ops: list[Op],
    probe: date,
) -> None:
    work = tmp_path_factory.mktemp("example")
    shutil.copy(db_path(template_dir), db_path(work))
    engine = make_engine(db_path(work))
    factory = make_session_factory(engine)
    try:
        with factory.begin() as s:
            account = _new_account(s)
        model: dict[int, tuple[date, str, int]] = {}  # current rows: id -> (date, source, minor)
        seen: dict[int, tuple[date, int]] = {}  # every row ever created: id -> (date, minor)
        for op in ops:
            ids = sorted(model)
            entry = ValuationInput(op.day, _amt(op.minor), op.source, "n")
            try:
                with factory.begin() as s:
                    if op.kind == "add":
                        v = svc.add_valuation(s, CTX, account, entry, today=TODAY)
                        model[v.id] = (op.day, op.source, op.minor)
                        seen[v.id] = (op.day, op.minor)
                    elif op.kind == "correct" and ids:
                        old = ids[op.pick % len(ids)]
                        new = svc.correct_valuation(s, CTX, old, entry, today=TODAY)
                        if new.id != old:
                            del model[old]
                        model[new.id] = (op.day, op.source, op.minor)
                        seen.setdefault(new.id, (op.day, op.minor))
                    elif op.kind == "remove" and ids:
                        gone = ids[op.pick % len(ids)]
                        svc.remove_valuation(s, CTX, gone)
                        del model[gone]
            except ValidationError:
                pass  # e.g. a date/source clash: rejected, and the model is unchanged

        with factory() as s:
            rows = {v.id: v for v in s.scalars(select(Valuation))}
            # nothing was ever lost or rewritten
            assert set(seen) <= set(rows)
            for vid, (day, minor) in seen.items():
                assert (rows[vid].as_at, rows[vid].amount.amount_minor) == (day, minor)
            # the current rows are exactly the model
            current = {
                v.id: (v.as_at, v.source, v.amount.amount_minor)
                for v in rows.values()
                if v.superseded_at is None
            }
            assert current == model
            # one current row per (date, source)
            assert len({(d, src) for d, src, _ in model.values()}) == len(model)
            # best on or before the probe date matches a brute-force answer
            best = svc.best_valuation(s, account, probe)
            eligible = [(d, src) for d, src, _ in model.values() if d <= probe]
            if not eligible:
                assert isinstance(best, NoValuation)
            else:
                rank = {"statement": 3, "manual": 2, "derived": 1, "estimate": 0}
                top = max(eligible, key=lambda k: (k[0], rank[k[1]]))
                assert not isinstance(best, NoValuation)
                assert (best.as_at, best.source) == top
    finally:
        engine.dispose()


def _amt(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100}.{abs(minor) % 100:02d}"
