"""What counts as a figure in free text: a currency amount or a run of six or more digits.

One definition, shared (ADR-0021 section 3, ADR-0032 section 6): the AI gateway's redaction
replaces such text before it leaves the instance, and an Answer rating's note is refused if it
holds any. Pure: no database and no Capability, so ``capabilities.ai`` and
``capabilities.questions`` can both use it without depending on each other.
"""

from __future__ import annotations

import re

_CURRENCY = r"(?:A\$|US\$|NZ\$|\$|AUD|USD|NZD)"
#: A currency amount: ``$1,234``, ``AUD 500``, ``1,200 dollars``, ``2k``.
AMOUNT = (
    rf"{_CURRENCY}\s?\d+(?:,\d{{3}})*(?:\.\d+)?(?:\s?[km]\b)?"
    r"|\d+(?:,\d{3})*(?:\.\d+)?\s?(?:AUD|USD|NZD|dollars?)\b"
    r"|\b\d+(?:\.\d+)?[km]\b"
)
#: Six or more digits, in one run or split by single spaces or dashes (an account number).
DIGITS = r"\d(?:[ -]?\d){5,}"
#: A date (2026-11-06) or financial year (2026-27) is not a number worth hiding.
DATE_LIKE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")

_AMOUNT = re.compile(AMOUNT, re.IGNORECASE)
_DIGITS = re.compile(DIGITS)


def has_figure(text: str) -> bool:
    """Whether ``text`` carries a currency amount or a run of six or more digits."""
    if _AMOUNT.search(text):
        return True
    return any(not DATE_LIKE.fullmatch(m.group(0)) for m in _DIGITS.finditer(text))
