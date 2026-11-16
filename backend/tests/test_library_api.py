"""The Library through its API against a real temporary SQLite database and the synthetic household
(ADR-0026): add, deduplicate, extract text, link and unlink through the kind registry,
delete only when unlinked, serve a file with the safe headers behind the sign-in, check health,
audit, and survive a restore. Every file here is invented.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.library import files, registry
from finance360.capabilities.library import service as library
from finance360.capabilities.library.registry import Described, LinkKind
from finance360.config import Settings
from finance360.core import backup
from finance360.core.audit import AuditContext
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.migrate import head_revision
from finance360.core.models.library import DocumentLink, LibraryDocument
from finance360.main import create_app
from support import ORIGIN, TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
ALEX = 1
PDF = b"%PDF-1.4\n% invented document, not a real one\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"invented"
RECEIPT = (
    REPO / "fixtures" / "sample_household" / "evidence" / "sample_donation_receipt.txt"
).read_bytes()
SAMPLE_PDF = (
    REPO / "fixtures" / "sample_household" / "documents" / "ato_income_statement_sample.pdf"
).read_bytes()
RECEIPT_NAME = "sample_donation_receipt.txt"
ATO_SAMPLE_NAME = "ato_income_statement_sample.pdf"
CTX = AuditContext("owner", "login:1", "test")


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    shutil.copytree(REPO / "fixtures" / "sample_household" / "evidence", data / "evidence")
    return data


def settings_for(data: Path) -> Settings:
    return Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)


@pytest.fixture
def sample(data_dir: Path) -> Iterator[TestClient]:
    with owner_client(create_app(settings_for(data_dir))) as c:
        yield c


def add(
    client: TestClient,
    content: bytes = PDF,
    *,
    name: str = "scan.pdf",
    media_type: str = "application/pdf",
) -> Any:
    return client.post(
        "/api/library/documents",
        params={"file_name": name},
        content=content,
        headers={"Content-Type": media_type},
    )


def make_record(client: TestClient, description: str = "Invented receipts") -> int:
    res = client.post(
        "/api/tax/records",
        json={
            "member_id": ALEX,
            "record_type": "line",
            "label": "work_other",
            "amount": "10.00",
            "from_on": "2026-01-10",
            "description": description,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def link(client: TestClient, document_id: int, record_id: int) -> Any:
    return client.post(
        "/api/library/links",
        json={"document_id": document_id, "target_kind": "tax_record", "target_id": record_id},
    )


def documents_of(client: TestClient, record_id: int) -> list[dict[str, Any]]:
    res = client.get("/api/library/links", params={"kind": "tax_record", "id": record_id})
    assert res.status_code == 200, res.text
    return res.json()


def audit_of(client: TestClient, entity_type: str) -> list[dict[str, Any]]:
    res = client.get("/api/audit", params={"entity_type": entity_type})
    return res.json()["items"]


def stored_files(data: Path) -> list[Path]:
    return sorted(p for p in (data / "documents").rglob("*") if p.is_file())


def donation_document(client: TestClient) -> dict[str, Any]:
    [item] = [
        d
        for d in client.get("/api/library/documents").json()["items"]
        if d["sha256"] == sha(RECEIPT)
    ]
    return item


# --- add -------------------------------------------------------------------------------------


def test_the_synthetic_household_has_its_receipt_in_the_library_linked_to_a_record(
    sample: TestClient, data_dir: Path
) -> None:
    receipt = donation_document(sample)
    assert (receipt["file_name"], receipt["media_type"], receipt["size_bytes"]) == (
        "sample_donation_receipt.txt",
        "text/plain",
        len(RECEIPT),
    )
    assert (receipt["text_status"], receipt["link_count"]) == ("extracted", 1)
    assert stored_files(data_dir) == [files.path_for(data_dir, sha(RECEIPT))]
    detail = sample.get(f"/api/library/documents/{receipt['id']}").json()
    [where] = detail["links"]
    assert (where["target_kind"], where["kind_title"], where["label"]) == (
        "tax_record",
        "Tax record",
        "Donation to Sample Charity",
    )
    assert where["route"] == f"/tax/records/{where['target_id']}" and where["removed"] is False
    assert sample.get("/api/library/check").json() == {"checked": 1, "problems": []}


def test_a_file_is_added_once_however_often_it_is_sent(sample: TestClient, data_dir: Path) -> None:
    count = len(stored_files(data_dir))
    first = add(sample, name="one.pdf")
    again = add(sample, name="renamed.pdf")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["already_in_library"] is False
    assert again.json()["already_in_library"] is True
    assert again.json()["document"]["id"] == first.json()["document"]["id"]
    assert again.json()["document"]["file_name"] == "one.pdf"  # it keeps the name first given
    assert len(stored_files(data_dir)) == count + 1
    assert len([e for e in audit_of(sample, "library_document") if e["action"] == "create"]) == 2


def test_no_file_bytes_and_no_text_are_in_an_api_response(sample: TestClient) -> None:
    made = add(sample, RECEIPT, name="r.txt", media_type="text/plain").json()
    got = sample.get(f"/api/library/documents/{made['document']['id']}")
    assert "text" not in got.json() and "SAMPLE DONATION RECEIPT" not in got.text
    assert "SAMPLE DONATION RECEIPT" not in sample.get("/api/library/documents").text


def test_no_file_bytes_are_in_the_database(sample: TestClient, data_dir: Path) -> None:
    assert add(sample).status_code == 201
    sample.close()
    for part in data_dir.glob("finance360.sqlite*"):  # the database and its WAL
        raw = part.read_bytes()
        assert PDF not in raw and b"invented document" not in raw


def test_the_limits_refuse_a_bad_file_and_store_nothing(sample: TestClient, data_dir: Path) -> None:
    before = stored_files(data_dir)
    rejected = {
        "empty": add(sample, b""),
        "too big": add(sample, b"a" * (files.MAX_BYTES + 1), media_type="text/plain"),
        "gif": add(sample, b"GIF89a", media_type="image/gif"),
        "no type": add(sample, PDF, media_type="application/octet-stream"),
        "html": add(sample, b"<script></script>", media_type="text/html"),
        "not a pdf": add(sample, b"plain words", media_type="application/pdf"),
    }
    for label, res in rejected.items():
        assert res.status_code == 422, label
        [problem] = res.json()["errors"]  # the shared validation shape
        assert problem["field"] == "file" and problem["message"], label
    assert "larger than 10 MB" in rejected["too big"].json()["errors"][0]["message"]
    assert "PDF, PNG image, JPEG image, text, CSV" in rejected["gif"].json()["errors"][0]["message"]
    assert stored_files(data_dir) == before
    assert add(sample, b"a" * files.MAX_BYTES, media_type="text/plain").status_code == 201


def test_a_hostile_file_name_cannot_choose_where_the_file_goes(
    sample: TestClient, data_dir: Path
) -> None:
    before = {p for p in data_dir.parent.rglob("*") if p.is_file()}
    for i, name in enumerate(["../../../escape.txt", "..\\..\\escape2.txt", "/abs/escape3.txt"]):
        res = add(sample, f"body {i}".encode(), name=name, media_type="text/plain")
        assert res.status_code == 201, res.text
        stored = res.json()["document"]
        assert "/" not in stored["file_name"] and "\\" not in stored["file_name"]
        assert files.path_for(data_dir, stored["sha256"]).read_bytes() == f"body {i}".encode()
    added = {p for p in data_dir.parent.rglob("*") if p.is_file()} - before
    library_dir = (data_dir / "documents" / "library").resolve()
    assert all(
        p.resolve().is_relative_to(library_dir) or p.name.startswith("finance360") for p in added
    )
    assert not (data_dir.parent / "escape.txt").exists()


# --- text extraction -------------------------------------------------------------------------


def test_text_is_taken_from_a_pdf_text_layer_and_a_text_file(
    sample: TestClient, data_dir: Path
) -> None:
    pdf = add(sample, SAMPLE_PDF, name="statement.pdf").json()["document"]
    txt = add(sample, b"Invented words about a rental.", name="n.txt", media_type="text/plain")
    csv = add(sample, b"a,b\n1,2\n", name="n.csv", media_type="text/csv")
    assert pdf["text_status"] == "extracted"
    assert txt.json()["document"]["text_status"] == csv.json()["document"]["text_status"]
    sample.close()
    engine = make_engine(db_path(data_dir))
    try:
        with make_session_factory(engine)() as session:
            stored = session.get(LibraryDocument, pdf["id"])
            assert stored is not None and stored.text is not None
            assert "Gross" in stored.text  # the income statement's own words, from page one
            note = session.scalars(
                select(LibraryDocument).where(
                    LibraryDocument.sha256 == sha(b"Invented words about a rental.")
                )
            ).one()
            assert note.text == "Invented words about a rental."
    finally:
        engine.dispose()


def test_an_image_a_scan_and_an_oversized_text_get_no_text_and_say_why(
    sample: TestClient,
) -> None:
    from test_documents_loader import minimal_pdf

    from finance360.capabilities.documents import loader

    image = add(sample, PNG, name="p.png", media_type="image/png").json()["document"]
    scan = add(sample, minimal_pdf(1), name="scan.pdf").json()["document"]
    long_pdf = add(sample, minimal_pdf(21), name="long.pdf").json()["document"]
    huge = add(
        sample, b"word " * (loader.MAX_TEXT // 4), name="huge.txt", media_type="text/plain"
    ).json()["document"]
    broken = add(sample, b"%PDF-1.4\nnot really a pdf", name="broken.pdf").json()["document"]
    assert [d["text_status"] for d in (image, scan, long_pdf, huge, broken)] == [
        "none",
        "none",
        "too_large",
        "too_large",
        "failed",
    ]


def test_extracting_text_never_logs_what_the_document_says(
    sample: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    secret = b"INVENTED-SECRET-PHRASE-7731"
    with caplog.at_level(logging.DEBUG):
        add(sample, secret, name="s.txt", media_type="text/plain")
        add(sample, SAMPLE_PDF, name="statement.pdf")
        add(sample, b"%PDF-1.4\n" + secret, name="broken.pdf")
    assert secret.decode() not in caplog.text


# --- links -----------------------------------------------------------------------------------


def test_a_document_links_to_a_record_lists_there_and_unlinks(sample: TestClient) -> None:
    record = make_record(sample)
    doc = add(sample).json()["document"]
    made = link(sample, doc["id"], record)
    assert made.status_code == 201, made.text
    item = made.json()
    assert (item["target_kind"], item["target_id"], item["label"]) == (
        "tax_record",
        record,
        "Invented receipts",
    )
    [listed] = documents_of(sample, record)
    assert listed["link"]["id"] == item["id"] and listed["document"]["id"] == doc["id"]
    assert listed["document"]["link_count"] == 1
    assert link(sample, doc["id"], record).json()["id"] == item["id"]  # again: the same link
    assert len([e for e in audit_of(sample, "document_link") if e["action"] == "create"]) == 2

    assert sample.delete(f"/api/library/links/{item['id']}").status_code == 204
    assert documents_of(sample, record) == []
    assert sample.get(f"/api/library/documents/{doc['id']}").json()["link_count"] == 0
    assert sample.get(f"/api/library/documents/{doc['id']}/file").status_code == 200  # it stays


def test_one_document_serves_many_records_and_a_record_has_many_documents(
    sample: TestClient,
) -> None:
    first, second = make_record(sample, "First"), make_record(sample, "Second")
    one, two = (
        add(sample).json()["document"],
        add(sample, PNG, media_type="image/png").json()["document"],
    )
    for document, record in ((one, first), (one, second), (two, first)):
        assert link(sample, document["id"], record).status_code == 201
    assert [d["document"]["id"] for d in documents_of(sample, first)] == [one["id"], two["id"]]
    assert [d["document"]["id"] for d in documents_of(sample, second)] == [one["id"]]
    detail = sample.get(f"/api/library/documents/{one['id']}").json()
    assert [(x["label"], x["kind_title"]) for x in detail["links"]] == [
        ("First", "Tax record"),
        ("Second", "Tax record"),
    ]
    assert sample.get("/api/library/documents").json()["items"][0]["link_count"] in (1, 2)


def test_a_link_to_a_missing_record_or_an_unknown_kind_or_document_is_refused(
    sample: TestClient,
) -> None:
    doc = add(sample).json()["document"]
    assert link(sample, doc["id"], 9999).status_code == 404
    assert link(sample, 9999, make_record(sample)).status_code == 404
    unknown = sample.post(
        "/api/library/links",
        json={"document_id": doc["id"], "target_kind": "spaceship", "target_id": 1},
    )
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["field"] == "target_kind"
    assert (
        sample.get("/api/library/links", params={"kind": "spaceship", "id": 1}).status_code == 422
    )
    assert sample.delete("/api/library/links/9999").status_code == 404
    assert sample.get("/api/library/documents/9999").status_code == 404
    assert sample.get("/api/library/documents/9999/file").status_code == 404
    assert sample.get(f"/api/library/documents/{doc['id']}").json()["link_count"] == 0


def test_a_document_is_deleted_only_when_unlinked_and_its_file_goes(
    sample: TestClient, data_dir: Path
) -> None:
    record = make_record(sample)
    doc = add(sample).json()["document"]
    item = link(sample, doc["id"], record).json()
    refused = sample.delete(f"/api/library/documents/{doc['id']}")
    assert refused.status_code == 409 and "linked to 1 record" in refused.json()["message"]
    assert files.path_for(data_dir, doc["sha256"]).read_bytes() == PDF

    sample.delete(f"/api/library/links/{item['id']}")
    assert sample.delete(f"/api/library/documents/{doc['id']}").status_code == 204
    assert not files.path_for(data_dir, doc["sha256"]).exists()
    assert sample.get(f"/api/library/documents/{doc['id']}").status_code == 404
    [event, *_] = audit_of(sample, "library_document")
    assert (event["action"], event["summary"]) == ("delete", "Deleted 'scan.pdf' from the Library")
    assert sample.delete(f"/api/library/documents/{doc['id']}").status_code == 404
    # the same bytes can be added again
    assert add(sample).status_code == 201 and files.path_for(data_dir, doc["sha256"]).exists()


def test_removing_a_tax_record_drops_its_links_and_keeps_the_document(
    sample: TestClient, data_dir: Path
) -> None:
    record = make_record(sample)
    doc = add(sample).json()["document"]
    link(sample, doc["id"], record)
    assert sample.delete(f"/api/tax/records/{record}").status_code == 204
    still = sample.get(f"/api/library/documents/{doc['id']}").json()
    assert still["link_count"] == 0 and still["links"] == []
    assert files.path_for(data_dir, doc["sha256"]).read_bytes() == PDF
    deleted = audit_of(sample, "tax_record")[0]
    assert deleted["action"] == "delete"
    shown = {c["field"]: c["before"] for c in deleted["changes"]}
    assert shown["Evidence"] == f"scan.pdf ({doc['sha256'][:8]})"
    # a record that is deleted is not 'linked to': the document can now be deleted
    assert sample.delete(f"/api/library/documents/{doc['id']}").status_code == 204


def test_linking_and_unlinking_are_audited_with_the_document_and_the_record_named(
    sample: TestClient,
) -> None:
    record = make_record(sample)
    doc = add(sample).json()["document"]
    item = link(sample, doc["id"], record).json()
    sample.delete(f"/api/library/links/{item['id']}")
    unlinked, linked = audit_of(sample, "document_link")[:2]
    assert linked["action"] == "create" and unlinked["action"] == "delete"
    assert linked["summary"] == "Linked 'scan.pdf' to Tax record 'Invented receipts'"
    assert linked["changes"] == [
        {"field": "Document", "before": None, "after": f"scan.pdf ({doc['sha256'][:8]})"},
        {"field": "Target", "before": None, "after": f"tax_record {record}"},
    ]
    assert unlinked["summary"] == f"Unlinked 'scan.pdf' from Tax record {record}"


# --- the kind registry -----------------------------------------------------------------------


def stand_in_kind(known: set[int], lineage: dict[int, list[int]], removed: set[int]) -> LinkKind:
    def describe(_s: Session, ids: Sequence[int]) -> dict[int, Described]:
        return {
            i: Described(f"Thing {i}", None if i in removed else f"/things/{i}", i in removed)
            for i in ids
        }

    return LinkKind(
        key="thing",
        title="Thing",
        exists=lambda _s, i: i in known,
        lineage=lambda _s, i: lineage.get(i, [i]),
        describe=describe,
        candidates=lambda _s, text: [(i, f"Thing {i}") for i in sorted(known) if text in str(i)],
    )


@pytest.fixture
def things() -> Iterator[None]:
    registry.register(stand_in_kind({1, 2, 3}, {1: [1, 2], 2: [1, 2], 3: [3]}, {3}))
    yield
    registry.unregister("thing")


def test_a_link_is_validated_by_the_registered_kind_and_follows_its_lineage(
    data_dir: Path, things: None
) -> None:
    from finance360.core.migrate import migrate

    migrate(data_dir)
    engine = make_engine(db_path(data_dir))
    try:
        with make_session_factory(engine).begin() as s:
            doc = library.add_document(
                s, CTX, data_dir, file_name="a.pdf", media_type="application/pdf", content=PDF
            ).document
            with pytest.raises(NotFoundError, match="Thing 9 does not exist"):
                library.link(s, CTX, doc.id, "thing", 9)
            with pytest.raises(ValidationError, match="'unregistered'"):
                library.link(s, CTX, doc.id, "unregistered", 1)
            library.link(s, CTX, doc.id, "thing", 1)
            other = library.add_document(
                s, CTX, data_dir, file_name="b.png", media_type="image/png", content=PNG
            ).document
            library.link(s, CTX, other.id, "thing", 2)
            library.link(s, CTX, other.id, "thing", 3)
            # a record's documents are those linked to any version of it
            for asked in (1, 2):
                assert [d.document.id for d in library.documents_of(s, "thing", asked)] == [
                    doc.id,
                    other.id,
                ]
            assert [d.document.id for d in library.documents_of(s, "thing", 3)] == [other.id]
            # a removed record keeps its link and says so
            info = {i.link.target_id: i for i in library.links_of_document(s, other.id)}
            assert (info[2].label, info[2].route, info[2].removed) == (
                "Thing 2",
                "/things/2",
                False,
            )
            assert (info[3].route, info[3].removed) == (None, True)
    finally:
        engine.dispose()


def test_a_link_whose_kind_is_no_longer_registered_still_shows_and_can_be_unlinked(
    data_dir: Path,
) -> None:
    from finance360.core.migrate import migrate

    migrate(data_dir)
    engine = make_engine(db_path(data_dir))
    registry.register(stand_in_kind({1}, {}, set()))
    try:
        with make_session_factory(engine).begin() as s:
            doc = library.add_document(
                s, CTX, data_dir, file_name="a.pdf", media_type="application/pdf", content=PDF
            ).document
            row = library.link(s, CTX, doc.id, "thing", 1)
            registry.unregister("thing")
            [info] = library.links_of_document(s, doc.id)
            assert (info.kind_title, info.label, info.route) == ("thing", "thing 1", None)
            library.unlink(s, CTX, row.id)
            assert library.links_of_document(s, doc.id) == []
    finally:
        registry.unregister("thing")
        engine.dispose()


def test_registering_a_different_kind_under_a_taken_key_is_an_error(things: None) -> None:
    with pytest.raises(ValueError, match="already registered"):
        registry.register(stand_in_kind({9}, {}, set()))
    registry.register(registry.get("thing"))  # type: ignore[arg-type]  # the same kind: fine


def test_the_tax_record_kind_finds_records_to_link(sample: TestClient, data_dir: Path) -> None:
    sample.close()
    engine = make_engine(db_path(data_dir))
    try:
        with make_session_factory(engine)() as s:
            kind = registry.get("tax_record")
            assert kind is not None
            found = kind.candidates(s, "Donation")
            assert [label for _, label in found] == ["Donation to Sample Charity"]
            assert kind.candidates(s, "100%") == []  # a wildcard is just text
            assert len(kind.candidates(s, "")) <= 20
    finally:
        engine.dispose()


# --- the file route --------------------------------------------------------------------------


def test_the_file_route_serves_the_stored_type_with_the_safe_headers(sample: TestClient) -> None:
    doc = add(sample, name='sc"an\r\n.pdf').json()["document"]
    got = sample.get(f"/api/library/documents/{doc['id']}/file")
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["cross-origin-resource-policy"] == "same-origin"
    csp = got.headers["content-security-policy"]
    assert csp.startswith("default-src 'none'") and "frame-ancestors 'self'" in csp
    assert "sandbox" not in csp
    assert got.headers["x-frame-options"] == "SAMEORIGIN"
    disposition = got.headers["content-disposition"]
    assert disposition.startswith("inline;") and "\r" not in disposition and "\n" not in disposition
    download = sample.get(f"/api/library/documents/{doc['id']}/file", params={"download": "true"})
    assert download.headers["content-disposition"].startswith("attachment;")


def test_text_and_csv_are_served_as_plain_text_and_an_image_as_its_own_type(
    sample: TestClient,
) -> None:
    csv = add(sample, b"a,b\n1,2\n", name="n.csv", media_type="text/csv").json()["document"]
    txt = add(sample, b"<script>alert(1)</script>", name="n.txt", media_type="text/plain").json()[
        "document"
    ]
    png = add(sample, PNG, name="p.png", media_type="image/png").json()["document"]
    types = {
        d["file_name"]: sample.get(f"/api/library/documents/{d['id']}/file").headers["content-type"]
        for d in (csv, txt, png)
    }
    assert types == {
        "n.csv": "text/plain; charset=utf-8",
        "n.txt": "text/plain; charset=utf-8",
        "p.png": "image/png",
    }


def test_the_file_route_needs_a_sign_in(data_dir: Path) -> None:
    with owner_client(create_app(settings_for(data_dir))) as signed_in:
        doc = add(signed_in).json()["document"]
    with TestClient(create_app(settings_for(data_dir)), headers=ORIGIN) as anonymous:
        for path in (
            f"/api/library/documents/{doc['id']}/file",
            f"/api/library/documents/{doc['id']}",
            "/api/library/documents",
            "/api/library/check",
        ):
            assert anonymous.get(path).status_code == 401, path
        assert anonymous.post("/api/library/documents", content=PDF).status_code == 401


def test_a_file_that_fails_its_check_is_not_served_and_the_message_says_what_to_do(
    sample: TestClient, data_dir: Path
) -> None:
    record = make_record(sample)
    gone = add(sample, PDF).json()["document"]
    changed = donation_document(sample)
    link(sample, gone["id"], record)
    assert sample.get("/api/library/check").json() == {"checked": 2, "problems": []}
    files.path_for(data_dir, gone["sha256"]).unlink()
    files.path_for(data_dir, changed["sha256"]).write_bytes(RECEIPT + b"tampered")

    result = sample.get("/api/library/check").json()
    assert result["checked"] == 2
    by_name = {p["file_name"]: p for p in result["problems"]}
    assert by_name["scan.pdf"]["problem"] == "missing"
    assert by_name["scan.pdf"]["links"] == [{"target_kind": "tax_record", "target_id": record}]
    assert by_name["sample_donation_receipt.txt"]["problem"] == "changed"
    miss = sample.get(f"/api/library/documents/{gone['id']}/file")
    assert miss.status_code == 409 and "missing" in miss.json()["message"]
    bad = sample.get(f"/api/library/documents/{changed['id']}/file")
    assert bad.status_code == 409 and "changed" in bad.json()["message"]
    # adding the same bytes again puts a deleted file back; a changed one is never overwritten
    assert add(sample, PDF).status_code == 200
    assert files.path_for(data_dir, gone["sha256"]).read_bytes() == PDF
    assert add(sample, RECEIPT, media_type="text/plain").status_code == 200
    assert files.path_for(data_dir, changed["sha256"]).read_bytes().endswith(b"tampered")


# --- before the files have moved ---------------------------------------------------------------


def test_a_file_still_under_documents_tax_is_served_checked_and_then_adopted_at_start_up(
    data_dir: Path,
) -> None:
    with owner_client(create_app(settings_for(data_dir))) as first:
        doc = add(first, PDF).json()["document"]
    # put the file back where the previous release kept it
    new = files.path_for(data_dir, doc["sha256"])
    old = files.legacy_path_for(data_dir, doc["sha256"])
    old.parent.mkdir(parents=True)
    shutil.move(new, old)
    receipt_old = files.legacy_path_for(data_dir, sha(RECEIPT))
    receipt_old.parent.mkdir(parents=True)
    shutil.move(files.path_for(data_dir, sha(RECEIPT)), receipt_old)

    # read it straight from the service (no start-up): the fallback serves and checks it
    engine = make_engine(db_path(data_dir))
    try:
        with make_session_factory(engine)() as s:
            assert library.document_file(s, data_dir, doc["id"])[1] == PDF
            assert library.check(s, data_dir).problems == ()
    finally:
        engine.dispose()
    assert old.exists() and not new.exists()

    with owner_client(create_app(settings_for(data_dir))) as second:  # start-up adopts
        assert second.get(f"/api/library/documents/{doc['id']}/file").content == PDF
        assert second.get("/api/library/check").json()["problems"] == []
    assert new.read_bytes() == PDF and not old.exists()
    assert files.path_for(data_dir, sha(RECEIPT)).read_bytes() == RECEIPT
    assert not (data_dir / "documents" / "tax").exists()


def test_start_up_reports_a_missing_file_and_does_not_fail_or_drop_the_row(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with owner_client(create_app(settings_for(data_dir))) as first:
        doc = add(first, PDF).json()["document"]
    files.path_for(data_dir, doc["sha256"]).unlink()
    with caplog.at_level(logging.WARNING), owner_client(create_app(settings_for(data_dir))) as c:
        problems = c.get("/api/library/check").json()["problems"]
        assert [(p["document_id"], p["problem"]) for p in problems] == [(doc["id"], "missing")]
        assert c.get(f"/api/library/documents/{doc['id']}").status_code == 200  # the row is kept
    assert doc["sha256"][:12] in caplog.text


# --- restore -----------------------------------------------------------------------------------


def test_documents_library_is_inside_what_a_backup_carries_and_a_restore_returns_it(
    sample: TestClient, data_dir: Path, tmp_path: Path
) -> None:
    """A Backup holds the database and ``documents/`` from one moment (ADR-0011): the staged set
    names the whole folder, so ``documents/library/`` is in it, and moving it in with the existing
    restore code returns files and rows that agree. The target had other files of its own, which
    the restore replaces on both sides."""
    record = make_record(sample)
    kept = add(sample).json()["document"]
    link(sample, kept["id"], record)
    sample.close()

    staged_set = backup._stage(
        settings_for(data_dir), "manual", datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    )
    documents = data_dir / "documents"
    assert documents in staged_set  # the whole folder, not a list of files
    assert files.path_for(data_dir, kept["sha256"]).is_relative_to(documents)
    manifest = (staged_set[1]).read_text(encoding="utf-8")
    assert '"documents/"' in manifest

    staged = tmp_path / "staged"
    staged.mkdir()
    source, copy = sqlite3.connect(db_path(data_dir)), sqlite3.connect(staged / "finance360.sqlite")
    with copy:
        source.backup(copy)  # a consistent copy, as the Backup takes
    source.close()
    copy.close()
    shutil.copytree(data_dir / "documents", staged / "documents")

    target = tmp_path / "target"
    (target / "documents" / "library" / "zz").mkdir(parents=True)
    (target / "documents" / "library" / "zz" / "stray").write_bytes(b"not in the backup")
    (target / "finance360.sqlite").write_bytes(b"a different database")
    restored = backup._Restored(
        db=staged / "finance360.sqlite",
        documents=staged / "documents",
        snapshot_id="0" * 64,
        manifest={},
        from_revision=head_revision(),
        to_revision=head_revision(),
    )
    backup._swap(target, restored, "20260310T120000Z")

    engine = make_engine(db_path(target))
    try:
        with make_session_factory(engine)() as session:
            result = library.check(session, target)
            hashes = {d.sha256 for d in session.scalars(select(LibraryDocument))}
            links = session.scalars(select(DocumentLink)).all()
    finally:
        engine.dispose()
    assert result.checked == 2 and result.problems == ()
    assert hashes == {sha(RECEIPT), kept["sha256"]}
    on_disk = {p.name for p in (target / "documents" / "library").rglob("*") if p.is_file()}
    assert on_disk == hashes  # every file is referenced and every reference has its file
    assert files.read(target, kept["sha256"]) == PDF
    assert len(links) == 2 and (kept["id"], record) in {(k.document_id, k.target_id) for k in links}


# --- the list: search, filters, paging ------------------------------------------


def listed(client: TestClient, **params: Any) -> dict[str, Any]:
    res = client.get("/api/library/documents", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def names(page: dict[str, Any]) -> list[str]:
    return [d["file_name"] for d in page["items"]]


@pytest.fixture
def library_of_four(sample: TestClient) -> TestClient:
    """The household's receipt (linked), plus a text note, a CSV and a PDF with no links."""
    add(sample, b"Invented words about a rental.", name="rental_note.txt", media_type="text/plain")
    add(sample, b"a,b\n1,2\n", name="figures.csv", media_type="text/csv")
    add(sample, SAMPLE_PDF, name="statement.pdf")
    return sample


