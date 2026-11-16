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
