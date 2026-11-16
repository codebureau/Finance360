"""Local-only: every Importer must read the real exports kept in ``private/samples/<vendor>/``.

The folder is gitignored and absent in CI and in fresh clones, so this skips there. It exists
because an Importer built from a second-hand description (and fixtures invented to match) can pass
every test and still reject a real export. Nothing here asserts a value: it checks that the
registry's Importer claims each file, parses it, and finds a Stream and an account reference. On
failure only a row number and column are reported, never a value.

The folder is found in the main checkout via git's common dir (so it works from an agent worktree),
or via ``FINANCE360_PRIVATE_SAMPLES``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from finance360.capabilities.imports import registry
from finance360.capabilities.imports.importer import STREAMS
from finance360.plugins.importers.vanguard_pi import IMPORTER

VENDOR = "vanguard"  # the folder under private/samples/ holding this Importer's exports


def _samples_root() -> Path | None:
    if env := os.environ.get("FINANCE360_PRIVATE_SAMPLES"):
        return Path(env)
    here = Path(__file__).resolve().parent
    try:
        common = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=here,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(common).parent / "private" / "samples"


def _exports() -> list[Path]:
    root = _samples_root()
    folder = root / VENDOR if root else None
    return sorted(folder.glob("*.csv")) if folder and folder.is_dir() else []


EXPORTS = _exports()


@pytest.mark.skipif(not EXPORTS, reason="no real exports in private/samples/vanguard")
@pytest.mark.parametrize("path", EXPORTS, ids=[f"export-{n}" for n in range(len(EXPORTS))])
def test_the_vanguard_importer_reads_a_real_export(path: Path) -> None:
    registry.register(IMPORTER)
    content, name = path.read_bytes(), path.name
    assert registry.claimants(content, name) == [IMPORTER], "no single Importer claims the file"
    assert IMPORTER.claims(content, ""), "recognition must not depend on the file name"
    parsed = IMPORTER.parse(content, {}, name)  # an ImporterError names only a row and column
    assert parsed.rows, "the file parsed to no rows"
    assert parsed.stream in STREAMS
    assert parsed.account_reference is not None, "no account reference found"
    assert parsed.coverage_from <= parsed.coverage_to
