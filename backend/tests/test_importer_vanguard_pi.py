"""Contract tests for the Vanguard PI Importer: synthetic fixture files in, canonical rows out.
No database, no session, no network (ADR-0013 section 5)."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from finance360.capabilities.imports import registry
from finance360.capabilities.imports.importer import (
    CASH_KINDS,
    TRADE_KINDS,
    CanonicalRow,
    ImporterError,
    ParsedFile,
)
from finance360.core.money import Money
from finance360.plugins.importers import vanguard_pi
from finance360.plugins.importers.vanguard_pi import IMPORTER

REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
HEADER_TRADE = (
    "Account number,Investment,Product ID,Product Type,Trade Date,Type,Unit Price,Quantity,"
    "Value,Brokerage\n"
)
HEADER_CASH = "Date,Type,Product Type,Product Name,Product ID,Units,Total\n"
ACC = "SAMPLE-PI-0001"
#: A file name that follows the export pattern, for the in-test files below.
NAMED = "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv"

OTHER = "investment_transactions_accSAMPLE-PI-9999_21-Sep-2026_21-Sep-2026.csv"
SELL_ONLY = "investment_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv"
TRADES = "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
CASH_LATER = "cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_05-Oct-2026.csv"
CASH = "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv"


def _file(name: str) -> bytes:
    return (IMPORTS / name).read_bytes()


def _parse(name: str) -> ParsedFile:
    return IMPORTER.parse(_file(name), {}, name)


def _aud(amount_minor: int) -> Money:
    return Money(amount_minor, "AUD")


# --- claiming ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        TRADES,
        CASH,
        CASH_LATER,
        SELL_ONLY,
        OTHER,
    ],
)
def test_every_fixture_export_is_claimed(name: str) -> None:
    assert IMPORTER.claims(_file(name), name)


@pytest.mark.parametrize(
    "content",
    [
        _file("sample_bank_statement.csv"),
        (REPO / "fixtures" / "sample_household" / "household.json").read_bytes(),
        b"",
        b"\xff\xfe\x00 not text at all \x80",
        b"Date,Amount\n01-Jan-2026,5.00\n",
    ],
    ids=["bank csv", "json", "empty", "binary", "date and amount only"],
)
def test_a_file_that_is_not_a_vanguard_export_is_not_claimed(content: bytes) -> None:
    assert not IMPORTER.claims(content, NAMED)
    with pytest.raises(ImporterError):
        IMPORTER.parse(content, {}, NAMED)


@pytest.mark.parametrize("name", [TRADES, CASH])
def test_recognition_needs_only_the_header_not_the_file_name(name: str) -> None:
    assert IMPORTER.claims(_file(name), "whatever.csv") and IMPORTER.claims(_file(name), "")
    assert not IMPORTER.claims(b"Date,Amount\n", NAMED)  # a good name does not make a claim


# --- the trade Stream ----------------------------------------------------------------------


def test_the_trade_file_parses_to_signed_canonical_rows_in_file_order() -> None:
    parsed = _parse(TRADES)
    assert parsed.stream == "trade"
    rows = parsed.rows
    assert [r.row_no for r in rows] == [1, 2, 3, 4, 5]
    # Descending file order is kept; unsigned units become signed by the Type column.
    assert [r.occurred_on for r in rows] == [
        date(2026, 9, 30),
        date(2026, 9, 15),
        date(2026, 9, 1),
        date(2026, 8, 12),
        date(2026, 8, 3),
    ]
    assert [(r.kind, r.quantity) for r in rows] == [
        ("buy", Decimal("20")),
        ("sell", Decimal("-40")),
        ("buy", Decimal("50")),
        ("buy", Decimal("100")),
        ("buy", Decimal("30")),
    ]
    first = rows[0]
    assert (first.instrument_ref, first.instrument_ref_scheme) == ("SBME", "ticker")
    assert first.unit_price == Decimal("96.1075") and first.unit_price_ccy == "AUD"
    assert first.fee == _aud(950)  # informational: only the cash Stream's fee row moves cash
    assert first.amount is None
    assert all(r.kind in TRADE_KINDS for r in rows)


def test_product_type_becomes_an_instrument_kind_hint_and_the_name_is_kept() -> None:
    rows = _parse(TRADES).rows
    assert [(r.instrument_ref, r.instrument_kind_hint) for r in rows] == [
        ("SBME", "etf"),
        ("SMPM", "share"),
        ("SNHX", "etf"),
        ("SMPM", "share"),
        ("SBME", "etf"),
    ]
    assert rows[2].instrument_name == "Sample New Horizons ETF"


def test_trade_coverage_is_the_min_to_max_row_date_when_the_file_name_states_no_period() -> None:
    parsed = IMPORTER.parse(_file(TRADES), {}, "export.csv")
    assert (parsed.coverage_from, parsed.coverage_to) == (date(2026, 8, 3), date(2026, 9, 30))
    assert any("states no period" in w for w in parsed.warnings)
    named = _parse(TRADES)  # the same dates come from the name, with nothing to warn about
    assert (named.coverage_from, named.coverage_to) == (date(2026, 8, 3), date(2026, 9, 30))
    assert named.warnings == ()


def test_a_stated_period_is_the_coverage_even_when_the_rows_are_narrower() -> None:
    parsed = _parse(SELL_ONLY)
    assert (parsed.coverage_from, parsed.coverage_to) == (date(2026, 9, 1), date(2026, 9, 30))
    assert parsed.warnings == ()
    (sell,) = parsed.rows
    assert (sell.kind, sell.quantity) == ("sell", Decimal("-10"))


# --- the cash Stream -----------------------------------------------------------------------


def test_the_cash_file_keeps_signed_amounts_and_maps_every_kind() -> None:
    parsed = _parse(CASH)
    assert parsed.stream == "cash"
    assert (parsed.coverage_from, parsed.coverage_to) == (date(2026, 7, 1), date(2026, 9, 30))
    assert parsed.warnings == ()
    rows = parsed.rows
    assert len(rows) == 14
    assert {r.kind for r in rows} == {
        "interest",
        "fee",
        "trade_settlement",
        "distribution",
        "deposit",
    }
    assert {r.kind for r in rows} <= set(CASH_KINDS)
    amounts = {(r.occurred_on, r.kind, r.instrument_ref): r.amount for r in rows}
    assert amounts[(date(2026, 9, 30), "trade_settlement", "SBME")] == _aud(-192215)
    assert amounts[(date(2026, 9, 15), "trade_settlement", "SMPM")] == _aud(25600)
    assert amounts[(date(2026, 7, 2), "deposit", None)] == _aud(500000)
    assert amounts[(date(2026, 9, 30), "interest", None)] == _aud(417)


def test_a_buy_settles_without_its_fee_which_is_a_separate_cash_row() -> None:
    cash = _parse(CASH).rows
    trades = _parse(TRADES).rows
    buy = next(r for r in trades if r.occurred_on == date(2026, 8, 12) and r.kind == "buy")
    settle = next(
        r for r in cash if r.occurred_on == buy.occurred_on and r.kind == "trade_settlement"
    )
    assert buy.quantity is not None and buy.unit_price is not None and buy.fee is not None
    assert settle.amount == _aud(-int(buy.quantity * buy.unit_price * 100))  # value only
    fees = [r for r in cash if r.occurred_on == buy.occurred_on and r.kind == "fee"]
    assert [f.amount for f in fees] == [_aud(-950)] and buy.fee == _aud(950)
    assert fees[0].instrument_ref is None


def test_distributions_carry_the_product_name_but_no_product_id() -> None:
    rows = _parse(CASH).rows
    distributions = [r for r in rows if r.kind == "distribution"]
    assert [(r.instrument_ref, r.instrument_ref_scheme) for r in distributions] == [
        ("Sample Broad Market Index ETF", "name"),
        ("Sample Mining Limited", "name"),
    ]
    # ...whereas a settlement carries the Product ID.
    settle = next(r for r in rows if r.kind == "trade_settlement")
    assert settle.instrument_ref_scheme == "ticker"


def test_the_later_overlapping_cash_export_is_narrower_and_later() -> None:
    full = _parse(CASH)
    later = _parse(CASH_LATER)
    assert (later.coverage_from, later.coverage_to) == (date(2026, 9, 1), date(2026, 10, 5))
    assert later.coverage_from > full.coverage_from and later.coverage_to > full.coverage_to
    assert (later.coverage_to - later.coverage_from) < (full.coverage_to - full.coverage_from)
    keep = lambda r: (r.occurred_on, r.kind, r.amount)  # noqa: E731
    overlap = {keep(r) for r in full.rows if r.occurred_on >= later.coverage_from}
    assert overlap <= {keep(r) for r in later.rows}


def test_one_buy_has_no_matching_cash_settlement() -> None:
    """The deliberate gap the later reconciliation slice must flag."""
    trades = _parse(TRADES).rows
    settled = {
        (r.occurred_on, r.instrument_ref) for r in _parse(CASH).rows if r.kind == "trade_settlement"
    }
    unmatched = [r for r in trades if (r.occurred_on, r.instrument_ref) not in settled]
    assert [(r.instrument_ref, r.occurred_on) for r in unmatched] == [("SNHX", date(2026, 9, 1))]


# --- account reference ---------------------------------------------------------------------


def test_the_account_reference_is_emitted_on_the_parsed_file_not_the_rows() -> None:
    ref = _parse(TRADES).account_reference
    assert ref is not None
    assert (ref.kind, ref.value, ref.institution) == (
        "vendor_account_id",
        "SAMPLE-PI-0001",
        "Vanguard PI",
    )
    other = _parse(OTHER).account_reference
    assert other is not None and other.value == "SAMPLE-PI-9999"


def _trade(
    when: str = "01-Sep-2026",
    typ: str = "Buy Trade",
    qty: str = "1",
    price: str = "10.00",
    fee: str = "0",
    ptype: str = "ETF",
    acc: str = ACC,
) -> str:
    return f"{acc},Sample ETF,SBME,{ptype},{when},{typ},{price},{qty},10.00,{fee}\n"


def _cash(when: str = "01-Sep-2026", typ: str = "Deposit", total: str = "5.00") -> str:
    return f"{when},{typ},Cash account,Deposit from linked bank,CASH,,{total}\n"


def test_a_file_without_an_account_number_still_parses_and_says_so() -> None:
    content = (HEADER_CASH + _cash()).encode()
    parsed = IMPORTER.parse(content, {}, "export.csv")  # a cash file's number is only in its name
    assert parsed.account_reference is None
    assert any("no account number" in w for w in parsed.warnings)


def test_the_cash_files_account_number_comes_from_its_file_name() -> None:
    ref = _parse(CASH).account_reference
    assert ref is not None and (ref.kind, ref.value) == ("vendor_account_id", ACC)


def test_the_trade_files_account_number_is_on_its_rows_and_one_account_only() -> None:
    content = (HEADER_TRADE + _trade()).encode()
    ref = IMPORTER.parse(content, {}, "export.csv").account_reference
    assert ref is not None and ref.value == ACC
    mixed = (HEADER_TRADE + _trade() + _trade(acc="SAMPLE-PI-9999")).encode()
    with pytest.raises(ImporterError) as raised:
        IMPORTER.parse(mixed, {}, "export.csv")
    assert "SAMPLE-PI" not in str(raised.value)


def test_a_blank_brokerage_is_zero_and_a_deposit_names_no_instrument() -> None:
    trade = IMPORTER.parse((HEADER_TRADE + _trade(fee="")).encode(), {}, NAMED).rows[0]
    assert trade.fee == _aud(0)
    deposit = IMPORTER.parse((HEADER_CASH + _cash()).encode(), {}, NAMED).rows[0]
    assert (deposit.instrument_ref, deposit.instrument_name) == (None, None)
    assert deposit.description == "Deposit from linked bank"


# --- errors and warnings never echo the account reference ----------------------------------

BAD_FILES = {
    "date": HEADER_TRADE + _trade(when="2026-09-01"),
    "units": HEADER_TRADE + _trade(qty="-1"),
    "fee": HEADER_TRADE + _trade(fee="abc"),
    "cash amount": HEADER_CASH + _cash(total="5.005"),
}


@pytest.mark.parametrize("content", list(BAD_FILES.values()), ids=list(BAD_FILES))
def test_a_bad_row_is_rejected_with_a_message_that_never_echoes_the_account_number(
    content: str,
) -> None:
    with pytest.raises(ImporterError) as raised:
        IMPORTER.parse(content.encode(), {}, NAMED)
    assert "SAMPLE-PI" not in str(raised.value) and str(raised.value)


def test_a_file_with_no_rows_and_no_stated_period_is_rejected() -> None:
    with pytest.raises(ImporterError):
        IMPORTER.parse(HEADER_CASH.encode(), {}, "export.csv")


@pytest.mark.parametrize(
    "name",
    [
        "cash_transactions_accSAMPLE-PI-0001_30-Sep-2026_01-Sep-2026.csv",  # backwards
        "cash_transactions_accSAMPLE-PI-0001_31-Feb-2026_01-Mar-2026.csv",  # not a real date
    ],
)
def test_a_file_name_period_that_is_not_real_or_runs_backwards_is_rejected(name: str) -> None:
    with pytest.raises(ImporterError) as raised:
        IMPORTER.parse((HEADER_CASH + _cash()).encode(), {}, name)
    assert "SAMPLE-PI" not in str(raised.value)


def test_a_repeat_download_suffix_does_not_hide_the_file_names_account_or_period() -> None:
    parsed = _parse("cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026 (1).csv")
    assert (parsed.coverage_from, parsed.coverage_to) == (date(2026, 7, 1), date(2026, 9, 30))
    assert parsed.account_reference is not None


def test_no_warning_echoes_the_account_number() -> None:
    for name in (TRADES, CASH, CASH_LATER):
        parsed = _parse(name)
        assert not any("SAMPLE-PI" in w for w in parsed.warnings)
    odd = HEADER_TRADE + _trade(when="05-Aug-2026", ptype="Bond")
    parsed = IMPORTER.parse(odd.encode(), {}, NAMED)
    assert len(parsed.warnings) == 2  # unknown product type, row outside the period
    assert not any("SAMPLE-PI" in w for w in parsed.warnings)


# --- canonical records ---------------------------------------------------------------------


def test_a_canonical_record_holds_money_as_minor_units_and_numbers_as_strings() -> None:
    row = _parse(TRADES).rows[0]
    record = row.to_record()
    assert record["quantity"] == "20" and record["unit_price"] == "96.1075"
    assert record["fee"] == {"minor": 950, "ccy": "AUD"} and record["amount"] is None
    assert record["occurred_on"] == "2026-09-30"
    cash = _parse(CASH).rows[0].to_record()
    assert cash["amount"] == {"minor": 417, "ccy": "AUD"}
    assert not any(isinstance(v, float) for v in record.values())


def test_parsing_is_deterministic() -> None:
    assert _parse(CASH) == _parse(CASH)


def test_the_config_schema_takes_a_type_map_and_rejects_anything_else() -> None:
    from jsonschema import ValidationError, validate

    validate({}, IMPORTER.config_schema)
    ok = {"type_map": {"trade": {"Odd": "buy"}, "cash": {"Odd": "ignore"}}}
    validate(ok, IMPORTER.config_schema)
    for bad in (
        {"colour": "red"},
        {"type_map": {"trade": {"Odd": "deposit"}}},  # not a trade meaning
        {"type_map": {"cash": {"Odd": "buy"}}},  # not a cash meaning
        {"type_map": {"other": {}}},
    ):
        with pytest.raises(ValidationError):
            validate(bad, IMPORTER.config_schema)


# --- DRP, unrecognised Types and the owner's type_map -------------------------


def _rows(content: str, config: Mapping[str, Any] | None = None) -> tuple[CanonicalRow, ...]:
    return IMPORTER.parse(content.encode(), config or {}, NAMED).rows


def test_a_drp_is_a_buy_that_needs_no_cash_settlement() -> None:
    drp, buy = _rows(HEADER_TRADE + _trade(typ="DRP", fee="") + _trade(typ="Buy Trade"))
    assert (drp.kind, drp.quantity, drp.no_settlement) == ("buy", Decimal(1), True)
    assert (buy.kind, buy.no_settlement) == ("buy", False)
    assert drp.to_record()["no_settlement"] is True


def test_an_unknown_type_is_emitted_unrecognised_with_its_raw_type_not_rejected() -> None:
    (trade,) = _rows(HEADER_TRADE + _trade(typ="Hold Trade"))
    (cash,) = _rows(HEADER_CASH + _cash(typ="Gift"))
    for row, raw in ((trade, "Hold Trade"), (cash, "Gift")):
        assert (row.kind, row.raw_type) == ("unrecognised", raw)
        assert row.instrument_ref is None and row.quantity is None and row.amount is None


def test_the_type_map_gives_an_unknown_type_its_meaning_ignoring_case() -> None:
    config = {"type_map": {"trade": {"hold trade": "sell"}, "cash": {"Gift": "deposit"}}}
    (sell,) = _rows(HEADER_TRADE + _trade(typ="Hold Trade"), config)
    assert (sell.kind, sell.quantity, sell.raw_type) == ("sell", Decimal(-1), None)
    (gift,) = _rows(HEADER_CASH + _cash(typ="Gift"), config)
    assert (gift.kind, gift.amount) == ("deposit", _aud(500))


def test_a_type_mapped_to_ignore_is_ignored_and_the_map_is_per_stream() -> None:
    config = {"type_map": {"cash": {"Gift": "ignore"}}}
    (gift,) = _rows(HEADER_CASH + _cash(typ="Gift"), config)
    assert (gift.kind, gift.raw_type) == ("ignored", "Gift")
    (other,) = _rows(HEADER_TRADE + _trade(typ="Gift"), config)  # same label, other Stream
    assert other.kind == "unrecognised"


def test_the_map_does_not_override_a_built_in_type() -> None:
    (row,) = _rows(
        HEADER_TRADE + _trade(typ="Buy Trade"), {"type_map": {"trade": {"Buy Trade": "sell"}}}
    )
    assert row.kind == "buy"


def test_a_unit_price_finer_than_scale_6_is_rounded_half_even_with_one_warning() -> None:
    content = HEADER_TRADE + _trade(price="2.30574350") + _trade(price="1.1234565")
    parsed = IMPORTER.parse(content.encode(), {}, NAMED)
    assert [r.unit_price for r in parsed.rows] == [Decimal("2.305744"), Decimal("1.123456")]
    assert sum("rounded" in w for w in parsed.warnings) == 1


# --- the registry --------------------------------------------------------------------------


def test_the_registry_finds_the_claimant_and_refuses_a_second_importer_under_the_same_key() -> None:
    registry.register(IMPORTER)
    registry.register(IMPORTER)  # the same object again is harmless
    assert registry.get("vanguard_pi") is IMPORTER
    assert registry.claimants(_file(CASH)) == [IMPORTER]
    assert registry.claimants(_file("sample_bank_statement.csv")) == []

    class Impostor:
        key = "vanguard_pi"
        name = "Impostor"
        config_schema: dict[str, object] = {}

        def claims(self, content: bytes, file_name: str) -> bool:
            return True

        def parse(self, content: bytes, config: object, file_name: str) -> object:
            raise NotImplementedError

    with pytest.raises(ValueError):
        registry.register(Impostor())  # type: ignore[arg-type]


# --- purity --------------------------------------------------------------------------------

#: The Importer may import only these (plus the standard library modules listed): a pure function
#: of bytes. No sqlalchemy, no core.db/models/audit, no os/pathlib/socket/urllib/httpx.
ALLOWED_FIRST_PARTY = (
    "finance360.capabilities.imports.importer",
    "finance360.core.money",
    "finance360.plugins.importers.vanguard_pi",
)
ALLOWED_STDLIB = {"__future__", "csv", "io", "re", "collections", "datetime", "decimal", "typing"}


def test_the_importer_package_imports_nothing_that_does_io() -> None:
    package = Path(vanguard_pi.__file__).parent
    seen: set[str] = set()
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                seen.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                seen.add(node.module)
    assert seen, "the walk found no imports"
    for module in seen:
        top = module.split(".")[0]
        if top == "finance360":
            assert module.startswith(ALLOWED_FIRST_PARTY), module
        else:
            assert top in ALLOWED_STDLIB, module


def test_canonical_rows_are_immutable() -> None:
    row = CanonicalRow(row_no=1, occurred_on=date(2026, 1, 1), kind="buy")
    with pytest.raises(AttributeError):
        row.kind = "sell"  # type: ignore[misc]
