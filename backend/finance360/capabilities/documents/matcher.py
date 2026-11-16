"""Match: a Document profile picks the figures out of a Document (ADR-0024 section 3).

Pure: a Document and a profile in, raw matches out. No database, no filesystem, no network, no AI.
The matcher and its vocabulary are closed: exact matching on the normalised label (a label is the
whole Line or the start of it), the ``same_row`` and ``next_line`` pairings, nothing fuzzy and no
patterns. **A label that matches more than once in its scope is ambiguous**: it is not filled.

What it returns is the raw text beside each label. Only ``checks`` turns text into a figure.
"""

from __future__ import annotations

from dataclasses import dataclass

from finance360.capabilities.documents import reading as r
from finance360.capabilities.documents.model import Document, Line, all_lines
from finance360.capabilities.documents.profile import FieldSpec, Profile, Section
from finance360.capabilities.documents.text import normalise, tokens

#: A word that is only the start of an amount, which the next word completes.
_PREFIXES = frozenset({"$", "-", "(", "($", "-$", "$-"})


@dataclass(frozen=True)
class Hit:
    text: str  # the raw text beside the label
    page: int
    label: str  # the profile's label variant that matched
    #: For a field that ``repeats``: the raw text and page of every row, in reading order.
    rows: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class Miss:
    reason: str  # one of the reading.py reason codes


@dataclass(frozen=True)
class Matches:
    hits: dict[str, Hit]
    misses: dict[str, Miss]


def claims(document: Document, profile: Profile) -> bool:
    """True when every ``claim`` phrase appears in the document (normalised, in order)."""
    words = " ".join(normalise(w.text) for line in all_lines(document) for w in line.words)
    haystack = f" {' '.join(words.split())} "
    return all(f" {normalise(phrase)} " in haystack for phrase in profile.claim)


def _label_end(line: Line, label: str) -> int | None:
    """If the Line starts with ``label``, the index of the word that completes it, else ``None``."""
    wanted = tokens(label)
    seen: list[str] = []
    for index, word in enumerate(line.words):
        seen.extend(tokens(word.text))
        if len(seen) >= len(wanted):
            return index if seen[: len(wanted)] == wanted else None
    return None


def _find(lines: list[Line], labels: tuple[str, ...]) -> list[tuple[int, int, str]]:
    """Every Line (once) that starts with one of the labels: (line index, end word, label)."""
    found: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        # The longest variant that fits wins, so "Gross pay" is not read as "Gross" plus a value.
        for label in sorted(labels, key=lambda lb: -len(tokens(lb))):
            end = _label_end(line, label)
            if end is not None:
                found.append((i, end, label))
                break
    return found


def _scope(lines: list[Line], section: Section) -> list[Line] | str:
    """The Lines strictly between the section's start and end labels, or a reason code."""
    starts = _find(lines, (section.start,))
    if len(starts) != 1:
        return r.AMBIGUOUS if starts else r.SECTION_NOT_FOUND
    first = starts[0][0] + 1
    stop = len(lines)
    if section.end:
        ends = [i for i, _, _ in _find(lines, (section.end,)) if i >= first]
        if ends:
            stop = ends[0]
    return lines[first:stop]


def _candidate(words: list[str], kind: str) -> str:
    """The value text from the words paired to a label."""
    if kind != "money" or not words:
        return " ".join(words)
    if words[0] in _PREFIXES and len(words) > 1:
        return words[0] + words[1]
    return words[0]


def _match_field(spec: FieldSpec, scope: list[Line], whole: list[Line]) -> Hit | Miss:
    found = _find(scope, spec.labels)
    if not found:
        return Miss(r.LABEL_NOT_FOUND)
    if len(found) > 1 and not spec.repeats:
        return Miss(r.AMBIGUOUS)
    rows: list[tuple[str, int, str]] = []
    for index, end, label in found:
        line = scope[index]
        if spec.pair == "same_row":
            words = [w.text for w in line.words[end + 1 :]]
            page = line.page
        else:  # next_line: the Line below the label's, in the document
            position = next(i for i, other in enumerate(whole) if other is line)
            below = whole[position + 1] if position + 1 < len(whole) else None
            words = [w.text for w in below.words] if below else []
            page = below.page if below else line.page
        text = _candidate(words, spec.type)
        if not text:  # one empty row makes the whole figure unreliable
            return Miss(r.NO_VALUE)
        rows.append((text, page, label))
    first = rows[0]
    if not spec.repeats:
        return Hit(first[0], first[1], first[2])
    return Hit(first[0], first[1], first[2], tuple((t, p) for t, p, _ in rows))


def match(document: Document, profile: Profile) -> Matches:
    """Pick every field of the profile out of the document."""
    lines = list(all_lines(document))
    sections = {s.key: _scope(lines, s) for s in profile.sections}
    hits: dict[str, Hit] = {}
    misses: dict[str, Miss] = {}
    for spec in profile.fields:
        scope = lines if spec.section is None else sections[spec.section]
        if isinstance(scope, str):  # the section itself could not be found
            misses[spec.key] = Miss(r.SECTION_NOT_FOUND)
            continue
        outcome = _match_field(spec, scope, lines)
        if isinstance(outcome, Hit):
            hits[spec.key] = outcome
        else:
            misses[spec.key] = outcome
    return Matches(hits, misses)
