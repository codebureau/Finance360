"""Preview and commit a staged Import (ADR-0013 section 5).

``preview`` is read-only: it compares the staged rows with the Account's **current** Transactions of
the same Import source and Stream inside the Import's range (a multiset comparison, ``diff``) and
adds cross-Stream reconciliation warnings (``reconcile``). ``commit`` does it for real, in the
caller's one database transaction:

* inserts the added rows as Transactions;
* supersedes (``superseded_at``, never delete) the current rows the file no longer has;
* leaves rows the file still has untouched, so an unchanged row keeps its original Import;
* records each traded Instrument's unit price on its trade date as an ``import`` Price (the first
  row in file order wins a day), superseding an earlier ``import`` Price whose value differs;
* marks the Import ``committed``;
* writes **one** audit event for the whole commit (counts, not rows; the Price counts included).

Nothing else changes: another Import source's rows, the other Stream's rows, rows outside the range
and manual Opening position rows are never touched. The range is the Import's Coverage, widened to
cover any row the file holds outside it, so no row is left that a later file could not supersede.

A double commit is impossible: the Import moves ``staged`` to ``committed`` in a conditional UPDATE
that only one request can win. Any failure raises, and the caller's transaction rolls back, leaving
no partial rows.

Warnings never block commit: the other Stream may simply not be imported yet.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from finance360.capabilities.imports import opening, service
from finance360.capabilities.imports.diff import Diff, RowKey, multiset_diff
from finance360.capabilities.imports.importer import IGNORED, UNRECOGNISED
from finance360.capabilities.imports.reconcile import CashLeg, TradeLeg, Unmatched, reconcile
from finance360.capabilities.reference import prices as reference_prices
from finance360.core import audit
from finance360.core import transaction as core_transactions
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, ValidationError
from finance360.core.models.account import Account
from finance360.core.models.imports import Import, ImportSource, StagedRow
from finance360.core.models.instrument import Instrument
from finance360.core.models.price import Price
from finance360.core.models.transaction import Transaction
from finance360.core.money import Money, MoneyError, format_amount
from finance360.core.price import IMPORT_SOURCE, parse_price


@dataclass(frozen=True)
class DiffWarning:
    code: str
    message: str


@dataclass(frozen=True)
class Plan:
    """What committing this Import would do."""

    #: The range compared and superseded: Coverage widened to the file's own rows.
    scope_from: date
    scope_to: date
    #: Staged rows that become Transactions.
    added: tuple[StagedRow, ...]
    #: Staged rows an equal current Transaction already covers.
    unchanged: tuple[StagedRow, ...]
    #: Current Transactions this commit supersedes.
    removed: tuple[Transaction, ...]
    warnings: tuple[DiffWarning, ...]


@dataclass(frozen=True)
class CommitResult:
    import_: Import
    added: int
    removed: int
    unchanged: int
    warnings: int


@dataclass(frozen=True)
class CommittedSummary:
    """What a committed Import did, read back from the Transactions it wrote and replaced."""

    added: int
    replaced: int
    unchanged: int


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- keys ----------------------------------------------------------------------------------


def _money(raw: Any) -> tuple[int, str] | None:
    if not isinstance(raw, dict):
        return None
    fields = cast("dict[str, Any]", raw)
    return int(fields["minor"]), str(fields["ccy"])


def _decimal(raw: Any) -> Decimal | None:
    return None if raw is None else Decimal(str(raw))


def _staged_key(row: StagedRow) -> RowKey:
    rec = row.record
    return RowKey(
        occurred_on=date.fromisoformat(rec["occurred_on"]),
        kind=rec["kind"],
        instrument_id=row.instrument_id,
        quantity=_decimal(rec.get("quantity")),
        unit_price=_decimal(rec.get("unit_price")),
        unit_price_ccy=rec.get("unit_price_ccy"),
        fee=_money(rec.get("fee")),
        amount=_money(rec.get("amount")),
    )


def _transaction_key(txn: Transaction) -> RowKey:
    fee = None if txn.fee is None else (txn.fee.amount_minor, txn.fee.currency)
    amount = None if txn.amount is None else (txn.amount.amount_minor, txn.amount.currency)
    return RowKey(
        occurred_on=txn.occurred_on,
        kind=txn.kind,
        instrument_id=txn.instrument_id,
        quantity=txn.quantity,
        unit_price=txn.unit_price,
        unit_price_ccy=txn.unit_price_ccy,
        fee=fee,
        amount=amount,
    )


# --- reads ---------------------------------------------------------------------------------


def _staged(session: Session, import_id: int) -> list[StagedRow]:
    """The rows that can become Transactions: rows the owner chose to ignore are left out."""
    return [
        r
        for r in session.scalars(
            select(StagedRow).where(StagedRow.import_id == import_id).order_by(StagedRow.row_no)
        )
        if r.record["kind"] != IGNORED
    ]


def _unsettled_rows(session: Session, txns: list[Transaction]) -> set[tuple[int, int]]:
    """(Import id, row number) of the given trades whose staged row said they have no cash
    settlement. The Transaction does not carry the flag; its staged row does."""
    ids = {t.import_id for t in txns if t.import_id is not None}
    if not ids:
        return set()
    return {
        (r.import_id, r.row_no)
        for r in session.scalars(select(StagedRow).where(StagedRow.import_id.in_(ids)))
        if r.record.get("no_settlement")
    }


def _source_and_account(session: Session, imp: Import) -> tuple[ImportSource, Account]:
    source = session.get(ImportSource, imp.import_source_id)
    account = session.get(Account, source.account_id) if source else None
    if source is None or account is None:
        raise ValidationError([FieldError("import", "This Import has lost its Import source.")])
    return source, account


def _scope(imp: Import, rows: list[StagedRow]) -> tuple[date, date]:
    dates = [date.fromisoformat(r.record["occurred_on"]) for r in rows]
    if not dates:
        return imp.coverage_from, imp.coverage_to
    return min(imp.coverage_from, min(dates)), max(imp.coverage_to, max(dates))


def _current_in_scope(
    session: Session, imp: Import, account: Account, lo: date, hi: date
) -> list[Transaction]:
    """The Account's current Transactions of this Import source and Stream inside the range."""
    return list(
        session.scalars(
            select(Transaction)
            .join(Import, Import.id == Transaction.import_id)
            .where(
                Transaction.account_id == account.id,
                Transaction.stream == imp.stream,
                Transaction.superseded_at.is_(None),
                Transaction.occurred_on >= lo,
                Transaction.occurred_on <= hi,
                Import.import_source_id == imp.import_source_id,
            )
            .order_by(Transaction.occurred_on, Transaction.id)
        )
    )


