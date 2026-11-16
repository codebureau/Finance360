"""Migration 0028 on live data (ADR-0026 section 2): tax Evidence becomes the Library.

Invariant work. A database at the previous revision is filled with Evidence rows (ids with gaps, a
document on two records, a record with two documents, an unlinked document), upgraded, and every
row must be there under the same id with the same links; the way back must rebuild the tax tables
exactly. Then the whole path as a real start-up does it: migrate, adopt the files from
``documents/tax/``, serve them. Rows only in Alembic; the files move in the start-up step. Every
value here is invented. (The populated-database upgrade of the whole synthetic household, with its
file, is in ``test_upgrade_populated``.)
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from alembic import command

from finance360.capabilities.library import files
from finance360.config import Settings
from finance360.core.db import db_path
from finance360.core.migrate import MIGRATIONS_DIR, alembic_config
from finance360.main import create_app
from support import owner_client

BLOBS = {
    1: (b"%PDF-1.4\n% invented one\n", "application/pdf", "one.pdf"),
    2: (b"Invented note.\n", "text/plain", "note.txt"),
    5: (b"\x89PNG\r\n\x1a\ninvented", "image/png", "photo.png"),
    7: (b"a,b\n1,2\n", "text/csv", "unlinked.csv"),
}
#: (record, document, added_at): record 1 has two documents; document 1 is on two records.
LINKS = [
    (1, 1, "2026-10-01 09:00:00"),
    (1, 2, "2026-10-01 09:05:00"),
    (2, 1, "2026-10-02 10:00:00"),
    (3, 5, "2026-10-03 11:00:00"),
]


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_previous_release(data_dir: Path) -> None:
    """A database at 0027 holding Evidence as the previous release stored it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(data_dir, MIGRATIONS_DIR), "0027")
    conn = sqlite3.connect(db_path(data_dir))
    try:
        for record in (1, 2, 3):
            conn.execute(
                "INSERT INTO tax_record (id, record_type, from_on, description, payload_json, "
                "created_at) VALUES (?, 'line', '2026-01-10', ?, '{}', '2026-10-01 00:00:00')",
                (record, f"Invented record {record}"),
            )
        for doc_id, (content, media_type, name) in BLOBS.items():
            conn.execute(
                "INSERT INTO tax_evidence VALUES (?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    sha(content),
                    name,
                    media_type,
                    len(content),
                    f"2026-09-0{doc_id} 08:00:00",
                ),
            )
        conn.executemany(
            "INSERT INTO tax_record_evidence (record_id, evidence_id, added_at) VALUES (?, ?, ?)",
            LINKS,
        )
        conn.commit()
    finally:
        conn.close()


def rows(data_dir: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db_path(data_dir))
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def tables(data_dir: Path) -> set[str]:
    return {
        str(r[0]) for r in rows(data_dir, "SELECT name FROM sqlite_master WHERE type = 'table'")
    }


EVIDENCE = (
    "SELECT id, sha256, file_name, media_type, size_bytes, added_at FROM tax_evidence ORDER BY id"
)
LINK_ROWS = "SELECT record_id, evidence_id, added_at FROM tax_record_evidence ORDER BY 1, 2"


def test_every_evidence_row_becomes_a_library_document_under_the_same_id(tmp_path: Path) -> None:
    build_previous_release(tmp_path)
    before = rows(tmp_path, EVIDENCE)
    assert len(before) == 4
    command.upgrade(alembic_config(tmp_path, MIGRATIONS_DIR), "0028")
    after = rows(
        tmp_path,
        "SELECT id, sha256, file_name, media_type, size_bytes, added_at FROM library_document "
        "ORDER BY id",
    )
    assert after == before  # same ids, hashes, names, types, sizes and dates
    text = rows(tmp_path, "SELECT id, text, text_status FROM library_document ORDER BY id")
    assert text == [(i, None, "none") for i in (1, 2, 5, 7)]  # nothing extracted yet, said so


def test_every_record_link_becomes_a_document_link_of_kind_tax_record(tmp_path: Path) -> None:
    build_previous_release(tmp_path)
    command.upgrade(alembic_config(tmp_path, MIGRATIONS_DIR), "0028")
    links = rows(
        tmp_path,
        "SELECT target_kind, target_id, document_id, added_at FROM document_link ORDER BY 2, 3",
    )
    assert links == [("tax_record", record, doc, added) for record, doc, added in LINKS]
    # the unlinked document stays in the Library, unlinked
    linked = {r[0] for r in rows(tmp_path, "SELECT DISTINCT document_id FROM document_link")}
    assert linked == {1, 2, 5} and 7 not in linked


