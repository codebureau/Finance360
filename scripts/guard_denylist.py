"""Fail if text being added matches the private denylist (personal terms that must never be public).

The denylist is never committed: locally ``private/denylist.txt`` (gitignored), in CI the
``FINANCE360_DENYLIST`` Actions secret written to a temp file. ``FINANCE360_DENYLIST`` (an
environment variable holding a file path) overrides the local path.

Denylist format: one term per line; ``#`` starts a comment; blank lines are ignored; matching is
case-insensitive on word boundaries; a term may contain spaces. A ``docs:`` prefix blocks the term
only in prose (``docs/**``, ``*.md``, the Guide, templates, ``.claude/agents/*``, commit and PR
text), not in code; an unprefixed term is blocked everywhere.

Modes:
  (default)         added lines only: the staged diff, or ``--base REF [--head REF]`` (CI)
  --all             every tracked file (the final gate, and the weekly run)
  --text FILE ...   text such as a commit message or a PR title or body

Output never reveals the denylist: a hit prints ``path:line: matches denylist entry N (scope)``.
Only the opt-in ``--show`` prints the term (local use; never in CI).

Standard library only, so it runs under pre-commit (any Python) and in CI without ``uv sync``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path("private") / "denylist.txt"
ENV_PATH = "FINANCE360_DENYLIST"
ENV_FORK = "FINANCE360_FORK_PR"

_PROSE_PREFIXES = (
    "docs/",
    ".claude/agents/",
    ".github/issue_template/",
    ".github/pull_request_template",
    "frontend/src/guide/",
)


@dataclass(frozen=True)
class Entry:
    number: int  # 1-based position among the entries (not the line number)
    scope: str  # "docs" or "all"
    term: str
    pattern: re.Pattern[str]


def parse_denylist(text: str) -> list[Entry]:
    entries: list[Entry] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        scope = "all"
        if line.lower().startswith("docs:"):
            scope, line = "docs", line[5:].strip()
            if not line:
                continue
        words = [re.escape(word) for word in line.split()]
        # Lookarounds rather than \b, so a term that starts or ends with punctuation still works.
        pattern = re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)
        entries.append(Entry(len(entries) + 1, scope, line, pattern))
    return entries


def is_prose(path: str) -> bool:
    p = path.replace("\\", "/").removeprefix("./").lower()
    return p.startswith(_PROSE_PREFIXES) or p.endswith(".md")


def scan_line(entries: list[Entry], line: str, prose: bool) -> list[Entry]:
    return [e for e in entries if (prose or e.scope == "all") and e.pattern.search(line)]


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    entry: Entry


def scan_lines(
    entries: list[Entry], path: str, numbered: list[tuple[int, str]], prose: bool | None = None
) -> list[Hit]:
    in_prose = is_prose(path) if prose is None else prose
    return [
        Hit(path, number, e)
        for number, text in numbered
        for e in scan_line(entries, text, in_prose)
    ]


def _git(*args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode != 0:
        print(f"guard_denylist: git {args[0]} failed: {done.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return done.stdout


_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """Parse ``git diff -U0`` output into ``{path: [(line number, added text)]}``."""
    result: dict[str, list[tuple[int, str]]] = {}
    path: str | None = None
    number = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].split("\t", 1)[0]
            path = None if target == "/dev/null" else target.removeprefix("b/")
        elif raw.startswith("--- "):
            continue
        elif (hunk := _HUNK.match(raw)) is not None:
            number = int(hunk.group(1))
        elif raw.startswith("+") and path is not None:
            result.setdefault(path, []).append((number, raw[1:]))
            number += 1
    return result


def _numbered(text: str) -> list[tuple[int, str]]:
    return list(enumerate(text.splitlines(), start=1))


def tracked_files() -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    for name in _git("ls-files", "-z").split("\0"):
        if not name:
            continue
        try:
            data = Path(name).read_bytes()
        except OSError:
            continue
        if b"\0" in data:  # binary
            continue
        result[name] = _numbered(data.decode("utf-8", errors="replace"))
    return result


def resolve_denylist(env: dict[str, str]) -> Path:
    override = env.get(ENV_PATH, "").strip()
    return Path(override) if override else DEFAULT_PATH


def _in_ci(env: dict[str, str]) -> bool:
    return env.get("CI", "").lower() in {"1", "true"} or bool(env.get("GITHUB_ACTIONS"))


def _label(path: str, entries: list[Entry]) -> str:
    """The path, unless the path itself contains a term (the output must not reveal one)."""
    return "<path redacted>" if scan_line(entries, path, True) else path


def main(argv: list[str], env: dict[str, str] | None = None) -> int:
    env = dict(os.environ) if env is None else env
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", help="scan lines added since this ref (CI)")
    parser.add_argument("--head", default="HEAD", help="with --base: the tip to compare")
    parser.add_argument("--all", action="store_true", help="scan every tracked file")
    parser.add_argument("--text", nargs="+", metavar="FILE", help="scan text files")
    parser.add_argument("--show", action="store_true", help="print matched terms (local only)")
    args = parser.parse_args(argv)

    source = resolve_denylist(env)
    entries = parse_denylist(source.read_text(encoding="utf-8")) if source.is_file() else []
    if not entries:
        if not _in_ci(env):
            print("guard_denylist: no denylist found (private/denylist.txt); skipped.")
            return 0
        if env.get(ENV_FORK, "").lower() in {"1", "true"}:
            print(
                "guard_denylist: pull request from a fork has no access to the denylist; skipped."
            )
            return 0
        print(
            "guard_denylist: FAILED: the denylist is missing or empty in CI. Set the "
            f"{ENV_PATH} Actions secret (one term per line).",
            file=sys.stderr,
        )
        return 1

    hits: list[Hit] = []
    if args.text:
        for name in args.text:
            text = Path(name).read_text(encoding="utf-8", errors="replace")
            hits += scan_lines(entries, name, _numbered(text), prose=True)
    else:
        if args.all:
            files = tracked_files()
        elif args.base:
            files = added_lines(
                _git("diff", "-U0", "--diff-filter=ACMR", f"{args.base}...{args.head}")
            )
        else:
            files = added_lines(_git("diff", "--cached", "-U0", "--diff-filter=ACMR"))
        for path, numbered in files.items():
            hits += scan_lines(entries, path, numbered)
            hits += [Hit(path, 0, e) for e in scan_line(entries, path, is_prose(path))]

    if not hits:
        return 0
    print("guard_denylist: blocked: text matches the private denylist (details withheld).")
    for hit in hits:
        where = _label(hit.path, entries)
        place = f"{where}:{hit.line}" if hit.line else f"{where} (file name)"
        extra = f" [{hit.entry.term}]" if args.show else ""
        print(f"  {place}: matches denylist entry {hit.entry.number} ({hit.entry.scope}){extra}")
    print("Remove it, or use an invented value. Entry numbers count terms in the denylist file.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