def _instrument_names(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    found = session.scalars(select(Instrument).where(Instrument.id.in_(ids)))
    return {i.id: i.name for i in found}


# --- reconciliation ------------------------------------------------------------------------


def _money_text(money: Money) -> str:
    return f"{format_amount(money)} {money.currency}"


def _warning(label: str, miss: Unmatched) -> DiffWarning:
    parts: list[str] = []
    if miss.settlement is not None:
        parts.append(f"settlement of {_money_text(miss.settlement)}")
    if miss.fee is not None:
        parts.append(f"fee of {_money_text(miss.fee)}")
    return DiffWarning("no_matching_cash", f"{label}: no matching cash {' or '.join(parts)}.")


def _label(on: date, kind: str, quantity: Decimal, name: str) -> str:
    return f"{on.isoformat()} {kind.capitalize()} of {abs(quantity).normalize():f} {name}"


def _trade_leg(ref: int, key: RowKey, settles: bool = True) -> TradeLeg | None:
    if key.quantity is None or key.unit_price is None or key.unit_price_ccy is None:
        return None
    fee = None if key.fee is None else Money(*key.fee)
    return TradeLeg(
        ref,
        key.occurred_on,
        key.kind,
        key.quantity,
        key.unit_price,
        key.unit_price_ccy,
        fee,
        settles,
    )


def _cash_legs(keys: list[RowKey]) -> list[CashLeg]:
    return [CashLeg(k.occurred_on, k.kind, Money(*k.amount)) for k in keys if k.amount is not None]


def _reconciliation(
    session: Session,
    imp: Import,
    account: Account,
    rows: list[StagedRow],
    diff: Diff,
    current: list[Transaction],
    scope: tuple[date, date],
) -> list[DiffWarning]:
    """Trades without their cash, as the Account would look after this commit."""
    by_row = {r.row_no: r for r in rows}
    labels: dict[int, str] = {}
    trades: list[TradeLeg] = []
    cash_keys: list[RowKey]
    names = _instrument_names(
        session,
        {r.instrument_id for r in rows if r.instrument_id}
        | {
            t.instrument_id
            for t in core_transactions.current(session, account.id, "trade")
            if t.instrument_id
        },
    )

    def name_of(instrument_id: int | None, fallback: str) -> str:
        return names.get(instrument_id, fallback) if instrument_id else fallback

    if imp.stream == "trade":
        for row in rows:
            leg = _trade_leg(row.row_no, _staged_key(row), not row.record.get("no_settlement"))
            if leg is None:
                continue
            trades.append(leg)
            fallback = str(row.record.get("instrument_name") or row.instrument_ref or "")
            labels[leg.ref] = _label(
                leg.occurred_on, leg.kind, leg.quantity, name_of(row.instrument_id, fallback)
            )
        cash_keys = [
            _transaction_key(t) for t in core_transactions.current(session, account.id, "cash")
        ]
    else:
        lo, hi = scope
        in_scope = [
            t
            for t in core_transactions.current(session, account.id, "trade")
            if lo <= t.occurred_on <= hi
        ]
        unsettled = _unsettled_rows(session, in_scope)
        for txn in in_scope:
            leg = _trade_leg(
                txn.id, _transaction_key(txn), (txn.import_id, txn.row_no) not in unsettled
            )
            if leg is None:
                continue
            trades.append(leg)
            labels[leg.ref] = _label(
                leg.occurred_on,
                leg.kind,
                leg.quantity,
                name_of(txn.instrument_id, "unnamed Instrument"),
            )
        removed = set(diff.removed)
        cash_keys = [
            _transaction_key(t)
            for t in core_transactions.current(session, account.id, "cash")
            if t.id not in removed
        ]
        cash_keys += [_staged_key(by_row[n]) for n in diff.added]
    misses = reconcile(trades, _cash_legs(cash_keys))
    return [_warning(labels[m.ref], m) for m in misses]


# --- possible missing Opening positions (ADR-0013 section 7) ----------------------------------


def _staged_change(row: StagedRow) -> core_transactions.Change | None:
    rec = row.record
    on = date.fromisoformat(rec["occurred_on"])
    quantity, amount = _decimal(rec.get("quantity")), _money(rec.get("amount"))
    if row.instrument_id is not None and quantity is not None:
        return core_transactions.Change(on, row.instrument_id, None, quantity)
    if quantity is None and amount is not None:  # a cash row, even one that names an Instrument
        return core_transactions.Change(on, None, amount[1], Decimal(amount[0]))
    return None


def _opening_warnings(
    session: Session,
    imp: Import,
    account: Account,
    rows: list[StagedRow],
    diff: Diff,
    scope_from: date,
) -> list[DiffWarning]:
    """Where the Account's balances would go negative once this Import is committed (a possible
    missing Opening position), and Opening positions this file's Coverage reaches back past."""
    removed = set(diff.removed)
    by_row = {r.row_no: r for r in rows}
    remaining = [
        t for t in core_transactions.current(session, account.id, imp.stream) if t.id not in removed
    ]
    changes = core_transactions.changes_of(remaining)
    changes += [c for n in diff.added if (c := _staged_change(by_row[n])) is not None]
    negatives = core_transactions.first_negatives(changes)
    ids = {n.instrument_id for n in negatives if n.instrument_id}
    opening_rows = opening.coverage_conflicts(session, account.id, imp.stream, scope_from)
    ids |= {t.instrument_id for t in opening_rows if t.instrument_id}
    names = _instrument_names(session, ids)
    warnings: list[DiffWarning] = []
    for n in negatives:
        if n.instrument_id is not None:
            name = names.get(n.instrument_id, "an unnamed Instrument")
            warnings.append(
                DiffWarning(
                    "negative_holding",
                    f"{n.on.isoformat()}: {name} would go to {n.balance.normalize():f} units: "
                    "possible missing Opening position.",
                )
            )
        else:
            money = Money(int(n.balance), n.currency or account.currency)
            warnings.append(
                DiffWarning(
                    "negative_cash",
                    f"{n.on.isoformat()}: cash would go to {_money_text(money)}: "
                    "possible missing Opening position.",
                )
            )
    for t in opening_rows:
        if t.quantity is not None:
            name = names.get(t.instrument_id or 0, "an Instrument")
            what = f"{t.quantity.normalize():f} units of {name}"
        else:
            what = f"{_money_text(t.amount)} cash" if t.amount is not None else "an amount"
        warnings.append(
            DiffWarning(
                "coverage_past_opening",
                f"Coverage starts {scope_from.isoformat()}, before the Opening position of {what} "
                f"dated {t.occurred_on.isoformat()}: this file may count it twice.",
            )
        )
    return warnings


# --- preview -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceCandidate:
    """A unit price an Import offers as an ``import`` Price."""

    instrument: Instrument
    as_at: date
    value: Decimal
    #: How many rows of the file priced this Instrument on this day (the first is used).
    rows: int


def _price_candidates(
    session: Session, rows: list[StagedRow]
) -> tuple[list[PriceCandidate], list[DiffWarning]]:
    """One candidate per (Instrument, date) from the file's rows that carry a unit price, the first
    in file order. A row priced in a currency other than the Instrument's, or with a value that is
    not a valid Price, is skipped with a warning: it never fails the commit."""
    ids = {r.instrument_id for r in rows if r.instrument_id}
    found = session.scalars(select(Instrument).where(Instrument.id.in_(ids)))
    instruments = {i.id: i for i in found}
    seen: dict[tuple[int, date], PriceCandidate] = {}
    warnings: list[DiffWarning] = []
    for row in rows:  # ordered by row number: file order
        rec = row.record
        raw = rec.get("unit_price")
        instrument = instruments.get(row.instrument_id or 0)
        if instrument is None or raw is None:
            continue
        on = date.fromisoformat(rec["occurred_on"])
        label = f"{on.isoformat()} {instrument.name}"
        if rec.get("unit_price_ccy") != instrument.currency:
            warnings.append(
                DiffWarning(
                    "price_currency",
                    f"{label}: the file prices it in {rec.get('unit_price_ccy')} but it is priced "
                    f"in {instrument.currency}, so no Price was recorded.",
                )
            )
            continue
        try:
            value = parse_price(format(Decimal(str(raw)), "f"))
        except (MoneyError, ArithmeticError, ValueError):
            warnings.append(
                DiffWarning("price_invalid", f"{label}: the unit price is not usable as a Price.")
            )
            continue
        first = seen.get((instrument.id, on))
        if first is None:
            seen[(instrument.id, on)] = PriceCandidate(instrument, on, value, 1)
        else:
            seen[(instrument.id, on)] = PriceCandidate(instrument, on, first.value, first.rows + 1)
    return list(seen.values()), warnings


def _record_prices(session: Session, imp: Import, candidates: list[PriceCandidate]) -> Counter[str]:
    outcomes: Counter[str] = Counter()
    for c in candidates:
        note = f"Unit price from Import {imp.id} ({imp.file_name})"
        if c.rows > 1:
            note += f"; the first of {c.rows} rows that day"
        outcomes[
            reference_prices.record_import_price(session, c.instrument, c.as_at, c.value, note)
        ] += 1
    return outcomes


#: Who wrote the Prices :func:`backfill_import_prices` fills in: the app itself, at start-up.
BACKFILL_CONTEXT = AuditContext("system", "startup", "backfill:import-prices")


def backfill_import_prices(session: Session) -> int:
    """Record ``import`` Prices for Imports committed before commits recorded them.

    Runs at start-up. Committed Imports are visited oldest commit first, and only an (Instrument,
    date) with no current ``import`` Price is filled: nothing is revised, so a later restart finds
    nothing to do and writes nothing. One audit event per Instrument that gained Prices. Returns
    how many Prices were added."""
    existing = set(
        session.execute(
            select(Price.instrument_id, Price.as_at).where(
                Price.source == IMPORT_SOURCE, Price.superseded_at.is_(None)
            )
        ).tuples()
    )
    committed = session.scalars(
        select(Import).where(Import.status == "committed").order_by(Import.committed_at, Import.id)
    ).all()
    added: Counter[int] = Counter()
    for imp in committed:
        candidates, _ = _price_candidates(session, _staged(session, imp.id))
        fresh = [c for c in candidates if (c.instrument.id, c.as_at) not in existing]
        if not fresh:
            continue
        _record_prices(session, imp, fresh)
        for c in fresh:
            existing.add((c.instrument.id, c.as_at))
            added[c.instrument.id] += 1
    session.flush()
    for instrument_id, count in sorted(added.items()):
        last_id = session.scalar(
            select(func.max(Price.id)).where(
                Price.instrument_id == instrument_id, Price.source == IMPORT_SOURCE
            )
        )
        audit.record(
            session,
            BACKFILL_CONTEXT,
            entity_type="price",
            entity_id=last_id or 0,
            action="create",
            after={"instrument_id": instrument_id, "source": IMPORT_SOURCE, "added": count},
            summary=f"Recorded {count} unit prices from earlier Imports",
        )
    return sum(added.values())


def _plan(session: Session, imp: Import) -> Plan:
    """Compare the staged rows with the current Transactions and reconcile the Streams."""
    _, account = _source_and_account(session, imp)
    rows = _staged(session, imp.id)
    lo, hi = _scope(imp, rows)
    current = _current_in_scope(session, imp, account, lo, hi)
    diff = multiset_diff(
        [(r.row_no, _staged_key(r)) for r in rows],
        [(t.id, _transaction_key(t)) for t in current],
    )
    by_row = {r.row_no: r for r in rows}
    by_id = {t.id: t for t in current}
    _, price_warnings = _price_candidates(session, rows)
    return Plan(
        scope_from=lo,
        scope_to=hi,
        added=tuple(by_row[n] for n in diff.added),
        unchanged=tuple(by_row[s] for s, _ in diff.unchanged),
        removed=tuple(by_id[i] for i in diff.removed),
        warnings=(
            *_reconciliation(session, imp, account, rows, diff, current, (lo, hi)),
            *_opening_warnings(session, imp, account, rows, diff, lo),
            *price_warnings,
        ),
    )


def preview(session: Session, import_id: int) -> Plan:
    """What committing would do, without doing it. Only a staged Import has a plan."""
    imp = service.get_import(session, import_id)
    if imp.status != "staged":
        raise ValidationError([FieldError("status", f"This Import is {imp.status}.")])
    return _plan(session, imp)


# --- commit --------------------------------------------------------------------------------


def _new_transaction(
    row: StagedRow, imp: Import, account: Account, recorded_at: datetime
) -> Transaction:
    rec = row.record
    fee, amount = _money(rec.get("fee")), _money(rec.get("amount"))
    txn = Transaction(
        account_id=account.id,
        stream=imp.stream,
        import_id=imp.id,
        row_no=row.row_no,
        occurred_on=date.fromisoformat(rec["occurred_on"]),
        kind=rec["kind"],
        instrument_id=row.instrument_id,
        quantity=_decimal(rec.get("quantity")),
        unit_price=_decimal(rec.get("unit_price")),
        unit_price_ccy=rec.get("unit_price_ccy"),
        recorded_at=recorded_at,
    )
    if fee:
        txn.fee_minor, txn.fee_ccy = fee
    if amount:
        txn.amount_minor, txn.amount_ccy = amount
    return txn


def _snapshot(imp: Import, **extra: Any) -> dict[str, Any]:
    return {
        "id": imp.id,
        "stream": imp.stream,
        "file_name": imp.file_name,
        "coverage_from": imp.coverage_from.isoformat(),
        "coverage_to": imp.coverage_to.isoformat(),
        "status": imp.status,
        **extra,
    }


def commit(session: Session, context: AuditContext, import_id: int) -> CommitResult:
    """Commit a staged Import in the caller's transaction, or raise and leave nothing behind."""
    service.check_committable(session, import_id)
    imp = service.get_import(session, import_id)
    _, account = _source_and_account(session, imp)
    if account.closed_on is not None:
        raise ValidationError([FieldError("status", "This Account is closed.")])

    now = _now()
    try:
        result = session.execute(
            update(Import)
            .where(and_(Import.id == import_id, Import.status == "staged"))
            .values(status="committed", committed_at=now)
            .execution_options(synchronize_session=False)
        )
        claimed: int = getattr(result, "rowcount", 0)
    except OperationalError:  # another request holds the write lock and won the race
        raise ValidationError(
            [FieldError("status", "This Import is being committed by another request.")]
        ) from None
    if claimed != 1:
        session.refresh(imp)
        raise ValidationError([FieldError("status", f"This Import is {imp.status}.")])
    session.refresh(imp)

    # Under the write lock now: the plan below is what is true, and stays true until we finish.
    if any(r.record["kind"] == UNRECOGNISED for r in _staged(session, imp.id)):
        raise ValidationError([FieldError("types", "Some transaction types are no longer mapped.")])
    if any(r.instrument_ref and r.instrument_id is None for r in _staged(session, imp.id)):
        raise ValidationError(
            [FieldError("references", "Some references are no longer matched to an Instrument.")]
        )
    plan = _plan(session, imp)
    for row in plan.added:
        session.add(_new_transaction(row, imp, account, now))
    for old in plan.removed:
        old.superseded_at = now
    session.flush()
    candidates, _ = _price_candidates(session, _staged(session, imp.id))
    prices = _record_prices(session, imp, candidates)

    counts = {
        "added": len(plan.added),
        "removed": len(plan.removed),
        "unchanged": len(plan.unchanged),
        "warnings": len(plan.warnings),
        "prices_added": prices["added"],
        "prices_revised": prices["revised"],
    }
    audit.record(
        session,
        AuditContext("import", context.actor_ref, f"import:{imp.id}"),
        entity_type="import",
        entity_id=imp.id,
        action="update",
        before=_snapshot(imp, status="staged"),
        after=_snapshot(imp, **counts),
        summary=(
            f"Committed {imp.stream} file {imp.file_name}: {counts['added']} added, "
            f"{counts['removed']} removed, {counts['unchanged']} unchanged"
        ),
    )
    return CommitResult(
        imp, counts["added"], counts["removed"], counts["unchanged"], counts["warnings"]
    )


def committed_summary(session: Session, imp: Import) -> CommittedSummary | None:
    """What a committed Import did. ``None`` for any other status."""
    if imp.status != "committed" or imp.committed_at is None:
        return None
    _, account = _source_and_account(session, imp)
    added = session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.import_id == imp.id)
    )
    replaced = session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.account_id == account.id,
            Transaction.stream == imp.stream,
            Transaction.superseded_at == imp.committed_at,
            Transaction.import_id != imp.id,
        )
    )
    rows = service.row_count(session, imp.id)
    return CommittedSummary(added or 0, replaced or 0, max(0, rows - (added or 0)))
