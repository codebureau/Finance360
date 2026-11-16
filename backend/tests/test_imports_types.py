"""Unrecognised transaction types stage for the owner to map (integration).

Real migrated temporary SQLite, the synthetic household, and the invented managed-fund export pair
(`SAMPLE-KIDS-0001`): `DRP` is built in, `Managed Fund Transaction` is not, so it stages unmapped,
blocks commit, and once the owner maps it the mapping is saved on the Import source and applies to
every later file for that source.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.imports import commit, service, transactions
from finance360.capabilities.position import accounts as position_accounts
from finance360.capabilities.reference import instruments as reference
from finance360.core import transaction as core_transaction
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.account import Account
from finance360.core.models.audit import AuditEvent
from finance360.core.models.household import Member
from finance360.core.models.imports import Import, ImportSource, StagedRow

CTX = AuditContext("owner", "owner", "api")
IMPORTS = Path(__file__).resolve().parents[2] / "fixtures" / "sample_household" / "imports"
TRADES = "investment_transactions_accSAMPLE-KIDS-0001_03-Aug-2026_30-Sep-2026.csv"
CASH = "cash_transactions_accSAMPLE-KIDS-0001_01-Jul-2026_30-Sep-2026.csv"
TYPE = "Managed Fund Transaction"
#: The fixture's units: 3 x Managed Fund Transaction (100 + 200 + 420) and 2 x DRP (8 + 10.5).
MFT_UNITS = Decimal(720)
DRP_UNITS = Decimal("18.5")


def _kids(s: Session) -> Account:
    member = s.scalars(select(Member)).first()
    assert member is not None
    return position_accounts.create_account(
        s,
        CTX,
        position_accounts.AccountInput(
            name="Sample Kids investment (Sample Bank)",
            category_key="investments",
            account_type_key="brokerage_account",
            owners=[position_accounts.OwnerInput(member.id)],
        ),
    )


def _stage(
    s: Session, account: Account, name: str = TRADES, content: bytes | None = None
) -> Import:
    data = content if content is not None else (IMPORTS / name).read_bytes()
    return service.stage(s, CTX, data, name, account.id).import_


def _create_fund(s: Session, imp: Import) -> None:
    service.create_for_reference(
        s,
        CTX,
        imp.id,
        "ticker",
        "SKGF",
        reference.InstrumentInput("Sample Kids Growth Fund", "fund", "AUD"),
    )


def _units(s: Session, account: Account) -> Decimal:
    held = transactions.holdings(s, account.id, None).holdings.units
    return sum(held.values(), Decimal(0))


def _kinds(s: Session, imp: Import) -> list[str]:
    rows = s.scalars(
        select(StagedRow).where(StagedRow.import_id == imp.id).order_by(StagedRow.row_no)
    )
    return [r.record["kind"] for r in rows]


def test_an_unmapped_type_stages_and_blocks_commit_until_it_is_mapped(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        kids = _kids(s)
        imp = _stage(s, kids)
        (waiting,) = service.unmapped_types(s, imp.id)
        assert (waiting.raw_type, waiting.row_count, waiting.stream) == (TYPE, 3, "trade")
        assert waiting.choices == ("buy", "sell", "ignore")  # DRP is built in, so not listed
        assert service.summarise(s, imp).unmapped_count == 3
        assert _kinds(s, imp).count("unrecognised") == 3
        _create_fund(s, imp)  # references are fine; only the Types block
        with pytest.raises(ValidationError) as blocked:
            commit.commit(s, CTX, imp.id)
        assert [e.field for e in blocked.value.errors] == ["types"]
        assert not core_transaction.current(s, kids.id, "trade")

        service.map_type(s, CTX, imp.id, TYPE, "buy")
        assert service.unmapped_types(s, imp.id) == []
        service.check_committable(s, imp.id)
        commit.commit(s, CTX, imp.id)
        assert _units(s, kids) == MFT_UNITS + DRP_UNITS


def test_the_mapping_is_saved_validated_and_audited_on_the_import_source(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        kids = _kids(s)
        imp = _stage(s, kids)
        with pytest.raises(ValidationError):  # a cash meaning for a trade Type
            service.map_type(s, CTX, imp.id, TYPE, "deposit")
        with pytest.raises(NotFoundError):  # DRP is built in, so it is not waiting
            service.map_type(s, CTX, imp.id, "DRP", "buy")
        service.map_type(s, CTX, imp.id, TYPE.upper(), "buy")  # matched ignoring case
        source = s.scalars(select(ImportSource).where(ImportSource.account_id == kids.id)).one()
        assert source.config == {"type_map": {"trade": {TYPE: "buy"}}}
        event = s.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "import_source")
            .order_by(AuditEvent.id.desc())
        ).first()
        assert event is not None and event.action == "update"
        assert event.before == {"id": source.id, "config": {}}
        assert event.after == {"id": source.id, "config": source.config}


def test_a_mapping_applies_to_every_staged_import_of_the_source_and_to_the_next_file(
    sample_factory: sessionmaker[Session],
) -> None:
    other = (
        (IMPORTS / TRADES).read_text().replace("03-Aug-2026", "04-Aug-2026").encode()
    )  # another file, so another hash
    with sample_factory.begin() as s:
        kids = _kids(s)
        first = _stage(s, kids)
        second = _stage(
            s,
            kids,
            "investment_transactions_accSAMPLE-KIDS-0001_03-Aug-2026_30-Sep-2026 (1).csv",
            other,
        )
        assert second.id != first.id
        service.map_type(s, CTX, first.id, TYPE, "buy")
        assert service.unmapped_types(s, second.id) == []  # re-applied, not asked again
        assert _kinds(s, second).count("buy") == 5

        third_bytes = other.replace(b"15-Aug-2026", b"16-Aug-2026")
        third = _stage(
            s,
            kids,
            "investment_transactions_accSAMPLE-KIDS-0001_03-Aug-2026_30-Sep-2026 (2).csv",
            third_bytes,
        )
        assert service.unmapped_types(s, third.id) == []  # the next file: applied on staging
        assert service.summarise(s, third).unmapped_count == 0


def test_an_ignored_type_is_never_committed(sample_factory: sessionmaker[Session]) -> None:
    with sample_factory.begin() as s:
        kids = _kids(s)
        imp = _stage(s, kids)
        _create_fund(s, imp)
        service.map_type(s, CTX, imp.id, TYPE, "ignore")
        assert _kinds(s, imp).count("ignored") == 3
        result = commit.commit(s, CTX, imp.id)
        assert result.added == 2  # only the two DRP rows
        assert _units(s, kids) == DRP_UNITS
        kinds = {t.kind for t in core_transaction.current(s, kids.id, "trade")}
        assert kinds == {"buy"}


def test_a_drp_raises_no_missing_settlement_warning_either_way_round(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory.begin() as s:
        kids = _kids(s)
        trades = _stage(s, kids)
        _create_fund(s, trades)
        service.map_type(s, CTX, trades.id, TYPE, "buy")
        cash = _stage(s, kids, CASH)
        # trades staged against a committed cash Stream: the DRP rows need no cash
        commit.commit(s, CTX, cash.id)
        assert commit.preview(s, trades.id).warnings == ()
        commit.commit(s, CTX, trades.id)
        # and the other way: the committed DRP trades are not missing settlements once the cash
        # file is restaged with a corrected row (supersession path reconciles the committed trades)
        corrected = (IMPORTS / CASH).read_text().replace("0.05", "0.06").encode()
        later = _stage(
            s,
            kids,
            "cash_transactions_accSAMPLE-KIDS-0001_01-Jul-2026_30-Sep-2026 (1).csv",
            corrected,
        )
        plan = commit.preview(s, later.id)
        assert [w for w in plan.warnings if w.code == "no_matching_cash"] == []
        assert (len(plan.added), len(plan.removed)) == (1, 1)  # only the interest row changed


def test_without_the_drp_flag_the_same_rows_would_warn(
    sample_factory: sessionmaker[Session],
) -> None:
    """The flag is what silences the warning: the same DRP rows mapped as ordinary buys warn."""
    with sample_factory.begin() as s:
        kids = _kids(s)
        text = (IMPORTS / TRADES).read_text().replace(",DRP,", f",{TYPE},")
        trades = _stage(s, kids, TRADES, text.encode())
        service.map_type(s, CTX, trades.id, TYPE, "buy")
        _create_fund(s, trades)  # the references appear once the Types are mapped
        cash = _stage(s, kids, CASH)
        commit.commit(s, CTX, cash.id)
        warned = commit.preview(s, trades.id).warnings
        assert len([w for w in warned if w.code == "no_matching_cash"]) == 2  # the two former DRPs
