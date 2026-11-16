"""Library writes over a real server (uvicorn, a random port) with a real sign-in and a stale
session, against the sample household. TestClient with an overridden principal hides
what this catches: the cookie, the origin check, commit-before-response, a session whose
``last_seen_at`` needs refreshing, and the headers a browser actually receives on the file route.
The same file added at once from two requests must be one document, one file and one audit event.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from test_scenarios_real_server import signed_in  # noqa: F401 - the shared real-server fixture

PDF = b"%PDF-1.4\n% invented library document, not a real one\n"


def test_documents_are_added_linked_and_served_over_a_real_server_and_a_race_makes_one(
    signed_in: httpx.Client,  # noqa: F811
    tmp_path: Path,
) -> None:
    http = signed_in
    digest = hashlib.sha256(PDF).hexdigest()
    record = http.post(
        "/api/tax/records",
        json={
            "member_id": 1,
            "record_type": "line",
            "label": "work_other",
            "amount": "10.00",
            "from_on": "2026-01-10",
            "description": "Library real server",
        },
    )
    assert record.status_code == 201, record.text
    record_id = record.json()["id"]

    def send(name: str) -> httpx.Response:
        return http.post(
            "/api/library/documents",
            params={"file_name": name},
            content=PDF,
            headers={"Content-Type": "application/pdf"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(send, ["a.pdf", "b.pdf"])
    assert sorted([first.status_code, second.status_code]) == [200, 201], (first.text, second.text)
    assert first.json()["document"]["id"] == second.json()["document"]["id"]  # one row
    doc_id = first.json()["document"]["id"]
    assert [d["sha256"] for d in http.get("/api/library/documents").json()["items"]].count(
        digest
    ) == 1
    stored = [p.name for p in (tmp_path / "data" / "documents").rglob("*") if p.is_file()]
    assert stored.count(digest) == 1  # and one file

    def link() -> httpx.Response:
        return http.post(
            "/api/library/links",
            json={"document_id": doc_id, "target_kind": "tax_record", "target_id": record_id},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        racing = list(pool.map(lambda _: link(), range(2)))
    assert [r.status_code for r in racing] == [201, 201], [r.text for r in racing]
    assert racing[0].json()["id"] == racing[1].json()["id"]  # one link

    # committed before the response returned: read back at once
    [listed] = http.get("/api/library/links", params={"kind": "tax_record", "id": record_id}).json()
    assert listed["document"]["id"] == doc_id and listed["document"]["link_count"] == 1
    trail = http.get("/api/audit", params={"entity_type": "library_document"}).json()["items"]
    assert len([e for e in trail if e["action"] == "create"]) == 1  # one event for one document

    got = http.get(f"/api/library/documents/{doc_id}/file")
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["cross-origin-resource-policy"] == "same-origin"
    assert "default-src 'none'" in got.headers["content-security-policy"]
    assert got.headers["content-disposition"].startswith("inline;")

    assert http.delete(f"/api/library/documents/{doc_id}").status_code == 409  # still linked
    assert http.delete(f"/api/library/links/{racing[0].json()['id']}").status_code == 204
    assert http.delete(f"/api/library/documents/{doc_id}").status_code == 204
    assert http.get(f"/api/library/documents/{doc_id}").status_code == 404
    assert not [p for p in (tmp_path / "data" / "documents").rglob("*") if p.name == digest]
