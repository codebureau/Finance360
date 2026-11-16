"""The Library: one document store and generic Document links (ADR-0026 sections 2 and 3).

Live-data migration, rows only (Alembic does no file I/O). ``tax_evidence`` becomes
``library_document`` (every row and id kept; ``text`` is NULL and ``text_status`` is ``none`` for
what was there) and ``tax_record_evidence`` becomes ``document_link`` rows of kind ``tax_record``.
The old tables are dropped only after the new ones are checked to hold every row. The files are
moved from ``documents/tax/`` to ``documents/library/`` by the start-up adoption step, which
verifies each hash; until then reads fall back to the old path.

Downgrade rebuilds the two tax tables from the new ones: every document becomes a ``tax_evidence``
row (same id) and every ``tax_record`` link a ``tax_record_evidence`` row. What the tax tables
cannot hold is dropped: the extracted ``text`` (re-derivable from the file), links to other kinds of
record (none exist before later slices add kinds) and a link to a Tax record that no longer exists.
The files are not moved back: they stay under ``documents/library/``, untouched.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _count(table: str) -> int:
    # Fixed table names from this module, never user input.
    return int(op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def upgrade() -> None:
    op.create_table(
        "library_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=150), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("text_status", sa.String(length=12), nullable=False),
        sa.CheckConstraint("length(sha256) = 64", name=op.f("ck_library_document_sha256_length")),
        sa.CheckConstraint(
            "length(trim(file_name)) > 0", name=op.f("ck_library_document_file_name_not_blank")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_library_document_size_positive")),
        sa.CheckConstraint(
            "text_status IN ('extracted', 'none', 'too_large', 'failed')",
            name=op.f("ck_library_document_text_status_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_library_document")),
        sa.UniqueConstraint("sha256", name=op.f("uq_library_document_sha256")),
    )
    op.create_table(
        "document_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("target_kind", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["library_document.id"],
            name=op.f("fk_document_link_document_id_library_document"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_link")),
        sa.UniqueConstraint(
            "document_id", "target_kind", "target_id", name=op.f("uq_document_link_document_id")
        ),
    )
    op.create_index("ix_document_link_target", "document_link", ["target_kind", "target_id"])

    op.execute(
        "INSERT INTO library_document "
        "(id, sha256, file_name, media_type, size_bytes, added_at, text, text_status) "
        "SELECT id, sha256, file_name, media_type, size_bytes, added_at, NULL, 'none' "
        "FROM tax_evidence ORDER BY id"
    )
    op.execute(
        "INSERT INTO document_link (document_id, target_kind, target_id, added_at) "
        "SELECT evidence_id, 'tax_record', record_id, added_at "
        "FROM tax_record_evidence ORDER BY added_at, evidence_id, record_id"
    )
    # Nothing is dropped until the new tables are known to hold every row.
    if _count("library_document") != _count("tax_evidence") or _count("document_link") != _count(
        "tax_record_evidence"
    ):
        raise RuntimeError(
            "The Library migration did not copy every Evidence row; nothing dropped."
        )

    op.drop_index("ix_tax_record_evidence_evidence_id", table_name="tax_record_evidence")
    op.drop_table("tax_record_evidence")
    op.drop_table("tax_evidence")


def downgrade() -> None:
    op.create_table(
        "tax_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=150), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(sha256) = 64", name=op.f("ck_tax_evidence_sha256_length")),
        sa.CheckConstraint(
            "length(trim(file_name)) > 0", name=op.f("ck_tax_evidence_file_name_not_blank")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_tax_evidence_size_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_evidence")),
        sa.UniqueConstraint("sha256", name=op.f("uq_tax_evidence_sha256")),
    )
    op.create_table(
        "tax_record_evidence",
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["tax_evidence.id"],
            name=op.f("fk_tax_record_evidence_evidence_id_tax_evidence"),
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["tax_record.id"],
            name=op.f("fk_tax_record_evidence_record_id_tax_record"),
        ),
        sa.PrimaryKeyConstraint("record_id", "evidence_id", name=op.f("pk_tax_record_evidence")),
    )
    op.create_index("ix_tax_record_evidence_evidence_id", "tax_record_evidence", ["evidence_id"])

    op.execute(
        "INSERT INTO tax_evidence (id, sha256, file_name, media_type, size_bytes, added_at) "
        "SELECT id, sha256, file_name, media_type, size_bytes, added_at "
        "FROM library_document ORDER BY id"
    )
    op.execute(
        "INSERT INTO tax_record_evidence (record_id, evidence_id, added_at) "
        "SELECT target_id, document_id, added_at FROM document_link "
        "WHERE target_kind = 'tax_record' AND target_id IN (SELECT id FROM tax_record) "
        "ORDER BY id"
    )

    op.drop_index("ix_document_link_target", table_name="document_link")
    op.drop_table("document_link")
    op.drop_table("library_document")
