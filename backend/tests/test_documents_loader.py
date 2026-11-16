"""The loader's limits and what its errors may say (ADR-0024 section 2, 8)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from finance360.capabilities.documents import loader

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "fixtures" / "sample_household" / "documents" / "ato_income_statement_sample.pdf"


def minimal_pdf(pages: int, trailer_extra: str = "", extra_objects: str = "") -> bytes:
    """A tiny valid PDF with ``pages`` blank pages (so: no text layer)."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>",
        *["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>" for _ in range(pages)],
    ]
    body = "%PDF-1.4\n"
    for number, obj in enumerate(objects, start=1):
        body += f"{number} 0 obj\n{obj}\nendobj\n"
    body += extra_objects
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R {trailer_extra} >>\n"
    body += "startxref\n0\n%%EOF\n"
    return body.encode("latin-1")


def test_the_sample_loads_with_words_boxes_and_two_pages() -> None:
    document = loader.load_pdf(SAMPLE.read_bytes())
    assert [p.number for p in document.pages] == [1, 2]
    first = document.pages[0]
    assert first.width > 0 and first.height > 0
    word = next(w for w in first.words if w.text == "Gross")
    assert 0 <= word.x0 < word.x1 <= first.width
    assert 0 <= word.top < word.bottom <= first.height  # origin top-left, top grows downwards


def test_a_blank_pdf_has_no_text_and_says_scans_are_not_read() -> None:
    with pytest.raises(loader.LoadError, match="no text; scans and images"):
        loader.load_pdf(minimal_pdf(1))


def test_more_than_twenty_pages_is_refused() -> None:
    assert loader.MAX_PAGES == 20
    with pytest.raises(loader.LoadError, match="more than 20 pages"):
        loader.load_pdf(minimal_pdf(21))
    with pytest.raises(loader.LoadError, match="no text"):  # twenty is within the limit
        loader.load_pdf(minimal_pdf(20))


def test_an_encrypted_pdf_is_refused_with_a_plain_message() -> None:
    encrypt = (
        "99 0 obj\n<< /Filter /Standard /V 1 /R 2 /P -4 "
        f"/O <{'00' * 32}> /U <{'00' * 32}> >>\nendobj\n"
    )
    content = minimal_pdf(1, "/Encrypt 99 0 R /ID [<00> <00>]", encrypt)
    with pytest.raises(loader.LoadError, match="encrypted"):
        loader.load_pdf(content)


def test_something_that_is_not_a_pdf_is_refused() -> None:
    with pytest.raises(loader.LoadError, match="not a PDF"):
        loader.load_pdf(b"plain text, not a pdf")


def test_a_parse_that_takes_too_long_is_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(content: bytes, cancelled: object) -> object:
        time.sleep(0.5)
        return loader.Document(())

    monkeypatch.setattr(loader, "_parse", slow)
    monkeypatch.setattr(loader, "TIME_LIMIT", 0.05)
    with pytest.raises(loader.LoadError, match="took too long"):
        loader.load_pdf(b"%PDF-1.4 anything")


def test_an_error_never_contains_text_from_the_file(caplog: pytest.LogCaptureFixture) -> None:
    planted = "PLANTED-SECRET-VALUE-4471"
    broken = f"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Note ({planted}) >>\n".encode("latin-1")
    with caplog.at_level(logging.DEBUG), pytest.raises(loader.LoadError) as raised:
        loader.load_pdf(broken)
    assert planted not in raised.value.message
    assert planted not in str(raised.value)
    assert planted not in caplog.text


def test_pasted_text_is_one_page_with_a_line_per_row_and_character_columns() -> None:
    document = loader.load_text("Gross  $5.00\n\nNext line")
    (page,) = document.pages
    gross, amount, nxt, line = page.words
    assert (gross.text, gross.x0, gross.x1) == ("Gross", 0, 5)
    assert (amount.text, amount.x0) == ("$5.00", 7)
    assert (nxt.top, line.top) == (2, 2)  # the blank row still takes a row


def test_empty_pasted_text_is_refused() -> None:
    with pytest.raises(loader.LoadError, match="no text"):
        loader.load_text("  \n \n")
