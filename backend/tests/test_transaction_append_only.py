"""The ``transaction`` table is append-only and exact in the database itself (ADR-0013 sections 1
and 2), on real SQLite built by the real migrations: a row is only ever superseded, an
amount, quantity or price is never a float, and the Stream/kind combinations the model allows are
the only ones the table accepts."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.core.models.account import Account

INSERT_CASH = (
    'INSERT INTO "transaction" (account_id, stream, occurred_on, kind, amount_minor, amount_ccy,'
    " recorded_at) VALUES (:a, 'cash', '2026-09-01', '{kind}', {amount}, 'AUD',"
    " '2026-10-01 00:00:00')"
)
INSERT_TRADE = (
    'INSERT INTO "transaction" (account_id, stream, occurred_on, kind, quantity, unit_price,'
    " unit_price_ccy, fee_minor, fee_ccy, recorded_at) VALUES (:a, 'trade', '2026-09-01', 'buy',"
    " {quantity}, 96107500, 'AUD', 950, 'AUD', '2026-10-01 00:00:00')"
)


@pytest.fixture
def account_id(sample_factory: sessionmaker[Session]) -> int:
    with sample_factory.begin() as s:
        return s.scalars(select(Account).where(Account.name == "Sample Brokerage account")).one().id


def _run(factory: sessionmaker[Session], sql: str, account_id: int) -> None:
    with factory.begin() as s:
        s.execute(text(sql), {"a": account_id})


def _cash(factory: sessionmaker[Session], account_id: int) -> None:
    _run(factory, INSERT_CASH.format(kind="deposit", amount=100), account_id)


@pytest.mark.parametrize(
    "statement",
    [
        'DELETE FROM "transaction"',
        'UPDATE "transaction" SET amount_minor = 1',
        "UPDATE \"transaction\" SET amount_ccy = 'USD'",
        "UPDATE \"transaction\" SET occurred_on = '2026-01-01'",
        'UPDATE "transaction" SET import_id = 1',
        'UPDATE "transaction" SET superseded_by_id = 1',  # linking a replacement on a current row
    ],
)
def test_raw_update_and_delete_are_rejected(
    sample_factory: sessionmaker[Session], account_id: int, statement: str
) -> None:
    _cash(sample_factory, account_id)
    with pytest.raises(DatabaseError, match="append-only"), sample_factory.begin() as s:
        s.execute(text(statement))


def test_a_row_can_be_superseded_once_and_never_changed_again(
    sample_factory: sessionmaker[Session], account_id: int
) -> None:
    _cash(sample_factory, account_id)
    with sample_factory.begin() as s:
        s.execute(text("UPDATE \"transaction\" SET superseded_at = '2026-10-02 00:00:00'"))
    for statement in (
        "UPDATE \"transaction\" SET superseded_at = '2026-10-03 00:00:00'",
        'UPDATE "transaction" SET superseded_at = NULL',
        'UPDATE "transaction" SET amount_minor = 5',
    ):
        with pytest.raises(DatabaseError, match="append-only"), sample_factory.begin() as s:
            s.execute(text(statement))


@pytest.mark.parametrize(
    ("sql", "column"),
    [
        (INSERT_CASH.format(kind="deposit", amount="100.5"), "amount_minor"),
        (INSERT_TRADE.format(quantity="20.5"), "quantity"),
    ],
)
def test_an_amount_or_quantity_that_is_not_an_exact_integer_is_refused(
    sample_factory: sessionmaker[Session], account_id: int, sql: str, column: str
) -> None:
    # SQLite would otherwise store 100.5 as a REAL in a BIGINT column: no floats, ever.
    with pytest.raises(DatabaseError, match=f"{column} must be an integer"):
        _run(sample_factory, sql, account_id)


@pytest.mark.parametrize(
    "sql",
    [
        INSERT_CASH.format(kind="buy", amount=100),  # a trade kind in the cash Stream
        INSERT_TRADE.format(quantity=20).replace("'buy'", "'deposit'"),  # a cash kind in trade
        INSERT_CASH.format(kind="deposit", amount="NULL"),  # a cash row with no amount
        INSERT_TRADE.format(quantity="NULL"),  # a trade row with no quantity
    ],
)
def test_a_row_the_vocabulary_does_not_allow_is_refused(
    sample_factory: sessionmaker[Session], account_id: int, sql: str
) -> None:
    with pytest.raises(IntegrityError):
        _run(sample_factory, sql, account_id)


def test_identical_rows_are_allowed(sample_factory: sessionmaker[Session], account_id: int) -> None:
    for _ in range(2):
        _cash(sample_factory, account_id)
    with sample_factory.begin() as s:
        assert s.scalar(text('SELECT count(*) FROM "transaction"')) == 2
