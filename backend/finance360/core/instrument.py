"""Instrument vocabulary and identifier normalisation (ADR-0013 section 2). Pure: no I/O.

An Instrument is identified from outside by one or more identifiers (the crosswalk). Two spellings
of the same identifier must collide, so uniqueness is on the *normalised* value: case-folded, with
runs of whitespace collapsed to one space and the ends trimmed.
"""

from __future__ import annotations

import re

KINDS = ("etf", "share", "fund")
SCHEMES = ("ticker", "yahoo", "isin", "vendor_id", "name")

IDENTIFIER_MAX = 200
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


def tidy_identifier(value: str) -> str:
    """The identifier as stored for display: trimmed, inner whitespace runs collapsed."""
    return " ".join(value.split())


def normalise_identifier(value: str) -> str:
    """The key uniqueness and lookup use: ``tidy_identifier`` then case-folded."""
    return tidy_identifier(value).casefold()


def is_isin_shaped(value: str) -> bool:
    """Two letters, nine letters or digits, one check digit (shape only; no checksum)."""
    return _ISIN.fullmatch(normalise_identifier(value).upper()) is not None
