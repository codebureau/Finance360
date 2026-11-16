"""Super statement services against a real migrated temporary SQLite (ADR-0025): the Valuation kept
in step, supersession, warn-and-tick, malformed input, audit and the database's own guards."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.position import accounts as accounts_svc
from finance360.capabilities.position import super_statements as svc
from finance360.capabilities.position import valuations
from finance360.capabilities.position.accounts import AccountInput, OwnerInput
from finance360.capabilities.position.super_statements import OptionLineInput, SuperStatementInput
from finance360.capabilities.position.valuations import ValuationInput
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.models.super_statement import SuperStatement, SuperStatementOption
from finance360.core.models.valuation import Valuation
from finance360.core.money import Money

CTX = AuditContext("owner", "owner", "api")
TODAY = date(2026, 10, 17)
START, END = date(2025, 7, 1), date(2026, 6, 30)


def _account(s: Session, type_key: str = "super_fund_account", category: str = "superannuation"):
    household.ensure_household(s)
    seed_catalogue(s)
    member = s.scalar(select(household.Member)) or household.add_member(
        s, CTX, "Alex Sample", date(1984, 3, 12), today=TODAY
    )
    return accounts_svc.create_account(
        s,
        CTX,
        AccountInput("Sample Super", category, type_key, owners=[OwnerInput(member.id)]),
    ).id


def _roll(**over: Any) -> SuperStatementInput:
    """A consistent roll: 1000.00 + 200.00 + 50.00 - 30.00 = 1220.00."""
    fields: dict[str, Any] = {
        "period_start": START,
        "period_end": END,
        "opening": "1000.00",
        "contributions_in": "200.00",
        "investment_returns": "50.00",
        "withdrawals": "30.00",
        "closing": "1220.00",
    }
    fields.update(over)
    return SuperStatementInput(**fields)


def _rich(**over: Any) -> SuperStatementInput:
    fields: dict[str, Any] = {
        "contributions_tax": "20.00",
        "fees_before_rebate": "15.00",
        "rebate": "5.00",
        "fees_inside_investment": "9.00",
        "division_293_released": "0.00",
        "insurance_premiums": "0.00",
        "concessional_received": "150.00",
        "non_concessional_received": "50.00",
        "options": [
            OptionLineInput("Growth", "700.00", "100.5", "6.965174"),
            OptionLineInput("Balanced", "520.01", "200", "2.6"),
        ],
    }
    fields.update(over)
    return _roll(**fields)


def _fields(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


def _current_valuations(s: Session, account_id: int) -> list[Valuation]:
    return valuations.current_valuations(s, account_id)


def test_saving_records_the_statement_and_a_statement_valuation_at_30_june(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        statement = svc.create_super_statement(s, CTX, account, _rich(), today=TODAY)
        valuation = s.get(Valuation, statement.valuation_id)
        assert valuation is not None
        assert (valuation.as_at, valuation.source) == (END, "statement")
        assert valuation.amount == Money(122000, "AUD")
        assert "2025-26" in (valuation.provenance_note or "")
        assert valuation.superseded_at is None
        assert statement.amount("closing") == Money(122000, "AUD")
        assert statement.amount("other_withdrawals") is None  # not stated
        assert statement.amount("division_293_released") == Money(0, "AUD")  # stated zero
        assert [(o.name, o.value_minor) for o in statement.options] == [
            ("Growth", 70000),
            ("Balanced", 52001),
        ]
        assert statement.options[0].units == Decimal("100.5")
        assert statement.options[0].unit_price == Decimal("6.965174")


def test_the_valuation_ranks_in_the_normal_position_selector(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        svc.create_super_statement(s, CTX, account, _roll(), today=TODAY)
        best = valuations.best_valuation(s, account, END)
        assert not hasattr(best, "on")  # not NoValuation
        assert best.amount == Money(122000, "AUD")  # type: ignore[union-attr]


def test_a_correction_supersedes_the_statement_and_corrects_its_valuation(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        first = svc.create_super_statement(s, CTX, account, _rich(), today=TODAY)
        first_id, first_valuation = first.id, first.valuation_id
        second = svc.correct_super_statement(
            s,
            CTX,
            first_id,
            _rich(
                opening="1010.00", closing="1230.00", options=[OptionLineInput("Growth", "1230.00")]
            ),
            today=TODAY,
        )
        s.flush()
        old = s.get(SuperStatement, first_id)
        assert old is not None
        assert old.superseded_at is not None and old.superseded_by_id == second.id
        assert second.superseded_at is None
        # The old row and its option lines stay retrievable.
        assert [o.name for o in old.options] == ["Growth", "Balanced"]
        assert [o.name for o in second.options] == ["Growth"]
        # One current statement, and exactly one current statement Valuation, at the new closing.
        current = s.scalars(
            select(SuperStatement).where(SuperStatement.superseded_at.is_(None))
        ).all()
        assert [c.id for c in current] == [second.id]
        live = _current_valuations(s, account)
        assert [(v.as_at, v.source, v.amount) for v in live] == [
            (END, "statement", Money(123000, "AUD"))
        ]
        assert second.valuation_id == live[0].id != first_valuation
        old_valuation = s.get(Valuation, first_valuation)
        assert old_valuation is not None
        assert old_valuation.superseded_by_id == live[0].id  # superseded with the statement


def test_a_correction_that_changes_nothing_writes_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        first = svc.create_super_statement(s, CTX, account, _rich(), today=TODAY)
        again = svc.correct_super_statement(s, CTX, first.id, _rich(), today=TODAY)
        assert again.id == first.id
        assert s.scalar(select(func.count()).select_from(SuperStatement)) == 1


def test_a_correction_that_keeps_the_closing_keeps_the_same_valuation(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        first = svc.create_super_statement(s, CTX, account, _rich(), today=TODAY)
        second = svc.correct_super_statement(
            s, CTX, first.id, _rich(fees_inside_investment="10.00"), today=TODAY
        )
        assert second.id != first.id
        assert second.valuation_id == first.valuation_id
        assert len(_current_valuations(s, account)) == 1


def test_removing_supersedes_the_statement_and_its_valuation_with_no_replacement(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        statement = svc.create_super_statement(s, CTX, account, _rich(), today=TODAY)
        svc.remove_super_statement(s, CTX, statement.id)
        s.flush()
        assert statement.superseded_at is not None and statement.superseded_by_id is None
        assert svc.list_super_statements(s, account) == []
        assert _current_valuations(s, account) == []
        valuation = s.get(Valuation, statement.valuation_id)
        assert valuation is not None and valuation.superseded_at is not None
        # The year is free again.
        again = svc.create_super_statement(s, CTX, account, _roll(), today=TODAY)
        assert again.superseded_at is None
        with pytest.raises(ValidationError):
            svc.remove_super_statement(s, CTX, statement.id)  # already removed


def test_a_valuation_removed_by_hand_is_recorded_again_by_a_correction(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        statement = svc.create_super_statement(s, CTX, account, _roll(), today=TODAY)
        valuations.remove_valuation(s, CTX, statement.valuation_id)
        fixed = svc.correct_super_statement(
            s, CTX, statement.id, _roll(contributions_in="201.00", closing="1221.00"), today=TODAY
        )
        live = _current_valuations(s, account)
        assert [v.id for v in live] == [fixed.valuation_id]
        assert live[0].amount == Money(122100, "AUD")
        # And removing the statement is fine when its Valuation was already removed by hand.
        valuations.remove_valuation(s, CTX, fixed.valuation_id)
        svc.remove_super_statement(s, CTX, fixed.id)


def test_a_failed_check_needs_its_tick_and_the_ticked_names_are_stored(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        off = _rich(closing="1225.00")  # roll out by 5.00; the options still add to 1220.01
        with pytest.raises(ValidationError) as roll_error:
            svc.create_super_statement(s, CTX, account, off, today=TODAY)
        assert set(_fields(roll_error)) == {"checks.roll", "checks.options"}
        assert s.scalar(select(func.count()).select_from(SuperStatement)) == 0
        assert s.scalar(select(func.count()).select_from(Valuation)) == 0  # nothing left behind

        ticked = _rich(closing="1225.00", ticked_checks=["roll", "options", "outflows"])
        saved = svc.create_super_statement(s, CTX, account, ticked, today=TODAY)
        # Only the checks that actually fail are stored; "outflows" agreed so its tick is dropped.
        assert svc.ticked_checks(saved) == ["roll", "options"]
        assert json.loads(saved.checks_ticked_json) == ["roll", "options"]


def test_check_super_statement_reports_without_storing(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        results = {r.name: r.status for r in svc.check_super_statement(s, account, _rich())}
        assert results == {"roll": "ok", "outflows": "ok", "options": "ok"}
        results = {r.name: r.status for r in svc.check_super_statement(s, account, _roll())}
        assert results == {"roll": "ok", "outflows": "not_applicable", "options": "not_applicable"}
        assert s.scalar(select(func.count()).select_from(SuperStatement)) == 0


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"closing": "twelve"}, "closing"),
        ({"opening": ""}, "opening"),
        ({"withdrawals": "1.234"}, "withdrawals"),
        ({"rebate": "oops"}, "rebate"),
        ({"period_end": date(2026, 6, 29)}, "period_start"),
        ({"period_start": date(2025, 1, 1), "period_end": date(2025, 12, 31)}, "period_start"),
        ({"period_start": date(2024, 7, 1)}, "period_start"),  # two years
        ({"options": [OptionLineInput("", "1.00")]}, "options.0.name"),
        ({"options": [OptionLineInput("Growth", "x")]}, "options.0.value"),
        ({"options": [OptionLineInput("Growth", "1.00", "-1")]}, "options.0.units"),
        ({"options": [OptionLineInput("Growth", "1.00", "1.0000001")]}, "options.0.units"),
        ({"options": [OptionLineInput("Growth", "1.00", None, "-0.5")]}, "options.0.unit_price"),
        ({"options": [OptionLineInput("Growth", "1.00", "1e3")]}, "options.0.units"),
    ],
)
def test_malformed_input_blocks_whatever_is_ticked(
    session_factory: sessionmaker[Session], override: dict[str, Any], field: str
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        data = _roll(ticked_checks=["roll", "outflows", "options"], **override)
        with pytest.raises(ValidationError) as error:
            svc.create_super_statement(s, CTX, account, data, today=TODAY)
        assert field in _fields(error)
        assert s.scalar(select(func.count()).select_from(SuperStatement)) == 0


def test_a_second_current_statement_for_the_year_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        svc.create_super_statement(s, CTX, account, _roll(), today=TODAY)
        with pytest.raises(ValidationError) as error:
            svc.create_super_statement(s, CTX, account, _roll(), today=TODAY)
        assert "Correct that one instead" in _fields(error)["period_start"]
        assert s.scalar(select(func.count()).select_from(Valuation)) == 1


def test_a_non_super_account_is_refused_and_a_missing_one_is_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        cash = _account(s, "transaction_account", "cash")
        with pytest.raises(ValidationError) as error:
            svc.create_super_statement(s, CTX, cash, _roll(), today=TODAY)
        assert "account_id" in _fields(error)
        with pytest.raises(NotFoundError):
            svc.create_super_statement(s, CTX, 9999, _roll(), today=TODAY)


def test_a_year_that_has_not_ended_is_refused_and_nothing_is_left_behind(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        future = _roll(period_start=date(2026, 7, 1), period_end=date(2027, 6, 30))
        with pytest.raises(ValidationError) as error:
            svc.create_super_statement(s, CTX, account, future, today=TODAY)
        assert "period_end" in _fields(error)
        assert s.scalar(select(func.count()).select_from(SuperStatement)) == 0


def test_a_statement_valuation_already_typed_for_30_june_blocks_the_save_and_says_so(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        valuations.add_valuation(
            s,
            CTX,
            account,
            ValuationInput(END, "999.00", "statement", "Typed by hand"),
            today=TODAY,
        )
        with pytest.raises(ValidationError) as error:
            svc.create_super_statement(s, CTX, account, _roll(), today=TODAY)
        assert "Correct or remove that one first" in _fields(error)["period_end"]
        assert s.scalar(select(func.count()).select_from(SuperStatement)) == 0


def test_a_manual_valuation_on_30_june_is_left_alone_by_the_statement(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        valuations.add_valuation(
            s, CTX, account, ValuationInput(END, "999.00", "manual", "Typed by hand"), today=TODAY
        )
        statement = svc.create_super_statement(s, CTX, account, _roll(), today=TODAY)
        live = {(v.source, v.amount) for v in _current_valuations(s, account)}
        assert live == {("manual", Money(99900, "AUD")), ("statement", Money(122000, "AUD"))}
        svc.remove_super_statement(s, CTX, statement.id)
        assert {v.source for v in _current_valuations(s, account)} == {"manual"}


def test_a_correction_cannot_move_the_financial_year(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        statement = svc.create_super_statement(s, CTX, account, _roll(), today=TODAY)
        moved = _roll(period_start=date(2024, 7, 1), period_end=date(2025, 6, 30))
        with pytest.raises(ValidationError) as error:
            svc.correct_super_statement(s, CTX, statement.id, moved, today=TODAY)
        assert "period_start" in _fields(error)


def test_every_write_is_audited_with_the_figures(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        first = svc.create_super_statement(s, CTX, account, _rich(), today=TODAY)
        second = svc.correct_super_statement(
            s, CTX, first.id, _roll(investment_returns="51.00", closing="1221.00"), today=TODAY
        )
        svc.remove_super_statement(s, CTX, second.id)
        events = [
            (e.action, e.summary, e.after, e.before)
            for e in s.scalars(
                select(AuditEvent).where(AuditEvent.entity_type == "super_statement")
            ).all()
        ]
    assert [e[0] for e in events] == ["create", "supersede", "supersede"]
    create_after = events[0][2]
    assert create_after is not None
    assert create_after["closing"] == {"minor": 122000, "ccy": "AUD"}
    assert create_after["other_withdrawals"] is None
    assert [o["name"] for o in create_after["options"]] == ["Growth", "Balanced"]
    assert create_after["financial_year"] == 2026
    assert events[1][3] is not None and events[2][2] is None


def test_super_years_hand_tax_and_returns_frozen_values(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        svc.create_super_statement(s, CTX, account, _rich(), today=TODAY)
        earlier = _roll(
            period_start=date(2024, 7, 1),
            period_end=date(2025, 6, 30),
            opening="900.00",
            closing="1120.00",
        )
        svc.create_super_statement(s, CTX, account, earlier, today=TODAY)
        years = svc.list_super_years(s, account_id=account)
        assert [y.financial_year for y in years] == [2025, 2026]
        latest = svc.list_super_years(s, financial_year=2026)[0]
        assert latest.concessional_received == Money(15000, "AUD")
        assert latest.division_293_released == Money(0, "AUD")
        assert latest.other_withdrawals is None
        assert latest.closing == Money(122000, "AUD")
        with pytest.raises(AttributeError):
            latest.closing = Money(0, "AUD")  # type: ignore[misc]


def test_the_database_keeps_statements_and_lines_append_only_and_integer(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        account = _account(s)
        statement = svc.create_super_statement(s, CTX, account, _rich(), today=TODAY)
        statement_id = statement.id
    with session_factory() as s:
        for sql in (
            "DELETE FROM super_statement",
            "UPDATE super_statement SET closing_minor = 1",
            "DELETE FROM super_statement_option",
            "UPDATE super_statement_option SET name = 'x'",
            "INSERT INTO super_statement_option VALUES (99, :sid, 9, 'F', 1, 1, 1.5)",
        ):
            with pytest.raises(DBAPIError):
                s.execute(text(sql), {"sid": statement_id} if ":sid" in sql else {})
            s.rollback()
        assert s.scalar(select(func.count()).select_from(SuperStatementOption)) == 2
