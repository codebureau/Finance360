"""Tests for deploy/deploy.py (ADR-0007): the sequence, driven through an injected runner.

No SSH, Docker or network: a fake host answers each command. The real thing is exercised by the
local-Docker simulation in docs/deploy.md.
"""

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "deploy.py"
_spec = importlib.util.spec_from_file_location("deploy_script", _SCRIPT)
assert _spec is not None and _spec.loader is not None
deploy = importlib.util.module_from_spec(_spec)
sys.modules["deploy_script"] = deploy
_spec.loader.exec_module(deploy)

TAG = "v1.2.3"
SHA = "abc123def456"
HEAD = "rev0001"

Result = deploy.Result


class FakeHost:
    """Answers the commands deploy sends; records them in order."""

    def __init__(
        self,
        *,
        dirty: str = "",
        running: bool = True,
        backup_exit: int = 0,
        up_exit: int = 0,
        health_sha: str = SHA,
        health_schema: str = HEAD,
        healthy_after: int = 0,
        fetch_exit: int = 0,
    ) -> None:
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.dirty, self.running, self.backup_exit = dirty, running, backup_exit
        self.up_exit, self.health_sha, self.health_schema = up_exit, health_sha, health_schema
        self.healthy_after, self.fetch_exit = healthy_after, fetch_exit
        self.health_calls = 0

    def __call__(
        self,
        argv: list[str],
        *,
        input: str | None = None,
        stream: bool = False,
        anywhere: bool = False,
    ):
        self.calls.append(argv)
        self.inputs.append(input)
        line = " ".join(argv)
        if line.startswith("git status"):
            return Result(0, self.dirty)
        if line.startswith("git describe"):
            return Result(1, "", "no tag")
        if line.startswith("git fetch"):
            return Result(
                self.fetch_exit, "", "fatal: couldn't find remote ref" if self.fetch_exit else ""
            )
        if line.startswith("git rev-parse") and "^{commit}" in line:
            return Result(0, SHA + "\n")
        if line.startswith("git rev-parse"):
            return Result(0, "prevsha00000\n")
        if " ps " in line:
            return Result(0, "containerid\n" if self.running else "")
        if "finance360.backup" in line:
            return Result(
                self.backup_exit, "backup: pre-deploy succeeded. Snapshot ab12.\n", "boom"
            )
        if "head_revision" in line:
            return Result(0, HEAD + "\n")
        if "urlopen" in line:
            self.health_calls += 1
            if self.health_calls <= self.healthy_after:
                return Result(1, "", "connection refused")
            body = {
                "status": "healthy",
                "git_sha": self.health_sha,
                "schema_version": self.health_schema,
            }
            return Result(0, json.dumps(body))
        if " up " in line:
            return Result(self.up_exit)
        return Result(0)

    def names(self) -> list[str]:
        """The step each call belongs to, in order."""
        out = []
        for argv in self.calls:
            line = " ".join(argv)
            for key, name in [
                ("git status", "status"),
                ("git fetch", "fetch"),
                ("finance360.backup", "backup"),
                ("git checkout", "checkout"),
                ("cat > .env", "env"),
                (" up ", "up"),
                ("urlopen", "health"),
            ]:
                if key in line:
                    out.append(name)
                    break
        return out


def make(host: FakeHost, out: list[str] | None = None, timeout: float = 10):
    clock = iter(range(0, 10_000, 1))
    target = deploy.Target(
        transport="local",
        remote_dir="/x",
        app_env={"FINANCE360_PORT": "8000", "FINANCE360_ALLOWED_HOSTS": "192.0.2.10:8843"},
    )
    return deploy.Deployer(
        target,
        host,
        say=(out if out is not None else []).append,
        sleep=lambda _s: None,
        clock=lambda: float(next(clock)),
        health_timeout=timeout,
    )


# --- the sequence ------------------------------------------------------------------------------


