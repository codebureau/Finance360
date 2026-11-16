"""Read a document against the profiles registered for a target (ADR-0024 section 1).

Read-only: it loads, matches and checks in memory and returns a Reading. It stores nothing, writes
no audit row, calls no AI and logs nothing from the document. The caller (the API) holds the bytes
for the request only.
"""

from __future__ import annotations

from finance360.capabilities.documents import checks, loader, matcher, registry
from finance360.capabilities.documents.loader import LoadError as LoadError
from finance360.capabilities.documents.model import Document
from finance360.capabilities.documents.profile import Profile as Profile
from finance360.capabilities.documents.profile import to_json as to_json
from finance360.capabilities.documents.reading import UNRECOGNISED, Reading
from finance360.capabilities.documents.reading import Check as Check
from finance360.capabilities.documents.reading import Found as Found
from finance360.capabilities.documents.reading import NotFound as NotFound
from finance360.capabilities.documents.registry import get_target as get_target
from finance360.capabilities.documents.registry import saved_profile as saved_profile
from finance360.capabilities.documents.registry import shipped_profile as shipped_profile
from finance360.capabilities.documents.registry import shipped_profiles as shipped_profiles


class UnknownTarget(ValueError):
    """No Document profile is registered for the target."""


def read_document(document: Document, target: str, expected_fy: int | None = None) -> Reading:
    profiles = registry.for_target(target)
    if not profiles:
        raise UnknownTarget(target)
    for profile in profiles:  # the first (by key) that claims the document reads it
        if matcher.claims(document, profile):
            return checks.evaluate(
                profile, matcher.match(document, profile), expected_fy=expected_fy
            )
    return Reading(UNRECOGNISED, None, None, (), (), ())


def read_with_profile(
    document: Document, profile: Profile, expected_fy: int | None = None
) -> Reading:
    """Read ``document`` with this one profile, registered or not (a draft being tested). The same
    matcher and checks as ``read_document``; nothing is stored. A document the profile does not
    claim is ``unrecognised``."""
    if not matcher.claims(document, profile):
        return Reading(UNRECOGNISED, None, None, (), (), ())
    return checks.evaluate(profile, matcher.match(document, profile), expected_fy=expected_fy)


def read_pdf(content: bytes, target: str, expected_fy: int | None = None) -> Reading:
    """Raises ``LoadError`` (a plain message) when the PDF cannot be loaded, ``UnknownTarget``."""
    if not registry.for_target(target):
        raise UnknownTarget(target)
    return read_document(loader.load_pdf(content), target, expected_fy)


def read_text(text: str, target: str, expected_fy: int | None = None) -> Reading:
    if not registry.for_target(target):
        raise UnknownTarget(target)
    return read_document(loader.load_text(text), target, expected_fy)


def failed_profiles() -> dict[int, tuple[str, str]]:
    """Saved profiles that failed to load at start-up: row id -> (key, reason)."""
    return registry.failed()
