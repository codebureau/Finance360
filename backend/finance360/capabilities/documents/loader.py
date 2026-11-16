"""Load: bytes (a PDF with a text layer) or pasted text into one ``Document`` (ADR-0024 section 2).

The only module that touches the PDF library (``pdfminer.six``, pinned). It runs no JavaScript and
no network. Limits keep a hostile or huge file from holding the server: the caller caps the size
(10 MB), at most ``MAX_PAGES`` pages, a time box, and an encrypted PDF is refused with no password
prompt.

**A ``LoadError`` message never contains anything from the document**, nor the library's own error
text (which can echo file content). It names the problem only.

Pasted text loads into the same shape: one page, one Line per text line, ``x0`` the character
column. Position-based pairing (``below``, not built) would be unavailable on it.
"""

from __future__ import annotations

import io
import logging
import re
import threading
from collections.abc import Iterable, Iterator
from typing import cast

from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams, LTChar, LTContainer, LTPage, LTTextLine
from pdfminer.pdfdocument import PDFDocument, PDFEncryptionError
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

from finance360.capabilities.documents.model import Document, Page, Word

# pdfminer logs fragments of the file it is parsing (object contents, tokens) at DEBUG and WARNING.
# A document's text is never logged (ADR-0024 section 8), so its logger is silenced.
logging.getLogger("pdfminer").setLevel(logging.CRITICAL)

MAX_PAGES = 20
#: Seconds a parse may take before it is abandoned.
TIME_LIMIT = 20.0
#: The most characters of pasted text taken.
MAX_TEXT = 200_000


class LoadError(Exception):
    """The document could not be loaded. ``message`` is safe to show: it holds no document text."""

    #: Why, for a caller that stores a status rather than a message (the Library): ``failed``
    #: (damaged or encrypted), ``too_large`` (over the page or time limit) or ``no_text`` (a scan
    #: or image).
    def __init__(self, message: str, reason: str = "failed") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


class _Cancelled(Exception):
    pass


def _words_of(line: LTTextLine, page_height: float) -> Iterator[Word]:
    chars: list[LTChar] = []

    def flush() -> Word | None:
        if not chars:
            return None
        word = Word(
            "".join(c.get_text() for c in chars),
            min(c.x0 for c in chars),
            max(c.x1 for c in chars),
            page_height - max(c.y1 for c in chars),
            page_height - min(c.y0 for c in chars),
        )
        chars.clear()
        return word

    for item in line:
        if isinstance(item, LTChar) and not item.get_text().isspace():
            chars.append(item)
        elif (word := flush()) is not None:  # a space, or the end of the line
            yield word
    if (word := flush()) is not None:
        yield word


def _text_lines(items: Iterable[object]) -> Iterator[LTTextLine]:
    for item in items:
        if isinstance(item, LTTextLine):
            yield item
        elif isinstance(item, LTContainer):
            yield from _text_lines(cast("Iterable[object]", item))


def _parse(content: bytes, cancelled: threading.Event) -> Document:
    try:
        document = PDFDocument(PDFParser(io.BytesIO(content)))
    except PDFEncryptionError:
        raise LoadError("This PDF is encrypted; save an unprotected copy and try again.") from None
    if document.encryption is not None:
        raise LoadError("This PDF is encrypted; save an unprotected copy and try again.")
    manager = PDFResourceManager()
    aggregator = PDFPageAggregator(manager, laparams=LAParams())
    interpreter = PDFPageInterpreter(manager, aggregator)
    pages: list[Page] = []
    for number, pdf_page in enumerate(PDFPage.create_pages(document), start=1):
        if cancelled.is_set():
            raise _Cancelled
        if number > MAX_PAGES:
            raise LoadError(f"This PDF has more than {MAX_PAGES} pages.", "too_large")
        interpreter.process_page(pdf_page)
        layout: LTPage = aggregator.get_result()
        words = tuple(w for line in _text_lines(layout) for w in _words_of(line, layout.height))
        pages.append(Page(number, layout.width, layout.height, words))
    return Document(tuple(pages))


def load_pdf(content: bytes) -> Document:
    """The PDF's text layer as a Document. Raises ``LoadError`` with a plain message."""
    if not content.startswith(b"%PDF-"):
        raise LoadError("This is not a PDF file.")
    outcome: list[Document] = []
    failure: list[BaseException] = []
    cancelled = threading.Event()

    def run() -> None:
        try:
            outcome.append(_parse(content, cancelled))
        except _Cancelled:
            pass
        except BaseException as error:  # noqa: BLE001 - reported by kind below, never by text
            failure.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(TIME_LIMIT)
    if worker.is_alive():
        cancelled.set()
        raise LoadError("This PDF took too long to read.", "too_large")
    if failure:
        if isinstance(failure[0], LoadError):
            raise failure[0]
        raise LoadError("This PDF could not be read.") from None
    document = outcome[0]
    if not any(page.words for page in document.pages):
        raise LoadError("This PDF has no text; scans and images are not read yet.", "no_text")
    return document


def load_text(text: str) -> Document:
    """Pasted text as a one-page Document: a Line per text line, ``x0`` the character column."""
    rows = text[:MAX_TEXT].splitlines()
    words: list[Word] = []
    width = 0
    for index, row in enumerate(rows):
        width = max(width, len(row))
        for part in re.finditer(r"\S+", row):
            words.append(Word(part.group(), part.start(), part.end(), index, index + 1))
    if not words:
        raise LoadError("There is no text to read.")
    return Document((Page(1, float(width), float(len(rows)), tuple(words)),))
