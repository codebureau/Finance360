"""Commit, supersession and reconciliation against a real migrated temporary SQLite and the
synthetic household (integration).

The invariants worth naming: the trade fee never moves cash; identical same-day rows both persist;
a failed commit leaves nothing; a double commit is impossible; a later overlapping file supersedes
only the same Import source and Stream inside its range; superseded rows stay retrievable; and
(Hypothesis) committing A then an overlapping B leaves exactly what B alone would within B's range.
"""

from __future__ import annotations

import itertools
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.imports import commit, service, transactions
from finance360.capabilities.position import accounts as position_accounts
from finance360.capabilities.reference import instruments as reference
from finance360.core import audit
from finance360.core import transaction as core_transaction
from finance360.core.audit import AuditContext
from finance360.core.errors import ValidationError
from finance360.core.models.account import Account
from finance360.core.models.audit import AuditEvent
from finance360.core.models.imports import Import, ImportSource
from finance360.core.models.instrument import Instrument
from finance360.core.models.transaction import Transaction
from finance360.core.money import Money

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
PI = "Sample Brokerage account"
AUD = "AUD"


def _file(name: str) -> bytes:
    return (IMPORTS / name).read_bytes()


def _account(s: Session, name: str = PI) -> Account:
    return s.scalars(select(Account).where(Account.name == name)).one()


def _stage(s: Session, name: str, account: Account | None = None) -> Import:
    return service.stage(s, CTX, _file(name), name, (account or _account(s)).id).import_


class _Csv(bytes):
    """Synthetic file content that also knows its export-style file name (the cash file's account
    number and stated period come from the name)."""

    file_name: str = "synthetic.csv"


def _stage_bytes(s: Session, content: bytes, account: Account) -> Import:
    name = getattr(content, "file_name", "synthetic.csv")
    return service.stage(s, CTX, content, name, account.id).import_


def _match_snhx(s: Session, imp: Import) -> None:
    service.create_for_reference(
        s,
        CTX,
        imp.id,
        "ticker",
        "SNHX",
        reference.InstrumentInput("Sample New Horizons ETF", "etf", AUD),
    )


def _instrument(s: Session, ticker: str) -> Instrument:
    found = reference.resolve(s, ticker, "ticker")
    assert isinstance(found, Instrument)
    return found


def _current(s: Session, account: Account, stream: str) -> list[Transaction]:
    return core_transaction.current(s, account.id, stream)


def _cash_csv(rows: list[tuple[str, str, str]], start: str, end: str) -> bytes:
    """A synthetic cash export. Rows are (dd-MMM-yyyy, Type, signed amount) with no product."""
    # The (ignored) Units column carries the period so files for different periods differ byte for
    # byte, as real exports do; otherwise the same rows would be the same file to the hash check.
    lines = [
        "Date,Type,Product Type,Product Name,Product ID,Units,Total",
        *(
            f"{day},{kind},Cash account,Synthetic,CASH,{start}..{end},{amount}"
            for day, kind, amount in rows
        ),
    ]
    csv = _Csv(("\n".join(lines) + "\n").encode())
    csv.file_name = f"cash_transactions_accSAMPLE-PI-0001_{start}_{end}.csv"
    return csv


def _file_cash_sum(name: str) -> Decimal:
    """The cash effect of a sample file, summed independently of the application."""
    rows = [line.split(",") for line in _file(name).decode().splitlines()[1:]]
    return sum((Decimal(r[6]) for r in rows), Decimal(0))


def _cash_total(s: Session, account: Account) -> Decimal:
    return Decimal(core_transaction.cash_balance(s, account.id, AUD).amount_minor).scaleb(-2)


def _audits(s: Session, entity_type: str = "import") -> list[AuditEvent]:
    return list(
        s.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == entity_type).order_by(AuditEvent.id)
        )
    )


# --- commit creates current Transactions ----------------------------------------------


