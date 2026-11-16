"""Query expansion from CONTEXT.md (ADR-0023 section 2). Deterministic; no AI.

CONTEXT.md names each term the app uses and, under ``_Avoid_`` and ``_Also_``, the words people say
instead (Valuation: "Balance", "Snapshot"). A question that uses one of those words gains the
term in its query, so "what is my balance" also finds the Guide's pages on Valuations.

Only a plain entry expands. One with a qualifier in brackets ("Account (an Account is a
financial holding)", "Position (within an Account)") is a disambiguation, not a synonym, and is
skipped. A word that is itself a defined term is never a synonym of another ("Account" is listed
under "Income stream" only to say that an Income stream is not an Account).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_TERM = re.compile(r"^\*\*(.+?)\*\*:")
_LISTED = re.compile(r"^_(Avoid|Also)_:\s*(.*)$")
_WORD = re.compile(r"[a-z0-9]+")


def normalise(phrase: str) -> tuple[str, ...]:
    """A phrase as words with a plural ``s`` dropped, so "balances" meets "balance"."""
    return tuple(
        w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _WORD.findall(phrase.lower())
    )


def _items(text: str) -> list[str]:
    """Split a comma list, but not inside brackets."""
    items: list[str] = []
    depth, current = 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += ch
    items.append(current)
    return [i.strip().rstrip(".").strip() for i in items if i.strip()]


@dataclass(frozen=True)
class Expansions:
    #: normalised phrase -> the terms it stands for, in the order CONTEXT.md lists them.
    by_phrase: dict[tuple[str, ...], tuple[str, ...]]

    def terms_for(self, words: Sequence[str]) -> list[str]:
        """The terms any phrase in ``words`` (a question's words, in order) stands for."""
        seen = normalise(" ".join(words))
        found: list[str] = []
        for phrase, terms in self.by_phrase.items():
            n = len(phrase)
            if any(seen[i : i + n] == phrase for i in range(len(seen) - n + 1)):
                found.extend(t for t in terms if t not in found)
        return found


def expansions_from(context: str) -> Expansions:
    entries: list[tuple[str, list[str]]] = []
    term: str | None = None
    listing: list[str] | None = None
    for line in context.replace("\r\n", "\n").split("\n"):
        if (head := _TERM.match(line)) is not None:
            term, listing = head.group(1).strip(), None
        elif term is not None and (item := _LISTED.match(line)) is not None:
            listing = [item.group(2)]
            entries.append((term, listing))
        elif listing is not None and line.strip() and not line.startswith(("**", "_", ">", "#")):
            listing.append(line.strip())  # a listing wrapped onto the next line
        else:
            listing = None
    defined = {normalise(t) for t, _ in entries}
    defined |= {normalise(m.group(1)) for line in context.split("\n") if (m := _TERM.match(line))}
    by_phrase: dict[tuple[str, ...], list[str]] = {}
    for entry_term, parts in entries:
        for word in _items(" ".join(parts)):
            if "(" in word or ")" in word:
                continue  # a qualifier makes it a disambiguation, not a synonym
            phrase = normalise(word)
            if not phrase or phrase in defined or phrase == normalise(entry_term):
                continue
            if entry_term not in by_phrase.setdefault(phrase, []):
                by_phrase[phrase].append(entry_term)
    return Expansions({k: tuple(v) for k, v in by_phrase.items()})
