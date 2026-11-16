"""The Imports API over HTTP -> service -> real temporary SQLite with the synthetic household:
detect, stage, idempotent re-add, rows, discard, and the unresolved-references panel."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
OCTET = {"Content-Type": "application/octet-stream"}
SECRET = "SAMPLE-PI-0001"
MASKED = "••• 0001"
TRADES_NAME = "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _file(name: str) -> bytes:
    return (IMPORTS / name).read_bytes()


def _accounts(c: TestClient) -> dict[str, int]:
    return {a["name"]: a["id"] for a in c.get("/api/accounts").json()}


def _detect(c: TestClient, name: str) -> Any:
    return c.post(
        "/api/imports/detect", content=_file(name), headers=OCTET, params={"file_name": name}
    )


def _stage(c: TestClient, name: str, account: int, **params: Any) -> Any:
    return c.post(
        "/api/imports",
        content=_file(name),
        headers=OCTET,
        params={"account_id": account, "file_name": name, **params},
    )


def test_detect_names_the_importer_stream_coverage_and_preselects_the_matching_account(
    client: TestClient,
) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    found = _detect(client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
    assert found.status_code == 200
    body = found.json()
    assert (body["importer_key"], body["stream"], body["row_count"]) == ("vanguard_pi", "trade", 5)
    assert (body["coverage_from"], body["coverage_to"]) == ("2026-08-03", "2026-09-30")
    assert body["matches"] == [{"id": pi, "name": "Sample Brokerage account"}]
    assert body["reference_masked"] == MASKED and SECRET not in found.text
    assert body["duplicate_import_id"] is None
    assert body["warnings"] == []  # the file name states the period, so Coverage is not inferred
    unnamed = client.post("/api/imports/detect", content=_file(TRADES_NAME), headers=OCTET)
    assert any("no period" in w for w in unnamed.json()["warnings"])


def test_detect_stores_nothing(client: TestClient) -> None:
    _detect(client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv")
    assert client.get("/api/imports").json() == []


def test_detect_of_an_unknown_reference_offers_no_account(client: TestClient) -> None:
    body = _detect(
        client, "investment_transactions_accSAMPLE-PI-9999_21-Sep-2026_21-Sep-2026.csv"
    ).json()
    assert body["matches"] == [] and body["reference_masked"] == "••• 9999"


def test_staging_returns_201_then_200_for_the_same_file_with_the_same_import(
    client: TestClient,
) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    first = _stage(
        client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", pi
    )
    assert first.status_code == 201
    body = first.json()
    assert body["created"] is True and body["status"] == "staged" and body["stream"] == "trade"
    assert (body["account_id"], body["row_count"]) == (pi, 5)
    assert SECRET not in first.text

    again = _stage(
        client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", pi
    )
    assert again.status_code == 200
    assert again.json()["id"] == body["id"] and again.json()["created"] is False
    assert len(client.get("/api/imports").json()) == 1
    assert client.get(f"/api/imports/{body['id']}/rows").json()["total"] == 5

    # Detect now points at the Import that already holds this file.
    assert (
        _detect(
            client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
        ).json()["duplicate_import_id"]
        == body["id"]
    )


def test_rows_page_in_file_order_with_exact_money_and_resolved_instruments(
    client: TestClient,
) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    imp = _stage(
        client, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv", pi
    ).json()
    page = client.get(f"/api/imports/{imp['id']}/rows", params={"limit": 5}).json()
    assert page["total"] == 14 and [r["row_no"] for r in page["items"]] == [1, 2, 3, 4, 5]
    first, buy = page["items"][0], page["items"][2]
    assert first["kind"] == "interest" and first["amount"] == {
        "amount": "4.17",
        "amount_minor": 417,
        "currency": "AUD",
    }
    assert buy["kind"] == "trade_settlement" and buy["amount"]["amount"] == "-1922.15"
    assert (buy["instrument_ref"], buy["resolved_name"]) == ("SBME", "Sample Broad Market ETF")
    rest = client.get(f"/api/imports/{imp['id']}/rows", params={"offset": 10, "limit": 50}).json()
    assert [r["row_no"] for r in rest["items"]] == [11, 12, 13, 14]
    distribution = next(
        r
        for r in client.get(f"/api/imports/{imp['id']}/rows", params={"limit": 50}).json()["items"]
        if r["kind"] == "distribution"
    )
    assert distribution["resolved_name"] == "Sample Broad Market ETF"  # via the name alias
    assert client.get("/api/imports/9999/rows").status_code == 404


def test_an_unrecognised_file_is_a_clear_422_in_the_shared_error_shape(client: TestClient) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    for call in (
        lambda: _detect(client, "sample_bank_statement.csv"),
        lambda: _stage(client, "sample_bank_statement.csv", pi),
    ):
        response = call()
        assert response.status_code == 422
        error = response.json()["errors"][0]
        assert error["field"] == "file" and "does not recognise" in error["message"]
    assert client.get("/api/imports").json() == []
    empty = client.post("/api/imports/detect", content=b"", headers=OCTET)
    assert empty.status_code == 422 and empty.json()["errors"][0]["field"] == "file"


def test_an_oversize_body_is_refused_before_it_is_parsed(client: TestClient) -> None:
    huge = b"x" * 5_000_001
    response = client.post("/api/imports/detect", content=huge, headers=OCTET)
    assert response.status_code == 422 and "larger" in response.json()["errors"][0]["message"]


def test_staging_for_an_unknown_account_is_a_404_and_saving_the_reference_teaches_the_match(
    client: TestClient,
) -> None:
    assert (
        _stage(
            client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", 9999
        ).status_code
        == 404
    )
    everyday = _accounts(client)["Everyday account (Sample Bank)"]
    staged = _stage(
        client,
        "investment_transactions_accSAMPLE-PI-9999_21-Sep-2026_21-Sep-2026.csv",
        everyday,
        save_identifier="true",
    )
    assert staged.status_code == 201
    assert _detect(
        client, "investment_transactions_accSAMPLE-PI-9999_21-Sep-2026_21-Sep-2026.csv"
    ).json()["matches"] == [{"id": everyday, "name": "Everyday account (Sample Bank)"}]
    shown = client.get(f"/api/accounts/{everyday}/identifiers").json()
    assert [i["masked_value"] for i in shown] == ["••• 9999"]


def test_discard_marks_the_import_discarded_and_the_file_can_be_staged_again(
    client: TestClient,
) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    imp = _stage(
        client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", pi
    ).json()
    discarded = client.post(f"/api/imports/{imp['id']}/discard")
    assert discarded.status_code == 200 and discarded.json()["status"] == "discarded"
    assert discarded.json()["commit_blocked"] is False  # nothing to commit any more
    assert client.get(f"/api/imports/{imp['id']}/rows").json()["total"] == 5  # kept
    again = client.post(f"/api/imports/{imp['id']}/discard")
    assert again.status_code == 422 and again.json()["errors"][0]["field"] == "status"
    restaged = _stage(
        client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", pi
    )
    assert restaged.status_code == 201 and restaged.json()["id"] != imp["id"]
    assert client.post("/api/imports/9999/discard").status_code == 404


def test_the_unresolved_reference_blocks_commit_until_it_is_mapped(client: TestClient) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    imp = _stage(
        client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", pi
    ).json()
    assert imp["commit_blocked"] is True and imp["unresolved_count"] == 1
    detail = client.get(f"/api/imports/{imp['id']}").json()
    assert detail["unresolved"] == [
        {
            "scheme": "ticker",
            "ref": "SNHX",
            "name": "Sample New Horizons ETF",
            "kind_hint": "etf",
            "row_count": 1,
        }
    ]
    fund = next(i["id"] for i in client.get("/api/instruments").json() if i["kind"] == "fund")
    mapped = client.post(
        f"/api/imports/{imp['id']}/references/map",
        json={"scheme": "ticker", "ref": "SNHX", "instrument_id": fund},
    )
    assert mapped.status_code == 200
    assert (mapped.json()["commit_blocked"], mapped.json()["unresolved"]) == (False, [])
    rows = client.get(f"/api/imports/{imp['id']}/rows").json()["items"]
    assert next(r for r in rows if r["instrument_ref"] == "SNHX")["instrument_id"] == fund
    trail = client.get("/api/audit", params={"entity_type": "instrument", "entity_id": fund}).json()
    assert trail["items"][0]["summary"] == "Added ticker 'SNHX' to Sample Balanced Growth Fund"


def test_mapping_needs_an_instrument_and_a_waiting_reference(client: TestClient) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    imp = _stage(
        client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", pi
    ).json()
    none = client.post(
        f"/api/imports/{imp['id']}/references/map", json={"scheme": "ticker", "ref": "SNHX"}
    )
    assert none.status_code == 422 and none.json()["errors"][0]["field"] == "instrument_id"
    fund = client.get("/api/instruments").json()[0]["id"]
    wrong = client.post(
        f"/api/imports/{imp['id']}/references/map",
        json={"scheme": "ticker", "ref": "SBME", "instrument_id": fund},
    )
    assert wrong.status_code == 404
    assert client.get(f"/api/imports/{imp['id']}").json()["commit_blocked"] is True


def test_creating_an_instrument_for_the_reference_clears_the_block(client: TestClient) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    imp = _stage(
        client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", pi
    ).json()
    bad = client.post(
        f"/api/imports/{imp['id']}/references/create",
        json={"scheme": "ticker", "ref": "SNHX", "name": "", "kind": "etf", "currency": "AUD"},
    )
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "name"
    made = client.post(
        f"/api/imports/{imp['id']}/references/create",
        json={
            "scheme": "ticker",
            "ref": "SNHX",
            "name": "Sample New Horizons ETF",
            "kind": "etf",
            "currency": "AUD",
        },
    )
    assert made.status_code == 200 and made.json()["commit_blocked"] is False
    found = client.get("/api/instruments/lookup", params={"value": "snhx", "scheme": "ticker"})
    assert found.status_code == 200 and found.json()["name"] == "Sample New Horizons ETF"


def test_the_list_shows_every_import_newest_first_with_its_account(client: TestClient) -> None:
    pi = _accounts(client)["Sample Brokerage account"]
    _stage(client, "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv", pi)
    _stage(client, "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv", pi)
    listed = client.get("/api/imports").json()
    assert [(i["stream"], i["account_name"]) for i in listed] == [
        ("cash", "Sample Brokerage account"),
        ("trade", "Sample Brokerage account"),
    ]
    assert listed[0]["unresolved_count"] == 0 and listed[1]["unresolved_count"] == 1
    assert listed[1]["importer_name"] == "Vanguard Personal Investor"
