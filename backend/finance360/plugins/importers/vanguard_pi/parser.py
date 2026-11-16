"""Vanguard Personal Investor export parsing: pure bytes -> ``ParsedFile`` (ADR-0013 section 5).

No session, database, network, filesystem or audit; no Instrument creation. The layouts were
checked against real exports kept out of the repo (``private/samples/vanguard``, exercised by
``test_importer_real_samples.py``); the fixtures under ``fixtures/sample_household/imports`` are
invented values in the same layouts.

Layouts (recognised from the header row alone):

* trade: ``Account number, Investment, Product ID, Product Type, Trade Date, Type, Unit Price,
  Quantity, Value, Brokerage`` (the account number is on every row);
* cash: ``Date, Type, Product Type, Product Name, Product ID, Units, Total`` (no account number).

The file name, ``<cash|investment>_transactions_acc<number>_<dd-MMM-yyyy>_<dd-MMM-yyyy>.csv``,
supplies the cash file's account number and both files' stated period. Recognition never needs it.

Format quirks handled:

* ``dd-MMM-yyyy`` dates, files in descending order (rows keep file order; Coverage uses min/max);
* the trade Stream is unsigned (the Type column says buy or sell), the cash Stream is signed;
* a buy's cash row excludes the brokerage fee, which has its own cash "Fees and Charges" row, so
  the trade row's ``fee`` is informational and only the cash fee row moves cash;
* a blank Brokerage means zero;
* distributions carry the product *name* but no Product ID, so their reference is a ``name`` alias;
* deposits, interest and fees carry the Product ID ``CASH`` and no Instrument; their Product Name
  becomes the description;
* the cash ``Units`` column is not needed and is ignored;
* the trade Product Type becomes an Instrument kind hint;
* ``DRP`` (a distribution reinvestment) is a buy flagged ``no_settlement``;
* a Type the Importer does not know is not an error: the row is ``unrecognised`` with its raw
  Type, and the owner's ``type_map`` config (per Stream) says what it means, or ``ignore``.

Errors name a row and column, never the account reference.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, cast

from finance360.capabilities.imports.importer import (
    IGNORED,
    UNRECOGNISED,
    AccountReference,
    CanonicalRow,
    ImporterError,
    ParsedFile,
)
from finance360.core.money import Money, MoneyError, parse_amount

KEY = "vanguard_pi"
NAME = "Vanguard Personal Investor"
CURRENCY = "AUD"
#: The identifier kind (and institution) the file's account number is matched against.
REFERENCE_KIND = "vendor_account_id"

#: What the owner may map a Type this Importer does not know to, per Stream (``ignore`` rows are
#: never committed). ``trade_settlement`` is not offered: cash ``Buy``/``Sell`` are built in.
TRADE_CHOICES = ("buy", "sell", "ignore")
CASH_CHOICES = ("deposit", "withdrawal", "distribution", "interest", "fee", "ignore")

#: ``type_map``: per Stream, a raw Type (matched ignoring case) to what it means. Only Types the
#: Importer does not already know are consulted; nothing is guessed.
CONFIG_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "type_map": {
            "type": "object",
            "properties": {
                "trade": {
                    "type": "object",
                    "additionalProperties": {"enum": list(TRADE_CHOICES)},
                },
                "cash": {
                    "type": "object",
                    "additionalProperties": {"enum": list(CASH_CHOICES)},
                },
            },
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}

TRADE_COLUMNS = (
    "account number",
    "investment",
    "product id",
    "product type",
    "trade date",
    "type",
    "unit price",
    "quantity",
    "brokerage",
)
CASH_COLUMNS = ("date", "type", "product type", "product name", "product id", "total")

_FILE_NAME = re.compile(
    r"(?:cash|investment)_transactions_acc(?P<acc>.+)_"
    r"(?P<start>\d{1,2}-[A-Za-z]{3}-\d{4})_(?P<end>\d{1,2}-[A-Za-z]{3}-\d{4})"
    r"(?: \(\d+\))?\.csv",  # the optional " (1)" is a browser's mark on a repeat download
    re.IGNORECASE,
)
#: The Product ID of a cash row that names no Instrument (deposit, interest, fee).
_CASH_PRODUCT_ID = "cash"

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_DATE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})")
_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")
_ROUNDED_PRICE = "Unit Prices with more than 6 decimal places were rounded to 6."
_SCALE = 6  # quantities and unit prices are held at scale 6 (ADR-0013 section 2)

_TRADE_TYPES = {"buy trade": "buy", "sell trade": "sell", "drp": "buy"}
#: A distribution reinvestment adds units and has no cash settlement.
_NO_SETTLEMENT = ("drp",)
_CASH_TYPES = {
    "deposit": "deposit",
    "withdrawal": "withdrawal",
    "buy": "trade_settlement",
    "sell": "trade_settlement",
    "distribution": "distribution",
    "interest": "interest",
    "fees and charges": "fee",
}
_PRODUCT_TYPES = {"etf": "etf", "share": "share", "fund": "fund", "managed fund": "fund"}
#: Cash kinds that name an Instrument.
_NEEDS_PRODUCT = ("trade_settlement", "distribution")


def _decode(content: bytes) -> str | None:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _cells(text: str) -> list[list[str]]:
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text)) if any(row)]


class _Layout:
    def __init__(self, stream: str, columns: dict[str, int]) -> None:
        self.stream = stream
        self.columns = columns


def _layout(rows: list[list[str]]) -> _Layout | None:
    """Which Stream the header row (the first row) is, from its column names alone."""
    if not rows:
        return None
    names = [c.casefold() for c in rows[0]]
    for stream, wanted in (("trade", TRADE_COLUMNS), ("cash", CASH_COLUMNS)):
        if set(wanted) <= set(names):
            return _Layout(stream, {n: i for i, n in enumerate(names)})
    return None


def claims(content: bytes, file_name: str) -> bool:
    """Recognised from the header alone; the file name is not needed."""
    del file_name
    text = _decode(content)
    if text is None:
        return False
    try:
        return _layout(_cells(text)) is not None
    except csv.Error:
        return False


def _parse_date(text: str, row_no: int, column: str) -> date:
    m = _DATE.fullmatch(text.strip())
    month = _MONTHS.get(m.group(2).casefold()) if m else None
    if not m or month is None:
        raise ImporterError(f"Row {row_no}: {column} is not a dd-MMM-yyyy date.")
    try:
        return date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        raise ImporterError(f"Row {row_no}: {column} is not a real date.") from None


def _parse_scaled(text: str, row_no: int, column: str, rounded: list[int] | None = None) -> Decimal:
    """A positive decimal at scale 6. Given ``rounded``, finer precision (a managed fund's unit
    price) is rounded half-even to scale 6 and the row number noted; otherwise it is an error."""
    clean = text.strip().replace(",", "")
    if not _DECIMAL.fullmatch(clean):
        raise ImporterError(f"Row {row_no}: {column} is not a positive number.")
    value = Decimal(clean)
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > _SCALE:
        if rounded is not None:
            rounded.append(row_no)
            return value.quantize(Decimal(1).scaleb(-_SCALE), rounding=ROUND_HALF_EVEN)
        raise ImporterError(f"Row {row_no}: {column} has more than {_SCALE} decimal places.")
    return value


def _parse_money(text: str, row_no: int, column: str) -> Money:
    try:
        return parse_amount(text, CURRENCY)
    except MoneyError:
        raise ImporterError(f"Row {row_no}: {column} is not an amount.") from None


def _from_file_name(file_name: str) -> tuple[str | None, tuple[date, date] | None]:
    """The account number and stated period the file name carries, if it follows the pattern."""
    m = _FILE_NAME.fullmatch(file_name.replace("\\", "/").rsplit("/", 1)[-1])
    if m is None:
        return None, None
    try:
        start = _parse_date(m.group("start"), 0, "period")
        end = _parse_date(m.group("end"), 0, "period")
    except ImporterError:
        raise ImporterError("The period in the file name is not two dd-MMM-yyyy dates.") from None
    if start > end:
        raise ImporterError("The period in the file name ends before it starts.")
    return m.group("acc"), (start, end)


def _cell(row: list[str], layout: _Layout, column: str) -> str:
    at = layout.columns[column]
    return row[at] if at < len(row) else ""


def _kind_of(
    stream: str, raw_type: str, known: Mapping[str, str], config: Mapping[str, Any]
) -> str | None:
    """What a raw Type means: a built-in, else the owner's ``type_map`` entry (``ignored`` for
    ``ignore``), else ``None`` (not yet known)."""
    built_in = known.get(raw_type.casefold())
    if built_in is not None:
        return built_in
    type_map = cast("dict[str, Any]", config.get("type_map") or {})
    mapped = cast("dict[str, Any]", type_map.get(stream) or {})
    for name, meaning in mapped.items():
        if name.casefold() == raw_type.casefold():
            return IGNORED if meaning == "ignore" else str(meaning)
    return None


def _placeholder(
    row: list[str], layout: _Layout, row_no: int, date_column: str, kind: str
) -> CanonicalRow:
    """An unrecognised or ignored row: only its date and raw Type are read."""
    return CanonicalRow(
        row_no=row_no,
        occurred_on=_parse_date(_cell(row, layout, date_column), row_no, date_column.title()),
        kind=kind,
        raw_type=_cell(row, layout, "type"),
    )


def _trade_row(
    row: list[str], layout: _Layout, row_no: int, warnings: list[str], config: Mapping[str, Any]
) -> CanonicalRow:
    def cell(column: str) -> str:
        return _cell(row, layout, column)

    kind = _kind_of("trade", cell("type"), _TRADE_TYPES, config)
    if kind is None:
        return _placeholder(row, layout, row_no, "trade date", UNRECOGNISED)
    if kind == IGNORED:
        return _placeholder(row, layout, row_no, "trade date", IGNORED)
    units = _parse_scaled(cell("quantity"), row_no, "Quantity")  # unsigned in the file
    if units == 0:
        raise ImporterError(f"Row {row_no}: Quantity must not be zero.")
    finer: list[int] = []
    price = _parse_scaled(cell("unit price"), row_no, "Unit Price", finer)
    if finer and _ROUNDED_PRICE not in warnings:
        warnings.append(_ROUNDED_PRICE)
    brokerage = cell("brokerage")
    fee = _parse_money(brokerage, row_no, "Brokerage") if brokerage else Money.zero(CURRENCY)
    if fee.amount_minor < 0:
        raise ImporterError(f"Row {row_no}: Brokerage must not be negative.")
    product_id, product_name = cell("product id"), cell("investment")
    if product_id:
        ref, scheme = product_id, "ticker"
    elif product_name:
        ref, scheme = product_name, "name"
    else:
        raise ImporterError(f"Row {row_no}: the trade has neither a Product ID nor an Investment.")
    hint = _PRODUCT_TYPES.get(cell("product type").casefold())
    if hint is None:
        warnings.append(
            f"Row {row_no}: the Product Type is not recognised, so no kind is suggested."
        )
    return CanonicalRow(
        row_no=row_no,
        occurred_on=_parse_date(cell("trade date"), row_no, "Trade Date"),
        kind=kind,
        instrument_ref=ref,
        instrument_ref_scheme=scheme,
        instrument_name=product_name or None,
        instrument_kind_hint=hint,
        quantity=units if kind == "buy" else -units,
        unit_price=price,
        unit_price_ccy=CURRENCY,
        fee=fee,
        no_settlement=cell("type").casefold() in _NO_SETTLEMENT,
    )


def _cash_row(
    row: list[str], layout: _Layout, row_no: int, warnings: list[str], config: Mapping[str, Any]
) -> CanonicalRow:
    def cell(column: str) -> str:
        return _cell(row, layout, column)

    kind = _kind_of("cash", cell("type"), _CASH_TYPES, config)
    if kind is None:
        return _placeholder(row, layout, row_no, "date", UNRECOGNISED)
    if kind == IGNORED:
        return _placeholder(row, layout, row_no, "date", IGNORED)
    amount = _parse_money(cell("total"), row_no, "Total")  # signed in the file
    product_id, product_name = cell("product id"), cell("product name")
    ref = scheme = name = description = None
    if kind in _NEEDS_PRODUCT:
        name = product_name or None
        if product_id and product_id.casefold() != _CASH_PRODUCT_ID:
            ref, scheme = product_id, "ticker"
        elif product_name:  # distributions: a name and no Product ID
            ref, scheme = product_name, "name"
        else:
            warnings.append(
                f"Row {row_no}: a {kind.replace('_', ' ')} has neither a Product ID nor a "
                "Product Name."
            )
    else:  # deposit, withdrawal, interest, fee: the Product Name says what it was
        description = product_name or None
    return CanonicalRow(
        row_no=row_no,
        occurred_on=_parse_date(cell("date"), row_no, "Date"),
        kind=kind,
        instrument_ref=ref,
        instrument_ref_scheme=scheme,
        instrument_name=name,
        amount=amount,
        description=description,
    )


def parse(content: bytes, config: Mapping[str, Any], file_name: str) -> ParsedFile:
    """Parse a Vanguard PI trade or cash export. Raises ``ImporterError`` with a safe message."""
    text = _decode(content)
    if text is None:
        raise ImporterError("The file is not text.")
    try:
        rows = _cells(text)
    except csv.Error:
        raise ImporterError("The file is not readable as CSV.") from None
    layout = _layout(rows)
    if layout is None:
        raise ImporterError("The file is not a Vanguard Personal Investor export.")
    reference, period = _from_file_name(file_name)
    body = rows[1:]
    if layout.stream == "trade":  # the account number is on every trade row
        numbers = {_cell(r, layout, "account number") for r in body} - {""}
        if len(numbers) > 1:
            raise ImporterError("The file holds more than one account, which is not supported.")
        reference = numbers.pop() if numbers else reference
    warnings: list[str] = []
    build = _trade_row if layout.stream == "trade" else _cash_row
    parsed = tuple(build(row, layout, n, warnings, config) for n, row in enumerate(body, 1))
    if period is not None:
        start, end = period
        outside = [r.row_no for r in parsed if not start <= r.occurred_on <= end]
        if outside:
            shown = ", ".join(str(n) for n in outside[:5])
            warnings.append(f"Rows {shown} fall outside the period in the file name.")
    elif parsed:
        start = min(r.occurred_on for r in parsed)
        end = max(r.occurred_on for r in parsed)
        warnings.append(
            "The file name states no period, so Coverage runs from its first to last row."
        )
    else:
        raise ImporterError("The file has no rows and its name states no period.")
    if reference is None:
        warnings.append("The file carries no account number, so the Account must be chosen.")
    return ParsedFile(
        stream=layout.stream,
        coverage_from=start,
        coverage_to=end,
        rows=parsed,
        account_reference=(
            AccountReference(REFERENCE_KIND, reference, "Vanguard PI") if reference else None
        ),
        warnings=tuple(warnings),
    )
