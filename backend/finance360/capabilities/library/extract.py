"""Text extraction on add (ADR-0026 section 2): a document's plain text, for search.

Uses the Document intake loaders (the PDF text layer, plain text) within their limits. Images and
over-limit files get no text; OCR will fill the same column later. **Nothing here logs
document content**: a ``LoadError`` carries a message with no text from the file (and the PDF
library's own logger is silenced in the loader).
"""

from __future__ import annotations

from finance360.capabilities.documents import loader
from finance360.capabilities.documents.model import all_lines

#: Status when text was taken, and the reasons it was not (``core.models.library.TEXT_STATUSES``).
EXTRACTED, NONE, TOO_LARGE, FAILED = "extracted", "none", "too_large", "failed"

_REASONS = {"too_large": TOO_LARGE, "no_text": NONE}


def extract(media_type: str, content: bytes) -> tuple[str | None, str]:
    """``(text, status)``. The text is ``None`` unless the status is ``extracted``."""
    if media_type == "application/pdf":
        try:
            document = loader.load_pdf(content)
        except loader.LoadError as problem:
            return None, _REASONS.get(problem.reason, FAILED)
        text = "\n".join(line.text for line in all_lines(document))
    elif media_type in ("text/plain", "text/csv"):
        text = content.decode("utf-8", errors="replace")
    else:
        return None, NONE  # an image: nothing to read until OCR
    text = text.replace("\x00", "")
    if len(text) > loader.MAX_TEXT:
        return None, TOO_LARGE
    if not text.strip():
        return None, NONE
    return text, EXTRACTED
