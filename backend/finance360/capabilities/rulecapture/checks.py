"""The deterministic checks on an AI-proposed Rule figure (ADR-0022 section 5). Pure.

A proposal is *data to validate* (AGENTS.md rule 4). Before it can be shown as a candidate it must:

1. parse to the spec's kind and unit and sit in the spec's plausible range: the **same parser as the
   typed field** (``core.figure_capture.parse_figure``), so one validator serves both;
2. carry a **quote** that appears in the pasted text, after whitespace, case and typographic-quote
   normalisation;
3. have its **number in that quote** (``number_in_quote``).

``confidence`` is display only: nothing here reads it.

The number match is deliberately simple and documented. Numbers are read from the quote as exact
``Decimal``s (never a float): digits with optional thousands commas and decimals, so ``$1,234`` is
1234 and ``0.88`` is 0.88. A money figure matches a token equal to its dollars (``$0.88``,
``0.880``), or a token equal to its cents when the token is followed by ``c``, ``cent`` or ``cents``
(``88 cents``, ``88c``). A rate matches a token equal to its percent (``16.5%`` and ``16.50 per
cent``). A whole number matches a token equal to it. Brackets need every non-zero threshold and
every non-zero rate to appear (a zero is usually written ``nil``, ``$0`` or left implicit).
Spelled-out numbers (``twelve``) and abbreviations (``2k``) do not match: such a proposal is shown
as "not found; enter it", which is the safe failure.
"""

from __future__ import annotations

import re
from decimal import Decimal

from finance360.core.money import Money
from finance360.core.rate import Rate
from finance360.core.rulepack import Value

_TYPOGRAPHIC = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_NUMBER = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s?(?:cents?|c)\b)?",
    re.IGNORECASE,
)
_CENTS_PER_DOLLAR = Decimal(100)
_PPM_PER_PERCENT = 10_000


def normalise(text: str) -> str:
    """Whitespace collapsed, case folded, typographic quotes and dashes made plain."""
    return " ".join(text.translate(_TYPOGRAPHIC).split()).casefold()


def quote_in_text(quote: str, page_text: str) -> bool:
    wanted = normalise(quote)
    return bool(wanted) and wanted in normalise(page_text)


def _numbers(text: str) -> tuple[set[Decimal], set[Decimal]]:
    """The numbers written in ``text``, and those of them followed by a cents word."""
    plain: set[Decimal] = set()
    cents: set[Decimal] = set()
    for match in _NUMBER.finditer(text):
        number = Decimal(match.group(1).replace(",", ""))
        plain.add(number)
        if match.group(2):
            cents.add(number)
    return plain, cents


def _dollars(money: Money) -> Decimal:
    return Decimal(money.amount_minor) / _CENTS_PER_DOLLAR


def _percent(rate: Rate) -> Decimal:
    return Decimal(rate.ppm) / _PPM_PER_PERCENT


def number_in_quote(value: Value, quote: str) -> bool:
    """Whether the parsed ``value`` is written in ``quote`` (formats: see the module note)."""
    plain, cents = _numbers(quote)

    def money(amount: Money) -> bool:
        dollars = _dollars(amount)
        return dollars in plain or dollars * _CENTS_PER_DOLLAR in cents

    if isinstance(value, Money):
        return money(value)
    if isinstance(value, Rate):
        return _percent(value) in plain
    if isinstance(value, tuple):
        return all(
            (b.threshold.amount_minor == 0 or money(b.threshold))
            and (b.rate.ppm == 0 or _percent(b.rate) in plain)
            for b in value
        )
    return Decimal(value) in plain
