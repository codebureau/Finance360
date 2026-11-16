"""Fail if any given path is one that must never be committed (see scripts/guard_patterns.txt).

Usage: ``python scripts/guard_paths.py PATH [PATH ...]``. Exit status 1 lists every offender.
Standard library only, so it runs under pre-commit (any Python) and in CI without ``uv sync``.
"""

from __future__ import annotations

import sys
from fnmatch import fnmatchcase
from pathlib import Path

PATTERNS_FILE = Path(__file__).with_name("guard_patterns.txt")


def load_patterns(path: Path = PATTERNS_FILE) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _matches(pattern: str, path: str) -> bool:
    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        return path.startswith(pattern) or (not anchored and f"/{pattern}" in f"/{path}")
    if "/" in pattern or anchored:
        return fnmatchcase(path, pattern)
    return fnmatchcase(path.rsplit("/", 1)[-1], pattern)


def is_blocked(path: str, patterns: list[str]) -> bool:
    """Last matching line wins; a leading ``!`` re-allows."""
    normal = path.replace("\\", "/").removeprefix("./").lower()
    blocked = False
    for raw in patterns:
        negated = raw.startswith("!")
        if _matches(raw.removeprefix("!").lower(), normal):
            blocked = not negated
    return blocked


def main(argv: list[str]) -> int:
    patterns = load_patterns()
    offenders = [p for p in argv if is_blocked(p, patterns)]
    if not offenders:
        return 0
    print("Blocked: these paths match scripts/guard_patterns.txt (real financial data or secrets):")
    for offender in offenders:
        print(f"  {offender}")
    print("Unstage them (git restore --staged <path>). Real data never belongs in this repo.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
