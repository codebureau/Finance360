"""The Library endpoints (ADR-0026). Thin: every rule is in ``capabilities.library``.

A file is added as the raw request body, its Content-Type the media type. Documents are linked to
records through the kind registry, so one set of routes serves every kind of record. The file is
served from its own route, behind the sign-in like all of ``/api``, with the headers that keep a
stored document from ever being run by the browser (ADR-0026 section 5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.api.uploads import FILE_BODY, DataDirDep, FileDep
from finance360.capabilities.library import service
from finance360.core.models.library import LibraryDocument

router = APIRouter(prefix="/api/library", tags=["library"])

#: A stored document is shown by the browser's own viewer, never run: nothing may load or execute,
#: only the document itself is drawn. ``frame-ancestors 'self'`` lets the app's own pages (and no
#: other origin) frame it for the side-by-side pane. No ``sandbox`` directive: it stops Chromium's
#: PDF viewer. Proved in a browser check: Chromium's PDF viewer renders a stored PDF in an
#: ``<iframe>`` under exactly this policy, so it stays as strict as it is.
FILE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'"
)


class LibraryDocumentOut(BaseModel):
    id: int
    file_name: str = Field(description="The name it was first added with, as a bare file name.")
    media_type: str
    size_bytes: int
    sha256: str
    added_at: datetime
    text_status: str = Field(
        description="'extracted' (its text is stored for search), 'none' (nothing to read: an "
        "image or scan), 'too_large' (over the reading limits) or 'failed' (could not be read)."
    )
    link_count: int = Field(description="How many records it is linked to; zero when unlinked.")


class LibraryDocumentPage(BaseModel):
    items: list[LibraryDocumentOut]
    total: int = Field(description="How many documents match the filters, over all pages.")
    limit: int
    offset: int


class LibraryKindOut(BaseModel):
    key: str
    title: str = Field(description="What the screen calls this kind of record.")


class LibraryAddedOut(BaseModel):
    document: LibraryDocumentOut
    already_in_library: bool = Field(description="True when the same file was already stored.")


class LibraryLinkOut(BaseModel):
    id: int
    document_id: int
    target_kind: str
    kind_title: str = Field(description="What the screen calls this kind of record.")
    target_id: int
    label: str = Field(description="The record, described by its kind.")
    route: str | None = Field(description="The app route that opens the record, if it has one.")
    removed: bool = Field(description="True when the record has since been removed.")
    added_at: datetime


class LibraryDocumentDetailOut(LibraryDocumentOut):
    links: list[LibraryLinkOut]


class LibraryLinkedDocumentOut(BaseModel):
    link: LibraryLinkOut
    document: LibraryDocumentOut


class LibraryLinkIn(BaseModel):
    document_id: int
    target_kind: str
    target_id: int


class LibraryLinkRefOut(BaseModel):
    target_kind: str
    target_id: int


class LibraryProblemOut(BaseModel):
    document_id: int
    file_name: str
    problem: str = Field(
        description="'missing' (no file at its address) or 'changed' (wrong hash)."
    )
    links: list[LibraryLinkRefOut] = Field(description="The records the document is linked to.")


class LibraryCheckOut(BaseModel):
    checked: int
    problems: list[LibraryProblemOut]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def _document_out(row: LibraryDocument, link_count: int) -> LibraryDocumentOut:
    return LibraryDocumentOut(
        id=row.id,
        file_name=row.file_name,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        added_at=_utc(row.added_at),
        text_status=row.text_status,
        link_count=link_count,
    )


def _link_out(info: service.LinkInfo) -> LibraryLinkOut:
    link = info.link
    return LibraryLinkOut(
        id=link.id,
        document_id=link.document_id,
        target_kind=link.target_kind,
        kind_title=info.kind_title,
        target_id=link.target_id,
        label=info.label,
        route=info.route,
        removed=info.removed,
        added_at=_utc(link.added_at),
    )


@router.get(
    "/documents",
    response_model=LibraryDocumentPage,
    operation_id="listLibraryDocuments",
    responses=ERRORS,  # type: ignore[arg-type]
)
def list_documents(
    session: SessionDep,
    q: Annotated[str, Query(description="Words in the file name or the extracted text.")] = "",
    media_type: Annotated[str, Query(description="Only this media type.")] = "",
    linked_kind: Annotated[
        str, Query(description="Only documents linked to a record of this kind.")
    ] = "",
    unlinked: Annotated[bool, Query(description="Only documents linked to nothing.")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LibraryDocumentPage:
    """The Library, the latest added first. Every filter given must match; ``total`` counts all
    the matches, not just this page."""
    found = service.list_documents(
        session,
        q=q,
        media_type=media_type,
        linked_kind=linked_kind,
        unlinked=unlinked,
        limit=limit,
        offset=offset,
    )
    counts = service.link_counts(session, [r.id for r in found.items])
    return LibraryDocumentPage(
        items=[_document_out(r, counts[r.id]) for r in found.items],
        total=found.total,
        limit=limit,
        offset=offset,
    )


@router.get("/kinds", response_model=list[LibraryKindOut], operation_id="listLibraryKinds")
def list_kinds() -> list[LibraryKindOut]:
    """The kinds of record a document can be linked to, for the Library's filter."""
    return [LibraryKindOut(key=key, title=title) for key, title in service.kinds()]


