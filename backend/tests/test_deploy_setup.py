"""Tests for ``deploy setup`` (ADR-0012): a fake host answers each command; nothing real runs.

The real thing (Docker, Caddy, a taken port, a second run) is exercised by the local simulation in
docs/deploy.md. Here: the order, the key handling, idempotence, refusals and the target-file edit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath

import pytest
from test_deploy_script import SHA, FakeHost, Result, deploy

KEY = "synthetic-key-0123456789-abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJ"
PEM = "-----BEGIN CERTIFICATE-----\nc3ludGhldGljCg==\n-----END CERTIFICATE-----\n"
TS_NAME = "finance.tailnet-example.ts.net"
KEY_PATH = "/srv/f360/secrets/backup-key"


class SetupHost(FakeHost):
    """FakeHost plus the commands only setup sends."""

    def __init__(
        self,
        *,
        tools_missing: tuple[str, ...] = (),
        port_in_use: bool = False,
        proxy_running: bool = False,
        container_publishes: bool = False,
        key_exists: bool = False,
        clone_exists: bool = True,
        running: bool = False,
        probe_code: str = "422",
        **kwargs: object,
    ) -> None:
        super().__init__(running=running, **kwargs)  # type: ignore[arg-type]
        self.tools_missing, self.port_in_use = tools_missing, port_in_use
        self.proxy_running, self.key_exists = proxy_running, key_exists
        self.clone_exists, self.probe_code = clone_exists, probe_code
        self.container_publishes = container_publishes

    def __call__(
        self,
        argv: list[str],
        *,
        input: str | None = None,
        stream: bool = False,
        anywhere: bool = False,
    ):
        if argv[0] in self.tools_missing:
            self.calls.append(argv)
            self.inputs.append(input)
            return Result(127, "", "not found")
        if argv[:2] == ["docker", "--version"] or argv[:3] == ["docker", "compose", "version"]:
            return self._record(argv, input, Result(0, "ok"))
        if argv[0] == "git" and argv[1] == "--version" or argv[:2] == ["tailscale", "version"]:
            return self._record(argv, input, Result(0, "ok"))
        if argv[:2] == ["sh", "-c"] and argv[2] == deploy.DISK_SCRIPT:
            out = (
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 1 1 9999999 1% /"
            )
            return self._record(argv, input, Result(0, out))
        if argv[:2] == ["sh", "-c"] and argv[2] == deploy.PORT_SCRIPT:
            return self._record(argv, input, Result(0 if self.port_in_use else 1))
        if argv[:2] == ["docker", "ps"] and any(a.startswith("publish=") for a in argv):
            out = "def456\n" if self.container_publishes else ""
            return self._record(argv, input, Result(0, out))
        if argv[:2] == ["docker", "ps"]:
            return self._record(argv, input, Result(0, "abc123\n" if self.proxy_running else ""))
        if argv[0] == "test":
            return self._record(argv, input, Result(0 if self.clone_exists else 1))
        if argv[:2] == ["sh", "-c"] and argv[2] == deploy.MKDIR_SCRIPT:
            return self._record(argv, input, Result(0))
        if argv[:2] == ["git", "clone"] or argv[0] in {"mkdir", "chown"}:
            return self._record(argv, input, Result(0 if argv[0] != "chown" else 1))
        if argv[:2] == ["sh", "-c"] and argv[2] == deploy.KEY_SCRIPT:
            state = "exists" if self.key_exists else "created"
            return self._record(argv, input, Result(0, state + "\n"))
        if argv[:2] == ["tailscale", "status"]:
            body = json.dumps({"Self": {"DNSName": TS_NAME + "."}})
            return self._record(argv, input, Result(0, body))
        if argv[:2] == ["tailscale", "serve"]:
            return self._record(argv, input, Result(0))
        if argv[0] == "curl":
            return self._record(argv, input, Result(0, self.probe_code))
        if "proxy" in argv and "cat" in argv:
            return self._record(argv, input, Result(0, PEM))
        return super().__call__(argv, input=input, stream=stream, anywhere=anywhere)

    def _record(self, argv: list[str], input: str | None, result: Result) -> Result:
        self.calls.append(argv)
        self.inputs.append(input)
        return result


def config(tmp_path: Path) -> Path:
    path = tmp_path / "target.local.env"
    path.write_text(
        "# my host\n"
        "DEPLOY_TRANSPORT=ssh\nDEPLOY_SSH_TARGET=ops@host.example\nDEPLOY_REMOTE_DIR=/srv/f360/app\n"
        "DEPLOY_REPO_URL=https://example.invalid/finance360.git\n"
        "FINANCE360_HTTPS_PORT=8843\nFINANCE360_ALLOWED_HOSTS=192.0.2.10:8843\n"
        f"FINANCE360_BACKUP_KEY_FILE={KEY_PATH}\nFINANCE360_RCLONE_CONFIG_DIR=/srv/f360/rclone\n",
        encoding="utf-8",
    )
    return path


def make(tmp_path: Path, host: SetupHost, *, tailscale: bool = False):  # noqa: ANN201
    path = config(tmp_path)
    out: list[str] = []
    clock = iter(range(0, 10_000))
    deployer = deploy.Deployer(
        deploy.load_target(path),
        host,
        say=out.append,
        sleep=lambda _s: None,
        clock=lambda: float(next(clock)),
        health_timeout=10,
    )
    setup = deploy.Setup(
        deployer, path, tmp_path / "root.crt", tailscale=tailscale, new_key=lambda: KEY
    )
    return setup, out, path


@pytest.fixture(autouse=True)
def _fake_der(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy.ssl, "PEM_cert_to_DER_cert", lambda _pem: b"der")


def steps(host: SetupHost) -> list[str]:
    names = []
    for argv in host.calls:
        line = " ".join(argv)
        for key, name in [
            ("ss -ltnH", "port"),
            ("git clone", "clone"),
            ("mkdir", "folders"),
            ('cat > "$1"', "key"),
            ("git checkout", "checkout"),
            (" up ", "up"),
            ("proxy cat", "ca"),
        ]:
            if key in line:
                names.append(name)
                break
    return names


# --- the run -----------------------------------------------------------------------------------


def test_setup_checks_first_then_makes_folders_key_deploys_and_exports_the_ca(
    tmp_path: Path,
) -> None:
    host = SetupHost(clone_exists=False)
    setup, out, _ = make(tmp_path, host)
    assert setup.go("v1.2.3") == 0
    order = steps(host)
    assert order[0] == "port"
    assert order.index("port") < order.index("clone") < order.index("key")
    assert order.index("key") < order.index("checkout") < order.index("up") < order.index("ca")
    # first deploy: nothing running, so no Backup is attempted
    assert not any("finance360.backup" in " ".join(c) for c in host.calls)
    assert (tmp_path / "root.crt").read_text(encoding="ascii") == PEM
    assert hashlib.sha256(b"der").hexdigest() in "\n".join(out)
    text = "\n".join(out)
    for os_name in ("Windows", "iOS", "Android"):
        assert os_name in text
    assert "https://192.0.2.10:8843" in text


def test_the_backup_key_is_shown_once_and_only_travels_on_stdin(tmp_path: Path) -> None:
    host = SetupHost()
    setup, out, _ = make(tmp_path, host)
    setup.go("v1.2.3")
    shown = [m for m in out if KEY in m]
    assert len(shown) == 1
    # never in a command line, and never in the .env written for compose
    assert not any(KEY in part for argv in host.calls for part in argv)
    inputs = [i for i in host.inputs if i]
    assert [i for i in inputs if KEY in i] == [KEY]
    assert not any(KEY in i for i in inputs if "GIT_SHA" in i)
    # restrictive modes: the key is umask 077 and chmod 400; the folders are chmod 700
    assert "umask 077" in deploy.KEY_SCRIPT and "chmod 400" in deploy.KEY_SCRIPT
    assert "chmod 700" in deploy.MKDIR_SCRIPT


def test_a_second_run_never_regenerates_or_shows_the_key(tmp_path: Path) -> None:
    host = SetupHost(key_exists=True, proxy_running=True, running=True)
    setup, out, path = make(tmp_path, host)
    before = path.read_text(encoding="utf-8")
    assert setup.go("v1.2.3") == 0
    assert not any(KEY in m for m in out)
    assert any("left alone" in m for m in out)
    assert path.read_text(encoding="utf-8") == before  # target.local.env untouched
    assert not any(c[:2] == ["git", "clone"] for c in host.calls)  # the clone is kept
    assert any("finance360.backup" in " ".join(c) for c in host.calls)  # a normal deploy


# --- refusals: nothing changes -----------------------------------------------------------------


def test_a_taken_port_is_refused_before_anything_changes(tmp_path: Path) -> None:
    host = SetupHost(port_in_use=True, clone_exists=False)
    setup, _, _ = make(tmp_path, host)
    with pytest.raises(deploy.DeployError, match="port 8843 is already in use"):
        setup.go("v1.2.3")
    assert steps(host) == ["port"]
    assert not any(c[0] in {"mkdir", "chown"} for c in host.calls)


def test_a_port_published_by_another_container_is_refused(tmp_path: Path) -> None:
    # Socket and ss probes can miss Docker-published ports (Docker Desktop, no userland proxy).
    host = SetupHost(port_in_use=False, container_publishes=True, clone_exists=False)
    setup, _, _ = make(tmp_path, host)
    with pytest.raises(deploy.DeployError, match="port 8843 is already in use"):
        setup.go("v1.2.3")
    assert not any(c[0] in {"mkdir", "chown"} or c[:2] == ["git", "clone"] for c in host.calls)


def test_our_own_running_proxy_does_not_count_as_a_taken_port(tmp_path: Path) -> None:
    host = SetupHost(port_in_use=True, proxy_running=True)
    setup, _, _ = make(tmp_path, host)
    assert setup.check() == 8843


def test_missing_prerequisites_are_named_and_nothing_is_installed(tmp_path: Path) -> None:
    host = SetupHost(tools_missing=("docker", "tailscale"))
    setup, _, _ = make(tmp_path, host, tailscale=True)
    with pytest.raises(deploy.DeployError) as err:
        setup.check()
    message = str(err.value)
    assert "Docker is not installed" in message and "compose plugin" in message
    assert "tailscale is not installed" in message
    assert "nothing was installed" in message
    assert not any(c[0] in {"apt", "apt-get", "sudo", "curl", "brew"} for c in host.calls)


# --- Tailscale ---------------------------------------------------------------------------------


def test_tailscale_adds_the_name_once_and_points_serve_at_the_proxy(tmp_path: Path) -> None:
    host = SetupHost()
    setup, out, path = make(tmp_path, host, tailscale=True)
    setup.go("v1.2.3")
    text = path.read_text(encoding="utf-8")
    assert f"FINANCE360_ALLOWED_HOSTS=192.0.2.10:8843,{TS_NAME}\n" in text
    assert text.startswith("# my host\n")  # everything else is as it was
    serve = next(c for c in host.calls if c[:2] == ["tailscale", "serve"])
    assert serve[-1] == "https+insecure://127.0.0.1:8843"  # the proxy, never the app
    assert any("Host intact" in m for m in out)
    # the new name reached the host's .env (the deploy ran after the file changed)
    env = next(i for i in host.inputs if i and i.startswith("GIT_SHA"))
    assert f"FINANCE360_ALLOWED_HOSTS=192.0.2.10:8843,{TS_NAME}" in env
    # run again: not added twice
    again, _, _ = make_again(path, host, tmp_path)
    again.go("v1.2.3")
    assert path.read_text(encoding="utf-8").count(TS_NAME) == 1


def make_again(path: Path, host: SetupHost, tmp_path: Path):  # noqa: ANN201
    out: list[str] = []
    deployer = deploy.Deployer(
        deploy.load_target(path), host, say=out.append, sleep=lambda _s: None
    )
    return deploy.Setup(deployer, path, tmp_path / "r.crt", tailscale=True), out, path


def test_a_serve_that_changes_the_host_is_flagged(tmp_path: Path) -> None:
    host = SetupHost(probe_code="403")
    setup, out, _ = make(tmp_path, host, tailscale=True)
    setup.go("v1.2.3")
    assert any("WARNING" in m and "403" in m and "Host" in m for m in out)


def test_add_allowed_host_changes_only_that_line(tmp_path: Path) -> None:
    path = tmp_path / "t.env"
    path.write_bytes(b'A=1\r\nFINANCE360_ALLOWED_HOSTS="a.example"\r\n# c\r\n')
    assert deploy.add_allowed_host(path, "B.example") is True
    assert path.read_bytes() == b"A=1\r\nFINANCE360_ALLOWED_HOSTS=a.example,B.example\r\n# c\r\n"
    assert deploy.add_allowed_host(path, "b.example") is False
    path.write_text("A=1\n", encoding="utf-8")
    with pytest.raises(deploy.DeployError, match="not set"):
        deploy.add_allowed_host(path, "x.example")


def test_setup_needs_allowed_hosts_and_the_key_path(tmp_path: Path) -> None:
    host = SetupHost()
    setup, _, _ = make(tmp_path, host)
    setup.deployer.target = deploy.Target(
        transport="ssh", remote_dir="/srv/f360/app", ssh_target="ops@h", repo_url="u"
    )
    with pytest.raises(deploy.DeployError) as err:
        setup.check()
    assert "FINANCE360_ALLOWED_HOSTS is not set" in str(err.value)
    assert "FINANCE360_BACKUP_KEY_FILE is not set" in str(err.value)
    assert SHA  # imported for the shared fixtures; keeps the module's constants in use


def test_host_paths_stay_posix_when_deploy_runs_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # deploy runs on the Windows desktop; host paths must never pick up its separators.
    monkeypatch.setattr(deploy, "Path", PureWindowsPath)
    host = SetupHost(clone_exists=False)
    setup, _, _ = make(tmp_path, host)
    setup.folders()
    flat = " ".join(" ".join(c) for c in host.calls)
    assert "\\" not in flat, flat
    assert "/srv/f360" in flat
