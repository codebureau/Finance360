"""The Library: stored documents and their links to records (ADR-0026 sections 2 and 3).
Services: ``capabilities/library``.

``library_document`` is the **reference** to a file kept under ``documents/library/`` in the
instance data directory, content-addressed by its SHA-256: the database holds the hash, the name the
file was added with, the media type, the size, the date added and the document's extracted plain
text (for search), **never the file's bytes**. One row per distinct file, so a duplicate upload is
the same row. It replaced ``tax_evidence`` (ADR-0019 section 7), keeping every row and id.

``document_link`` is the one table of associations between a document and a record of any
registered kind (``target_kind`` is a key of the Library's kind registry; ``target_id`` is checked
at write by that kind, so there is no per-kind foreign key). It replaced ``tax_record_evidence``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

FILE_NAME_MAX = 150

#: What came of reading the document's text on add (ADR-0026 section 2). ``extracted``: its text
#: is stored. ``none``: it has no text to take (an image, a scan). ``too_large``: over the loaders'
#: page, time or size limits. ``failed``: the loader could not read it (encrypted, damaged).
TEXT_STATUSES = ("extracted", "none", "too_large", "failed")


class LibraryDocument(Base):
    __tablename__ = "library_document"
    __table_args__ = (
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        CheckConstraint("length(trim(file_name)) > 0", name="file_name_not_blank"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint(
            "text_status IN ('extracted', 'none', 'too_large', 'failed')", name="text_status_known"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Lower-case hex SHA-256 of the file's bytes, which is also where the file is kept.
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    #: The name given at the first upload, reduced to a bare file name. User data: never a path.
    file_name: Mapped[str] = mapped_column(String(FILE_NAME_MAX))
    #: One of ``capabilities.library.files.MEDIA_TYPES``.
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    #: UTC, stored naive like every timestamp here.
    added_at: Mapped[datetime] = mapped_column(DateTime)
    #: The document's plain text, for search; ``None`` unless ``text_status`` is ``extracted``.
    #: Never logged and never returned by a list.
    text: Mapped[str | None] = mapped_column(Text)
    text_status: Mapped[str] = mapped_column(String(12))


class DocumentLink(Base):
    __tablename__ = "document_link"
    __table_args__ = (
        UniqueConstraint("document_id", "target_kind", "target_id"),
        Index("ix_document_link_target", "target_kind", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("library_document.id"))
    #: A key of the kind registry (``capabilities.library.registry``), such as ``tax_record``.
    target_kind: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[int]
    added_at: Mapped[datetime] = mapped_column(DateTime)
