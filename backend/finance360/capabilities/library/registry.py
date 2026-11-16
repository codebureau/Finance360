"""The Link kind registry (ADR-0026 section 3).

Records of any kind can have documents. The Library owns the ``document_link`` table and nothing
about any record: each record-owning Capability builds a ``LinkKind`` for its own records (what
exists, how a record's versions are chained, how to show it, what could be linked) and ``main.py``,
the composition root, registers it like an Importer. A link is validated by the registered kind,
never by the Library reaching into tax or position, so this module imports no Capability.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy.orm import Session


class Described(NamedTuple):
    """How a record is shown beside a document."""

    #: A short human label, such as a Tax record's description.
    label: str
    #: The app route that opens the record (without a host); ``None`` when it has no page.
    route: str | None
    #: True when the record's lineage ends in removal. The link is kept (traceability).
    removed: bool


@dataclass(frozen=True)
class LinkKind:
    #: The value stored in ``document_link.target_kind``; lower-case snake case.
    key: str
    #: What the screen calls this kind of record, such as "Tax record".
    title: str
    #: Is there a record with this id?
    exists: Callable[[Session, int], bool]
    #: Every id that is the same record as this one (itself, its earlier and later versions), so a
    #: corrected record keeps its documents. A record never superseded is just ``[id]``.
    lineage: Callable[[Session, int], Sequence[int]]
    #: How each of these ids is shown, described by the current record in its lineage.
    describe: Callable[[Session, Sequence[int]], dict[int, Described]]
    #: Records whose label contains ``text``, as ``(id, label)``: backs "link to an existing
    #: record".
    candidates: Callable[[Session, str], Sequence[tuple[int, str]]]


_KINDS: dict[str, LinkKind] = {}


def register(kind: LinkKind) -> None:
    """Add a kind. Registering a different kind under the same key is a programming error."""
    existing = _KINDS.get(kind.key)
    if existing is not None and existing != kind:
        raise ValueError(f"A Link kind is already registered under {kind.key!r}")
    _KINDS[kind.key] = kind


def unregister(key: str) -> None:
    """For tests that register a stand-in."""
    _KINDS.pop(key, None)


def get(key: str) -> LinkKind | None:
    return _KINDS.get(key)


def keys() -> list[str]:
    return sorted(_KINDS)
