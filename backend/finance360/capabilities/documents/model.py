"""The Document: the one shape every loader produces and the matcher reads (ADR-0024 section 2).

**Geometry is floating point, and that is allowed.** AGENTS.md rule 3 forbids floats for money,
quantities and prices. A coordinate on a page is none of those and never becomes a figure: every
value read from a Document is text until ``checks`` parses it into exact ``Money``. Do not "fix"
the coordinates into integers.

A Document keeps no file name and no bytes. Nothing in it assumes a text layer: a later OCR loader
produces the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    """One word and its box. The origin is the top-left of the page; ``top`` grows downwards."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(frozen=True)
class Page:
    number: int  # 1-based
    width: float
    height: float
    words: tuple[Word, ...]


@dataclass(frozen=True)
class Document:
    pages: tuple[Page, ...]


@dataclass(frozen=True)
class Line:
    """Words that share a row, left to right. Derived from a Page; never stored."""

    page: int
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def lines_of(page: Page) -> tuple[Line, ...]:
    """The page's Lines in reading order (top, then left).

    A word joins the current Line when its ``top`` is within half its own height of the Line's
    first word, which scales with the page (a 10 point font, or one text row of height 1).
    """
    ordered = sorted(page.words, key=lambda w: (w.top, w.x0))
    rows: list[list[Word]] = []
    for word in ordered:
        if rows:
            first = rows[-1][0]
            tolerance = max(word.bottom - word.top, first.bottom - first.top) / 2
            if abs(word.top - first.top) <= tolerance:
                rows[-1].append(word)
                continue
        rows.append([word])
    return tuple(Line(page.number, tuple(sorted(r, key=lambda w: w.x0))) for r in rows)


def all_lines(document: Document) -> tuple[Line, ...]:
    """Every Line of every page, in reading order."""
    return tuple(line for page in document.pages for line in lines_of(page))
