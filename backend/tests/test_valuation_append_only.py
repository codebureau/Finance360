"""The ``valuation`` table is append-only in the database itself, on real SQLite
built by the real migrations."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.position import accounts as accounts_svc
from finance360.capabilities.position import valuations as svc
from finance360.capabilities.position.accounts import AccountInput, OwnerInput
from finance360.capabilities.position.valuations import ValuationInput
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue

CTX = AuditContext("owner", "owner", "api")
TODAY = date(2026, 10, 17)

INSERT = (
    "INSERT INTO valuation (account_id, as_at, amount_minor, amount_ccy, source, provenance_note,"
    " recorded_at) VALUES (:a, '2026-03-31', {amount}, 'AUD', 'manual', 'n', '2026-04-01 00:00:00')"
)


@pytest.fixture
def account_id(session_factory: sessionmaker[Session]) -> int:
    with session_factory.begin() as s:
        household.ensure_household(s)
        seed_catalogue(s)
        member = household.add_member(s, CTX, "Alex Sample", date(1984, 3, 12), today=TODAY)
        return accounts_svc.create_account(
            s,
            CTX,
            AccountInput(
                name="Everyday",
                category_key="cash",
                account_type_key="transaction_account",
                owners=[OwnerInput(member.id)],
                currency="AUD",
            ),
        ).id


def _add(factory: sessionmaker[Session], account_id: int, amount: str = "10") -> int:
    with factory.begin() as s:
        data = ValuationInput(date(2026, 3, 31), amount, "manual", "n")
        return svc.add_valuation(s, CTX, account_id, data, today=TODAY).id


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM valuation",
        "UPDATE valuation SET amount_minor = 1",
        "UPDATE valuation SET amount_ccy = 'USD'",
        "UPDATE valuation SET as_at = '2026-01-01'",
        "UPDATE valuation SET provenance_note = 'tampered'",
        "UPDATE valuation SET superseded_by_id = 1",  # linking a replacement on a current row
    ],
)
def test_raw_update_and_delete_are_rejected(
    session_factory: sessionmaker[Session], account_id: int, statement: str
) -> None:
    _add(session_factory, account_id)
    with pytest.raises(DatabaseError, match="append-only"), session_factory.begin() as s:
        s.execute(text(statement))
    with session_factory() as s:
        row = s.execute(text("SELECT amount_minor, amount_ccy, as_at FROM valuation")).one()
    assert tuple(row) == (1000, "AUD", "2026-03-31")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE valuation SET superseded_at = NULL",
        "UPDATE valuation SET superseded_at = '2030-01-01 00:00:00'",
        "UPDATE valuation SET superseded_by_id = 1",
    ],
)
def test_a_superseded_row_cannot_be_changed_again(
    session_factory: sessionmaker[Session], account_id: int, statement: str
) -> None:
    vid = _add(session_factory, account_id)
    with session_factory.begin() as s:
        svc.correct_valuation(
            s, CTX, vid, ValuationInput(date(2026, 3, 31), "20", "manual", "n"), today=TODAY
        )
    with pytest.raises(DatabaseError, match="append-only"), session_factory.begin() as s:
        s.execute(text(statement + f" WHERE id = {vid}"))


def test_supersession_through_the_service_still_works(
    session_factory: sessionmaker[Session], account_id: int
) -> None:
    vid = _add(session_factory, account_id)
    with session_factory.begin() as s:
        new = svc.correct_valuation(
            s, CTX, vid, ValuationInput(date(2026, 3, 31), "20", "manual", "n"), today=TODAY
        )
        newer = new.id
    with session_factory.begin() as s:
        svc.write_closing_valuation(s, CTX, accounts_svc.get_account(s, account_id), TODAY)
        svc.remove_valuation(s, CTX, newer)
    with session_factory() as s:
        rows = s.execute(
            text("SELECT id, superseded_by_id, superseded_at IS NOT NULL FROM valuation ORDER BY 1")
        ).all()
    assert [tuple(r) for r in rows] == [(vid, newer, 1), (newer, None, 1), (3, None, 0)]


#: SQLite itself coerces a numeric string such as '12' to an integer before the trigger sees it.
@pytest.mark.parametrize("amount", ["1.5", "'abc'", "NULL"])
def test_a_non_integer_amount_cannot_be_inserted(
    session_factory: sessionmaker[Session], account_id: int, amount: str
) -> None:
    with pytest.raises(DatabaseError), session_factory.begin() as s:
        s.execute(text(INSERT.format(amount=amount)), {"a": account_id})
    with session_factory() as s:
        assert s.execute(text("SELECT count(*) FROM valuation")).scalar_one() == 0


def test_an_integer_amount_can_be_inserted(
    session_factory: sessionmaker[Session], account_id: int
) -> None:
    with session_factory.begin() as s:
        s.execute(text(INSERT.format(amount="-250")), {"a": account_id})
    with session_factory() as s:
        assert s.execute(text("SELECT amount_minor FROM valuation")).scalar_one() == -250
