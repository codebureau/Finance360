"""The Library service: add a document, link it to records, check and delete (ADR-0026).

Every write is audited in the caller's transaction. A file is written first (content-addressed,
idempotent), then its row, so a failure leaves at worst a harmless orphan file. A document is
removed only by an explicit delete of an unlinked one; removing a record only drops its links.
Link validation goes through the registered kind (``library.registry``), never into a record's
Capability.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import ColumnElement, delete, event, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance360.capabilities.library import extract, files, registry
from finance360.capabilities.library.files import MAX_BYTES as MAX_BYTES
from finance360.capabilities.library.files import MEDIA_TYPES as MEDIA_TYPES
from finance360.capabilities.library.files import AdoptionReport as AdoptionReport
from finance360.capabilities.library.files import media_type_names as media_type_names
from finance360.capabilities.library.registry import Described, LinkKind
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.library import DocumentLink, LibraryDocument


class DocumentFileError(Exception):
    """A document's file is missing from the data directory, or no longer matches its hash."""


class DocumentInUseError(Exception):
    """A document that is still linked to a record cannot be deleted."""


@dataclass(frozen=True)
class Added:
    document: LibraryDocument
    #: False when the same bytes were already in the Library: nothing new was stored.
    created: bool


@dataclass(frozen=True)
class LinkInfo:
    """A Document link, with the record it points at described by its kind."""

    link: DocumentLink
    kind_title: str
    label: str
    route: str | None
    removed: bool


@dataclass(frozen=True)
class LinkedDocument:
    link: DocumentLink
    document: LibraryDocument


@dataclass(frozen=True)
class Problem:
    document: LibraryDocument
    #: ``missing`` or ``changed``.
    problem: str
    #: The records the document is linked to, as ``(kind, id)``, so the owner can find where to
    #: put it right.
    links: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Check:
    checked: int
    problems: tuple[Problem, ...]


def snapshot(document: LibraryDocument) -> str:
    """For the Audit trail: a document by name and the start of its hash, so it reads as a list."""
    return f"{document.file_name} ({document.sha256[:8]})"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _kind(key: str) -> LinkKind:
    kind = registry.get(key)
    if kind is None:
        raise ValidationError(
            [FieldError("target_kind", f"Documents cannot be linked to '{key}' records.")]
        )
    return kind


# --- reading ---------------------------------------------------------------------------------


def get_document(session: Session, document_id: int) -> LibraryDocument:
    document = session.get(LibraryDocument, document_id)
    if document is None:
        raise NotFoundError(f"Document {document_id} is not in the Library.")
    return document


@dataclass(frozen=True)
class DocumentPage:
    """One page of the list, with how many documents match in all."""

    items: list[LibraryDocument]
    total: int