def test_the_list_is_a_page_with_a_total_the_latest_added_first(
    library_of_four: TestClient,
) -> None:
    page = listed(library_of_four)
    assert page["total"] == 4 and (page["limit"], page["offset"]) == (50, 0)
    assert names(page) == ["statement.pdf", "figures.csv", "rental_note.txt", RECEIPT_NAME]
    assert [d["link_count"] for d in page["items"]] == [0, 0, 0, 1]


def test_search_matches_the_file_name_and_the_extracted_text_without_regard_to_case(
    library_of_four: TestClient,
) -> None:
    assert names(listed(library_of_four, q="RENTAL")) == ["rental_note.txt"]  # name and text
    assert names(listed(library_of_four, q="a rental")) == ["rental_note.txt"]  # text only
    assert names(listed(library_of_four, q="charity")) == [RECEIPT_NAME]  # text only
    assert names(listed(library_of_four, q="figures")) == ["figures.csv"]  # name only
    assert listed(library_of_four, q="no such words")["total"] == 0
    assert listed(library_of_four, q="   ")["total"] == 4  # blank is no search


def test_search_takes_percent_and_underscore_literally(library_of_four: TestClient) -> None:
    add(library_of_four, b"Interest of 5% a year.", name="rate_note.txt", media_type="text/plain")
    assert names(listed(library_of_four, q="5%")) == ["rate_note.txt"]
    assert names(listed(library_of_four, q="%")) == ["rate_note.txt"]  # not "everything"
    assert names(listed(library_of_four, q="rate_")) == ["rate_note.txt"]
    assert listed(library_of_four, q="rat_ note")["total"] == 0  # '_' is not "any one letter"


