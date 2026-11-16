"""How labels and document text are compared (ADR-0024 section 3).

Comparison is exact on the *normalised* text: case, whitespace, punctuation and non-breaking
spaces do not matter, and nothing else does. Not fuzzy.
"""

from __future__ import annotations

import re
import unicodedata

_NOT_ALNUM = re.compile(r"[^0-9a-z]+")


def tokens(text: str) -> list[str]:
    """The normalised words of ``text``: lower case, letters and digits only."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return [t for t in _NOT_ALNUM.split(folded) if t]


def normalise(text: str) -> str:
    return " ".join(tokens(text))
