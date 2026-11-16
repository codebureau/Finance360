"""Cross-site request check for unsafe methods (ADR-0010).

With ``SameSite=Strict`` and a JSON-only API this is defence in depth: POST, PUT, PATCH and DELETE
are refused unless ``Origin`` (else ``Referer``) names the host the request was sent to. No tokens.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def same_origin(method: str, headers: Mapping[str, str], host: str) -> bool:
    """True if the request may proceed. ``headers`` keys are lower-case (as ASGI/Starlette give).

    ``host`` is the effective host (``hosts.effective_host``), already checked against the allowed
    list. A request with neither ``Origin`` nor ``Referer`` is refused: every browser sends one on
    an unsafe request, so its absence means something other than a browser form or fetch.
    """
    if method.upper() not in UNSAFE_METHODS:
        return True
    source = headers.get("origin") or headers.get("referer")
    if not source or not host:
        return False
    return urlsplit(source).netloc.lower() == host.lower()
