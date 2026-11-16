"""Opening positions (ADR-0013 section 7).

An Opening position is what an Account already held before its first imported Transaction: a
manual, dated Transaction of kind ``opening_balance`` with a null ``import_id``, audited. Either
units of an Instrument (trade Stream) or cash (cash Stream). It sits outside any Coverage, so an
Import never supersedes it. The Importer never guesses one: the owner records it, usually after
staging warned that a Holding or the cash balance would go negative.

If a later Import's Coverage reaches back past an Opening position's date, the same history may be
counted twice; ``coverage_conflicts`` finds those so staging can warn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.account import Account
from finance360.core.models.instrument import Instrument
from finance360.core.models.transaction import Transaction
from finance360.core.money import MoneyError, format_amount, parse_amount
from finance360.core.transaction import parse_quantity

KINDS = ("holding", "cash")


@dataclass(frozen=True)
class OpeningInput:
    #: ``holding`` (units of an Instrument) or ``cash`` (in the Account's currency).
    kind: str
    occurred_on: date | None
    instrument_id: int | None = None
    quantity: str = ""
    amount: str = ""


def _snapshot(txn: Transaction) -> dict[str, Any]:
    return {
        "id": txn.id,
        "account_id": txn.account_id,
        "stream": txn.stream,
        "kind": txn.kind,
        "occurred_on": txn.occurred_on.isoformat(),
        "instrument_id": txn.instrument_id,
        "quantity": None if txn.quantity is None else str(txn.quantity),
        "amount": (
            None if txn.amount_minor is None else {"minor": txn.amount_minor, "ccy": txn.amount_ccy}
        ),
    }


def record(
    session: Session, context: AuditContext, account_id: int, data: OpeningInput
) -> Transaction:
    """Record an Opening position, or raise ``ValidationError`` naming the field at fault."""
    account = session.get(Account, account_id)
    if account is None:
        raise NotFoundError(f"Account {account_id} does not exist.")
    if account.closed_on is not None:
        raise ValidationError([FieldError("account_id", "This Account is closed.")])
    errors: list[FieldError] = []
    if data.kind not in KINDS:
        errors.append(FieldError("kind", "Choose units of an Instrument or cash."))
    if data.occurred_on is None:
        errors.append(FieldError("occurred_on", "Enter the date of the Opening position."))

    txn = Transaction(
        account_id=account.id,
        stream="trade" if data.kind == "holding" else "cash",
        import_id=None,
        row_no=None,
        occurred_on=data.occurred_on or date.min,
        kind="opening_balance",
        recorded_at=datetime.now(UTC).replace(tzinfo=None),
    )
    what = ""
    if data.kind == "holding":
        if data.instrument_id is None or session.get(Instrument, data.instrument_id) is None:
            errors.append(FieldError("instrument_id", "Choose an Instrument."))
        txn.instrument_id = data.instrument_id
        try:
            quantity = parse_quantity(data.quantity)
            if quantity <= Decimal(0):
                errors.append(FieldError("quantity", "Units must be above zero."))
            txn.quantity = quantity
            what = f"{quantity.normalize():f} units"
        except MoneyError as exc:
            errors.append(FieldError("quantity", str(exc)))
    elif data.kind == "cash":
        try:
            amount = parse_amount(data.amount, account.currency)
            if amount.amount_minor <= 0:
                errors.append(FieldError("amount", "The amount must be above zero."))
            txn.amount_minor, txn.amount_ccy = amount.amount_minor, amount.currency
            what = f"{format_amount(amount)} {amount.currency} cash"
        except MoneyError as exc:
            errors.append(FieldError("amount", str(exc)))
    if errors:
        raise ValidationError(errors)

    session.add(txn)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="transaction",
        entity_id=txn.id,
        action="create",
        after=_snapshot(txn),
        summary=f"Recorded Opening position of {what} in {account.name} on {txn.occurred_on}",
    )
    return txn


def current_for(session: Session, account_id: int, stream: str) -> list[Transaction]:
    """The Account's current Opening positions in a Stream, oldest first."""
    return list(
        session.scalars(
            select(Transaction)
            .where(
                Transaction.account_id == account_id,
                Transaction.stream == stream,
                Transaction.kind == "opening_balance",
                Transaction.import_id.is_(None),
                Transaction.superseded_at.is_(None),
            )
            .order_by(Transaction.occurred_on, Transaction.id)
        )
    )


def coverage_conflicts(
    session: Session, account_id: int, stream: str, coverage_from: date
) -> list[Transaction]:
    """Current Opening positions in the Stream dated *after* the Import's Coverage starts: the file
    reaches back past them, so it probably holds what they already account for."""
    return [t for t in current_for(session, account_id, stream) if coverage_from < t.occurred_on]
