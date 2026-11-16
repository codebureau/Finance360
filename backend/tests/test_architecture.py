"""Prove the import-linter contract in pyproject.toml actually fails on violations.

Each case copies the real package into a temp tree, adds one deliberately violating module, and
runs import-linter there against the real contract. The unmodified copy must pass, so a failure
is attributable to the injected import alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_LINTER = "from importlinter.cli import lint_imports_command; lint_imports_command()"


def _lint(root: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(root / "backend")}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", RUN_LINTER],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _tree(tmp_path: Path) -> Path:
    shutil.copy(REPO / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(
        REPO / "backend" / "finance360",
        tmp_path / "backend" / "finance360",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return tmp_path


def test_unmodified_package_satisfies_the_contract(tmp_path: Path) -> None:
    result = _lint(_tree(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


# (module that gains the bad import, the import, text of the contract that must break)
VIOLATIONS = [
    ("core/bad.py", "import finance360.capabilities", "entry points > plugins"),
    ("core/bad.py", "import finance360.behaviours", "Core imports nothing above it"),
    ("core/bad.py", "import finance360.rulepacks", "Core imports nothing above it"),
    (
        "capabilities/household/bad.py",
        "import finance360.capabilities.position",
        "projection > tax > position > library > documents > household",
    ),
    (
        "capabilities/reference/bad.py",
        "import finance360.capabilities.household",
        "Reference Capability depends on core only",
    ),
    (
        "capabilities/reference/bad.py",
        "import finance360.capabilities.position",
        "Reference Capability depends on core only",
    ),
    (
        "capabilities/reference/bad.py",
        "import finance360.capabilities.auth",
        "Data Capabilities never depend on the auth",
    ),
    ("capabilities/position/bad.py", "import finance360.plugins", "entry points > plugins"),
    ("capabilities/auth/bad.py", "import finance360.plugins", "entry points > plugins"),
    (
        "capabilities/auth/bad.py",
        "import finance360.capabilities.household",
        "Auth Capability stands apart",
    ),
    (
        "capabilities/position/bad.py",
        "import finance360.capabilities.auth",
        "Data Capabilities never depend on the auth",
    ),
    (
        "capabilities/reference/bad.py",
        "import finance360.capabilities.imports",
        "imports > reference",
    ),
    (
        "capabilities/imports/bad.py",
        "import finance360.capabilities.position",
        "Imports Capability uses reference and core only",
    ),
    (
        "capabilities/imports/bad.py",
        "import finance360.capabilities.auth",
        "Auth Capability stands apart",
    ),
    (
        "capabilities/position/bad.py",
        "import finance360.capabilities.imports",
        "Position and household never import the Imports Capability",
    ),
    ("capabilities/imports/bad.py", "import finance360.plugins", "entry points > plugins"),
    (
        "capabilities/position/bad.py",
        "import finance360.capabilities.projection",
        "projection > tax > position > library > documents > household",
    ),
    (
        "capabilities/position/bad.py",
        "import finance360.capabilities.tax",
        "projection > tax > position > library > documents > household",
    ),
    (
        "capabilities/tax/service.py",
        "import finance360.capabilities.projection",
        "projection > tax > position > library > documents > household",
    ),
    (
        "capabilities/tax/engine.py",
        "import finance360.core.db",
        "The tax engine and its pure modules",
    ),
    (
        "capabilities/tax/rules.py",
        "import finance360.capabilities.position",
        "The tax engine and its pure modules",
    ),
    (
        "capabilities/tax/labels.py",
        "import finance360.capabilities.tax.service",
        "The tax engine and its pure modules",
    ),
    (
        "capabilities/tax/engine.py",
        "import finance360.core.rulepack",
        "The tax engine and its input never read a Rule pack",
    ),
    (
        "capabilities/tax/input.py",
        "import finance360.rulepacks",
        "The tax engine and its input never read a Rule pack",
    ),
    (
        "capabilities/tax/service.py",
        "import finance360.capabilities.imports",
        "Tax uses core, household and position only",
    ),
    (
        "capabilities/tax/service.py",
        "import finance360.capabilities.reference",
        "Tax uses core, household and position only",
    ),
    (
        "capabilities/tax/service.py",
        "import finance360.rulepacks.au",
        "Capabilities and the API never import a Rule pack",
    ),
    (
        "capabilities/projection/engine.py",
        "import finance360.capabilities.tax",
        "The projection engine and its input are pure",
    ),
    (
        "capabilities/reference/bad.py",
        "import finance360.capabilities.tax",
        "Reference Capability depends on core only",
    ),
    (
        "capabilities/projection/engine.py",
        "import finance360.core.db",
        "The projection engine and its input are pure",
    ),
    (
        "capabilities/projection/input.py",
        "import finance360.capabilities.position",
        "The projection engine and its input are pure",
    ),
    (
        "capabilities/projection/assumptions.py",
        "import finance360.core.models",
        "The projection engine and its input are pure",
    ),
    (
        "capabilities/projection/engine.py",
        "import finance360.core.rulepack",
        "never read a Rule pack",
    ),
    (
        "capabilities/projection/input.py",
        "import finance360.rulepacks",
        "never read a Rule pack",
    ),
    (
        "capabilities/projection/service.py",
        "import finance360.rulepacks.au",
        "Capabilities and the API never import a Rule pack",
    ),
    ("api/bad.py", "import finance360.rulepacks.au", "Capabilities and the API never import"),
    (
        "rulepacks/au/bad.py",
        "import finance360.capabilities.projection",
        "A Rule pack imports core only",
    ),
    ("rulepacks/au/bad.py", "import finance360.core.models", "A Rule pack imports core only"),
    ("rulepacks/au/bad.py", "import finance360.core.db", "A Rule pack imports core only"),
    (
        "plugins/importers/bad.py",
        "import finance360.core.db",
        "Importers are pure",
    ),
    (
        "plugins/importers/bad.py",
        "import finance360.core.audit",
        "Importers are pure",
    ),
    (
        "plugins/importers/bad.py",
        "import finance360.capabilities.imports.service",
        "Importers are pure",
    ),
    (
        "capabilities/ai/bad.py",
        "import finance360.capabilities.position",
        "The AI gateway imports core only",
    ),
    (
        "capabilities/ai/bad.py",
        "import finance360.capabilities.ask",
        "The AI gateway imports core only",
    ),
    (
        "capabilities/ask/bad.py",
        "import finance360.capabilities.tax",
        "Ask imports the AI gateway, What's due and core only",
    ),
    (
        "capabilities/due/bad.py",
        "import finance360.capabilities.ask",
        "Nothing imports Ask but the API",
    ),
    (
        "capabilities/position/bad.py",
        "import finance360.capabilities.ask",
        "Nothing imports Ask but the API",
    ),
    (
        "capabilities/ai/redact.py",
        "import finance360.core.models",
        "Redaction and pricing are pure",
    ),
    (
        "capabilities/ai/pricing.py",
        "import finance360.capabilities.ai.gateway",
        "Redaction and pricing are pure",
    ),
    (
        "plugins/ai_providers/bad.py",
        "import finance360.capabilities.ai.gateway",
        "AI providers see only a redacted request",
    ),
    (
        "plugins/ai_providers/bad.py",
        "import finance360.core.db",
        "AI providers see only a redacted request",
    ),
    (
        "api/bad.py",
        "import finance360.capabilities.ai.gateway",
        "The API reaches the AI gateway only through its service",
    ),
    (
        "capabilities/ai/bad.py",
        "import finance360.capabilities.auth",
        "Data Capabilities never depend on the auth",
    ),
    (
        "capabilities/library/bad.py",
        "import finance360.capabilities.position",
        "The Library imports core and the Document intake loaders only",
    ),
    (
        "capabilities/library/bad.py",
        "import finance360.capabilities.tax",
        "The Library imports core and the Document intake loaders only",
    ),
    (
        "capabilities/library/bad.py",
        "import finance360.capabilities.household",
        "The Library imports core and the Document intake loaders only",
    ),
    (
        "capabilities/library/bad.py",
        "import finance360.capabilities.position",
        "position > library > documents > household",
    ),
    (
        "capabilities/library/bad.py",
        "import finance360.plugins",
        "entry points > plugins",
    ),
    (
        "capabilities/library/bad.py",
        "import finance360.capabilities.auth",
        "Auth Capability stands apart",
    ),
    (
        "capabilities/documents/bad.py",
        "import finance360.capabilities.library",
        "position > library > documents > household",
    ),
    (
        "capabilities/household/bad.py",
        "import finance360.capabilities.library",
        "position > library > documents > household",
    ),
    (
        "capabilities/library/files.py",
        "import finance360.capabilities.library.service",
        "Library files, kinds and text extraction are plain",
    ),
    (
        "capabilities/library/registry.py",
        "import finance360.core.audit",
        "Library files, kinds and text extraction are plain",
    ),
    (
        "capabilities/library/files.py",
        "import finance360.core.db",
        "Library files, kinds and text extraction are plain",
    ),
    (
        "api/bad.py",
        "import finance360.capabilities.library.files",
        "The API reaches the Library only through its service",
    ),
    (
        "api/bad.py",
        "import finance360.capabilities.library.registry",
        "The API reaches the Library only through its service",
    ),
    (
        "capabilities/tax/bad.py",
        "import finance360.capabilities.documents.loader",
        "Nothing imports Document intake but the API",
    ),
    ("api/bad.py", "import finance360.plugins", "service layer"),
    ("api/bad.py", "import finance360.behaviours", "service layer"),
    ("jobs/bad.py", "import finance360.mcp", "do not depend on each other"),
]


@pytest.mark.parametrize(("path", "statement", "contract"), VIOLATIONS)
def test_violating_import_breaks_the_contract(
    tmp_path: Path, path: str, statement: str, contract: str
) -> None:
    root = _tree(tmp_path)
    (root / "backend" / "finance360" / path).write_text(f"{statement}\n")
    result = _lint(root)
    assert result.returncode != 0, result.stdout
    assert contract in result.stdout
    assert "BROKEN" in result.stdout
