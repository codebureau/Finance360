"""Service error types shared by every Capability and mapped to HTTP by the API layer.

They live in ``core`` so Capabilities (``household``, ``position``, ``reference``) can raise them
without depending on one another.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


class ValidationError(Exception):
    """The input was rejected. ``errors`` names each offending field with a readable message."""

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("; ".join(f"{e.field}: {e.message}" for e in errors))
        self.errors = errors


class NotFoundError(Exception):
    pass