def test_backup_comes_before_anything_changes_then_health_is_verified() -> None:
    host, out = FakeHost(), []
    assert make(host, out).deploy(TAG) == 0
    names = host.names()
    assert names[:5] == ["status", "fetch", "backup", "checkout", "env"]
    assert (
        names.index("backup") < names.index("checkout") < names.index("up") < names.index("health")
    )
    backup = next(c for c in host.calls if "finance360.backup" in " ".join(c))
    assert (
        backup[-5:] == ["python", "-m", "finance360.backup", "run", "--reason", "pre-deploy"][-5:]
    )
    assert backup[backup.index("--reason") + 1] == "pre-deploy"
    report = "\n".join(out)
    assert f"GIT_SHA         {SHA}" in report
    assert "roll back with: deploy prevsha00000" in report


def test_a_failed_backup_aborts_before_the_checkout_or_build() -> None:
    host = FakeHost(backup_exit=1)
    with pytest.raises(deploy.DeployError, match="Pre-deploy Backup failed") as err:
        make(host).deploy(TAG)
    assert "previous release is still running" in str(err.value)
    assert host.names() == ["status", "fetch", "backup"]  # no checkout, env, up


@pytest.mark.parametrize("code", [1, 2, 3, 137])
def test_any_non_zero_backup_exit_aborts(code: int) -> None:
    host = FakeHost(backup_exit=code)
    with pytest.raises(deploy.DeployError, match=f"exit {code}"):
        make(host).deploy(TAG)
    assert "up" not in host.names()


def test_backups_not_set_up_points_at_the_setup_doc() -> None:
    with pytest.raises(deploy.DeployError, match="backup-setup.md"):
        make(FakeHost(backup_exit=2)).deploy(TAG)


def test_a_stopped_app_needs_an_explicit_first_flag() -> None:
    host = FakeHost(running=False)
    with pytest.raises(deploy.DeployError, match="--first"):
        make(host).deploy(TAG)
    assert "backup" not in host.names() and "up" not in host.names()

    host = FakeHost(running=False)
    assert make(host).deploy(TAG, first=True) == 0
    assert "backup" not in host.names()  # nothing running, nothing to back up
    assert "up" in host.names()


def test_a_dirty_clone_is_refused_before_anything_else() -> None:
    host = FakeHost(dirty=" M docker-compose.yml")
    with pytest.raises(deploy.DeployError, match="local changes"):
        make(host).deploy(TAG)
    assert host.names() == ["status"]


def test_an_unknown_or_moved_tag_aborts_before_the_backup() -> None:
    host = FakeHost(fetch_exit=128)
    with pytest.raises(deploy.DeployError, match="fetching v1.2.3"):
        make(host).deploy(TAG)
    assert host.names() == ["status", "fetch"]


def test_a_moved_tag_shows_gits_reason_and_does_not_silence_it() -> None:
    host = FakeHost(fetch_exit=1)
    with pytest.raises(deploy.DeployError, match=r"failed \(1\): fatal: couldn't find remote ref"):
        make(host).deploy(TAG)
    fetch = next(c for c in host.calls if c[:2] == ["git", "fetch"])
    assert "--quiet" not in fetch


@pytest.mark.parametrize(
    "tag", ["main", "1.2.3", "v1.2", "v1.2.3;rm -rf /", "../v1.2.3", "", "v1.2.3\n"]
)
def test_only_release_tags_are_accepted(tag: str) -> None:
    host = FakeHost()
    with pytest.raises(deploy.DeployError, match="not a release tag"):
        make(host).deploy(tag)
    assert host.calls == []


# --- health verification -----------------------------------------------------------------------


def test_the_wrong_git_sha_fails_and_gives_rollback_guidance() -> None:
    with pytest.raises(deploy.DeployError, match="did not come up healthy") as err:
        make(FakeHost(health_sha="oldsha000000")).deploy(TAG)
    text = str(err.value)
    assert "git_sha='oldsha000000'" in text and f"expected git_sha='{SHA}'" in text
    assert "deploy prevsha00000" in text
    assert "restore-runbook.md" in text


def test_the_wrong_schema_fails() -> None:
    with pytest.raises(deploy.DeployError, match="schema='unmigrated'"):
        make(FakeHost(health_schema="unmigrated")).deploy(TAG)


def test_health_is_retried_until_the_app_answers() -> None:
    host = FakeHost(healthy_after=3)
    assert make(host).deploy(TAG) == 0
    assert host.health_calls == 4