def list_documents(
    session: Session,
    *,
    q: str = "",
    media_type: str = "",
    linked_kind: str = "",
    unlinked: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> DocumentPage:
    """The documents matching every filter given, the latest added first.

    ``q`` is a case-insensitive match on the file name or the extracted text (SQL ``LIKE``, with
    ``%`` and ``_`` taken literally). ``linked_kind`` keeps documents linked to a record of that
    kind; ``unlinked`` keeps those linked to nothing (the two cannot be asked together).
    """
    if unlinked and linked_kind:
        raise ValidationError(
            [FieldError("unlinked", "Ask for unlinked documents or for a linked kind, not both.")]
        )
    conditions: list[ColumnElement[bool]] = []
    if q.strip():
        needle = q.strip()
        conditions.append(
            or_(
                LibraryDocument.file_name.icontains(needle, autoescape=True),
                LibraryDocument.text.icontains(needle, autoescape=True),
            )
        )
    if media_type:
        conditions.append(LibraryDocument.media_type == media_type)
    if linked_kind:
        conditions.append(
            exists().where(
                DocumentLink.document_id == LibraryDocument.id,
                DocumentLink.target_kind == linked_kind,
            )
        )
    if unlinked:
        conditions.append(~exists().where(DocumentLink.document_id == LibraryDocument.id))
    total = session.scalar(select(func.count()).select_from(LibraryDocument).where(*conditions))
    statement = (
        select(LibraryDocument)
        .where(*conditions)
        .order_by(LibraryDocument.added_at.desc(), LibraryDocument.id.desc())
        .offset(offset)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return DocumentPage(list(session.scalars(statement)), total or 0)


def kinds() -> list[tuple[str, str]]:
    """The registered kinds of record a document can be linked to, as ``(key, title)``."""
    return [(k.key, k.title) for key in registry.keys() if (k := registry.get(key)) is not None]


def link_counts(session: Session, document_ids: Sequence[int]) -> dict[int, int]:
    """How many records each document is linked to (zero when unlinked)."""
    counts = dict.fromkeys(document_ids, 0)
    if document_ids:
        rows = session.execute(
            select(DocumentLink.document_id, func.count())
            .where(DocumentLink.document_id.in_(document_ids))
            .group_by(DocumentLink.document_id)
        )
        counts.update({document_id: n for document_id, n in rows})
    return counts


def describe_links(session: Session, links: Sequence[DocumentLink]) -> list[LinkInfo]:
    by_kind: dict[str, list[int]] = {}
    for link in links:
        by_kind.setdefault(link.target_kind, []).append(link.target_id)
    shown: dict[str, tuple[str, dict[int, Described]]] = {}
    for key, ids in by_kind.items():
        kind = registry.get(key)
        if kind is not None:
            shown[key] = (kind.title, kind.describe(session, sorted(set(ids))))
    out: list[LinkInfo] = []
    for link in links:
        title, described = shown.get(link.target_kind, (link.target_kind, {}))
        found = described.get(link.target_id)
        if found is None:  # an unregistered kind, or a record the kind cannot show
            out.append(LinkInfo(link, title, f"{title} {link.target_id}", None, False))
        else:
            out.append(LinkInfo(link, title, found.label, found.route, found.removed))
    return out


def links_of_document(session: Session, document_id: int) -> list[LinkInfo]:
    """Where a document is used: each link with the record it points at."""
    get_document(session, document_id)
    links = session.scalars(
        select(DocumentLink)
        .where(DocumentLink.document_id == document_id)
        .order_by(DocumentLink.added_at, DocumentLink.id)
    )
    return describe_links(session, list(links))


def documents_of(session: Session, kind_key: str, target_id: int) -> list[LinkedDocument]:
    """A record's documents: those linked to any id in its lineage (so a corrected record keeps its
    documents), in the order they were linked. A document appears once."""
    kind = _kind(kind_key)
    ids = list(kind.lineage(session, target_id)) or [target_id]
    rows = session.execute(
        select(DocumentLink, LibraryDocument)
        .join(LibraryDocument, LibraryDocument.id == DocumentLink.document_id)
        .where(DocumentLink.target_kind == kind_key, DocumentLink.target_id.in_(ids))
        .order_by(DocumentLink.added_at, DocumentLink.id)
    )
    seen: set[int] = set()
    out: list[LinkedDocument] = []
    for link, document in rows:
        if document.id not in seen:
            seen.add(document.id)
            out.append(LinkedDocument(link, document))
    return out


def get_link(session: Session, link_id: int) -> DocumentLink:
    link = session.get(DocumentLink, link_id)
    if link is None:
        raise NotFoundError(f"Document link {link_id} does not exist.")
    return link


def find_link(
    session: Session, document_id: int, kind_key: str, target_id: int
) -> DocumentLink | None:
    return session.scalars(
        select(DocumentLink).where(
            DocumentLink.document_id == document_id,
            DocumentLink.target_kind == kind_key,
            DocumentLink.target_id == target_id,
        )
    ).one_or_none()


def document_file(
    session: Session, data_dir: Path, document_id: int
) -> tuple[LibraryDocument, bytes]:
    """The document and its bytes, only if the file is there and still matches its hash."""
    document = get_document(session, document_id)
    content = files.read(data_dir, document.sha256)
    if content is None:
        found = files.state(data_dir, document.sha256)
        fix = (
            "Add the same file again, or restore it from a backup."
            if found == files.MISSING
            else "Restore it from a backup: a changed file is never overwritten."
        )
        raise DocumentFileError(f"The file for '{document.file_name}' is {found}. {fix}")
    return document, content


def check(
    session: Session, data_dir: Path, *, kind: str | None = None, target_id: int | None = None
) -> Check:
    """The health check: every document's file (or those of one record) must be present and hash
    to its address. A missing or changed file is reported, never hidden or repaired."""
    statement = select(LibraryDocument).order_by(LibraryDocument.id)
    if kind is not None and target_id is not None:
        statement = statement.where(
            LibraryDocument.id.in_([d.document.id for d in documents_of(session, kind, target_id)])
        )
    rows = list(session.scalars(statement))
    problems: list[Problem] = []
    for row in rows:
        found = files.state(data_dir, row.sha256)
        if found == files.OK:
            continue
        linked = session.execute(
            select(DocumentLink.target_kind, DocumentLink.target_id)
            .where(DocumentLink.document_id == row.id)
            .order_by(DocumentLink.target_kind, DocumentLink.target_id)
        )
        problems.append(Problem(row, found, tuple((k, i) for k, i in linked)))
    return Check(len(rows), tuple(problems))


# --- writing ---------------------------------------------------------------------------------


def add_document(
    session: Session,
    context: AuditContext,
    data_dir: Path,
    *,
    file_name: str,
    media_type: str,
    content: bytes,
) -> Added:
    """Put a file in the Library. The same bytes again are the same document: nothing is stored
    twice and nothing changes (and nothing is audited). Text is taken on first add."""
    declared = media_type.split(";", 1)[0].strip().lower()
    errors: list[FieldError] = []
    if not content:
        errors.append(FieldError("file", "The file is empty."))
    elif len(content) > files.MAX_BYTES:
        limit = files.MAX_BYTES // 1_000_000
        errors.append(FieldError("file", f"The file is larger than {limit} MB."))
    elif declared not in files.MEDIA_TYPES:
        errors.append(
            FieldError("file", f"Add a {files.media_type_names()} file, not '{declared}'.")
        )
    elif problem := files.content_problem(declared, content):
        errors.append(FieldError("file", problem))
    if errors:
        raise ValidationError(errors)

    sha = files.store(data_dir, content)
    by_sha = select(LibraryDocument).where(LibraryDocument.sha256 == sha)
    existing = session.scalars(by_sha).one_or_none()
    if existing is not None:
        return Added(existing, False)
    text, text_status = extract.extract(declared, content)
    document = LibraryDocument(
        sha256=sha,
        file_name=files.clean_file_name(file_name),
        media_type=declared,
        size_bytes=len(content),
        added_at=_now(),
        text=text,
        text_status=text_status,
    )
    try:
        with session.begin_nested():
            session.add(document)
    except IntegrityError:  # a concurrent add of the same file won: use its row
        return Added(session.scalars(by_sha).one(), False)
    audit.record(
        session,
        context,
        entity_type="library_document",
        entity_id=document.id,
        action="create",
        after={"document": snapshot(document), "text": text_status},
        summary=f"Added '{document.file_name}' to the Library",
    )
    return Added(document, True)


def link(
    session: Session, context: AuditContext, document_id: int, target_kind: str, target_id: int
) -> DocumentLink:
    """Link a document to a record. The kind validates the record. Linking what is already linked
    changes nothing (and is not audited)."""
    kind = _kind(target_kind)
    document = get_document(session, document_id)
    if not kind.exists(session, target_id):
        raise NotFoundError(f"{kind.title} {target_id} does not exist.")
    existing = find_link(session, document_id, target_kind, target_id)
    if existing is not None:
        return existing
    row = DocumentLink(
        document_id=document_id, target_kind=target_kind, target_id=target_id, added_at=_now()
    )
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError:  # a concurrent identical link won
        return session.scalars(
            select(DocumentLink).where(
                DocumentLink.document_id == document_id,
                DocumentLink.target_kind == target_kind,
                DocumentLink.target_id == target_id,
            )
        ).one()
    label = kind.describe(session, [target_id]).get(target_id)
    where = f"{kind.title} '{label.label}'" if label else f"{kind.title} {target_id}"
    audit.record(
        session,
        context,
        entity_type="document_link",
        entity_id=row.id,
        action="create",
        after={"document": snapshot(document), "target": f"{target_kind} {target_id}"},
        summary=f"Linked '{document.file_name}' to {where}",
    )
    return row


def unlink(session: Session, context: AuditContext, link_id: int) -> None:
    """Remove one link. The document stays in the Library, unlinked if it was the only one."""
    row = get_link(session, link_id)
    document = get_document(session, row.document_id)
    kind = registry.get(row.target_kind)
    title = kind.title if kind else row.target_kind
    before = {"document": snapshot(document), "target": f"{row.target_kind} {row.target_id}"}
    session.delete(row)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="document_link",
        entity_id=link_id,
        action="delete",
        before=before,
        summary=f"Unlinked '{document.file_name}' from {title} {row.target_id}",
    )


def remove_links_to(session: Session, kind_key: str, target_id: int) -> None:
    """Drop every link to a record that is being removed. The documents stay in the Library. Not
    audited here: the removing service audits the record and names its documents."""
    session.execute(
        delete(DocumentLink).where(
            DocumentLink.target_kind == kind_key, DocumentLink.target_id == target_id
        )
    )


def delete_document(
    session: Session, context: AuditContext, data_dir: Path, document_id: int
) -> None:
    """Delete a document: only when it has no links. The file goes after the transaction commits,
    so a failed delete never loses a file whose row survives."""
    document = get_document(session, document_id)
    used = link_counts(session, [document_id])[document_id]
    if used:
        noun = "record" if used == 1 else "records"
        raise DocumentInUseError(
            f"'{document.file_name}' is linked to {used} {noun}. Unlink it from them first."
        )
    sha, name, before = document.sha256, document.file_name, {"document": snapshot(document)}
    session.delete(document)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="library_document",
        entity_id=document_id,
        action="delete",
        before=before,
        summary=f"Deleted '{name}' from the Library",
    )

    def remove_file(_session: Session) -> None:
        files.remove(data_dir, sha)

    event.listen(session, "after_commit", remove_file, once=True)


