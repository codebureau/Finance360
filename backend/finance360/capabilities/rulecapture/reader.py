"""The page reader: finds Rule figures on a pasted official page by matching words (ADR-0023 s.6).

Pure and deterministic; **not an AI call**: no gateway, no log row, no cap, and the pasted
text never leaves the instance. For each figure asked for it finds the sentence of the page
that mentions most of the figure's label and reads the number out of it. It returns proposals in
the shape a model's reply has (``key, fy, value, quote, provisional, note``) with no
``confidence``, and they go through the same ``service._check`` as a model's: kind and unit,
plausible range, the quote must appear in the page, the number must appear in the quote. It
proposes whatever the sentence says, however absurd, so those checks do the rejecting. Bands are
not read; a figure it cannot match is "Not found; enter it".

A sentence answers one figure only, and the reader would rather say "not found" than guess: a
sentence must mention at least three quarters of a figure's label words (and two of them).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal
from fractions import Fraction

from finance360.core.rulepack import FigureSpec

#: How much of a figure's label a sentence must mention. High enough that a sentence about one
#: figure does not answer a neighbour that shares most of its wording.
MIN_COVERAGE = Fraction(3, 4)
MIN_WORDS = 2
NOTE = "Matched by words on the page."
PROVISIONAL_NOTE = "The page says this is announced, not yet law."

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z]{3,}")
_STOP = frozenset({"the", "and", "for", "per", "not", "than", "that", "with"})
_YEAR = re.compile(r"\b20\d\d(?:-\d\d)?\b")
_DOLLARS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
_CENTS = re.compile(r"(\d+(?:\.\d+)?)\s?(?:cents?|c)\b", re.IGNORECASE)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|per\s?cent)", re.IGNORECASE)
_WHOLE = re.compile(r"\b\d[\d,]*\b")
_PROVISIONAL = re.compile(r"proposed|announced|not yet law|subject to legislation", re.IGNORECASE)


def _stems(text: str) -> frozenset[str]:
    """The words of ``text`` that identify a figure: three letters or more, a trailing s ignored."""
    words = set(_WORD.findall(text.lower())) - _STOP
    return frozenset(w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words)


def _value(kind: str, sentence: str) -> str | None:
    """The number the sentence states, in the typed field's grammar; ``None`` if it has none."""
    text = _YEAR.sub(" ", sentence)
    if kind == "money":
        if dollars := _DOLLARS.search(text):
            return dollars.group(1).replace(",", "")
        if cents := _CENTS.search(text):
            return f"{Decimal(cents.group(1)) / 100:f}"
        return None
    if kind == "rate":
        percent = _PERCENT.search(text)
        return percent.group(1) if percent else None
    if kind == "int":
        whole = _WHOLE.search(text)
        return whole.group(0).replace(",", "") if whole else None
    return None  # bands are not read


def read(specs: Sequence[FigureSpec], fy: int, page_text: str) -> list[dict[str, object]]:
    """A proposal for each figure the page states, in the order asked; the rest are absent."""
    sentences = [s.strip() for s in _SENTENCE_END.split(page_text) if s.strip()]
    labels = [_stems(spec.label) for spec in specs]
    pairs: list[tuple[Fraction, int, int, int]] = []
    for i, words in enumerate(labels):
        if not words:
            continue
        for j, sentence in enumerate(sentences):
            hits = len(words & _stems(sentence))
            if hits >= MIN_WORDS and Fraction(hits, len(words)) >= MIN_COVERAGE:
                pairs.append((Fraction(hits, len(words)), hits, i, j))
    # Best match first; a sentence answers one figure only.
    pairs.sort(key=lambda p: (-p[0], -p[1], p[2], p[3]))
    taken_specs: set[int] = set()
    taken_sentences: set[int] = set()
    found: dict[int, dict[str, object]] = {}
    for _, _, i, j in pairs:
        if i in taken_specs or j in taken_sentences:
            continue
        value = _value(specs[i].kind, sentences[j])
        if value is None:
            continue
        taken_specs.add(i)
        taken_sentences.add(j)
        provisional = bool(_PROVISIONAL.search(sentences[j]))
        found[i] = {
            "key": specs[i].key,
            "fy": fy,
            "value": value,
            "quote": sentences[j],
            "provisional": provisional,
            "note": f"{NOTE} {PROVISIONAL_NOTE}" if provisional else NOTE,
        }
    return [found[i] for i in sorted(found)]
