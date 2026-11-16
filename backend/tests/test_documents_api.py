"""``POST /api/documents/read`` through the API against a real temporary SQLite database
(ADR-0024 sections 1, 2 and 8). Every file here is invented."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from finance360.config import Settings
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.models.audit import AuditEvent
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
SAMPLE = (
    REPO / "fixtures" / "sample_household" / "documents" / "ato_income_statement_sample.pdf"
).read_bytes()
SUMS_DIFFER = (
    REPO / "fixtures" / "documents" / "ato_income_statement_sums_differ.pdf"
).read_bytes()
NO_PAYGW = (REPO / "fixtures" / "documents" / "ato_income_statement_no_paygw.pdf").read_bytes()
BANK = (REPO / "fixtures" / "documents" / "sample_bank_statement.pdf").read_bytes()
TARGET = "tax.income_statement"


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Shared by the module: reading writes nothing (a test below proves it)."""
    data = tmp_path_factory.mktemp("documents") / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    shutil.copytree(REPO / "fixtures" / "sample_household" / "evidence", data / "evidence")
    return data


@pytest.fixture(scope="module")
def client(data_dir: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=data_dir, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def read(
    client: TestClient,
    content: bytes,
    *,
    media_type: str = "application/pdf",
    target: str = TARGET,
    **params: Any,
) -> Any:
    return client.post(
        "/api/documents/read",
        params={"target": target, **params},
        content=content,
        headers={"Content-Type": media_type},
    )


def found(body: dict[str, Any]) -> dict[str, str]:
    return {f["key"]: f["value"] for f in body["found"]}


def test_the_sample_statement_reads_to_decimal_strings_and_text(client: TestClient) -> None:
    res = read(client, SAMPLE, financial_year=2026)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["outcome"], body["profile_key"], body["profile_version"]) == (
        "ok",
        "ato-income-statement",
        3,
    )
    values = found(body)
    assert values["gross_payments"] == "84500.00"
    assert values["tax_withheld"] == "18250.00"
    assert values["salary_sacrifice_amount"] == "-3000.00"
    assert values["leave_payment_type"] == "Annual leave"
    assert values["employer_super"] == "10140.00"
    assert {f["key"]: f["kind"] for f in body["found"]}["period"] == "fy"
    assert body["not_found"] == []
    assert [(c["kind"], c["status"]) for c in body["checks"]] == [
        ("sum", "agree"),
        ("period", "agree"),
    ]
    assert res.headers["cache-control"] == "no-store"


def test_a_sum_that_disagrees_is_a_warning_with_the_figures(client: TestClient) -> None:
    body = read(client, SUMS_DIFFER).json()
    assert body["outcome"] == "warnings"
    assert found(body)["gross_payments"] == "84501.00"
    assert [c["status"] for c in body["checks"]] == ["disagree"]


def test_a_missing_required_label_needs_repair_and_still_returns_what_was_found(
    client: TestClient,
) -> None:
    body = read(client, NO_PAYGW).json()
    assert body["outcome"] == "needs_repair"
    values = found(body)
    assert values["gross_payments"] == "84500.00"  # the other required figure is filled
    assert "tax_withheld" not in values  # the missing one is flagged, not guessed
    assert body["not_found"] == [
        {"key": "tax_withheld", "reason": "label_not_found", "required": True}
    ]


def test_another_document_is_unrecognised_not_an_error(client: TestClient) -> None:
    res = read(client, BANK)
    assert res.status_code == 200
    assert res.json() == {
        "outcome": "unrecognised",
        "profile_key": None,
        "profile_version": None,
        "found": [],
        "not_found": [],
        "checks": [],
    }


def test_pasted_text_reads_through_the_same_endpoint(client: TestClient) -> None:
    text = "Tax withheld or foreign tax paid\nPAYGW amount $5.00\nLump sum amounts\n"
    text += "Total gross amount $5.00\nEmployer reported super\n"
    res = read(client, text.encode(), media_type="text/plain")
    assert res.status_code == 200, res.text
    assert found(res.json())["gross_payments"] == "5.00"


def test_a_period_for_another_year_is_reported_against_the_forms_year(client: TestClient) -> None:
    body = read(client, SAMPLE, financial_year=2025).json()
    assert body["outcome"] == "warnings"
    assert [(c["kind"], c["status"]) for c in body["checks"]][-1] == ("period", "disagree")


@pytest.mark.parametrize(
    ("content", "media_type", "field", "fragment"),
    [
        (b"not a pdf at all", "application/pdf", "file", "not a PDF"),
        (b"%PDF-1.4 garbage", "application/pdf", "file", "could not be read"),
        (b"   ", "text/plain", "file", "no text"),
        (b"{}", "application/json", "file", "PDF or a text file"),
    ],
)
def test_a_document_that_cannot_be_read_is_a_422_in_the_shared_shape(
    client: TestClient, content: bytes, media_type: str, field: str, fragment: str
) -> None:
    res = read(client, content, media_type=media_type)
    assert res.status_code == 422
    (error,) = res.json()["errors"]
    assert error["field"] == field and fragment in error["message"]


def test_a_target_nothing_reads_into_is_a_422_on_target(client: TestClient) -> None:
    res = read(client, SAMPLE, target="no.such.form")
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "target"


def test_a_file_over_the_limit_is_refused_before_it_is_read(client: TestClient) -> None:
    res = read(client, b"%PDF-" + b"0" * 10_000_000)
    assert res.status_code == 422
    assert res.json()["errors"] == [{"field": "file", "message": "The file is larger than 10 MB."}]


def test_reading_stores_nothing_and_writes_no_audit(client: TestClient, data_dir: Path) -> None:
    def audit_rows() -> int:
        engine = make_engine(db_path(data_dir))
        try:
            with make_session_factory(engine)() as session:
                return session.scalar(select(func.count()).select_from(AuditEvent)) or 0
        finally:
            engine.dispose()

    before = audit_rows()
    files = sorted(p for p in data_dir.rglob("*") if p.is_file())
    assert read(client, SAMPLE).status_code == 200
    assert read(client, SUMS_DIFFER).status_code == 200
    assert audit_rows() == before
    # No file appears anywhere (not even as Evidence: the form keeps the file until Save).
    assert sorted(p for p in data_dir.rglob("*") if p.is_file()) == files


def test_each_found_figure_carries_the_page_it_was_read_from(client: TestClient) -> None:
    """The side-by-side pane labels a figure "page N" (ADR-0026 section 5). The sample's first
    page holds the income and tax figures and its second the employer super and reportables."""
    body = read(client, SAMPLE, financial_year=2026).json()
    pages = {f["key"]: f["page"] for f in body["found"]}
    assert pages["gross_payments"] == 1
    assert pages["tax_withheld"] == 1
    assert pages["employer_super"] == 2
    assert pages["reportable_employer_super"] == 2
    assert all(f["page"] >= 1 for f in body["found"] if f["how"] == "read")


def test_an_error_and_the_logs_carry_nothing_from_the_file(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    planted = "PLANTED-SECRET-VALUE-4471"
    broken = f"%PDF-1.4\n1 0 obj\n<< /Note ({planted}) >>\n".encode("latin-1")
    with caplog.at_level(logging.DEBUG):
        res = read(client, broken)
    assert res.status_code == 422
    assert planted not in res.text
    assert planted not in caplog.text


def test_reading_needs_a_signed_in_owner(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "other", port=8000, git_sha="dev", static_dir=None)
    with TestClient(create_app(settings), headers={"Origin": "http://localhost"}) as anonymous:
        assert read(anonymous, SAMPLE).status_code == 401
