"""The commit guard blocks real-data paths and lets ordinary source through."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "guard_paths.py"
_spec = importlib.util.spec_from_file_location("guard_paths", _SCRIPT)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

PATTERNS = guard.load_patterns()


@pytest.mark.parametrize(
    "path",
    [
        "private/inventory.md",
        "docs/private/notes.txt",
        "data/finance360.sqlite",
        "backend/x.db",
        "downloads/acct_transactions_2025.csv",
        "Downloads\\ACCT_TRANSACTIONS_2025.CSV",
        "export/transactions-acc1234.csv",
        "bank.ofx",
        "bank.QIF",
        "secrets/key.txt",
        ".env",
        ".env.local",
        "backups/db.restic",
        # The sample-export allowance is for SAMPLE accounts in the fixture folder only.
        "fixtures/sample_household/imports/cash_transactions_acc12345678_01-Jul-2026_x.csv",
        "elsewhere/cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv",
        # Statements and common export names, anywhere outside private/.
        "docs/statement_2026-06.pdf",
        "Downloads/Statement.PDF",
        "fixtures/documents/real_statement.pdf",
        "fixtures/sample_household/documents/other.pdf",
        "exports/Transactions.csv",
        "exports/bank statement june.csv",
        "exports/payment_summary_2026.txt",
        "exports/holdings.xlsx",
        "exports/holdings.xls",
    ],
)
def test_blocks(path: str) -> None:
    assert guard.is_blocked(path, PATTERNS)


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "README.md",
        "backend/finance360/core/money.py",
        "backend/finance360/data/__init__.py",
        "frontend/src/data/x.ts",
        "docs/transactions.md",
        "backend/tests/fixtures/sample_export_01.csv",
        "fixtures/sample_household/imports/cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_x.csv",
        "fixtures/sample_household/imports/investment_transactions_accSAMPLE-PI-9999_a_b.csv",
        "fixtures/documents/ato_income_statement_payer_a.pdf",
        "fixtures/documents/sample_bank_statement.pdf",
        "fixtures/sample_household/documents/ato_income_statement_sample.pdf",
        "fixtures/sample_household/imports/sample_bank_statement.csv",
        "frontend/src/components/statement-reader.tsx",
    ],
)
def test_allows(path: str) -> None:
    assert not guard.is_blocked(path, PATTERNS)


def test_todays_tracked_tree_passes() -> None:
    root = Path(__file__).resolve().parents[2]
    listing = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True
    )
    tracked = [name for name in listing.stdout.splitlines() if name]
    assert tracked
    assert [name for name in tracked if guard.is_blocked(name, PATTERNS)] == []


def test_main_reports_offenders(capsys: pytest.CaptureFixture[str]) -> None:
    assert guard.main(["README.md"]) == 0
    assert guard.main(["README.md", "private/a.md"]) == 1
    assert "private/a.md" in capsys.readouterr().out
