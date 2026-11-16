"""Tests for scripts/review.py (ADR-0006): pure logic and fixture loading into a temp directory."""

import importlib.util
import sys
from pathlib import Path, PureWindowsPath

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review.py"
_spec = importlib.util.spec_from_file_location("review_script", _SCRIPT)
assert _spec is not None and _spec.loader is not None
review = importlib.util.module_from_spec(_spec)
sys.modules["review_script"] = review
_spec.loader.exec_module(review)

BODY = """## Summary

Something.

## How to test

```
review 42
```

1. Open http://localhost:8360
   **Expected:** the page.

## Verification

done
"""


def test_extracts_section_up_to_next_heading() -> None:
    text = review.extract_how_to_test(BODY)
    assert "Open http://localhost:8360" in text
    assert "review 42" in text
    assert "Verification" not in text


def test_fenced_hash_lines_do_not_end_the_section() -> None:
    body = "## How to test\n```\n# not a heading\n```\nstep\n## Next\n"
    assert "step" in review.extract_how_to_test(body)


def test_missing_section_fails_clearly() -> None:
    with pytest.raises(review.ReviewError, match="no 'How to test' section"):
        review.extract_how_to_test("## Summary\nhi\n")


def test_comment_only_section_counts_as_empty() -> None:
    with pytest.raises(review.ReviewError, match="empty"):
        review.extract_how_to_test("## How to test\n<!-- fill me -->\n\n## Next\n")


def test_slot_defaults_and_overrides(tmp_path: Path) -> None:
    default = review.Slot.from_env({})
    assert default.port == 8360
    # The default is derived from the checkout this script lives in, not from a fixed path.
    checkout = review.main_checkout(review.ROOT)
    assert default.path == checkout.with_name(checkout.name + "-review").resolve()
    assert default.data == checkout.with_name(checkout.name + "-review-data").resolve()
    custom = review.Slot.from_env(
        {"FINANCE360_REVIEW_SLOT": str(tmp_path / "slot"), "FINANCE360_REVIEW_PORT": "9999"}
    )
    assert custom.port == 9999
    assert custom.data == tmp_path / "slot-data"


def test_default_slot_is_a_sibling_named_after_the_repo_folder() -> None:
    """A checkout named ``Finance360`` gets the slot ``Finance360-review`` in the same folder."""
    checkout = PureWindowsPath(r"C:\work\code\Finance360")
    slot = review.default_slot(checkout)
    assert slot == PureWindowsPath(r"C:\work\code\Finance360-review")
    assert slot.with_name(slot.name + "-data") == PureWindowsPath(
        r"C:\work\code\Finance360-review-data"
    )
    # Another clone, another folder name: nothing is tied to this repo's name.
    assert review.default_slot(PureWindowsPath(r"D:\src\wealth")) == PureWindowsPath(
        r"D:\src\wealth-review"
    )


def test_a_linked_worktree_resolves_to_the_main_checkouts_slot(tmp_path: Path) -> None:
    main = tmp_path / "Finance360"
    admin = main / ".git" / "worktrees" / "feature"
    admin.mkdir(parents=True)
    worktree = main / ".claude" / "worktrees" / "feature"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {admin}\n")
    assert review.main_checkout(worktree) == main
    assert review.main_checkout(main) == main
    assert review.default_slot(review.main_checkout(worktree)) == tmp_path / "Finance360-review"


def test_slot_must_not_be_this_working_copy() -> None:
    slot = review.Slot.from_env({"FINANCE360_REVIEW_SLOT": str(review.ROOT)})
    with pytest.raises(review.ReviewError):
        slot.validate()