def test_an_app_that_never_answers_times_out() -> None:
    host = FakeHost(healthy_after=10_000)
    with pytest.raises(deploy.DeployError, match="did not come up healthy"):
        make(host, timeout=6).deploy(TAG)
    assert 2 <= host.health_calls < 10


def test_a_failed_compose_up_is_reported_with_rollback_guidance() -> None:
    with pytest.raises(deploy.DeployError, match="docker compose up failed") as err:
        make(FakeHost(up_exit=1)).deploy(TAG)
    assert "deploy prevsha00000" in str(err.value)


# --- the .env the host gets --------------------------------------------------------------------


def test_env_file_carries_the_sha_and_the_app_settings_only() -> None:
    host = FakeHost()
    make(host).deploy(TAG)
    body = next(
        i for c, i in zip(host.calls, host.inputs, strict=True) if "cat > .env" in " ".join(c)
    )
    assert body is not None
    assert body.splitlines() == [
        f"GIT_SHA={SHA}",
        "FINANCE360_ALLOWED_HOSTS=192.0.2.10:8843",
        "FINANCE360_PORT=8000",
    ]


# --- the target file ---------------------------------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "target.local.env"
    path.write_text(text, encoding="utf-8")
    return path


EXAMPLE = _SCRIPT.parent / "target.example.env"


def test_the_committed_example_is_refused_until_filled_in(tmp_path: Path) -> None:
    with pytest.raises(deploy.DeployError, match="placeholders") as err:
        deploy.load_target(EXAMPLE)
    for key in ("DEPLOY_SSH_TARGET", "DEPLOY_REMOTE_DIR", "FINANCE360_BACKUP_KEY_FILE"):
        assert key in str(err.value)


def test_main_refuses_with_a_clear_message_and_runs_nothing(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_target: object) -> Callable[..., object]:
        raise AssertionError("must not connect")

    monkeypatch.setattr(deploy, "make_runner", boom)
    assert deploy.main([TAG, "--config", str(EXAMPLE)]) == 1
    assert "still has placeholders" in capsys.readouterr().err


def test_a_missing_target_file_says_how_to_make_one(tmp_path: Path) -> None:
    with pytest.raises(deploy.DeployError, match="target.example.env"):
        deploy.load_target(tmp_path / "nope.env")


def test_a_filled_in_target_loads(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "# c\nDEPLOY_TRANSPORT=ssh\nDEPLOY_SSH_TARGET=user@host.example\n"
        "DEPLOY_REMOTE_DIR=/srv/f360\nDEPLOY_SSH_OPTIONS=-i /k/id\nFINANCE360_PORT=8123\n",
    )
    t = deploy.load_target(path)
    assert (t.transport, t.ssh_target, t.remote_dir) == ("ssh", "user@host.example", "/srv/f360")
    assert t.ssh_options == ("-i", "/k/id")
    assert t.app_env == {"FINANCE360_PORT": "8123"}


def test_ssh_needs_a_target_and_transport_must_be_known(tmp_path: Path) -> None:
    with pytest.raises(deploy.DeployError, match="DEPLOY_SSH_TARGET"):
        deploy.load_target(write(tmp_path, "DEPLOY_TRANSPORT=ssh\nDEPLOY_REMOTE_DIR=/x\n"))
    with pytest.raises(deploy.DeployError, match="DEPLOY_TRANSPORT must be"):
        deploy.load_target(write(tmp_path, "DEPLOY_TRANSPORT=ftp\nDEPLOY_REMOTE_DIR=/x\n"))


def test_ssh_runs_each_command_in_the_clone_with_quoting(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: object):
        seen.append(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    target = deploy.Target(transport="ssh", remote_dir="/srv/my app", ssh_target="u@h")
    deploy.make_runner(target)(["git", "checkout", "v1.2.3; x"])
    cmd = seen[0]
    assert cmd[:3] == ["ssh", "-o", "BatchMode=yes"] and cmd[3] == "u@h"
    assert cmd[4] == "cd '/srv/my app' && git checkout 'v1.2.3; x'"
