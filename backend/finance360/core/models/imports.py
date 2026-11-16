"""Import source, Import and staged rows (ADR-0013 sections 2, 5 and 5b).
Services: ``capabilities/imports``.

* ``ImportSource``: the binding of an Importer to one Account, with its validated ``config``.
  Unique per (Account, Importer).
* ``Import``: one file added for one Stream. It stores the raw file (inspectable, re-parsable,
  covered by the database Backup) and its Coverage. Unique per (file hash, Import source, Stream)
  among non-discarded rows, so re-adding the same file returns the existing Import.
* ``StagedRow``: one canonical record of an Import, kept after commit or discard for inspection.
  Nothing here is a Transaction; that arrives with the commit slice.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance360.core.db import Base

IMPORT_STATUSES = ("staged", "committed", "discarded")
IMPORT_STREAMS = ("trade", "cash")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ImportSource(Base):
    __tablename__ = "import_source"
    __table_args__ = (UniqueConstraint("account_id", "importer_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    importer_key: Mapped[str] = mapped_column(String(50))
    #: Validated against the Importer's JSON Schema on write.
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: UTC, naive.
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Import(Base):
    __tablename__ = "import"
    __table_args__ = (
        CheckConstraint(_in("status", IMPORT_STATUSES), name="status_known"),
        CheckConstraint(_in("stream", IMPORT_STREAMS), name="stream_known"),
        CheckConstraint("coverage_from <= coverage_to", name="coverage_ordered"),
        Index(
            "uq_import_current",
            "file_sha256",
            "import_source_id",
            "stream",
            unique=True,
            sqlite_where=text("status <> 'discarded'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_source_id: Mapped[int] = mapped_column(ForeignKey("import_source.id"), index=True)
    stream: Mapped[str] = mapped_column(String(10))
    file_sha256: Mapped[str] = mapped_column(String(64))
    #: Display only.
    file_name: Mapped[str] = mapped_column(String(255))
    #: The raw file, byte for byte.
    content: Mapped[bytes] = mapped_column(LargeBinary)
    coverage_from: Mapped[date] = mapped_column(Date)
    coverage_to: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="staged")
    #: The Importer's warnings, as text (never an account reference).
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    added_at: Mapped[datetime] = mapped_column(DateTime)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime)

    source: Mapped[ImportSource] = relationship()


class StagedRow(Base):
    __tablename__ = "staged_row"
    __table_args__ = (UniqueConstraint("import_id", "row_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import.id"), index=True)
    row_no: Mapped[int]
    #: The canonical record: money as ``{minor, ccy}``, quantities as decimal strings.
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    #: The raw identifier as read; ``None`` for a row that names no Instrument.
    instrument_ref: Mapped[str | None] = mapped_column(String(200))
    #: ``None`` until resolved through the crosswalk.
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instrument.id"), index=True)
