"""The Importer protocol and the canonical records it returns (ADR-0013 sections 2, 5 and 5b).

An Importer is a **pure function from bytes to canonical records**: no session, no database, no
network, no filesystem, no audit, no Instrument creation. It receives its Import source ``config``
as an argument and hard-codes nothing per Account. Only the service writes. Plug-ins import this
module and register themselves from the composition root (``main.py``).

Canonical ``kind`` values are fixed by ADR-0013 section 2; adding one is an ADR matter.

Errors and warnings an Importer produces never echo the file's account reference.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from finance360.core.money import Money

STREAMS = ("trade", "cash")
TRADE_KINDS = ("buy", "sell", "opening_balance")
CASH_KINDS = (
    "deposit",
    "withdrawal",
    "trade_settlement",
    "distribution",
    "interest",
    "fee",
    "opening_balance",
)
KINDS_BY_STREAM = {"trade": TRADE_KINDS, "cash": CASH_KINDS}

#: Not Transaction kinds: a row whose raw Type the Importer does not know (``unrecognised``, which
#: blocks commit until the owner maps the Type) or whose Type the owner chose to ignore
#: (``ignored``, never committed). Both carry the raw Type in ``raw_type``.
UNRECOGNISED = "unrecognised"
IGNORED = "ignored"
PSEUDO_KINDS = (UNRECOGNISED, IGNORED)

#: Instrument kinds an Importer may hint at (the Instrument vocabulary, ADR-0013 section 2).
INSTRUMENT_KIND_HINTS = ("etf", "share", "fund")


class ImporterError(Exception):
    """The file is claimed but cannot be parsed. The message is safe to show: it names the row and
    column, never an account reference."""


@dataclass(frozen=True)
class AccountReference:
    """The account reference as written in the file, and the identifier kind it matches."""

    kind: str
    value: str
    institution: str | None = None


@dataclass(frozen=True)
class CanonicalRow:
    """One canonical record. Money is ``Money``; quantities and prices are ``Decimal``.

    ``instrument_ref`` is the raw identifier as read and ``instrument_ref_scheme`` the crosswalk
    scheme to resolve it through (``ticker`` for the file's Product ID column, ``name`` for a name
    alone).
    """

    row_no: int
    occurred_on: date
    kind: str
    instrument_ref: str | None = None
    instrument_ref_scheme: str | None = None
    #: The name as read from the file, for display and for naming a new Instrument.
    instrument_name: str | None = None
    instrument_kind_hint: str | None = None
    #: Signed: buy positive, sell negative (trade Stream).
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    unit_price_ccy: str | None = None
    fee: Money | None = None
    #: Signed cash effect (cash Stream).
    amount: Money | None = None
    description: str | None = None
    #: The Type as read, on an ``unrecognised`` or ``ignored`` row.
    raw_type: str | None = None
    #: A trade that adds or removes units with no cash settlement (a distribution reinvestment), so
    #: reconciliation expects neither a settlement nor a fee for it.
    no_settlement: bool = False

    def to_record(self) -> dict[str, Any]:
        """The JSON stored in ``staged_row.record``: money as ``{minor, ccy}``, quantities and
        prices as decimal strings, dates as ISO text. Nothing here is a float."""
        return {
            "occurred_on": self.occurred_on.isoformat(),
            "kind": self.kind,
            "instrument_ref_scheme": self.instrument_ref_scheme,
            "instrument_name": self.instrument_name,
            "instrument_kind_hint": self.instrument_kind_hint,
            "quantity": None if self.quantity is None else str(self.quantity),
            "unit_price": None if self.unit_price is None else str(self.unit_price),
            "unit_price_ccy": self.unit_price_ccy,
            "fee": _money(self.fee),
            "amount": _money(self.amount),
            "description": self.description,
            "raw_type": self.raw_type,
            "no_settlement": self.no_settlement,
        }


def _money(value: Money | None) -> dict[str, Any] | None:
    return None if value is None else {"minor": value.amount_minor, "ccy": value.currency}


@dataclass(frozen=True)
class ParsedFile:
    """What an Importer returns for one file: one Stream, its Coverage, rows in file order."""

    stream: str
    coverage_from: date
    coverage_to: date
    rows: tuple[CanonicalRow, ...]
    account_reference: AccountReference | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.stream not in STREAMS:
            raise ValueError(f"Unknown Stream: {self.stream!r}")
        if self.coverage_from > self.coverage_to:
            raise ValueError("Coverage starts after it ends")
        allowed = (*KINDS_BY_STREAM[self.stream], *PSEUDO_KINDS)
        for row in self.rows:
            if row.kind not in allowed:
                raise ValueError(f"{row.kind!r} is not a {self.stream} Stream kind")


class Importer(Protocol):
    """A deterministic plug-in that interprets one source format."""

    #: Stable key stored on the Import source (``import_source.importer_key``).
    key: str
    #: Shown to the owner.
    name: str
    #: JSON Schema the Import source ``config`` is validated against on write.
    config_schema: Mapping[str, Any]

    def claims(self, content: bytes, file_name: str) -> bool:
        """True if this Importer recognises the file. Cheap; never raises. Recognition must work
        from the content alone: ``file_name`` is a hint at most."""
        ...

    def parse(self, content: bytes, config: Mapping[str, Any], file_name: str) -> ParsedFile:
        """Parse a file this Importer claims, or raise ``ImporterError``. ``file_name`` is the
        uploaded name (possibly empty); it may carry what the content lacks, such as an account
        reference or the stated period."""
        ...
