"""Keep credentials out of logs (ADR-0010): scrub password, TOTP code and Session cookie."""

from __future__ import annotations

import logging
import re

REDACTED = "[redacted]"

_PATTERNS = [
    # JSON ("password": "...") and form/query/cookie (password=...) shapes, any quoting.
    re.compile(
        r"""(?ix)
        (?P<key>["']?(?:password|current_password|new_password|totp|setup_code|f360_session)["']?)
        (?P<sep>\s*[:=]\s*)
        (?P<val>"[^"]*"|'[^']*'|[^\s,;&}]+)
        """
    ),
]


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: f"{m['key']}{m['sep']}{REDACTED}", text)
    return text


class RedactFilter(logging.Filter):
    """A logging filter that scrubs the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        scrubbed = redact(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = None
        return True
