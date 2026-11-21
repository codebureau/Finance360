"""The files the built app serves from its root (`frontend/public/`) are well formed.

A browser rejects an SVG that is not valid XML and falls back to a generic icon, silently: the
favicon once carried "--" inside an XML comment, which is not allowed, and no tab ever showed it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PUBLIC = Path(__file__).resolve().parents[2] / "frontend" / "public"
SVGS = sorted(PUBLIC.glob("*.svg"))


def test_there_is_a_favicon() -> None:
    assert PUBLIC / "favicon.svg" in SVGS


@pytest.mark.parametrize("svg", SVGS, ids=lambda p: p.name)
def test_every_public_svg_is_well_formed_xml(svg: Path) -> None:
    root = ET.parse(svg).getroot()  # noqa: S314 - our own committed files, not untrusted input
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
