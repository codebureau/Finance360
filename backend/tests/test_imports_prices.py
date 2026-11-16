"""Unit prices from committed Imports become ``import`` Prices (integration).

Real migrated temporary SQLite and the invented managed-fund export pair (`SAMPLE-KIDS-0001`). The
invariants: one current ``import`` Price per (Instrument, date), the first row in file order; a
re-commit adds nothing; a changed price supersedes (kept and linked); a failed commit writes no
Price; a currency that differs from the Instrument's is skipped with a warning, never a failure.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.behaviours.holdings_based import derive_value
from finance360.capabilities.imports import commit, service
from finance360.capabilities.position import accounts as position_accounts
from finance360.capabilities.position import derived as position_derived
from finance360.capabilities.reference import instruments as reference
from finance360.capabilities.reference import prices as reference_prices
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.derived import DerivedValue
from finance360.core.models.account import Account
from finance360.core.models.audit import AuditEvent
from finance360.core.models.household import Member
from finance360.core.models.imports import Import
from finance360.core.models.instrument import Instrument
from finance360.core.models.price import Price
from finance360.core.money import Money

CTX = AuditContext("owner", "owner", "api")
IMPORTS = Path(__file__).resolve().parents[2] / "fixtures" / "sample_household" / "imports"
TRADES = "investment_transactions_accSAMPLE-KIDS-0001_03-Aug-2026_30-Sep-2026.csv"
CASH = "cash_transactions_accSAMPLE-KIDS-0001_01-Jul-2026_30-Sep-2026.csv"
HEADER = (
    "Account number,Investment,Product ID,Product Type,Trade Date,Type,"
    "Unit Price,Quantity,Value,Brokerage\n"
)
#: The fixture's trade dates and unit prices, worked from the file.
EXPECTED = {
    date(2026, 8, 3): Decimal("1.15"),
    date(2026, 8, 15): Decimal("1.17"),
    date(2026, 8, 31): Decimal("1.18"),
    date(2026, 9, 15): Decimal("1.19"),
    date(2026, 9, 30): Decimal("1.20"),
}


def _kids(s: Session, type_key: str = "brokerage_account") -> Account:
    member = s.scalars(select(Member)).first()
    assert member is not None
    return position_accounts.create_account(
        s,
        CTX,
        position_accounts.AccountInput(
            name="Sample Kids investment (Sample Bank)",
            category_key="investments",
            account_type_key=type_key,
            owners=[position_accounts.OwnerInput(member.id)],
        ),
    )


def _csv(*rows: str) -> bytes:
    return (HEADER + "".join(rows)).encode()


def _row(day: str, price: str, kind: str = "DRP", units: str = "10") -> str:
    fund = "SAMPLE-KIDS-0001,Sample Kids Growth Fund,SKGF,Managed Fund"
    return f"{fund},{day},{kind},{price},{units},1.00,\n"


def _stage(
    s: Session, account: Account, name: str = TRADES, content: bytes | None = None
) -> Import:
    """Stage a trade file, match SKGF (creating the fund once) and map the fund-transaction type."""
    data = content if content is not None else (IMPORTS / name).read_bytes()
    imp = service.stage(s, CTX, data, name, account.id).import_
    if reference.resolve(s, "SKGF", "ticker") is None:
        fund = reference.InstrumentInput("Sample Kids Growth Fund", "fund", "AUD")
        service.create_for_reference(s, CTX, imp.id, "ticker", "SKGF", fund)
    if service.unmapped_types(s, imp.id):
        service.map_type(s, CTX, imp.id, "Managed Fund Transaction", "buy")
    return imp


def _fund(s: Session) -> Instrument:
    found = reference.resolve(s, "SKGF", "ticker")
    assert isinstance(found, Instrument)
    return found


def _all(s: Session) -> list[Price]:
    return list(s.scalars(select(Price).order_by(Price.id)))


def _current(s: Session) -> dict[date, Decimal]:
    return {
        p.as_at: p.value
        for p in s.scalars(select(Price).where(Price.superseded_at.is_(None)))
        if p.source == "import"
    }


def test_commit_records_an_import_price_on_each_trade_date_with_the_import_in_the_note(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s, _kids(s))
        events_before = s.scalar(select(func.count()).select_from(AuditEvent))
        commit.commit(s, CTX, imp.id)
        rows = _all(s)
        assert {p.as_at: p.value for p in rows} == EXPECTED
        assert {(p.source, p.ccy, p.instrument_id) for p in rows} == {
            ("import", "AUD", _fund(s).id)
        }
        # the note names the Import and its stored (masked) display name, not the raw account number
        assert {p.provenance_note for p in rows} == {
            f"Unit price from Import {imp.id} ({imp.file_name})"
        }
        assert "SAMPLE-KIDS-0001" not in (rows[0].provenance_note or "")
        # one audit event for the whole commit, carrying the Price counts
        assert s.scalar(select(func.count()).select_from(AuditEvent)) == (events_before or 0) + 1
        event = s.scalars(select(AuditEvent).order_by(AuditEvent.id.desc())).first()
        assert event is not None and event.after is not None
        assert (event.after["prices_added"], event.after["prices_revised"]) == (5, 0)


def test_recommitting_an_overlapping_file_adds_no_duplicates_and_keeps_the_same_rows(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        kids = _kids(s)
        first = _stage(s, kids)
        commit.commit(s, CTX, first.id)
        before = {p.as_at: p.id for p in _all(s)}
        # the same file plus one new later row: an overlapping export
        content = (IMPORTS / TRADES).read_bytes() + _row("05-Oct-2026", "1.2500").encode()
        second = _stage(
            s,
            kids,
            "investment_transactions_accSAMPLE-KIDS-0001_03-Aug-2026_05-Oct-2026.csv",
            content,
        )
        commit.commit(s, CTX, second.id)
        rows = _all(s)
        assert len(rows) == 6 and all(p.superseded_at is None for p in rows)
        assert {p.as_at: p.id for p in rows if p.as_at in before} == before
        assert _current(s)[date(2026, 10, 5)] == Decimal("1.25")


def test_a_changed_price_supersedes_the_earlier_import_price_which_stays_linked(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        kids = _kids(s)
        commit.commit(s, CTX, _stage(s, kids).id)
        old = next(p for p in _all(s) if p.as_at == date(2026, 9, 15))
        corrected = (IMPORTS / TRADES).read_text().replace("1.1900", "1.2100")
        second = _stage(
            s,
            kids,
            "investment_transactions_accSAMPLE-KIDS-0001_01-Aug-2026_30-Sep-2026.csv",
            corrected.encode(),
        )
        commit.commit(s, CTX, second.id)
        s.refresh(old)
        current = next(
            p for p in _all(s) if p.as_at == date(2026, 9, 15) and p.superseded_at is None
        )
        assert current.value == Decimal("1.21") and current.id != old.id
        assert old.superseded_at is not None and old.superseded_by_id == current.id
        assert old.value == Decimal("1.19")  # kept, not edited
        assert len(_all(s)) == 6
        event = s.scalars(select(AuditEvent).order_by(AuditEvent.id.desc())).first()
        assert event is not None and event.after is not None
        assert (event.after["prices_added"], event.after["prices_revised"]) == (0, 1)


def test_several_rows_for_one_instrument_and_day_use_the_first_in_file_order(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        content = _csv(_row("02-Sep-2026", "1.3000"), _row("02-Sep-2026", "1.4000"))
        imp = _stage(
            s,
            _kids(s),
            "investment_transactions_accSAMPLE-KIDS-0001_01-Sep-2026_30-Sep-2026.csv",
            content,
        )
        commit.commit(s, CTX, imp.id)
        [price] = _all(s)
        assert price.value == Decimal("1.3")
        assert (
            price.provenance_note is not None
            and "first of 2 rows that day" in price.provenance_note
        )


def test_a_failed_commit_writes_no_prices(
    sample_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with sample_factory.begin() as s:
        import_id = _stage(s, _kids(s)).id

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the audit write failed")

    # The Prices are written before the audit event, so this fails after them.
    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError), sample_factory.begin() as s:
        commit.commit(s, CTX, import_id)
    monkeypatch.undo()
    with sample_factory.begin() as s:
        assert _all(s) == []


def test_a_currency_that_differs_from_the_instruments_is_skipped_with_a_warning(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        kids = _kids(s)
        imp = service.stage(s, CTX, (IMPORTS / TRADES).read_bytes(), TRADES, kids.id).import_
        service.create_for_reference(
            s,
            CTX,
            imp.id,
            "ticker",
            "SKGF",
            reference.InstrumentInput("Sample Kids Growth Fund", "fund", "USD"),
        )
        service.map_type(s, CTX, imp.id, "Managed Fund Transaction", "buy")
        plan = commit.preview(s, imp.id)
        assert [w.code for w in plan.warnings].count("price_currency") == 5
        result = commit.commit(s, CTX, imp.id)  # the commit still succeeds
        assert result.added == 5 and _all(s) == []


def test_an_account_whose_instrument_has_only_import_prices_derives_a_value_flagged_stale(
    sample_factory: sessionmaker[Session],
) -> None:
    position_derived.register_hook("holdings_based", derive_value)
    with sample_factory.begin() as s:
        kids = _kids(s, "brokerage_holdings")
        commit.commit(s, CTX, _stage(s, kids).id)
        cash = service.stage(s, CTX, (IMPORTS / CASH).read_bytes(), CASH, kids.id).import_
        commit.commit(s, CTX, cash.id)
        assert {p.source for p in _all(s)} == {"import"}

        on_trade_date = position_derived.account_view(
            s, kids.id, date(2026, 9, 30), date(2026, 9, 30)
        )
        assert isinstance(on_trade_date.result, DerivedValue) and not on_trade_date.result.stale
        # 738.5 units x 1.20 (the 30 Sep import price) = 886.20, plus cash of 0.05
        holding = next(v for v in on_trade_date.lines if v.line.kind == "holding")
        assert holding.line.value == Money(88620, "AUD")

        later = position_derived.account_view(s, kids.id, date(2026, 9, 25), date(2026, 9, 25))
        assert isinstance(later.result, DerivedValue)
        assert later.result.stale  # 10 days past the 15 Sep price: carried forward, flagged
        held = next(v for v in later.lines if v.line.kind == "holding")
        assert held.line.price is not None and held.line.price.as_at == date(2026, 9, 15)
        assert held.line.value == Money(86632, "AUD")  # 728 units x 1.19


def test_a_manual_price_on_a_trade_date_beats_the_import_price_there(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        commit.commit(s, CTX, _stage(s, _kids(s)).id)
        fund = _fund(s)
        reference_prices.add_price(
            s, CTX, fund.id, reference_prices.PriceInput(date(2026, 9, 15), "1.5000", "Invented")
        )
        found = reference_prices.price_for(s, fund.id, date(2026, 9, 15))
        assert found.source == "manual" and found.value == Decimal("1.5")  # type: ignore[union-attr]
        # a day later the import price of 30 Sep is still not used before its date
        assert reference_prices.price_for(s, fund.id, date(2026, 9, 16)).value == Decimal("1.5")  # type: ignore[union-attr]


def _as_if_committed_before_133(s: Session) -> None:
    """Retire every import Price, as an Import committed before commits recorded them would have."""
    for p in s.scalars(select(Price).where(Price.source == "import")):
        p.superseded_at = p.recorded_at
    s.flush()


def test_the_backfill_records_prices_for_imports_committed_before_and_then_does_nothing(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        commit.commit(s, CTX, _stage(s, _kids(s)).id)
        _as_if_committed_before_133(s)
        assert _current(s) == {}

        assert commit.backfill_import_prices(s) == len(EXPECTED)
        assert _current(s) == EXPECTED
        backfilled = s.scalars(
            select(AuditEvent).where(
                AuditEvent.actor_kind == "system", AuditEvent.entity_type == "price"
            )
        ).all()
        assert [e.after["added"] for e in backfilled if e.after] == [len(EXPECTED)]

        rows, events = len(_all(s)), s.scalar(select(func.count()).select_from(AuditEvent))
        assert commit.backfill_import_prices(s) == 0  # a later restart: nothing to do
        assert len(_all(s)) == rows
        assert s.scalar(select(func.count()).select_from(AuditEvent)) == events


def test_the_backfill_never_revises_a_price_already_recorded(
    sample_factory: sessionmaker[Session],
) -> None:
    """Two committed Imports disagree on one date: the back-fill keeps whatever is current and
    never supersedes it, so restarts cannot flip it back and forth."""
    with sample_factory.begin() as s:
        kids = _kids(s)
        commit.commit(s, CTX, _stage(s, kids).id)
        later = _csv(_row("15-Aug-2026", "1.99"))
        name = "investment_transactions_accSAMPLE-KIDS-0001_15-Aug-2026_15-Aug-2026.csv"
        commit.commit(s, CTX, _stage(s, kids, name, later).id)
        current = _current(s)
        rows = len(_all(s))
        assert commit.backfill_import_prices(s) == 0
        assert _current(s) == current and len(_all(s)) == rows
