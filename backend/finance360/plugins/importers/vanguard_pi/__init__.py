"""Vanguard Personal Investor Importer: a trade Stream and a cash Stream, each its own file."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from finance360.capabilities.imports.importer import ParsedFile
from finance360.plugins.importers.vanguard_pi import parser


class VanguardPiImporter:
    key = parser.KEY
    name = parser.NAME
    config_schema: Mapping[str, Any] = parser.CONFIG_SCHEMA

    def claims(self, content: bytes, file_name: str) -> bool:
        return parser.claims(content, file_name)

    def parse(self, content: bytes, config: Mapping[str, Any], file_name: str) -> ParsedFile:
        return parser.parse(content, config, file_name)


IMPORTER = VanguardPiImporter()