#: Same file as ``household.FIXTURE_FILENAME``; the Library imports no record-owning Capability.
FIXTURE_FILENAME = "household.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


def load_fixture_documents(session: Session, data_dir: Path) -> int:
    """Add the fixture's unlinked documents to the Library, once, while it is empty.

    Reads ``library_documents`` from ``<data_dir>/household.json``: each entry names the ``file``
    (relative to the data directory) and its ``media_type``. They are added as an upload is and
    linked to nothing, so the Library's "unlinked" filter and Delete have something to show. Run
    before the documents the fixture links to records.
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file():
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("library_documents", [])
    if not entries or session.scalar(select(func.count()).select_from(LibraryDocument)):
        return 0
    done = 0
    for e in entries:
        source = data_dir / e["file"]
        if not source.is_file():
            continue
        add_document(
            session,
            FIXTURE_CONTEXT,
            data_dir,
            file_name=source.name,
            media_type=e["media_type"],
            content=source.read_bytes(),
        )
        done += 1
    return done


def adopt_files(session: Session, data_dir: Path) -> AdoptionReport:
    """Start-up: move each document's file from ``documents/tax/`` to ``documents/library/``
    (idempotent; see ``library.files``). A problem is logged and returned, never raised."""
    hashes = session.scalars(select(LibraryDocument.sha256).order_by(LibraryDocument.id))
    return files.adopt(data_dir, list(hashes))
