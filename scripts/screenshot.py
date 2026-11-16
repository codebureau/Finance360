"""Screenshot pages of a running Finance360 instance at phone and desktop width, light and dark.

Usage (against an agent's throwaway instance, never the review slot):

    uv run python scripts/screenshot.py --pr 123 --base http://127.0.0.1:18437 --password <pw> \
        accounts accounts/4 imports/1

Paths may omit the leading slash (Git Bash rewrites ``/accounts`` into a Windows path). Signs in
through the real login form once, then for every path x width x theme saves
``<slug>-<width>-<theme>.png`` and checks there is no sideways scroll. Exits 1 if any page
overflows, so it can gate a UI change.

Where they go: one folder per PR, issue or milestone (``--pr 123`` -> ``pr-123``, ``--issue 97``
-> ``issue-97``, ``--milestone M2`` -> ``m2``) under a fixed root beside the repo, so they outlive
agent worktrees: ``<repo>-screenshots`` (e.g. ``<parent>/Finance360-screenshots``),
or ``FINANCE360_SCREENSHOTS``. Each folder gets an ``index.html`` showing every shot: open that.

One-time setup: ``uv run playwright install chromium``.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

WIDTHS = (390, 1280)
THEMES = ("light", "dark")


def slug(path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-") or "root"


def screenshots_root() -> Path:
    """``FINANCE360_SCREENSHOTS``, else ``<main checkout>-screenshots`` beside the repo. Resolved
    through git's common dir, so an agent worktree finds the same root as the main checkout."""
    if env := os.environ.get("FINANCE360_SCREENSHOTS"):
        return Path(env)
    common = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent,
    ).stdout.strip()
    main = Path(common).parent
    return main.parent / f"{main.name}-screenshots"


def write_index(folder: Path, label: str) -> Path:
    shots = sorted(folder.glob("*.png"))
    cells = "\n".join(
        f'<figure><a href="{html.escape(s.name)}"><img src="{html.escape(s.name)}" loading="lazy">'
        f"</a><figcaption>{html.escape(s.stem)}</figcaption></figure>"
        for s in shots
    )
    index = folder / "index.html"
    index.write_text(
        f"<!doctype html><meta charset=utf-8><title>{html.escape(label)} screenshots</title>"
        "<style>body{font:14px system-ui;margin:16px}main{display:flex;flex-wrap:wrap;gap:16px}"
        "figure{margin:0;width:320px}img{width:100%;border:1px solid #ccc}"
        "figcaption{font-size:12px;word-break:break-all}</style>"
        f"<h1>{html.escape(label)}</h1><main>{cells}</main>",
        encoding="utf-8",
    )
    return index


def sign_in(page: Page, base: str, password: str) -> None:
    page.goto(base + "/")
    field = page.get_by_label("Password", exact=True)
    field.wait_for()
    field.fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("navigation").first.wait_for()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Screenshot Finance360 pages at phone and desktop width, light and dark."
    )
    parser.add_argument("paths", nargs="+", help="app paths, e.g. accounts/4")
    parser.add_argument("--base", required=True, help="instance URL, e.g. http://127.0.0.1:18437")
    parser.add_argument("--password", required=True, help="the instance's owner password")
    label = parser.add_mutually_exclusive_group(required=True)
    label.add_argument("--pr", type=int, help="file under pr-<n>")
    label.add_argument("--issue", type=int, help="file under issue-<n>")
    label.add_argument("--milestone", help="file under the milestone, e.g. M2")
    parser.add_argument("--widths", default=",".join(map(str, WIDTHS)))
    args = parser.parse_args(argv)
    if ":8360" in args.base:
        parser.error("8360 is the review slot; screenshot a throwaway instance instead")
    base = args.base.rstrip("/")
    paths = ["/" + p.lstrip("/") for p in args.paths]
    widths = [int(w) for w in args.widths.split(",")]
    name = (
        f"pr-{args.pr}"
        if args.pr is not None
        else f"issue-{args.issue}"
        if args.issue is not None
        else slug(args.milestone).lower()
    )
    out = screenshots_root() / name
    out.mkdir(parents=True, exist_ok=True)

    overflow: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": max(widths), "height": 900})
        page = context.new_page()
        sign_in(page, base, args.password)
        for theme in THEMES:
            page.evaluate("t => localStorage.setItem('theme', t)", theme)
            for width in widths:
                page.set_viewport_size({"width": width, "height": 900})
                for path in paths:
                    page.goto(base + path)
                    page.wait_for_load_state("networkidle")
                    shot = f"{slug(path)}-{width}-{theme}.png"
                    page.screenshot(path=str(out / shot), full_page=True)
                    scroll, client = page.evaluate(
                        "[document.documentElement.scrollWidth,"
                        " document.documentElement.clientWidth]"
                    )
                    ok = scroll <= client
                    flag = "ok  " if ok else "WIDE"
                    print(f"{flag} {shot}  scrollWidth={scroll} client={client}")
                    if not ok:
                        overflow.append(shot)
        browser.close()

    print(f"Screenshots: {write_index(out, name)}")
    if overflow:
        print(f"{len(overflow)} page(s) scroll sideways: {', '.join(overflow)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
