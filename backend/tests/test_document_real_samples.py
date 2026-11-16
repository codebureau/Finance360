"""Local-only: the ATO income statement profile must read the real statement kept in
``private/samples/ato-income-statement/`` (ADR-0024 section 8).

The folder is gitignored and absent in CI and in fresh clones, so this skips there. A profile built
from a description and a synthetic fixture can pass every test and still fail on the real page.
**Nothing here asserts or prints a value, a label's text, a name or a file name.** It checks
structure only: the PDF loads with a text layer within the limits, the profile claims it, the
outcome is not ``needs_repair``, the required figures were found, the sums agree and there is one
payer block. On failure only keys, reason codes and counts are reported. ``-s`` shows the same
structure summary on a pass.

The folder is found in the main checkout via git's common dir (so it works from an agent
worktree), or via ``FINANCE360_PRIVATE_SAMPLES``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from finance360.capabilities.documents import loader, matcher, reading
from finance360.capabilities.documents.checks import evaluate
from finance360.plugins.documents.ato_income_statement import PROFILE

FOLDER = "ato-income-statement"  # the folder under private/samples/ holding these statements


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


def _statements() -> list[Path]:
    root = _samples_root()
    folder = root / FOLDER if root else None
    return sorted(folder.glob("*.pdf")) if folder and folder.is_dir() else []


STATEMENTS = _statements()


@pytest.mark.skipif(not STATEMENTS, reason=f"no real statements in private/samples/{FOLDER}")
@pytest.mark.parametrize("index", range(len(STATEMENTS)), ids=lambda n: f"statement-{n}")
def test_the_ato_profile_reads_a_real_statement(index: int) -> None:
    # Parametrised by index, never by Path: a path can carry a personal name, and pytest prints
    # parameters and locals in a failure. Keep no Path in a local that an assertion can show.
    document = loader.load_pdf(STATEMENTS[index].read_bytes())  # a LoadError names no text
    assert 1 <= len(document.pages) <= loader.MAX_PAGES
    assert matcher.claims(document, PROFILE), "the profile does not claim the statement"

    result = matcher.match(document, PROFILE)
    outcome = evaluate(PROFILE, result)
    found = sorted(f.key for f in outcome.found)
    missing = sorted(f"{n.key}:{n.reason}" for n in outcome.not_found)
    sums = [(c.kind, c.status) for c in outcome.checks]
    print(  # structure only: no value, no label text
        f"\npages={len(document.pages)} outcome={outcome.outcome} found={found} "
        f"not_found={missing} checks={sums}"
    )

    assert outcome.outcome != reading.NEEDS_REPAIR, f"needs repair: {missing}"
    required = {f.key for f in PROFILE.fields if f.required}
    assert required <= set(found), f"required figures not found: {sorted(required - set(found))}"
    # One payer block: the required labels each matched exactly once (a second block would make
    # them ambiguous and fail the line above).
    assert not any(n.reason == reading.AMBIGUOUS for n in outcome.not_found), missing
    # Every sum agrees, or was skipped for a stated reason: a part the statement does not carry and
    # the profile does not let count as zero (a statement that gives only the total).
    assert sums, "no sum check was evaluated"
    zeroable = {f.key for f in PROFILE.fields if f.zero_in_sums}
    reasons = {n.key: n.reason for n in outcome.not_found}
    for check in outcome.checks:
        assert check.status != reading.DISAGREE, check.fields
        if check.status == reading.SKIPPED:
            blockers = [k for k in check.fields if k not in found and k not in zeroable]
            assert blockers and all(reasons.get(k) == reading.LABEL_NOT_FOUND for k in blockers), (
                check.fields,
                blockers,
            )
