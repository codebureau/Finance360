"""Reading Transactions (ADR-0013 section 2): an Account's per-Stream list, and the
current rows a staged Import would supersede.

Read-only. Superseded rows stay retrievable: they are what an earlier Import wrote and a later one
replaced, so ``include_superseded`` shows the whole history of a Stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.imports import commit
from finance360.core import transaction as core_transaction
from finance360.core.errors import NotFoundError
from finance360.core.models.account import Account
from finance360.core.models.imports import Import
from finance360.core.models.instrument import Instrument
from finance360.core.models.transaction import Transaction


@dataclass(frozen=True)
class TransactionRow:
    transaction: Transaction
    instrument_name: str | None
    #: The file the row came from; ``None`` for a manual Opening position row.
    file_name: str | None


@dataclass(frozen=True)
class TransactionPage:
    total: int
    items: list[TransactionRow]


def _rows(session: Session, transactions: list[Transaction]) -> list[TransactionRow]:
    instruments = {t.instrument_id for t in transactions if t.instrument_id}
    imports = {t.import_id for t in transactions if t.import_id}
    names = (
        {
            i.id: i.name
            for i in session.scalars(select(Instrument).where(Instrument.id.in_(instruments)))
        }
        if instruments
        else {}
    )
    files = (
        {i.id: i.file_name for i in session.scalars(select(Import).where(Import.id.in_(imports)))}
        if imports
        else {}
    )
    return [
        TransactionRow(
            t,
            names.get(t.instrument_id) if t.instrument_id else None,
            files.get(t.import_id) if t.import_id else None,
        )
        for t in transactions
    ]


def list_for_account(
    session: Session,
    account_id: int,
    stream: str,
    *,
    include_superseded: bool,
    offset: int,
    limit: int,
) -> TransactionPage:
    """One Stream of an Account, newest first. Current rows only unless asked."""
    if session.get(Account, account_id) is None:
        raise NotFoundError(f"Account {account_id} does not exist.")
    where = [Transaction.account_id == account_id, Transaction.stream == stream]
    if not include_superseded:
        where.append(Transaction.superseded_at.is_(None))
    total = session.scalar(select(func.count()).select_from(Transaction).where(*where)) or 0
    page = session.scalars(
        select(Transaction)
        .where(*where)
        .order_by(Transaction.occurred_on.desc(), Transaction.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return TransactionPage(total, _rows(session, list(page)))


def current_counts(session: Session, account_id: int) -> dict[str, int]:
    """How many current Transactions each Stream holds."""
    counts = {"trade": 0, "cash": 0}
    found = session.execute(
        select(Transaction.stream, func.count())
        .where(Transaction.account_id == account_id, Transaction.superseded_at.is_(None))
        .group_by(Transaction.stream)
    )
    for stream, n in found:
        counts[stream] = n
    return counts


def list_removed(session: Session, import_id: int, offset: int, limit: int) -> TransactionPage:
    """The current Transactions committing this staged Import would supersede."""
    removed = list(commit.preview(session, import_id).removed)
    return TransactionPage(len(removed), _rows(session, removed[offset : offset + limit]))


@dataclass(frozen=True)
class HoldingsView:
    holdings: core_transaction.HoldingsAsAt
    #: Instrument id -> name, for every Instrument named in ``holdings``.
    names: dict[int, str]


def holdings(session: Session, account_id: int, on: date | None) -> HoldingsView:
    """The Account's Holdings and cash on ``on`` (or ever), derived from current Transactions."""
    if session.get(Account, account_id) is None:
        raise NotFoundError(f"Account {account_id} does not exist.")
    derived = core_transaction.holdings_as_at(session, account_id, on)
    ids = set(derived.units) | {n.instrument_id for n in derived.invalid if n.instrument_id}
    names: dict[int, str] = {}
    if ids:
        for found in session.scalars(select(Instrument).where(Instrument.id.in_(ids))):
            names[found.id] = found.name
    return HoldingsView(derived, names)