def test_commit_creates_current_transactions_and_one_audit_event(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        imp = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
        events_before = len(_audits(s))
        result = commit.commit(s, CTX, imp.id)
        assert (result.added, result.removed, result.unchanged) == (14, 0, 0)
        assert imp.status == "committed" and imp.committed_at is not None

        rows = _current(s, pi, "cash")
        assert len(rows) == 14 and {r.import_id for r in rows} == {imp.id}
        assert {r.account_id for r in rows} == {pi.id} and not _current(s, pi, "trade")
        assert _cash_total(s, pi) == _file_cash_sum(
            "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv"
        )

        new = _audits(s)[events_before:]
        assert len(new) == 1, "one audit event for the whole commit, not one per row"
        [event] = new
        assert (event.action, event.actor_kind, event.source) == (
            "update",
            "import",
            f"import:{imp.id}",
        )
        assert event.entity_id == imp.id
        assert event.after is not None and event.before is not None
        assert event.before["status"] == "staged" and event.after["status"] == "committed"
        assert (event.after["added"], event.after["removed"], event.after["unchanged"]) == (
            14,
            0,
            0,
        )


def test_the_money_and_quantities_are_stored_exactly(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        _match_snhx(s, imp)
        commit.commit(s, CTX, imp.id)
        sbme = next(
            t for t in _current(s, _account(s), "trade") if t.occurred_on == date(2026, 9, 30)
        )
        assert (sbme.kind, sbme.quantity, sbme.unit_price) == (
            "buy",
            Decimal("20"),
            Decimal("96.1075"),
        )
        assert (sbme.unit_price_ccy, sbme.fee, sbme.amount) == (AUD, Money(950, AUD), None)
        sell = next(
            t for t in _current(s, _account(s), "trade") if t.occurred_on == date(2026, 9, 15)
        )
        assert sell.quantity == Decimal("-40")  # signed: a sell is negative


def test_the_trade_fee_never_moves_cash(sample_factory: sessionmaker[Session]) -> None:
    """Committing the trade Stream writes no cash row and leaves cash at zero; committing the
    cash Stream then moves cash by exactly its own rows, which already hold each fee once."""
    with sample_factory.begin() as s:
        pi = _account(s)
        trades = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        _match_snhx(s, trades)
        commit.commit(s, CTX, trades.id)
        trade_fees = sum((t.fee.amount_minor for t in _current(s, pi, "trade") if t.fee), start=0)
        assert trade_fees == 5 * 950  # the fees are recorded on the trades...
        assert not _current(s, pi, "cash")  # ...no cash row came from them...
        assert core_transaction.cash_balance(s, pi.id, AUD) == Money(0, AUD)  # ...cash is untouched

        cash = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
        commit.commit(s, CTX, cash.id)
        fee_rows = [t for t in _current(s, pi, "cash") if t.kind == "fee"]
        assert len(fee_rows) == 4  # one fee row per matched trade; the fifth trade has none
        assert _cash_total(s, pi) == _file_cash_sum(
            "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv"
        )
        without = _cash_total(s, pi) - Decimal(trade_fees).scaleb(-2)
        assert _cash_total(s, pi) != without  # a fee subtracted again would have shown here


def test_identical_same_day_rows_both_persist(sample_factory: sessionmaker[Session]) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        twin = ("02-Sep-2026", "Deposit", "100.00")
        imp = _stage_bytes(s, _cash_csv([twin, twin], "01-Sep-2026", "30-Sep-2026"), pi)
        result = commit.commit(s, CTX, imp.id)
        assert result.added == 2
        assert len(_current(s, pi, "cash")) == 2 and _cash_total(s, pi) == Decimal("200.00")
        # and the same two rows again are recognised as the same two, not four
        again = _stage_bytes(s, _cash_csv([twin, twin], "01-Sep-2026", "29-Sep-2026"), pi)
        plan = commit.preview(s, again.id)
        assert (len(plan.added), len(plan.unchanged), len(plan.removed)) == (0, 2, 0)


def test_a_failed_commit_leaves_no_partial_rows(
    sample_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with sample_factory.begin() as s:
        import_id = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv").id

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the audit write failed")

    # The rows are inserted and the Import is claimed before the audit write, so this fails late.
    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError), sample_factory.begin() as s:
        commit.commit(s, CTX, import_id)
    monkeypatch.undo()
    with sample_factory.begin() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 0
        assert service.get_import(s, import_id).status == "staged"
        assert service.get_import(s, import_id).committed_at is None


def test_unresolved_references_block_commit_and_write_nothing(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(
            s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
        )  # SNHX is not in the Instrument roster
        with pytest.raises(ValidationError, match="not matched to an Instrument"):
            commit.commit(s, CTX, imp.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 0
        assert imp.status == "staged"


def test_an_import_cannot_be_committed_twice(sample_factory: sessionmaker[Session]) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
        commit.commit(s, CTX, imp.id)
        events = len(_audits(s))
        with pytest.raises(ValidationError, match="committed"):
            commit.commit(s, CTX, imp.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 14
        assert len(_audits(s)) == events


def test_a_discarded_import_cannot_be_committed(sample_factory: sessionmaker[Session]) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
        service.discard(s, CTX, imp.id)
        with pytest.raises(ValidationError, match="discarded"):
            commit.commit(s, CTX, imp.id)


# --- the staged diff and supersession -------------------------------------------------


def test_a_later_overlapping_cash_export_shows_added_and_unchanged_then_commits(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        trades = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        _match_snhx(s, trades)
        commit.commit(s, CTX, trades.id)
        commit.commit(
            s, CTX, _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv").id
        )
        trade_ids = {t.id for t in _current(s, pi, "trade")}

        later = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_05-Oct-2026.csv")
        plan = commit.preview(s, later.id)
        assert (len(plan.added), len(plan.unchanged), len(plan.removed)) == (1, 7, 0)
        [new] = plan.added
        assert new.record["occurred_on"] == "2026-10-05"

        result = commit.commit(s, CTX, later.id)
        assert (result.added, result.removed, result.unchanged) == (1, 0, 7)
        # the audit counts are the diff's counts
        event = _audits(s)[-1]
        assert event.after is not None
        assert (event.after["added"], event.after["removed"], event.after["unchanged"]) == (1, 0, 7)
        # rows outside the later file's range are untouched; the trade Stream is untouched
        assert len(_current(s, pi, "cash")) == 15
        assert {t.id for t in _current(s, pi, "trade")} == trade_ids
        assert not [t for t in _current(s, pi, "trade") if t.superseded_at]


def test_a_changed_row_supersedes_the_old_one_which_stays_retrievable(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        first = _stage_bytes(
            s,
            _cash_csv(
                [("02-Sep-2026", "Deposit", "100.00"), ("09-Sep-2026", "Interest", "1.00")],
                "01-Sep-2026",
                "30-Sep-2026",
            ),
            pi,
        )
        commit.commit(s, CTX, first.id)
        second = _stage_bytes(
            s,
            _cash_csv(
                [("02-Sep-2026", "Deposit", "150.00"), ("09-Sep-2026", "Interest", "1.00")],
                "01-Sep-2026",
                "30-Sep-2026",
            ),
            pi,
        )
        plan = commit.preview(s, second.id)  # changed = one removed + one added
        assert (len(plan.added), len(plan.removed), len(plan.unchanged)) == (1, 1, 1)
        result = commit.commit(s, CTX, second.id)
        assert (result.added, result.removed, result.unchanged) == (1, 1, 1)

        assert _cash_total(s, pi) == Decimal("151.00")
        page = transactions.list_for_account(
            s, pi.id, "cash", include_superseded=True, offset=0, limit=50
        )
        assert page.total == 3  # the history keeps the replaced row
        old = [r.transaction for r in page.items if r.transaction.superseded_at is not None]
        assert [(t.amount.amount_minor if t.amount else None) for t in old] == [10000]
        assert old[0].import_id == first.id and old[0].superseded_at == second.committed_at
        current_only = transactions.list_for_account(
            s, pi.id, "cash", include_superseded=False, offset=0, limit=50
        )
        assert current_only.total == 2
        summary = commit.committed_summary(s, second)
        assert summary is not None and (summary.added, summary.replaced, summary.unchanged) == (
            1,
            1,
            1,
        )


def test_a_narrower_older_file_after_a_broader_one_shows_the_rows_it_would_remove(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        broad = _stage_bytes(
            s,
            _cash_csv(
                [
                    ("02-Sep-2026", "Deposit", "100.00"),
                    ("09-Sep-2026", "Interest", "1.00"),
                    ("20-Sep-2026", "Interest", "2.00"),
                ],
                "01-Sep-2026",
                "30-Sep-2026",
            ),
            pi,
        )
        commit.commit(s, CTX, broad.id)
        narrow = _stage_bytes(
            s,
            _cash_csv([("02-Sep-2026", "Deposit", "100.00")], "01-Sep-2026", "15-Sep-2026"),
            pi,
        )
        plan = commit.preview(s, narrow.id)
        # only the 09-Sep row is inside the narrow range and missing from it
        assert (len(plan.added), len(plan.unchanged)) == (0, 1)
        assert [t.occurred_on for t in plan.removed] == [date(2026, 9, 9)]
        commit.commit(s, CTX, narrow.id)
        assert sorted(t.occurred_on for t in _current(s, pi, "cash")) == [
            date(2026, 9, 2),
            date(2026, 9, 20),  # outside the narrow range: the earlier Import keeps it
        ]


def test_a_commit_leaves_other_sources_streams_ranges_and_opening_rows_alone(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        now = datetime(2026, 10, 1)
        other = ImportSource(account_id=pi.id, importer_key="other_csv", config={}, created_at=now)
        s.add(other)
        s.flush()
        foreign = Import(
            import_source_id=other.id,
            stream="cash",
            file_sha256="0" * 64,
            file_name="other.csv",
            content=b"x",
            coverage_from=date(2026, 9, 1),
            coverage_to=date(2026, 9, 30),
            status="committed",
            added_at=now,
            committed_at=now,
        )
        s.add(foreign)
        s.flush()

        def cash_row(on: date, import_id: int | None, kind: str = "deposit") -> Transaction:
            row = Transaction(
                account_id=pi.id,
                stream="cash",
                import_id=import_id,
                occurred_on=on,
                kind=kind,
                amount_minor=777,
                amount_ccy=AUD,
                recorded_at=now,
            )
            s.add(row)
            return row

        other_source = cash_row(date(2026, 9, 10), foreign.id)
        opening = cash_row(date(2026, 9, 5), None, "opening_balance")  # manual, in range
        trade = Transaction(
            account_id=pi.id,
            stream="trade",
            import_id=None,
            occurred_on=date(2026, 9, 5),
            kind="opening_balance",
            instrument_id=_instrument(s, "SBME").id,
            quantity=Decimal("10"),
            recorded_at=now,
        )
        s.add(trade)
        s.flush()

        mine = _stage_bytes(
            s, _cash_csv([("02-Sep-2026", "Deposit", "100.00")], "01-Sep-2026", "30-Sep-2026"), pi
        )
        plan = commit.preview(s, mine.id)
        assert not plan.removed  # none of the others is the file's to remove
        commit.commit(s, CTX, mine.id)
        for row in (other_source, opening, trade):
            s.refresh(row)
            assert row.superseded_at is None


def test_a_row_outside_the_stated_period_still_has_its_range_superseded(
    sample_factory: sessionmaker[Session],
) -> None:
    """The range is Coverage widened to the file's own rows, so such a row is never left behind
    where a later file could not supersede it."""
    with sample_factory.begin() as s:
        pi = _account(s)
        odd = _cash_csv([("02-Oct-2026", "Deposit", "5.00")], "01-Sep-2026", "30-Sep-2026")
        imp = _stage_bytes(s, odd, pi)
        assert commit.preview(s, imp.id).scope_to == date(2026, 10, 2)
        commit.commit(s, CTX, imp.id)
        later = _stage_bytes(s, _cash_csv([], "01-Sep-2026", "05-Oct-2026"), pi)
        assert [t.occurred_on for t in commit.preview(s, later.id).removed] == [date(2026, 10, 2)]


# --- cross-Stream reconciliation, fees once -------------------------------------------


def _warnings(s: Session, imp: Import) -> list[str]:
    return [w.message for w in commit.preview(s, imp.id).warnings]


def test_the_trade_file_alone_warns_for_every_trade_and_commit_is_still_allowed(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        warnings = _warnings(s, imp)
        assert len(warnings) == 5 and all("no matching cash" in w for w in warnings)
        _match_snhx(s, imp)
        commit.commit(s, CTX, imp.id)  # a one-Stream Account is allowed
        assert imp.status == "committed"
        event = _audits(s)[-1]
        assert event.after is not None and event.after["warnings"] == 5


def test_staging_the_cash_file_clears_the_matched_buys_leaving_exactly_the_unmatched_one(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        trades = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        _match_snhx(s, trades)
        commit.commit(s, CTX, trades.id)
        cash = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
        [only] = _warnings(s, cash)
        assert "Sample New Horizons ETF" in only and "2026-09-01" in only
        assert "617.28" in only and "9.50" in only  # the settlement and the fee, in one warning


def test_a_trade_file_staged_after_the_cash_is_committed_warns_only_for_the_unmatched_trade(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        commit.commit(
            s, CTX, _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv").id
        )
        trades = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        [only] = _warnings(s, trades)
        assert "2026-09-01" in only and "Sample New Horizons ETF" in only


def test_a_buy_whose_fee_row_is_missing_is_blamed_for_the_fee_only(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        no_fee = _Csv(
            b"Date,Type,Product Type,Product Name,Product ID,Units,Total\n"
            b"30-Sep-2026,Buy,ETF,Sample Broad Market Index ETF,SBME,20,-1922.15\n"
        )
        no_fee.file_name = "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv"
        cash = _stage_bytes(s, no_fee, pi)
        commit.commit(s, CTX, cash.id)
        trades = _stage(
            s, "investment_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv"
        )  # a different trade, unmatched
        unmatched = [w for w in _warnings(s, trades) if "no matching cash" in w]
        assert len(unmatched) == 1  # (it is also a sell with no earlier buy: a separate warning)
        both = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        _match_snhx(s, both)
        sept30 = [w for w in _warnings(s, both) if "2026-09-30" in w]
        assert len(sept30) == 1 and "fee of -9.50" in sept30[0] and "settlement" not in sept30[0]


# --- committing A then an overlapping B equals B alone within B's range ---------------

_counter = itertools.count(1)


def _fresh_account(s: Session) -> Account:
    template = _account(s)
    return position_accounts.create_account(
        s,
        CTX,
        position_accounts.AccountInput(
            name=f"Property test account {next(_counter)}",
            category_key="investments",
            account_type_key="brokerage_account",
            owners=[position_accounts.OwnerInput(o.member_id) for o in template.owners],
        ),
    )


days = st.integers(1, 28)
row = st.tuples(
    days,
    st.sampled_from(["Deposit", "Interest"]),
    st.integers(1, 3).map(lambda n: f"{n}.00"),
)


def _file_of(rows: list[tuple[int, str, str]], first: int, last: int) -> bytes:
    inside = [(f"{d:02d}-Sep-2026", kind, amount) for d, kind, amount in rows if first <= d <= last]
    return _cash_csv(inside, f"{first:02d}-Sep-2026", f"{last:02d}-Sep-2026")


def _canonical(
    s: Session, account: Account, lo: int, hi: int, inside: bool
) -> Counter[tuple[object, ...]]:
    return Counter(
        (t.occurred_on, t.kind, t.amount_minor)
        for t in _current(s, account, "cash")
        if (lo <= t.occurred_on.day <= hi) == inside
    )


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    a_rows=st.lists(row, max_size=8),
    b_rows=st.lists(row, max_size=8),
    a_range=st.tuples(days, days).map(sorted),
    b_range=st.tuples(days, days).map(sorted),
)
def test_committing_a_then_an_overlapping_b_equals_b_alone_within_bs_range(
    sample_factory: sessionmaker[Session],
    a_rows: list[tuple[int, str, str]],
    b_rows: list[tuple[int, str, str]],
    a_range: list[int],
    b_range: list[int],
) -> None:
    a_file = _file_of(a_rows, a_range[0], a_range[1])
    b_file = _file_of(b_rows, b_range[0], b_range[1])
    assume(a_file != b_file)  # the same file twice is the idempotent no-op, tested elsewhere
    lo, hi = b_range
    with sample_factory.begin() as s:
        both, alone = _fresh_account(s), _fresh_account(s)
        commit.commit(s, CTX, _stage_bytes(s, a_file, both).id)
        only_a = _canonical(s, both, lo, hi, inside=False)
        commit.commit(s, CTX, _stage_bytes(s, b_file, both).id)
        commit.commit(s, CTX, _stage_bytes(s, b_file, alone).id)
        # within B's range: exactly what B alone leaves
        assert _canonical(s, both, lo, hi, inside=True) == _canonical(s, alone, lo, hi, inside=True)
        # outside it: what A left, untouched
        assert _canonical(s, both, lo, hi, inside=False) == only_a


def test_the_how_to_test_sequence_ends_with_14_current_cash_rows(
    sample_factory: sessionmaker[Session],
) -> None:
    """Trades, cash, the later file, then the corrected file: the later file's 05-Oct row is
    outside the corrected file's Jul-Sep range, so it survives."""
    with sample_factory.begin() as s:
        pi = _account(s)
        trades = _stage(s, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        _match_snhx(s, trades)
        commit.commit(s, CTX, trades.id)
        commit.commit(
            s, CTX, _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv").id
        )
        commit.commit(
            s, CTX, _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_05-Oct-2026.csv").id
        )
        assert len(_current(s, pi, "cash")) == 15
        corrected = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026 (1).csv")
        plan = commit.preview(s, corrected.id)
        assert (len(plan.added), len(plan.unchanged), len(plan.removed)) == (0, 13, 1)
        assert plan.removed[0].amount == Money(100000, AUD)
        commit.commit(s, CTX, corrected.id)
        current = _current(s, pi, "cash")
        assert len(current) == 14  # 13 from the corrected file + the 05-Oct interest row
        assert date(2026, 10, 5) in {t.occurred_on for t in current}
        assert len(_current(s, pi, "trade")) == 5


def test_the_corrected_sample_export_supersedes_exactly_the_dropped_deposit(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        pi = _account(s)
        commit.commit(
            s, CTX, _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv").id
        )
        before = {t.id for t in _current(s, pi, "cash")}
        corrected = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026 (1).csv")
        plan = commit.preview(s, corrected.id)
        assert (len(plan.added), len(plan.unchanged), len(plan.removed)) == (0, 13, 1)
        [gone] = plan.removed
        assert (gone.occurred_on, gone.kind, gone.amount) == (
            date(2026, 9, 1),
            "deposit",
            Money(100000, AUD),
        )
        commit.commit(s, CTX, corrected.id)
        after = {t.id for t in _current(s, pi, "cash")}
        assert before - after == {gone.id}  # exactly that row, nothing else
        assert after <= before  # and nothing new was written
        assert gone.superseded_at is not None
