"""Guided Rule figure capture for the AU pack's ``tax.`` keys.

For each declared key it says "open this page, look here", asks for the figure, rejects an
out-of-range or off-unit entry, asks you to confirm a large change from last year, and records a
figure you cannot confirm as provisional with a note. It then rewrites
``backend/finance360/rulepacks/au/tax_figures.py`` and bumps ``REVISION`` in the pack's
``__init__.py``. It fetches nothing: you read the official page and type the figure.

    uv run python scripts/rulepack_update.py --fy 2026            # financial year 2025-26
    uv run python scripts/rulepack_update.py --fy 2026 --prefix tax.mls.
    uv run python scripts/rulepack_update.py --fy 2026 --pack-dir <a throwaway copy of rulepacks/au>

Afterwards update the pinned revision and hash in ``backend/tests/test_rulepack_au.py`` (the script
prints both) and review the diff. The declarations are ``FigureSpec`` values in
``rulepacks/au/tax_specs.py``; the Age Pension slice (B11) adds its own and reuses
``finance360.rulepacks.capture``.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from finance360.core.rulepack import fy_label
from finance360.rulepacks import capture
from finance360.rulepacks.au import tax_specs

DEFAULT_PACK_DIR = (
    Path(__file__).resolve().parents[1] / "backend" / "finance360" / "rulepacks" / "au"
)


def _load_texts(path: Path) -> tuple[dict, dict, dict]:
    namespace: dict[str, dict] = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102
    return namespace["FIGURES"], namespace["PROVISIONAL"], namespace["RETRIEVED"]


def run(
    argv: Sequence[str],
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    today: date | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--fy", type=int, required=True, help="year the FY ends in (2026 = 2025-26)"
    )
    parser.add_argument("--prefix", default="", help="only keys starting with this")
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK_DIR)
    args = parser.parse_args(argv)

    figures_path = args.pack_dir / "tax_figures.py"
    init_path = args.pack_dir / "__init__.py"
    specs = [s for s in tax_specs.SPECS if s.key.startswith(args.prefix)]
    if not specs:
        say(f"No declared key starts with {args.prefix!r}.")
        return 2

    data = capture.load_figures(tax_specs.SPECS, *_load_texts(figures_path))
    try:
        changed = capture.capture_year(specs, args.fy, data, ask, say, today or date.today())
    except (EOFError, KeyboardInterrupt):
        say("\nStopped without writing anything.")
        return 1
    if not changed:
        say("\nNothing changed; no file written.")
        return 0

    # newline="\n": without it Windows writes CRLF, unlike the LF the files are stored with.
    docstring = ast.get_docstring(ast.parse(figures_path.read_text(encoding="utf-8"))) or ""
    figures_path.write_text(
        capture.render_figures_module(docstring + "\n", tax_specs.SPECS, data),
        encoding="utf-8",
        newline="\n",
    )
    source, revision = capture.bump_revision(init_path.read_text(encoding="utf-8"))
    init_path.write_text(source, encoding="utf-8", newline="\n")

    say(f"\nWrote {len(changed)} figure(s) for {fy_label(args.fy)}; REVISION is now {revision}.")
    provisional = data.provisional.get(args.fy, {})
    if provisional:
        say("Provisional (an estimate will warn while these stand):")
        for key in sorted(provisional):
            say(f"  {key}: {provisional[key]}")
    say(_pin(args.pack_dir))
    return 0


def _pin(pack_dir: Path) -> str:
    """The revision and hash to pin in tests/test_rulepack_au.py, from a fresh interpreter."""
    code = "from finance360.rulepacks import au;p=au.PACK;print(p.revision, p.content_hash())"
    if pack_dir.resolve() != DEFAULT_PACK_DIR.resolve():
        return "Throwaway pack directory: pin not computed."
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return f"Could not compute the pin: {result.stderr.strip()}"
    revision, digest = result.stdout.split()
    return (
        "Pin these in backend/tests/test_rulepack_au.py:\n"
        f"  PINNED_REVISION = {revision}\n"
        f'  PINNED_HASH = "{digest}"'
    )


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
