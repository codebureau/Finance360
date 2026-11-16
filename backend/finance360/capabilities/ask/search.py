"""Keyword search over Passages: SQLite FTS5 with BM25 (ADR-0023 section 2). No AI, no gateway.

The index is a private in-memory ``sqlite3`` connection, built from the corpora on first use and
rebuilt when their digest changes. It is derived data, so it is never in the data directory: no
migration, no Backup content, and a new release's Guide is live at once.

The query is the question's alphanumeric words, a short stop-word list removed, each quoted, joined
with OR. Words the household uses for a term CONTEXT.md names differently (its ``_Avoid_`` and
``_Also_`` words) add the term to the query; the user's own word is never replaced, so ranking
decides. ``bm25`` scores are floats because they rank text; they are never money and never stored.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

from finance360.capabilities.ask.corpus import Corpus, Passage
from finance360.capabilities.ask.synonyms import Expansions, expansions_from

#: Column weights, title then heading then body; tuned by ``test_ask_findability``.
WEIGHTS = (4.0, 3.0, 1.0)
STOP_WORDS = frozenset(
    "a an and are as at be but by can could do does for from how i if in is it its me my of on "
    "or our should so that the their there this to us was we what when where which who why will "
    "with would you your".split()
)
_WORD = re.compile(r"[a-z0-9]+")


class Fts5Missing(RuntimeError):
    """SQLite was built without FTS5, which Ask's search needs."""


class Index:
    """The Passages of some corpora, searchable. Not thread-safe by itself: see ``_LOCK``."""

    def __init__(self, passages: Iterable[Passage]) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE passage USING fts5(title, heading, body, "
                "tokenize = 'porter unicode61')"
            )
        except sqlite3.OperationalError as problem:
            raise Fts5Missing(
                "This Python's SQLite has no FTS5 module, which Ask's search needs "
                f"(SQLite {sqlite3.sqlite_version}). Use a Python build that includes it."
            ) from problem
        self._passages: list[Passage] = []
        for p in passages:
            self._passages.append(p)
            self._conn.execute(
                "INSERT INTO passage (rowid, title, heading, body) VALUES (?, ?, ?, ?)",
                (len(self._passages), p.page_title, p.heading, p.text),
            )

    def __len__(self) -> int:
        return len(self._passages)

    def search(self, match: str, limit: int) -> list[Passage]:
        if not match:
            return []
        rows = self._conn.execute(
            "SELECT rowid FROM passage WHERE passage MATCH ? "
            "ORDER BY bm25(passage, ?, ?, ?), rowid LIMIT ?",
            (match, *WEIGHTS, limit),
        ).fetchall()
        return [self._passages[row[0] - 1] for row in rows]


_LOCK = threading.Lock()
_CACHE: dict[str, Index] = {}


def _digest(sources: Sequence[Corpus]) -> str:
    return "|".join(f"{c.name}:{c.digest()}" for c in sources)


def index_for(sources: Sequence[Corpus]) -> Index:
    """The index of ``sources``, built now if their content changed since the last call."""
    key = _digest(sources)
    with _LOCK:
        index = _CACHE.get(key)
        if index is None:
            _CACHE.clear()  # one live index: the corpus is small and a new release replaces it
            index = Index(p for c in sources for p in c.passages())
            _CACHE[key] = index
        return index


def words_of(question: str) -> list[str]:
    return [w for w in _WORD.findall(question.lower()) if w not in STOP_WORDS]


def build_match(question: str, expansions: Expansions | None = None) -> str:
    """The FTS5 query for ``question``: its words, plus the terms its words stand for. Empty when
    the question has no searchable word."""
    words = words_of(question)
    if not words:
        return ""
    parts = [f'"{w}"' for w in dict.fromkeys(words)]
    if expansions is not None:
        for term in expansions.terms_for(_WORD.findall(question.lower())):
            phrase = " ".join(normalise_phrase(term))
            if phrase and f'"{phrase}"' not in parts:
                parts.append(f'"{phrase}"')
    return " OR ".join(parts)


def normalise_phrase(term: str) -> list[str]:
    return _WORD.findall(term.lower())


def read_expansions(context_file: Path | None) -> Expansions | None:
    """The vocabulary expansions from CONTEXT.md; ``None`` (expansion off) if it is absent."""
    if context_file is None or not context_file.is_file():
        return None
    return expansions_from(context_file.read_text(encoding="utf-8"))


def search(
    sources: Sequence[Corpus],
    question: str,
    limit: int,
    context_file: Path | None = None,
) -> list[Passage]:
    """The best ``limit`` Passages for ``question`` over ``sources``; empty when nothing matches."""
    match = build_match(question, read_expansions(context_file))
    if not match:
        return []
    index = index_for(sources)
    with _LOCK:
        return index.search(match, limit)
