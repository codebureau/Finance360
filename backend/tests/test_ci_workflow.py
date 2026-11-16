"""CI's slow-tier path rule stays in step with the slow test files (AGENTS.md "Test tiers").

``ci.yml`` runs the slow tier only when a change touches what it exercises, including the slow
test files themselves, which it names. A file added to ``SLOW_FILES`` but not to the pattern would
never trigger its own tier; this test fails until both lists agree.
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import SLOW_FILES

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def slow_pattern() -> re.Pattern[str]:
    (line,) = [ln for ln in CI.read_text(encoding="utf-8").splitlines() if "[slow]=" in ln]
    return re.compile(line.split("'")[1])


def test_the_pattern_names_exactly_the_slow_test_files() -> None:
    named = re.search(r"backend/tests/test_\(([^)]*)\)", slow_pattern().pattern)
    assert named is not None
    assert {f"test_{name}.py" for name in named.group(1).split("|")} == SLOW_FILES


def test_plumbing_and_the_fixture_trigger_the_slow_tier_and_a_capability_alone_does_not() -> None:
    pattern = slow_pattern()
    for path in (
        "backend/finance360/migrations/versions/0024_example.py",
        "backend/finance360/core/models/tax_record.py",
        "backend/finance360/api/tax.py",
        "backend/finance360/main.py",
        "fixtures/sample_household/household.json",
        "uv.lock",
        "backend/tests/test_upgrade_populated.py",
    ):
        assert pattern.search(path), path
    for path in (
        "backend/finance360/capabilities/tax/engine.py",
        "backend/tests/test_tax_engine.py",
        "frontend/src/components/tax-page.tsx",
    ):
        assert not pattern.search(path), path


TEXT = CI.read_text(encoding="utf-8")
JOBS_HEADER = "\njobs:\n"
JOB_KEY = re.compile(r"^  ([a-z][\w-]*):\s*$", re.MULTILINE)


def job_names() -> set[str]:
    return set(JOB_KEY.findall(TEXT.split(JOBS_HEADER, 1)[1]))


def job_block(name: str) -> str:
    after = TEXT.split(JOBS_HEADER, 1)[1].split(f"\n  {name}:\n", 1)[1]
    return JOB_KEY.split(after)[0]


def test_ci_result_always_runs_and_needs_every_other_job() -> None:
    block = job_block("ci-result")
    assert re.search(r"^    name: CI result$", block, re.MULTILINE)
    assert re.search(r"^    if: always\(\)$", block, re.MULTILINE)
    needs = re.search(r"^    needs: \[([^\]]*)\]$", block, re.MULTILINE)
    assert needs is not None
    assert {n.strip() for n in needs.group(1).split(",")} == job_names() - {"ci-result"}


def test_ci_result_fails_on_failure_or_cancel_and_accepts_skipped() -> None:
    block = job_block("ci-result")
    assert "join(needs.*.result" in block
    assert "success|skipped) ;;" in block
    assert "*) " in block and "exit 1" in block


def test_docs_only_changes_start_ci_but_run_no_area_job() -> None:
    assert "paths-ignore:" not in TEXT
    for name in ("backend", "frontend", "docker"):
        assert "needs.changes.outputs" in job_block(name), name
    found = re.search(r"\[backend\]='([^']*)'", TEXT)
    assert found is not None
    for path in ("docs/adr/0001.md", "README.md", "frontend/src/a.tsx"):
        assert re.search(found.group(1), path), path