def test_each_filter_narrows_and_they_combine(library_of_four: TestClient) -> None:
    assert names(listed(library_of_four, media_type="text/csv")) == ["figures.csv"]
    assert sorted(names(listed(library_of_four, media_type="text/plain"))) == [
        "rental_note.txt",
        RECEIPT_NAME,
    ]
    assert names(listed(library_of_four, linked_kind="tax_record")) == [RECEIPT_NAME]
    assert listed(library_of_four, linked_kind="account")["total"] == 0
    assert names(listed(library_of_four, unlinked=True)) == [
        "statement.pdf",
        "figures.csv",
        "rental_note.txt",
    ]
    both = listed(library_of_four, unlinked=True, media_type="text/plain", q="rental")
    assert names(both) == ["rental_note.txt"]


def test_unlinking_and_linking_move_a_document_between_the_filters(
    library_of_four: TestClient,
) -> None:
    note = listed(library_of_four, q="rental")["items"][0]
    record = make_record(library_of_four)
    assert link(library_of_four, note["id"], record).status_code == 201
    assert "rental_note.txt" in names(listed(library_of_four, linked_kind="tax_record"))
    assert "rental_note.txt" not in names(listed(library_of_four, unlinked=True))
    link_id = documents_of(library_of_four, record)[0]["link"]["id"]
    assert library_of_four.delete(f"/api/library/links/{link_id}").status_code == 204
    assert "rental_note.txt" in names(listed(library_of_four, unlinked=True))


