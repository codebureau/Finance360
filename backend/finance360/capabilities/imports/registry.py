"""The Importer registry (ADR-0013 section 5). Plug-ins register at startup from ``main.py``;
the services look Importers up here and never import a plug-in."""

from __future__ import annotations

from finance360.capabilities.imports.importer import Importer

_IMPORTERS: dict[str, Importer] = {}


def register(importer: Importer) -> None:
    """Add an Importer. Registering the same key twice is a programming error."""
    existing = _IMPORTERS.get(importer.key)
    if existing is not None and existing is not importer:
        raise ValueError(f"An Importer is already registered under {importer.key!r}")
    _IMPORTERS[importer.key] = importer


def unregister(key: str) -> None:
    """For tests that register a stand-in."""
    _IMPORTERS.pop(key, None)


def get(key: str) -> Importer | None:
    return _IMPORTERS.get(key)


def all_importers() -> list[Importer]:
    return [_IMPORTERS[k] for k in sorted(_IMPORTERS)]


def claimants(content: bytes, file_name: str = "") -> list[Importer]:
    """Every registered Importer that claims the file."""
    return [i for i in all_importers() if i.claims(content, file_name)]
