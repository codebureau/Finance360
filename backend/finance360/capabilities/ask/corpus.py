"""Corpora and Passages: what Ask searches (ADR-0023 section 2).

A **Passage** is one ``##`` section of a Guide page (a section over ``SPLIT_AT`` characters is
split at paragraph breaks), carrying its corpus, page, heading, text and the in-app link. A
**Corpus** yields Passages and a digest that changes when its content does. The Guide is the only
corpus built; a new source (a Capability's help, notes) registers one more factory in
``FACTORIES``.

A corpus that is not ``public`` is searchable at Search only, but its Passages may not be sent to a
model until an ADR gives them a data class: ``answering_passages`` refuses them.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from finance360.capabilities.ask.guide import Guide, GuidePage, load

#: A section longer than this is split at paragraph breaks.
SPLIT_AT = 1500
EXCERPT_CHARS = 220

_HEADING = re.compile(r"^## +(.+?)\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKUP = re.compile(r"[*_`#>]")


@dataclass(frozen=True)
class Passage:
    corpus: str
    #: The page slug (the Guide's, for the Guide corpus).
    page: str
    page_title: str
    #: The ``##`` heading; empty for the opening of a page, before its first ``##``.
    heading: str
    text: str
    #: The in-app path that opens the page.
    link: str

    @property
    def shown_heading(self) -> str:
        return self.heading or self.page_title


class Corpus(Protocol):
    name: str
    #: May its Passages be sent to a model? Only a public corpus may.
    public: bool

    def passages(self) -> Iterable[Passage]: ...

    def digest(self) -> str:
        """Changes when, and only when, the content changes."""
        ...


def plain(markdown: str) -> str:
    """Markdown as one line of plain words (links keep their text)."""
    text = _LINK.sub(r"\1", markdown)
    return " ".join(_MARKUP.sub("", text).split())


def excerpt(passage: Passage, limit: int = EXCERPT_CHARS) -> str:
    """The start of the passage as plain text, cut at a word."""
    text = plain(passage.text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _split(body: str) -> list[str]:
    """A section's text in chunks of about ``SPLIT_AT`` characters, cut between paragraphs."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in re.split(r"\n\s*\n", body.strip()):
        if current and size + len(paragraph) > SPLIT_AT:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def page_passages(corpus: str, page: GuidePage) -> list[Passage]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in page.body.split("\n"):
        match = _HEADING.match(line)
        if match:
            sections.append((match.group(1), []))
        else:
            sections[-1][1].append(line)
    found: list[Passage] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        for chunk in _split(body):
            found.append(Passage(corpus, page.slug, page.title, heading, chunk, page.path))
    return found


class GuideCorpus:
    """The Guide: the Markdown pages in ``frontend/src/guide/`` (ADR-0021 section 2)."""

    name = "guide"
    public = True

    def __init__(self, guide_dir: Path) -> None:
        self._dir = guide_dir

    def guide(self) -> Guide:
        return load(self._dir)

    def passages(self) -> Iterable[Passage]:
        for page in self.guide().pages:
            yield from page_passages(self.name, page)

    def digest(self) -> str:
        sha = hashlib.sha256()
        for file in sorted(self._dir.glob("*.md")):
            sha.update(file.name.encode("utf-8"))
            sha.update(file.read_bytes())
        return sha.hexdigest()


#: How each corpus is built from the instance's settings. The Guide is the only one so far.
FACTORIES: list[Callable[[Path], Corpus]] = [GuideCorpus]


def corpora(guide_dir: Path) -> list[Corpus]:
    return [make(guide_dir) for make in FACTORIES]


def answering_passages(passages: Iterable[Passage], by_name: dict[str, Corpus]) -> list[Passage]:
    """Only the Passages a model may be sent: those of a public corpus."""
    return [p for p in passages if (c := by_name.get(p.corpus)) is not None and c.public]