def test_load_fixture_uses_the_slots_fixture_over_our_own(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A PR that changes the fixture must reset to that fixture, even run from main's script."""
    ours = tmp_path / "ours"
    ours.mkdir()
    (ours / "household.json").write_text("main")
    monkeypatch.setattr(review, "FIXTURE_DIR", ours)
    slot = _slot(tmp_path)
    theirs = slot.path / "fixtures" / "sample_household"
    theirs.mkdir(parents=True)
    (theirs / "household.json").write_text("pr")
    review.load_fixture(slot)
    assert (slot.data / "household.json").read_text() == "pr"


def test_load_fixture_copies_content_but_not_docs(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fixture = tmp_path / "fixture"
    (fixture / "documents").mkdir(parents=True)
    (fixture / "documents" / "a.txt").write_text("synthetic")
    (fixture / "README.md").write_text("docs")
    (fixture / ".gitkeep").write_text("")
    monkeypatch.setattr(review, "FIXTURE_DIR", fixture)
    slot = review.Slot.from_env(
        {
            "FINANCE360_REVIEW_SLOT": str(tmp_path / "slot"),
            "FINANCE360_REVIEW_DATA": str(tmp_path / "d"),
        }
    )
    review.load_fixture(slot)
    assert (slot.data / "documents" / "a.txt").read_text() == "synthetic"
    assert not (slot.data / "README.md").exists()
    assert not (slot.data / ".gitkeep").exists()


def test_real_fixture_directory_exists() -> None:
    assert review.FIXTURE_DIR.is_dir()


def _slot(tmp_path: Path):  # type: ignore[no-untyped-def]
    return review.Slot.from_env(
        {
            "FINANCE360_REVIEW_SLOT": str(tmp_path / "slot"),
            "FINANCE360_REVIEW_DATA": str(tmp_path / "d"),
        }
    )


def test_migrate_is_skipped_for_prs_without_alembic(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    monkeypatch.setattr(review, "run", lambda cmd, **_: calls.append(cmd))
    (tmp_path / "slot").mkdir()
    review.migrate(_slot(tmp_path), ["docker", "compose"], {})
    assert calls == []


def test_migrate_builds_then_runs_the_runner_in_the_image(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    monkeypatch.setattr(review, "run", lambda cmd, **_: calls.append(cmd))
    (tmp_path / "slot").mkdir()
    (tmp_path / "slot" / "alembic.ini").write_text("")
    review.migrate(_slot(tmp_path), ["docker", "compose"], {})
    assert calls == [
        ["docker", "compose", "build"],
        [
            "docker", "compose", "run", "--rm", "finance360",
            "python", "-m", "finance360.core.migrate",
        ],
    ]  # fmt: skip


def test_real_fixture_loads_into_the_data_dir_for_the_app_to_read(tmp_path: Path) -> None:
    review.load_fixture(_slot(tmp_path))
    assert (tmp_path / "d" / "household.json").is_file()


def _tooling(tmp_path: Path, name: str, script: str, compose: str = "x") -> Path:
    scripts = tmp_path / name / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "review.py").write_text(script, encoding="utf-8")
    (scripts / "review.compose.yml").write_text(compose, encoding="utf-8")
    return scripts / "review.py"


def _pair(tmp_path: Path, monkeypatch, mine_s: str, theirs_s: str, mc: str = "x", tc: str = "x"):  # type: ignore[no-untyped-def]
    mine = _tooling(tmp_path, "main", mine_s, mc)
    theirs = _tooling(tmp_path, "slot", theirs_s, tc)
    monkeypatch.setattr(review, "OVERRIDE", mine.with_name("review.compose.yml"))
    return mine, theirs


def test_hand_off_when_slot_script_differs_and_supports_it(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    m = review.HANDOFF_MARKER
    mine, theirs = _pair(tmp_path, monkeypatch, f"# {m} old", f"# {m} new")
    assert review.should_hand_off({}, mine, theirs)


def test_hand_off_when_only_compose_differs(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    m = f"# {review.HANDOFF_MARKER}"
    mine, theirs = _pair(tmp_path, monkeypatch, m, m, "a", "b")
    assert review.should_hand_off({}, mine, theirs)


def test_no_hand_off_when_tooling_is_identical(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    m = f"# {review.HANDOFF_MARKER}"
    mine, theirs = _pair(tmp_path, monkeypatch, m, m)
    assert not review.should_hand_off({}, mine, theirs)


def test_no_hand_off_to_a_script_that_predates_the_handoff(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mine, theirs = _pair(tmp_path, monkeypatch, f"# {review.HANDOFF_MARKER}", "# older")
    assert not review.should_hand_off({}, mine, theirs)


def test_no_hand_off_when_slot_has_no_script(tmp_path: Path) -> None:
    mine = _tooling(tmp_path, "main", f"# {review.HANDOFF_MARKER}")
    assert not review.should_hand_off({}, mine, tmp_path / "slot" / "scripts" / "review.py")


def test_no_loop_once_handed_off(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    m = review.HANDOFF_MARKER
    mine, theirs = _pair(tmp_path, monkeypatch, f"# {m} old", f"# {m} new")
    assert not review.should_hand_off({review.ORIGIN_ENV: str(tmp_path / "main")}, mine, theirs)


def test_hand_off_passes_origin_and_args_and_returns_exit_code(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    class Done:
        returncode = 3

    def fake_run(cmd, env, check):  # type: ignore[no-untyped-def]
        seen.update(cmd=cmd, env=env)
        return Done()

    monkeypatch.setattr(review.subprocess, "run", fake_run)
    assert review.hand_off(_slot(tmp_path), ["42"]) == 3
    assert seen["cmd"][1:] == [str(tmp_path / "slot" / "scripts" / "review.py"), "42"]  # type: ignore[index]
    assert seen["env"][review.ORIGIN_ENV] == str(review.ROOT)  # type: ignore[index]


def test_reset_hands_off_when_the_slot_tooling_differs(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Otherwise --reset restarts the slot with main's compose override, not the PR's.
    monkeypatch.setattr(review, "should_hand_off", lambda env, mine, theirs: True)
    monkeypatch.setattr(review, "hand_off", lambda slot, argv: 7)
    monkeypatch.setattr(review, "stop", lambda slot: pytest.fail("reset ran with our own tooling"))
    assert review.cmd_reset(_slot(tmp_path), ["--reset"]) == 7


def test_guard_still_refuses_slot_inside_the_invoking_copy(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # After a handoff ROOT is the invoking repo, not the slot the script now lives in.
    origin = tmp_path / "origin"
    monkeypatch.setattr(review, "ROOT", origin)
    inside = review.Slot.from_env({"FINANCE360_REVIEW_SLOT": str(origin / "slot")})
    with pytest.raises(review.ReviewError):
        inside.validate()
    _slot(tmp_path).validate()  # the slot itself, separate from the origin, is fine


# --- the slot signs in with a synthetic seeded password (ADR-0010) ---------------------------


def test_the_fixture_password_meets_the_policy_and_is_loaded_with_the_fixture(
    tmp_path: Path,
) -> None:
    from finance360.capabilities.auth.policy import password_problem

    slot = _slot(tmp_path)
    review.load_fixture(slot)
    assert password_problem(review.owner_password(slot)) is None


def test_ensure_owner_password_fills_a_data_dir_made_before_auth_and_never_overwrites(
    tmp_path: Path,
) -> None:
    slot = _slot(tmp_path)
    slot.data.mkdir()
    review.ensure_owner_password(slot)
    assert (
        review.owner_password(slot) == (review.FIXTURE_DIR / "owner-password").read_text().strip()
    )
    (slot.data / "owner-password").write_text("kept-as-is-synthetic\n")
    review.ensure_owner_password(slot)
    assert review.owner_password(slot) == "kept-as-is-synthetic"


def test_the_compose_override_points_the_app_at_the_seed_file_in_the_data_mount() -> None:
    text = review.OVERRIDE.read_text(encoding="utf-8")
    assert "FINANCE360_OWNER_PASSWORD_FILE: /data/owner-password" in text
    assert review.OWNER_PASSWORD_FILE == "owner-password"
    assert review.HANDOFF_MARKER in _SCRIPT.read_text(encoding="utf-8")


def test_capture_reads_utf8_output_whatever_the_console_encoding() -> None:
    # A PR body can hold an arrow or an ellipsis; on Windows the default decoding is cp1252.
    code = "import sys; sys.stdout.buffer.write('← Accounts …'.encode('utf-8'))"
    assert review.capture([sys.executable, "-c", code]) == "← Accounts …"