def test_asking_for_unlinked_and_a_linked_kind_together_is_refused(
    library_of_four: TestClient,
) -> None:
    res = library_of_four.get(
        "/api/library/documents", params={"unlinked": True, "linked_kind": "tax_record"}
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "unlinked"


def test_paging_keeps_the_total_and_the_order(library_of_four: TestClient) -> None:
    first = listed(library_of_four, limit=3)
    second = listed(library_of_four, limit=3, offset=3)
    assert (first["total"], second["total"]) == (4, 4)
    assert names(first) + names(second) == [
        "statement.pdf",
        "figures.csv",
        "rental_note.txt",
        RECEIPT_NAME,
    ]
    assert len(second["items"]) == 1
    assert listed(library_of_four, limit=3, offset=9)["items"] == []
    assert library_of_four.get("/api/library/documents", params={"limit": 0}).status_code == 422
    assert library_of_four.get("/api/library/documents", params={"limit": 500}).status_code == 422


def test_the_kinds_a_document_can_be_linked_to_are_listed_with_their_titles(
    sample: TestClient,
) -> None:
    assert sample.get("/api/library/kinds").json() == [
        {"key": "account", "title": "Account"},
        {"key": "super_statement", "title": "Super statement"},
        {"key": "tax_record", "title": "Tax record"},
    ]


def test_the_pre_library_tax_evidence_routes_are_gone(sample: TestClient, data_dir: Path) -> None:
    record = make_record(sample)
    for method, path in [
        ("get", f"/api/tax/records/{record}/evidence"),
        ("post", f"/api/tax/records/{record}/evidence"),
        ("delete", f"/api/tax/records/{record}/evidence/1"),
        ("get", "/api/tax/evidence/check"),
        ("get", "/api/tax/evidence/1/file"),
    ]:
        assert sample.request(method, path).status_code in (404, 405), (method, path)
    schema_paths = create_app(settings_for(data_dir)).openapi()["paths"]
    assert not [p for p in schema_paths if "evidence" in p]


def test_the_fixture_adds_an_unlinked_document_and_keeps_the_receipt_linked(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    shutil.copytree(REPO / "fixtures" / "sample_household", data)
    with owner_client(create_app(settings_for(data))) as client:
        page = listed(client)
        # The sample ATO statement is the test document of the fixture's Proposed profile.
        assert sorted(names(page)) == [
            ATO_SAMPLE_NAME,
            RECEIPT_NAME,
            "sample_savings_statement.txt",
        ]
        assert sorted(names(listed(client, unlinked=True))) == [
            ATO_SAMPLE_NAME,
            "sample_savings_statement.txt",
        ]
        assert names(listed(client, linked_kind="tax_record")) == [RECEIPT_NAME]
        assert names(listed(client, q="interest paid")) == ["sample_savings_statement.txt"]
        assert client.get("/api/library/check").json() == {"checked": 3, "problems": []}
    with owner_client(create_app(settings_for(data))) as again:  # a restart adds nothing
        assert listed(again)["total"] == 3


def test_the_check_can_be_narrowed_to_one_records_documents(
    sample: TestClient, data_dir: Path
) -> None:
    record = make_record(sample)
    item = add(sample).json()["document"]
    assert link(sample, item["id"], record).status_code == 201
    files.path_for(data_dir, item["sha256"]).unlink()
    everything = sample.get("/api/library/check").json()
    assert everything["checked"] == 2 and [p["problem"] for p in everything["problems"]] == [
        "missing"
    ]
    narrowed = sample.get("/api/library/check", params={"kind": "tax_record", "id": record}).json()
    assert narrowed["checked"] == 1 and narrowed["problems"][0]["document_id"] == item["id"]
    donation = donation_document(sample)["id"]
    [where] = sample.get(f"/api/library/documents/{donation}").json()["links"]
    other = sample.get(
        "/api/library/check", params={"kind": "tax_record", "id": where["target_id"]}
    ).json()
    assert other == {"checked": 1, "problems": []}  # the other record's file is fine