@router.post(
    "/documents",
    status_code=201,
    response_model=LibraryAddedOut,
    operation_id="addLibraryDocument",
    responses={**ERRORS, 200: {"model": LibraryAddedOut}},  # type: ignore[arg-type]
    openapi_extra=FILE_BODY,
)
def add_document(
    request: Request,
    response: Response,
    body: FileDep,
    session: SessionDep,
    ctx: ContextDep,
    data_dir: DataDirDep,
    file_name: str = "",
) -> LibraryAddedOut:
    """Add a file: the raw body, its Content-Type the media type. The same bytes again are the
    same document (200, ``already_in_library``) and nothing is stored twice."""
    added = service.add_document(
        session,
        ctx,
        data_dir,
        file_name=file_name,
        media_type=request.headers.get("content-type", ""),
        content=body,
    )
    if not added.created:
        response.status_code = 200
    count = service.link_counts(session, [added.document.id])[added.document.id]
    return LibraryAddedOut(
        document=_document_out(added.document, count), already_in_library=not added.created
    )


@router.get(
    "/documents/{document_id}",
    response_model=LibraryDocumentDetailOut,
    operation_id="getLibraryDocument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_document(document_id: int, session: SessionDep) -> LibraryDocumentDetailOut:
    row = service.get_document(session, document_id)
    links = service.links_of_document(session, document_id)
    base = _document_out(row, len(links))
    return LibraryDocumentDetailOut(**base.model_dump(), links=[_link_out(i) for i in links])


@router.delete(
    "/documents/{document_id}",
    status_code=204,
    operation_id="deleteLibraryDocument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def delete_document(
    document_id: int, session: SessionDep, ctx: ContextDep, data_dir: DataDirDep
) -> Response:
    """Delete a document: only when nothing links to it (409 otherwise). Its file goes with it."""
    try:
        service.delete_document(session, ctx, data_dir, document_id)
    except service.DocumentInUseError as problem:
        return JSONResponse({"message": str(problem)}, status_code=409)
    return Response(status_code=204)


def _disposition(kind: str, file_name: str) -> str:
    fallback = "".join(
        c if c.isascii() and c not in '"\\' and c.isprintable() else "_" for c in file_name
    )
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(file_name)}"


@router.get(
    "/documents/{document_id}/file",
    response_model=None,
    operation_id="downloadLibraryDocument",
    responses={
        **ERRORS,  # type: ignore[arg-type]
        200: {
            "description": "The file, for the browser's own viewer (or as a download).",
            "content": {
                m: {"schema": {"type": "string", "format": "binary"}} for m in service.MEDIA_TYPES
            },
        },
    },
)
def document_file(
    document_id: int,
    session: SessionDep,
    data_dir: DataDirDep,
    download: Annotated[bool, Query(description="Save it instead of showing it.")] = False,
) -> Response:
    """The file, only if it is present and still matches its hash (otherwise 409). The type is the
    stored one from the allowed list (text and CSV as plain text), never derived from the name."""
    try:
        row, content = service.document_file(session, data_dir, document_id)
    except service.DocumentFileError as problem:
        return JSONResponse({"message": str(problem)}, status_code=409)
    media_type = row.media_type
    if media_type in ("text/plain", "text/csv"):
        media_type = "text/plain; charset=utf-8"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": _disposition(
                "attachment" if download else "inline", row.file_name
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": FILE_CSP,
            "Cross-Origin-Resource-Policy": "same-origin",
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.get(
    "/links", response_model=list[LibraryLinkedDocumentOut], operation_id="listLibraryLinks"
)
def list_links(
    session: SessionDep,
    kind: Annotated[str, Query(description="A registered kind of record, such as tax_record.")],
    id: Annotated[int, Query(description="The record.")],  # noqa: A002 - the query name
) -> list[LibraryLinkedDocumentOut]:
    """A record's documents, including those linked to an earlier or later version of it."""
    found = service.documents_of(session, kind, id)
    counts = service.link_counts(session, [d.document.id for d in found])
    described = {
        info.link.id: info for info in service.describe_links(session, [d.link for d in found])
    }
    return [
        LibraryLinkedDocumentOut(
            link=_link_out(described[d.link.id]),
            document=_document_out(d.document, counts[d.document.id]),
        )
        for d in found
    ]


@router.post(
    "/links",
    status_code=201,
    response_model=LibraryLinkOut,
    operation_id="linkLibraryDocument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def link_document(body: LibraryLinkIn, session: SessionDep, ctx: ContextDep) -> LibraryLinkOut:
    """Link a document to a record. The record's kind checks it exists. Linking what is already
    linked changes nothing."""
    row = service.link(session, ctx, body.document_id, body.target_kind, body.target_id)
    [info] = service.describe_links(session, [row])
    return _link_out(info)


@router.delete(
    "/links/{link_id}",
    status_code=204,
    operation_id="unlinkLibraryDocument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def unlink_document(link_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    """Remove a link. The document stays in the Library."""
    service.unlink(session, ctx, link_id)
    return Response(status_code=204)


@router.get(
    "/check",
    response_model=LibraryCheckOut,
    operation_id="checkLibrary",
    responses=ERRORS,  # type: ignore[arg-type]
)
def check_library(
    session: SessionDep,
    data_dir: DataDirDep,
    kind: Annotated[str | None, Query(description="Only this kind of record's documents.")] = None,
    id: Annotated[int | None, Query(description="With kind: only this record's.")] = None,  # noqa: A002 - the query name
) -> LibraryCheckOut:
    """The health check: documents whose file is missing, or no longer matches its hash. With a
    record (``kind`` and ``id``), only its documents."""
    result = service.check(session, data_dir, kind=kind, target_id=id)
    return LibraryCheckOut(
        checked=result.checked,
        problems=[
            LibraryProblemOut(
                document_id=p.document.id,
                file_name=p.document.file_name,
                problem=p.problem,
                links=[LibraryLinkRefOut(target_kind=k, target_id=i) for k, i in p.links],
            )
            for p in result.problems
        ],
    )
