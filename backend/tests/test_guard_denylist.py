"""The personal-terms denylist guard: what it blocks, and that it reveals no term.

Every term here is invented (``Zorblatt``, ``Quxwidget``) and lives in a denylist file created
inside the test; ``private/`` is never read. The git-backed tests run the script as a subprocess
against a throwaway repository, because what matters is what it prints.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "guard_denylist.py"
_spec = importlib.util.spec_from_file_location("guard_denylist", SCRIPT)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules["guard_denylist"] = guard
_spec.loader.exec_module(guard)

SECRET_LINE = "the quarterly Zorblatt figure is private"
DENYLIST = "# comment\n\nZorblatt\ndocs: Quxwidget Corp\n"


def _env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"CI", "GITHUB_ACTIONS", "FINANCE360_DENYLIST", "FINANCE360_FORK_PR"}
    }
    env.update(extra)
    return env


def _git(repo: Path, *args: str) -> None:
    ident = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    subprocess.run(["git", *ident, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    return path


@pytest.fixture
def denylist(tmp_path: Path) -> Path:
    path = tmp_path / "denylist.txt"
    path.write_text(DENYLIST, encoding="utf-8")
    return path


def _run(
    repo: Path, *args: str, denylist: Path | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    if denylist is not None:
        env["FINANCE360_DENYLIST"] = str(denylist)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        env=_env(**env),
        capture_output=True,
        text=True,
        check=False,
    )


def _stage(repo: Path, name: str, text: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", name)


# --- Parsing and matching (unit) ---


def test_parse_skips_comments_and_blanks_and_numbers_the_entries() -> None:
    entries = guard.parse_denylist("# note\n\nZorblatt  # trailing\ndocs: Quxwidget Corp\n")
    assert [(e.number, e.scope, e.term) for e in entries] == [
        (1, "all", "Zorblatt"),
        (2, "docs", "Quxwidget Corp"),
    ]


@pytest.mark.parametrize(
    ("line", "hit"),
    [
        ("a Zorblatt here", True),
        ("ZORBLATT", True),
        ("(zorblatt)", True),
        ("Zorblatts", False),
        ("unzorblatt", False),
        ("zorblatt_id", False),
    ],
)
def test_matching_is_case_insensitive_on_word_boundaries(line: str, hit: bool) -> None:
    (entry,) = guard.parse_denylist("Zorblatt")
    assert bool(guard.scan_line([entry], line, prose=False)) is hit


def test_a_term_may_contain_spaces_of_any_width() -> None:
    (entry,) = guard.parse_denylist("Quxwidget Corp")
    assert guard.scan_line([entry], "by quxwidget   corp today", prose=False)
    assert not guard.scan_line([entry], "quxwidgetcorp", prose=False)


@pytest.mark.parametrize(
    ("path", "prose"),
    [
        ("docs/adr/0001-x.md", True),
        ("docs/guide.txt", True),
        ("README.md", True),
        ("CONTEXT.md", True),
        ("frontend/src/guide/01-intro.md", True),
        (".github/pull_request_template.md", True),
        (".claude/agents/developer.md", True),
        ("backend/finance360/plugins/importers/x.py", False),
        ("backend/tests/test_x.py", False),
        ("frontend/src/App.tsx", False),
    ],
)
def test_prose_paths(path: str, prose: bool) -> None:
    assert guard.is_prose(path) is prose


def test_added_lines_parses_paths_and_line_numbers() -> None:
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,0 +5,2 @@\n+one\n+two\n"
        "@@ -9 +12 @@\n-old\n+new\n"
        "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
    )
    assert guard.added_lines(diff) == {"x.py": [(5, "one"), (6, "two"), (12, "new")]}


# --- The script, end to end ---


def test_a_planted_term_in_the_staged_diff_fails_and_the_output_reveals_nothing(
    repo: Path, denylist: Path
) -> None:
    _stage(repo, "a.py", f"x = 1\n# {SECRET_LINE}\n")
    done = _run(repo, denylist=denylist)
    output = done.stdout + done.stderr
    assert done.returncode == 1
    assert "a.py:2: matches denylist entry 1 (all)" in output
    assert "zorblatt" not in output.lower()
    assert "quarterly" not in output  # nor any other part of the offending line
    assert SECRET_LINE not in output


def test_a_term_hidden_in_a_file_name_is_reported_without_the_name(
    repo: Path, denylist: Path
) -> None:
    _stage(repo, "zorblatt-notes.py", "x = 1\n")
    done = _run(repo, denylist=denylist)
    assert done.returncode == 1
    assert "zorblatt" not in (done.stdout + done.stderr).lower()
    assert "<path redacted>" in done.stdout


def test_show_is_opt_in_and_prints_the_term(repo: Path, denylist: Path) -> None:
    _stage(repo, "a.py", f"# {SECRET_LINE}\n")
    done = _run(repo, "--show", denylist=denylist)
    assert done.returncode == 1
    assert "[Zorblatt]" in done.stdout


def test_a_docs_term_blocks_prose_but_not_code(repo: Path, denylist: Path) -> None:
    _stage(repo, "notes.md", "We work with Quxwidget Corp.\n")
    blocked = _run(repo, denylist=denylist)
    assert blocked.returncode == 1
    assert "notes.md:1: matches denylist entry 2 (docs)" in blocked.stdout

    _git(repo, "reset", "-q")
    (repo / "notes.md").unlink()
    _stage(repo, "importer.py", "PROVIDER = 'Quxwidget Corp'\n")
    assert _run(repo, denylist=denylist).returncode == 0


def test_only_added_lines_count_in_base_mode(repo: Path, denylist: Path) -> None:
    _stage(repo, "old.py", "# Zorblatt was here long ago\n")
    _git(repo, "commit", "-q", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _stage(repo, "new.py", "x = 1\n")
    _git(repo, "commit", "-q", "-m", "clean")
    assert _run(repo, "--base", base, denylist=denylist).returncode == 0

    _stage(repo, "newer.py", f"# {SECRET_LINE}\n")
    _git(repo, "commit", "-q", "-m", "dirty")
    done = _run(repo, "--base", base, denylist=denylist)
    assert done.returncode == 1
    assert "newer.py:1:" in done.stdout and "old.py" not in done.stdout


def test_all_mode_scans_existing_files(repo: Path, denylist: Path) -> None:
    _stage(repo, "old.py", "# Zorblatt was here long ago\n")
    _git(repo, "commit", "-q", "-m", "base")
    done = _run(repo, "--all", denylist=denylist)
    assert done.returncode == 1
    assert "old.py:1: matches denylist entry 1 (all)" in done.stdout
    assert "zorblatt" not in (done.stdout + done.stderr).lower()


def test_commit_message_mode_fails_on_a_planted_term(tmp_path: Path, denylist: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(f"Add things\n\n{SECRET_LINE}\n", encoding="utf-8")
    done = _run(tmp_path, "--text", str(message), denylist=denylist)
    assert done.returncode == 1
    assert "COMMIT_EDITMSG:3: matches denylist entry 1" in done.stdout
    assert SECRET_LINE not in done.stdout + done.stderr
    message.write_text("Add things\n", encoding="utf-8")
    assert _run(tmp_path, "--text", str(message), denylist=denylist).returncode == 0


def test_a_docs_term_is_blocked_in_text_such_as_a_pr_body(tmp_path: Path, denylist: Path) -> None:
    body = tmp_path / "body.txt"
    body.write_text("Checked against Quxwidget Corp.\n", encoding="utf-8")
    assert _run(tmp_path, "--text", str(body), denylist=denylist).returncode == 1


# --- A missing denylist ---


def test_a_missing_denylist_passes_locally_with_a_notice(repo: Path, tmp_path: Path) -> None:
    done = _run(repo, FINANCE360_DENYLIST=str(tmp_path / "absent.txt"))
    assert done.returncode == 0
    assert "no denylist found" in done.stdout


def test_a_missing_or_empty_denylist_fails_in_ci(repo: Path, tmp_path: Path) -> None:
    missing = _run(repo, CI="true", FINANCE360_DENYLIST=str(tmp_path / "absent.txt"))
    assert missing.returncode == 1
    assert "FINANCE360_DENYLIST" in missing.stderr

    empty = tmp_path / "empty.txt"
    empty.write_text("\n# nothing\n", encoding="utf-8")
    assert _run(repo, CI="true", FINANCE360_DENYLIST=str(empty)).returncode == 1


def test_a_fork_pull_request_in_ci_skips_with_a_notice(repo: Path, tmp_path: Path) -> None:
    done = _run(
        repo, CI="true", FINANCE360_FORK_PR="true", FINANCE360_DENYLIST=str(tmp_path / "absent.txt")
    )
    assert done.returncode == 0
    assert "fork" in done.stdout


# --- gitleaks identifier shapes (tool-dependent, like the restic tests) ---

GITLEAKS = shutil.which("gitleaks")
needs_gitleaks = pytest.mark.skipif(GITLEAKS is None, reason="gitleaks is not on PATH")


def _gitleaks(tmp_path: Path, text: str) -> subprocess.CompletedProcess[str]:
    target = tmp_path / "scan"
    target.mkdir()
    (target / "sample.txt").write_text(text, encoding="utf-8")
    assert GITLEAKS is not None
    return subprocess.run(
        [
            GITLEAKS,
            "dir",
            str(target),
            "--no-banner",
            "--redact",
            "-c",
            str(ROOT / ".gitleaks.toml"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@needs_gitleaks
@pytest.mark.parametrize(
    "text",
    [
        # Each shape is split in the source so this file does not trip the rules it tests.
        "TFN: 1234" + "56782",
        "tax file number 123 " + "456 782",
        "ref 123 " + "456 782 on file",
        "BSB: 123" + "-456",
        "bsb 123" + "456",
        "Account number: 8765" + "4329",
        "acct 8765" + "4329",
        "Member number 5551" + "2345",
    ],
)
def test_gitleaks_flags_identifier_shapes(tmp_path: Path, text: str) -> None:
    assert _gitleaks(tmp_path, text).returncode == 1


@needs_gitleaks
@pytest.mark.parametrize(
    "text",
    [
        "ABN 12 345 678 901",
        "the members list has 87654329 rows",
        "Account 1234567",
        "an ordinary sentence 123-456 about nothing",
    ],
)
def test_gitleaks_leaves_ordinary_text_alone(tmp_path: Path, text: str) -> None:
    assert _gitleaks(tmp_path, text).returncode == 0
