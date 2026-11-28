"""Redaction: names, identifiers, e-mail addresses, long digit runs and amounts become tokens before
text leaves the instance, and the answer is restored afterwards (ADR-0021 section 3).

Pure: no database, SDK or Capability. The vocabulary is handed in (``vocabulary.py`` loads it).

What is replaced, in one left-to-right pass so a token is never re-scanned:

* an e-mail address                                  -> ``[EMAIL_n]``
* a vocabulary name (Household, Member, Account,
  institution), exact and case-insensitive, whole
  words only, longest first                          -> ``[ACCOUNT_n]`` ``[MEMBER_n]``
                                                        ``[ENTITY_n]``
* an Account identifier, whitespace and ``-`` ignored -> ``[NUMBER_n]``
* a currency amount (``$1,234``, ``AUD 500``, ``2k``)  -> ``[AMOUNT]``
* any run of 6 or more digits (dates and ``2026-27``
  financial years excepted)                          -> ``[NUMBER_n]``

``redact(text, keep_figures=True)`` leaves the last two kinds (amounts and digit runs) in place and
still replaces names, identifiers and e-mail addresses. Only the ``reference`` data class uses it,
for the pasted official page whose figures are the point (ADR-0022 section 5); the gateway refuses
it anywhere else.

Tokens are stable within one ``Redactor`` (one call). Restoring puts names and e-mail addresses
back; ``[AMOUNT]`` and ``[NUMBER_n]`` restore to nothing (Ask never repeats a figure); an unknown
token is left as it is. Tokens are keyed by the exact text matched, so ``restore(redact(x)) == x``
for any text built from names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finance360.core.figures import AMOUNT as _AMOUNT
from finance360.core.figures import DATE_LIKE as _DATE_LIKE
from finance360.core.figures import DIGITS as _DIGITS

ACCOUNT = "ACCOUNT"
MEMBER = "MEMBER"
ENTITY = "ENTITY"
NUMBER = "NUMBER"
EMAIL = "EMAIL"
AMOUNT = "AMOUNT"

#: Kinds a vocabulary entry may have. ``NUMBER`` entries are identifiers.
VOCABULARY_KINDS = (ACCOUNT, MEMBER, ENTITY, NUMBER)
#: Kinds whose original text is put back when restoring.
_RESTORED = (ACCOUNT, MEMBER, ENTITY, EMAIL)

_WORD = "A-Za-z0-9"
_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
_RESTORE = re.compile(r"\[(ACCOUNT|MEMBER|ENTITY|EMAIL)_(\d+)\]", re.IGNORECASE)
_DROP = re.compile(r"\[(?:NUMBER_\d+|AMOUNT)\]", re.IGNORECASE)


@dataclass(frozen=True)
class Vocabulary:
    """What the household knows: ``(kind, text)`` pairs, kind one of ``VOCABULARY_KINDS``."""

    entries: tuple[tuple[str, str], ...] = ()


def _name_pattern(text: str) -> str:
    return r"\s+".join(re.escape(part) for part in text.split())


def _identifier_pattern(text: str) -> str:
    return r"[ -]?".join(re.escape(ch) for ch in text if not ch.isspace())


def _compile(
    vocabulary: Vocabulary, *, strip_figures: bool
) -> tuple[re.Pattern[str], dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    entries: list[tuple[str, str]] = []
    for kind, text in sorted(vocabulary.entries, key=lambda e: len(e[1]), reverse=True):
        text = " ".join(text.split())
        if not text or (kind, text.casefold()) in seen:
            continue
        seen.add((kind, text.casefold()))
        entries.append((kind, text))
    kinds: dict[str, str] = {}
    parts = [f"(?P<email>{_EMAIL})"]
    for i, (kind, text) in enumerate(entries):
        body = _identifier_pattern(text) if kind == NUMBER else _name_pattern(text)
        parts.append(f"(?P<v{i}>(?<![{_WORD}]){body}(?![{_WORD}]))")
        kinds[f"v{i}"] = kind
    if strip_figures:
        parts.append(f"(?P<amount>{_AMOUNT})")
        parts.append(f"(?P<digits>{_DIGITS})")
    return re.compile("|".join(parts), re.IGNORECASE), kinds


class Redactor:
    """One call's redaction state: the same text gets the same token throughout."""

    def __init__(self, vocabulary: Vocabulary) -> None:
        self._pattern, self._kinds = _compile(vocabulary, strip_figures=True)
        self._pattern_keeping_figures, _ = _compile(vocabulary, strip_figures=False)
        self._tokens: dict[tuple[str, str], str] = {}
        self._originals: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def _token(self, kind: str, text: str) -> str:
        known = self._tokens.get((kind, text))
        if known is not None:
            return known
        self._counts[kind] = self._counts.get(kind, 0) + 1
        token = f"[{kind}_{self._counts[kind]}]"
        self._tokens[(kind, text)] = token
        if kind in _RESTORED:
            self._originals[token] = text
        return token

    def _replace(self, match: re.Match[str]) -> str:
        text = match.group(0)
        group = match.lastgroup
        if group == "email":
            return self._token(EMAIL, text)
        if group == "amount":
            return f"[{AMOUNT}]"
        if group == "digits":
            return text if _DATE_LIKE.fullmatch(text) else self._token(NUMBER, text)
        return self._token(self._kinds[str(group)], text)

    def redact(self, text: str, *, keep_figures: bool = False) -> str:
        pattern = self._pattern_keeping_figures if keep_figures else self._pattern
        return pattern.sub(self._replace, text)

    def restore(self, text: str) -> str:
        def put_back(match: re.Match[str]) -> str:
            token = f"[{match.group(1).upper()}_{match.group(2)}]"
            return self._originals.get(token, match.group(0))

        restored = _RESTORE.sub(put_back, text)
        if not _DROP.search(restored):
            return restored
        # Tidy the gap a dropped figure leaves behind.
        dropped = _DROP.sub("", restored)
        return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", dropped))
