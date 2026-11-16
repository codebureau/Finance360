"""Load and parse the Guide: Markdown in ``frontend/src/guide/`` (ADR-0021 section 2).

The format is the frontend's (``frontend/src/lib/guide.ts``): files named ``NN-slug.md``, the
first ``# `` line is the title. It is parsed again here, and a test holds the two to the same
slugs, titles and link targets so they cannot silently diverge.

The Guide is the only source the Ask panel answers from, and it also fixes the **closed set of
valid sources** an answer may cite: its page slugs, and the in-app screens its Markdown links to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

START_SLUG = "start-here"
#: The Guide's front door and the home screen (What's due), always citable.
HOME_PATH = "/"
HOME_LABEL = "What's due"

_FILE = re.compile(r"^(\d+)-(.+)\.md$")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class GuidePage:
    slug: str
    title: str
    body: str

    @property
    def path(self) -> str:
        return "/guide" if self.slug == START_SLUG else f"/guide/{self.slug}"


@dataclass(frozen=True)
class Guide:
    pages: tuple[GuidePage, ...]
    #: Screen path -> the text of the first Guide link to it. ``/`` is always present.
    screens: dict[str, str]

    def page(self, slug: str) -> GuidePage | None:
        return next((p for p in self.pages if p.slug == slug), None)


def link_targets(markdown: str) -> list[tuple[str, str]]:
    """``(text, target)`` for each Markdown link, in order."""
    return [(m.group(1), m.group(2)) for m in _LINK.finditer(markdown)]


def _screen(target: str) -> str | None:
    """The in-app screen a link points at, without query or fragment; None for anything else
    (external, a Guide page, a bare anchor)."""
    if not target.startswith("/") or target.startswith("//"):
        return None
    path = re.split(r"[?#]", target, maxsplit=1)[0] or HOME_PATH
    if path == "/guide" or path.startswith("/guide/"):
        return None
    return path


def load(guide_dir: Path) -> Guide:
    found: list[tuple[int, GuidePage]] = []
    for file in guide_dir.glob("*.md"):
        match = _FILE.match(file.name)
        lines = file.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
        at = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
        if match is None or at is None:
            raise ValueError(f'Guide page {file.name} needs an NN- prefix and a "# " title.')
        body = "\n".join([*lines[:at], *lines[at + 1 :]]).strip()
        found.append((int(match.group(1)), GuidePage(match.group(2), lines[at][2:].strip(), body)))
    pages = tuple(page for _, page in sorted(found, key=lambda f: f[0]))
    screens: dict[str, str] = {HOME_PATH: HOME_LABEL}
    for page in pages:
        for text, target in link_targets(page.body):
            path = _screen(target)
            if path is not None and path not in screens:
                screens[path] = text
    return Guide(pages, screens)