def test_the_old_tables_go_only_after_the_new_ones_hold_every_row(tmp_path: Path) -> None:
    build_previous_release(tmp_path)
    command.upgrade(alembic_config(tmp_path, MIGRATIONS_DIR), "0028")
    assert not {"tax_evidence", "tax_record_evidence"} & tables(tmp_path)
    assert {"library_document", "document_link"} <= tables(tmp_path)


def test_the_way_back_rebuilds_the_tax_tables_exactly_and_upgrading_again_is_clean(
    tmp_path: Path,
) -> None:
    build_previous_release(tmp_path)
    evidence, links = rows(tmp_path, EVIDENCE), rows(tmp_path, LINK_ROWS)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.upgrade(config, "0028")
    command.downgrade(config, "0027")
    assert rows(tmp_path, EVIDENCE) == evidence
    assert rows(tmp_path, LINK_ROWS) == links
    assert not {"library_document", "document_link"} & tables(tmp_path)
    command.upgrade(config, "head")  # check compares the models, which are at head
    command.check(config)
    assert len(rows(tmp_path, "SELECT id FROM document_link")) == len(LINKS)


def test_a_document_added_after_the_upgrade_survives_the_way_back_without_its_text(
    tmp_path: Path,
) -> None:
    """The tax tables cannot hold text or other kinds of link; those are dropped, the document is
    not, and a link to a record that no longer exists cannot be rebuilt (it has no row)."""
    build_previous_release(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.upgrade(config, "0028")
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        conn.execute(
            "INSERT INTO library_document (sha256, file_name, media_type, size_bytes, added_at, "
            "text, text_status) VALUES (?, 'new.txt', 'text/plain', 3, '2026-11-01 00:00:00', "
            "'new', 'extracted')",
            (sha(b"new"),),
        )
        new_id = conn.execute("SELECT max(id) FROM library_document").fetchone()[0]
        conn.executemany(
            "INSERT INTO document_link (document_id, target_kind, target_id, added_at) "
            "VALUES (?, ?, ?, '2026-11-01 00:00:00')",
            [(new_id, "tax_record", 1), (new_id, "account", 4), (new_id, "tax_record", 999)],
        )
        conn.commit()
    finally:
        conn.close()
    command.downgrade(config, "0027")
    assert (new_id, sha(b"new"), "new.txt") in {r[:3] for r in rows(tmp_path, EVIDENCE)}
    assert (1, new_id) in {r[:2] for r in rows(tmp_path, LINK_ROWS)}
    assert {r[1] for r in rows(tmp_path, LINK_ROWS)} == {1, 2, 5, new_id}
    assert 999 not in {r[0] for r in rows(tmp_path, LINK_ROWS)}


def test_start_up_on_the_previous_release_migrates_adopts_and_serves_every_file(
    tmp_path: Path,
) -> None:
    """The whole path of an upgrade: rows converted by Alembic, files moved by the start-up step,
    nothing lost, reads working before and after."""
    data = tmp_path / "data"
    build_previous_release(data)
    for content, _, _ in BLOBS.values():
        old = files.legacy_path_for(data, sha(content))
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(content)

    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as client:
        listed = client.get("/api/library/documents").json()["items"]
        assert sorted(d["id"] for d in listed) == [1, 2, 5, 7]
        assert client.get("/api/library/check").json() == {"checked": 4, "problems": []}
        for doc_id, (content, media_type, _) in BLOBS.items():
            got = client.get(f"/api/library/documents/{doc_id}/file")
            assert got.content == content, doc_id
            assert got.headers["content-type"].split(";")[0] in (media_type, "text/plain")
        on_record = client.get("/api/library/links", params={"kind": "tax_record", "id": 1}).json()
        assert [d["document"]["id"] for d in on_record] == [1, 2]
        assert on_record[0]["link"]["label"] == "Invented record 1"
        assert client.get("/api/library/documents/7").json()["links"] == []
    for content, _, _ in BLOBS.values():
        assert files.path_for(data, sha(content)).read_bytes() == content
        assert not files.legacy_path_for(data, sha(content)).exists()
    assert not (data / "documents" / "tax").exists()
    with owner_client(create_app(settings)) as client:  # a second start changes nothing
        assert client.get("/api/library/check").json() == {"checked": 4, "problems": []}
    assert sorted(p.name for p in (data / "documents").rglob("*") if p.is_file()) == sorted(
        sha(c) for c, _, _ in BLOBS.values()
    )
