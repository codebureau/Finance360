"""Import staging against a real migrated temporary SQLite and the synthetic household
(integration): idempotency, discard, Importer detection, the Account the owner picks, and
Instrument resolution through the crosswalk with the unresolved-references block."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.imports import account_identifiers, service
from finance360.capabilities.reference import instruments as reference
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.account import Account
from finance360.core.models.account_identifier import AccountIdentifier
from finance360.core.models.audit import AuditEvent
from finance360.core.models.imports import Import, ImportSource, StagedRow
from finance360.core.models.instrument import Instrument

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
PI = "Sample Brokerage account"


def _file(name: str) -> bytes:
    return (IMPORTS / name).read_bytes()


def _account(s: Session, name: str = PI) -> Account:
    return s.scalars(select(Account).where(Account.name == name)).one()


def _instrument(s: Session, ticker: str) -> Instrument:
    found = reference.resolve(s, ticker, "ticker")
    assert isinstance(found, Instrument)
    return found


def _count(s: Session, model: type) -> int:
    return s.scalar(select(func.count()).select_from(model)) or 0


def _audit_count(s: Session, entity_type: str) -> int:
    return (
        s.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_type == entity_type)
        )
        or 0
    )


def _stage(
    s: Session,
    name: str = "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv",
    account: str = PI,
) -> service.StageResult:
    return service.stage(s, CTX, _file(name), name, _account(s, account).id)


# --- staging and idempotency ---------------------------------------------------------


def test_staging_the_trade_file_stores_the_import_its_rows_and_the_raw_file(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        result = _stage(s)
        imp_id = result.import_.id
        assert result.created
    with sample_factory() as s:
        imp = s.get(Import, imp_id)
        assert imp is not None
        assert (imp.stream, imp.status, imp.file_name) == (
            "trade",
            "staged",
            # the account number in the file name is masked when stored
            "investment_transactions_acc••• 0001_03-Aug-2026_30-Sep-2026.csv",
        )
        assert imp.content == _file(
            "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
        )  # byte for byte
        assert len(imp.file_sha256) == 64
        assert (str(imp.coverage_from), str(imp.coverage_to)) == ("2026-08-03", "2026-09-30")
        source = s.get(ImportSource, imp.import_source_id)
        assert source is not None
        assert (source.importer_key, source.config) == ("vanguard_pi", {})
        assert source.account_id == _account(s).id
        rows = list(s.scalars(select(StagedRow).order_by(StagedRow.row_no)))
        assert [r.row_no for r in rows] == [1, 2, 3, 4, 5]
        assert rows[0].record["quantity"] == "20"
        assert rows[0].record["fee"] == {"minor": 950, "ccy": "AUD"}
        assert not any(isinstance(v, float) for r in rows for v in r.record.values())


def test_the_same_file_twice_returns_the_existing_import_and_changes_nothing(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        first = _stage(s)
    with sample_factory.begin() as s:
        before = (
            _count(s, Import),
            _count(s, StagedRow),
            _count(s, ImportSource),
            _count(s, AuditEvent),
        )
        # Same bytes under a different name is still the same file.
        again = service.stage(
            s,
            CTX,
            _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
            "renamed.csv",
            _account(s).id,
        )
        assert (again.created, again.import_.id) == (False, first.import_.id)
    with sample_factory() as s:
        assert before == (
            _count(s, Import),
            _count(s, StagedRow),
            _count(s, ImportSource),
            _count(s, AuditEvent),
        )
        assert before[:3] == (1, 5, 1)


def test_the_two_streams_and_an_overlapping_export_are_separate_imports_on_one_source(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        trade = _stage(s)
        cash = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv")
        later = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_05-Oct-2026.csv")
        assert len({trade.import_.id, cash.import_.id, later.import_.id}) == 3
        assert (trade.import_.stream, cash.import_.stream, later.import_.stream) == (
            "trade",
            "cash",
            "cash",
        )
        assert _count(s, ImportSource) == 1  # one Import source per Account and Importer
        assert _count(s, StagedRow) == 5 + 14 + 8


def test_discarding_a_staged_import_marks_it_discarded_keeps_rows_and_frees_the_file(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        first = _stage(s)
        discarded = service.discard(s, CTX, first.import_.id)
        assert discarded.status == "discarded"
        assert service.row_count(s, first.import_.id) == 5  # kept for inspection
    with sample_factory.begin() as s:
        again = _stage(s)  # a discarded Import no longer blocks the same file
        assert again.created and again.import_.id != first.import_.id
    with sample_factory() as s:
        events = list(
            s.scalars(
                select(AuditEvent).where(AuditEvent.entity_type == "import").order_by(AuditEvent.id)
            )
        )
        assert [e.summary.split(" ")[0] for e in events] == ["Staged", "Discarded", "Staged"]


def test_only_a_staged_import_can_be_discarded(sample_factory: sessionmaker[Session]) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s).import_
        imp.status = "committed"  # what the commit slice will do
    with sample_factory.begin() as s, pytest.raises(ValidationError) as raised:
        service.discard(s, CTX, imp.id)
    assert "committed" in raised.value.errors[0].message
    with sample_factory.begin() as s, pytest.raises(NotFoundError):
        service.discard(s, CTX, 9999)


def test_an_unrecognised_file_is_refused_clearly_and_writes_nothing(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        account_id = _account(s).id
        before = _count(s, AuditEvent)
    for content in (_file("sample_bank_statement.csv"), b"", b"\x00\x01\x02"):
        with sample_factory.begin() as s, pytest.raises(ValidationError) as raised:
            service.stage(s, CTX, content, "x.csv", account_id)
        assert raised.value.errors[0].field == "file"
    with sample_factory() as s:
        assert (_count(s, Import), _count(s, ImportSource), _count(s, AuditEvent)) == (0, 0, before)
    with sample_factory() as s:
        assert "does not recognise" in _message(s, _file("sample_bank_statement.csv"))


def _message(s: Session, content: bytes) -> str:
    with pytest.raises(ValidationError) as raised:
        service.detect(s, content)
    return raised.value.errors[0].message


def test_a_malformed_row_is_refused_with_the_row_number_and_never_the_account_number(
    sample_factory: sessionmaker[Session],
) -> None:
    bad = _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv").replace(
        b"30-Sep-2026,Buy Trade", b"2026-09-30,Buy Trade"
    )
    with sample_factory() as s:
        message = _message(s, bad)
    assert "Row 1" in message and "SAMPLE-PI" not in message


def test_a_closed_account_and_a_currency_mismatch_are_refused(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        gbp = _account(s, "Sample UK Pension").id
        closed = _account(s, "Family Car")
        closed.closed_on = date(2026, 1, 1)
        closed_id = closed.id
    with sample_factory.begin() as s, pytest.raises(ValidationError) as raised:
        service.stage(
            s,
            CTX,
            _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
            "t.csv",
            gbp,
        )
    assert "GBP" in raised.value.errors[0].message
    with sample_factory.begin() as s, pytest.raises(ValidationError) as raised:
        service.stage(
            s,
            CTX,
            _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
            "t.csv",
            closed_id,
        )
    assert "closed" in raised.value.errors[0].message
    with sample_factory.begin() as s, pytest.raises(NotFoundError):
        service.stage(
            s,
            CTX,
            _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
            "t.csv",
            9999,
        )


def test_the_import_audit_trail_names_no_account_reference(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        _stage(s)
    with sample_factory() as s:
        events = list(s.scalars(select(AuditEvent).where(AuditEvent.actor_ref == "owner")))
        assert {e.entity_type for e in events} == {"import_source", "import"}
        assert "SAMPLE-PI" not in repr([(e.summary, e.before, e.after) for e in events])


# --- Account resolution --------------------------------------------------------------


def test_the_files_reference_resolves_to_the_account_that_holds_the_identifier(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory() as s:
        found = service.detect(
            s, _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
        )
        assert [a.name for a in found.accounts] == [PI]


def test_an_unknown_reference_resolves_to_no_account_and_saving_it_teaches_the_next_add(
    sample_factory: sessionmaker[Session],
) -> None:
    other = _file("investment_transactions_accSAMPLE-PI-9999_21-Sep-2026_21-Sep-2026.csv")
    with sample_factory.begin() as s:
        assert service.detect(s, other).accounts == []
        everyday = _account(s, "Everyday account (Sample Bank)")
        result = service.stage(s, CTX, other, "o.csv", everyday.id, save_identifier=True)
        assert result.created
    with sample_factory() as s:
        found = service.detect(s, other)
        assert [a.name for a in found.accounts] == ["Everyday account (Sample Bank)"]
        # The audited identifier snapshot holds the masked value only.
        saved = s.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "account_identifier")
            .order_by(AuditEvent.id)
        ).all()
        assert "SAMPLE-PI-9999" not in repr([(e.summary, e.after) for e in saved])


def test_saving_an_identifier_that_already_resolves_adds_nothing(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        before = _count(s, AccountIdentifier)
        service.stage(
            s,
            CTX,
            _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
            "t.csv",
            _account(s).id,
            save_identifier=True,
        )
        assert _count(s, AccountIdentifier) == before


def test_saving_an_identifier_another_account_holds_is_refused_and_stages_nothing(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        everyday = _account(s, "Everyday account (Sample Bank)").id
    with sample_factory.begin() as s, pytest.raises(ValidationError) as raised:
        service.stage(
            s,
            CTX,
            _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
            "t.csv",
            everyday,
            save_identifier=True,
        )
    assert raised.value.errors[0].field == "value" and "SAMPLE-PI" not in str(raised.value)


def test_several_matching_accounts_means_the_owner_picks(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        everyday = _account(s, "Everyday account (Sample Bank)").id
        # Same number, no institution: both Accounts now answer to the file's reference.
        account_identifiers.add_identifier(
            s,
            CTX,
            everyday,
            account_identifiers.IdentifierInput("vendor_account_id", "sample-pi-0001"),
        )
    with sample_factory() as s:
        assert (
            len(
                service.detect(
                    s,
                    _file("investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"),
                ).accounts
            )
            == 2
        )


# --- Instrument resolution -----------------------------------------------------------


def test_rows_resolve_through_the_crosswalk_including_name_only_distributions(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        etf, mining = _instrument(s, "SBME"), _instrument(s, "SMPM")
        cash = _stage(s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv").import_
        rows = list(
            s.scalars(
                select(StagedRow).where(StagedRow.import_id == cash.id).order_by(StagedRow.row_no)
            )
        )
        by_kind = {
            (r.record["kind"], r.record["occurred_on"]): r.instrument_id
            for r in rows
            if r.instrument_ref
        }
        # Distributions name the product only; the 'name' alias carries them to the Instrument.
        assert by_kind[("distribution", "2026-09-10")] == etf.id
        assert by_kind[("distribution", "2026-08-25")] == mining.id
        assert by_kind[("trade_settlement", "2026-09-30")] == etf.id  # by Product ID (ticker)
        assert by_kind[("trade_settlement", "2026-09-15")] == mining.id
        # Rows that name no product need no Instrument.
        assert all(
            r.instrument_id is None and r.instrument_ref is None
            for r in rows
            if r.record["kind"] in ("deposit", "interest", "fee")
        )
        assert service.unresolved_references(s, cash.id) == []


def test_one_product_outside_the_roster_is_flagged_as_an_unresolved_reference(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s).import_
        pending = service.unresolved_references(s, imp.id)
        assert pending == [
            service.UnresolvedReference(
                "ticker", "SNHX", "Sample New Horizons ETF", "etf", row_count=1
            )
        ]
        assert service.summarise(s, imp).unresolved_count == 1
        resolved = s.scalars(
            select(StagedRow).where(
                StagedRow.import_id == imp.id, StagedRow.instrument_id.is_not(None)
            )
        ).all()
        assert len(resolved) == 4


def test_commit_is_refused_while_any_reference_is_unresolved(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s).import_
        with pytest.raises(ValidationError) as raised:
            service.check_committable(s, imp.id)
        assert raised.value.errors[0].field == "references"
        assert "1 reference" in raised.value.errors[0].message
        fund = reference.resolve(s, "SBGF-0001", "vendor_id")
        assert isinstance(fund, Instrument)
        service.map_reference(s, CTX, imp.id, "ticker", "SNHX", fund.id)
        service.check_committable(s, imp.id)  # clear: no longer raises
        discarded = _stage(
            s, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv"
        ).import_
        service.discard(s, CTX, discarded.id)
        with pytest.raises(ValidationError) as raised:
            service.check_committable(s, discarded.id)
        assert raised.value.errors[0].field == "status"


def test_mapping_a_reference_adds_an_audited_identifier_and_the_rows_re_resolve(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s).import_
        fund = reference.resolve(s, "SBGF-0001", "vendor_id")
        assert isinstance(fund, Instrument)
        fund_id = fund.id
        service.map_reference(s, CTX, imp.id, "ticker", " snhx ", fund_id)  # case and space ignored
    with sample_factory() as s:
        row = s.scalars(
            select(StagedRow).where(
                StagedRow.import_id == imp.id, StagedRow.instrument_ref == "SNHX"
            )
        ).one()
        assert row.instrument_id == fund_id
        assert service.unresolved_references(s, imp.id) == []
        found = reference.resolve(s, "SNHX", "ticker")
        assert isinstance(found, Instrument) and found.id == fund_id
        event = s.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "instrument", AuditEvent.entity_id == fund_id)
            .order_by(AuditEvent.id.desc())
        ).first()
        assert event is not None and event.actor_ref == "owner"
        assert event.summary == "Added ticker 'SNHX' to Sample Balanced Growth Fund"


def test_mapping_re_resolves_the_same_reference_in_every_other_staged_import(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        first = _stage(s).import_
        second = _stage(s, account="Everyday account (Sample Bank)").import_
        assert first.id != second.id
        fund = reference.resolve(s, "SBGF-0001", "vendor_id")
        assert isinstance(fund, Instrument)
        service.map_reference(s, CTX, first.id, "ticker", "SNHX", fund.id)
        assert service.unresolved_references(s, second.id) == []


def test_a_reference_not_waiting_in_the_import_cannot_be_mapped(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s).import_
        fund = reference.resolve(s, "SBGF-0001", "vendor_id")
        assert isinstance(fund, Instrument)
        with pytest.raises(NotFoundError):
            service.map_reference(s, CTX, imp.id, "ticker", "SBME", fund.id)  # already resolved
        with pytest.raises(NotFoundError):
            service.map_reference(s, CTX, imp.id, "ticker", "NOPE", fund.id)


def test_creating_an_instrument_for_a_reference_adds_the_ticker_and_the_name_alias(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s).import_
        made = service.create_for_reference(
            s,
            CTX,
            imp.id,
            "ticker",
            "SNHX",
            reference.InstrumentInput("Sample New Horizons ETF", "etf", "AUD"),
        )
        made_id = made.id
        assert {(i.scheme, i.value) for i in made.identifiers} == {
            ("ticker", "SNHX"),
            ("name", "Sample New Horizons ETF"),
        }
        assert service.unresolved_references(s, imp.id) == []
    with sample_factory() as s:
        row = s.scalars(
            select(StagedRow).where(
                StagedRow.import_id == imp.id, StagedRow.instrument_ref == "SNHX"
            )
        ).one()
        assert row.instrument_id == made_id


def test_creating_an_instrument_with_bad_fields_is_refused_and_the_reference_stays_unresolved(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        imp = _stage(s).import_
    with sample_factory.begin() as s, pytest.raises(ValidationError) as raised:
        service.create_for_reference(
            s, CTX, imp.id, "ticker", "SNHX", reference.InstrumentInput("", "bond", "XX")
        )
    assert {e.field for e in raised.value.errors} >= {"name", "kind", "currency"}
    with sample_factory() as s:
        assert len(service.unresolved_references(s, imp.id)) == 1


def test_an_unsupported_config_is_refused_by_the_importers_schema(
    sample_factory: sessionmaker[Session],
) -> None:
    from finance360.plugins.importers.vanguard_pi import IMPORTER

    with pytest.raises(ValidationError) as raised:
        service._validated_config(IMPORTER, {"delimiter": ";"})  # pyright: ignore[reportPrivateUsage]
    assert raised.value.errors[0].field == "config"
