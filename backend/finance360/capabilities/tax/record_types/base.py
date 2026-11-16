"""The shapes every Record type shares (ADR-0019 section 2). Pure."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import InputError, Total


@dataclass(frozen=True)
class Record:
    """A Tax record as the pure code sees it: the columns that matter and the parsed payload."""

    id: int
    member_id: int | None
    record_type: str
    from_on: date
    to_on: date | None
    #: What the Record type's ``parse`` returned (a frozen value).
    value: Any


@dataclass(frozen=True)
class Contribution:
    """What one record adds to one label: ``(record_id, label, value)``."""

    record_id: int
    label: str
    value: Total


@dataclass(frozen=True)
class RecordRules:
    """The Rule pack whole-number figures the Record types read, for the years the caller holds.

    Built by the service from the pack (``pack_rules.record_rules``) and passed to a Record type's
    ``contributions`` as its ``rules`` argument (ADR-0019 section 2), so a type never reads the
    pack. ``figures`` is ``(pack key, financial year, value)``; a year the pack lacks is absent.
    """

    figures: tuple[tuple[str, int, int], ...] = ()

    def figure(self, key: str, financial_year: int) -> int | None:
        for held, year, value in self.figures:
            if held == key and year == financial_year:
                return value
        return None


NO_RULES = RecordRules()


@dataclass(frozen=True)
class Operand:
    """One number in a worked step. ``value`` is an exact decimal string (never a float)."""

    value: str
    #: Shown after the value, such as ``"hours"``, ``"an hour"``, ``"days"`` or ``"%"``.
    unit: str | None = None
    #: A short caption saying what the number is.
    label: str | None = None
    #: How it joins the operand before it (``"×"``, ``"÷"`` or ``"+"``); ``None`` for the first.
    operator: str | None = None


@dataclass(frozen=True)
class Step:
    """One line of a record's working: ``operands`` combine left to right into ``result``."""

    title: str
    operands: tuple[Operand, ...]
    result: Operand
    note: str | None = None


def no_figures(record: Record, rules: RecordRules) -> tuple[str, ...]:
    return ()


def no_figures_read(record: Record) -> tuple[tuple[str, int], ...]:
    return ()


def no_steps(record: Record, rules: RecordRules) -> tuple[Step, ...]:
    return ()


@dataclass(frozen=True)
class RecordType:
    key: str
    name: str
    #: Raw payload (decoded JSON, no floats) to the frozen value. Raises ``InputError``.
    parse: Callable[[object], Any]
    #: The frozen value back to its canonical payload (money as ``{"minor", "ccy"}``).
    to_payload: Callable[[Any], dict[str, Any]]
    #: The Return label the register shows the record against (the first it feeds).
    label_of: Callable[[Any], str]
    #: The Contributions of one record to one financial year, each rounded once. A type that
    #: reads a Rule pack figure takes it from ``rules``; the others ignore both arguments.
    contributions: Callable[[Record, int, RecordRules], tuple[Contribution, ...]]
    #: The Rule pack keys this record needs for ``rules`` and does not have (so its Contribution
    #: cannot be worked out). Surfaced, never skipped silently.
    missing_figures: Callable[[Record, RecordRules], tuple[str, ...]] = no_figures
    #: The Rule pack figures the record reads, as ``(pack key, financial year)``, whether or not the
    #: pack holds them. Drives the trace on the record's page.
    figures_read: Callable[[Record], tuple[tuple[str, int], ...]] = no_figures_read
    #: The record's working as exact steps, for its page. Empty for a type with no calculation.
    calculation: Callable[[Record, RecordRules], tuple[Step, ...]] = no_steps


def check_label(label_key: str, record_type: str, kinds: tuple[str, ...]) -> labels.Label:
    """The label, if it exists, takes this Record type and has one of the value ``kinds``."""
    label = labels.get(label_key)
    if label is None:
        raise InputError(f"{label_key!r} is not a Return label.")
    if record_type not in label.record_types or label.kind not in kinds:
        raise InputError(f"'{label.name}' does not take a {record_type} record.")
    return label
