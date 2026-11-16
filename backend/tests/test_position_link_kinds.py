"""Documents on Accounts and Super statements (ADR-0026 slice 4).

The ``account`` and ``super_statement`` Link kinds, against a real temporary SQLite database and the
synthetic household: a document links to an Account and to a Super statement; a corrected statement
keeps its documents (lineage both ways); a removed statement keeps its link, shown as removed; and
an address saved before a correction still opens the statement as it stands. Files are invented.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from finance360.capabilities.library import registry
from finance360.config import Settings
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
FY = 2026
PDF = b"%PDF-1.4\n% invented statement, not a real one\n"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    shutil.copytree(REPO / "fixtures" / "sample_household" / "evidence", data / "evidence")
    return data


@pytest.fixture
def sample(data_dir: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=data_dir, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def super_account(client: TestClient) -> int:
    [account] = [a for a in client.get("/api/accounts").json() if a["name"] == "Sample Super Fund"]
    return int(account["id"])


def statement_of(client: TestClient, account_id: int, fy: int = FY) -> dict[str, Any]:
    [row] = [
        s
        for s in client.get(f"/api/accounts/{account_id}/super-statements").json()
        if s["financial_year"] == fy
    ]
    return dict(row)


def add_document(client: TestClient, name: str = "statement.pdf", content: bytes = PDF) -> int:
    res = client.post(
        "/api/library/documents",
        params={"file_name": name},
        content=content,
        headers={"Content-Type": "application/pdf"},
    )
    assert res.status_code in (200, 201), res.text
    return int(res.json()["document"]["id"])


def link(client: TestClient, document_id: int, kind: str, target_id: int) -> dict[str, Any]:
    res = client.post(
        "/api/library/links",
        json={"document_id": document_id, "target_kind": kind, "target_id": target_id},
    )
    assert res.status_code == 201, res.text
    return dict(res.json())


def documents_of(client: TestClient, kind: str, target_id: int) -> list[dict[str, Any]]:
    res = client.get("/api/library/links", params={"kind": kind, "id": target_id})
    assert res.status_code == 200, res.text
    return list(res.json())


def correct(client: TestClient, statement: dict[str, Any], **changes: str | None) -> dict[str, Any]:
    fields = (
        "period_start period_end opening contributions_in investment_returns withdrawals closing "
        "contributions_tax fees_before_rebate rebate fees_inside_investment "
        "division_293_released insurance_premiums non_concessional_received concessional_received"
    ).split()
    body: dict[str, Any] = {k: statement[k] for k in fields}
    body["options"] = []
    body.update(changes)
    res = client.put(f"/api/super-statements/{statement['id']}", json=body)
    assert res.status_code == 200, res.text
    return dict(res.json())


def test_both_kinds_are_registered_with_their_titles(sample: TestClient) -> None:
    kinds = {k["key"]: k["title"] for k in sample.get("/api/library/kinds").json()}
    assert kinds["account"] == "Account"
    assert kinds["super_statement"] == "Super statement"


def test_a_document_links_to_an_account_and_describes_it_with_its_page(
    sample: TestClient,
) -> None:
    account_id = super_account(sample)
    doc = add_document(sample)
    made = link(sample, doc, "account", account_id)
    assert (made["target_kind"], made["label"], made["route"]) == (
        "account",
        "Sample Super Fund",
        f"/accounts/{account_id}",
    )
    [listed] = documents_of(sample, "account", account_id)
    assert listed["document"]["id"] == doc
    assert listed["link"]["kind_title"] == "Account"


def test_a_document_links_to_a_super_statement_and_describes_it_with_its_view(
    sample: TestClient,
) -> None:
    account_id = super_account(sample)
    statement = statement_of(sample, account_id)
    doc = add_document(sample)
    made = link(sample, doc, "super_statement", statement["id"])
    assert made["label"] == "Super statement FY 2025-26, Sample Super Fund"
    assert made["route"] == f"/accounts/{account_id}/super-statements/{statement['id']}"
    assert made["removed"] is False
    assert [
        d["document"]["id"] for d in documents_of(sample, "super_statement", statement["id"])
    ] == [doc]


def test_a_link_to_a_missing_account_or_statement_is_refused(sample: TestClient) -> None:
    doc = add_document(sample)
    for kind in ("account", "super_statement"):
        res = sample.post(
            "/api/library/links",
            json={"document_id": doc, "target_kind": kind, "target_id": 99999},
        )
        assert res.status_code == 404, res.text


def test_a_corrected_statement_keeps_its_documents_and_new_ones_show_on_every_version(
    sample: TestClient,
) -> None:
    account_id = super_account(sample)
    old = statement_of(sample, account_id)
    before = add_document(sample, "before.pdf", PDF + b"a")
    link(sample, before, "super_statement", old["id"])

    new = correct(sample, old, concessional_received="14500.00")
    assert new["id"] != old["id"]
    # The document linked before the correction is on the corrected statement, and still on the
    # old version's address.
    assert [d["document"]["id"] for d in documents_of(sample, "super_statement", new["id"])] == [
        before
    ]
    assert [d["document"]["id"] for d in documents_of(sample, "super_statement", old["id"])] == [
        before
    ]
    # One attached to the current version shows from the old version too (the whole chain).
    after = add_document(sample, "after.pdf", PDF + b"b")
    link(sample, after, "super_statement", new["id"])
    shown = [d["document"]["id"] for d in documents_of(sample, "super_statement", old["id"])]
    assert shown == [before, after]
    # The first link is now described by the current statement, with the route to it.
    [first, _] = documents_of(sample, "super_statement", new["id"])
    assert first["link"]["route"] == f"/accounts/{account_id}/super-statements/{new['id']}"
    assert first["link"]["removed"] is False
    # A second correction extends the chain: all three ids see both documents.
    newer = correct(sample, new, concessional_received="14600.00")
    for ident in (old["id"], new["id"], newer["id"]):
        shown = [d["document"]["id"] for d in documents_of(sample, "super_statement", ident)]
        assert shown == [before, after]


def test_a_removed_statement_shows_its_link_as_removed_and_keeps_the_document(
    sample: TestClient,
) -> None:
    account_id = super_account(sample)
    statement = statement_of(sample, account_id)
    doc = add_document(sample)
    link(sample, doc, "super_statement", statement["id"])
    assert sample.delete(f"/api/super-statements/{statement['id']}").status_code == 204

    [listed] = documents_of(sample, "super_statement", statement["id"])
    assert listed["link"]["removed"] is True
    assert listed["link"]["route"] == f"/accounts/{account_id}"  # the Account still has a page
    assert sample.get(f"/api/library/documents/{doc}").json()["link_count"] == 1
    assert sample.get(f"/api/library/documents/{doc}/file").status_code == 200


def test_the_statement_endpoint_follows_a_correction_and_is_404_once_removed(
    sample: TestClient,
) -> None:
    account_id = super_account(sample)
    old = statement_of(sample, account_id)
    assert sample.get(f"/api/super-statements/{old['id']}").json()["id"] == old["id"]

    new = correct(sample, old, concessional_received="14500.00")
    followed = sample.get(f"/api/super-statements/{old['id']}")
    assert followed.status_code == 200
    assert (followed.json()["id"], followed.json()["concessional_received"]) == (
        new["id"],
        "14500.00",
    )

    assert sample.delete(f"/api/super-statements/{new['id']}").status_code == 204
    assert sample.get(f"/api/super-statements/{old['id']}").status_code == 404
    assert sample.get(f"/api/super-statements/{new['id']}").status_code == 404
    assert sample.get("/api/super-statements/99999").status_code == 404


def test_the_kinds_find_records_to_link(sample: TestClient, data_dir: Path) -> None:
    sample.close()
    engine = make_engine(db_path(data_dir))
    try:
        with make_session_factory(engine)() as s:
            account = registry.get("account")
            statement = registry.get("super_statement")
            assert account is not None and statement is not None
            assert [label for _, label in account.candidates(s, "Sample Super Fund")] == [
                "Sample Super Fund"
            ]
            assert account.candidates(s, "100%") == []  # a wildcard is just text
            assert len(account.candidates(s, "")) <= 20
            found = [label for _, label in statement.candidates(s, "Sample Super Fund")]
            assert found == ["Super statement FY 2025-26, Sample Super Fund"]
            assert statement.candidates(s, "100%") == []
    finally:
        engine.dispose()
