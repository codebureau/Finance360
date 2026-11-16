"""Ship a release tag to the home host (ADR-0007): ``deploy <tag>``.

    deploy v0.1.0           back up, check out the tag on the host, build, start, verify /health
    deploy v0.1.0 --first   the first deploy to a new host (nothing is running, so no Backup)
    deploy setup v0.1.0     bootstrap a new host (ADR-0012), then run the first deploy; idempotent

The host builds from its own git clone: no registry. Every host command runs over SSH (key-based,
through Tailscale). Standard library only (Python 3.10+); needs `ssh` on PATH. The host needs git,
Docker with the compose plugin, and a clone of the repo (docs/deploy.md).

Host details come from ``deploy/target.local.env`` (gitignored; copy ``target.example.env``).
``--config PATH`` points at another file, for example a local throwaway "host" (transport `local`).

The sequence, aborting at the first failure:

 1. check the clone is clean; remember what it runs now (the rollback target)
 2. fetch tags (never overwriting one) and resolve the tag
 3. pre-deploy Backup, in the running app: `python -m finance360.backup run --reason pre-deploy`
 4. check out the tag, write `.env`, `docker compose up -d --build`
 5. wait for /health to report the tag's GIT_SHA and the current schema revision
 6. report

`deploy setup` checks the host (Docker, the clone's tooling, disk, the HTTPS port), makes the
folders and the clone, generates the Backup key (shown once), deploys the tag, exports the Caddy
root certificate for devices and, with --tailscale, points Tailscale Serve at the proxy. It never
installs anything and never overwrites an existing key or setting.

Rollback is `deploy <previous tag>`, plus a restore of the pre-deploy Backup if the schema changed
(docs/restore-runbook.md). The failure message says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import shlex
import socket
import ssl
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

DEFAULT_CONFIG = Path(__file__).resolve().parent / "target.local.env"
# Release tags: vMAJOR.MINOR.PATCH with an optional pre-release suffix (docs/deploy.md).
TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")
PLACEHOLDER = re.compile(r"<[^<>]*>|CHANGE_?ME", re.IGNORECASE)
SHA_LENGTH = 12
HEALTH_TIMEOUT = 120.0

REQUIRED = ("DEPLOY_TRANSPORT", "DEPLOY_REMOTE_DIR")
TRANSPORTS = ("ssh", "local")

# Runs inside the container, so it needs neither curl nor a published port.
HEALTH_SNIPPET = (
    "import os,urllib.request;"
    "print(urllib.request.urlopen('http://localhost:%s/health' % os.environ['FINANCE360_PORT'],"
    " timeout=5).read().decode())"
)
HEAD_SNIPPET = "from finance360.core.migrate import head_revision;print(head_revision())"


class DeployError(Exception):
    """A user-facing failure: printed without a traceback."""


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    """Runs one command on the host. ``stream`` lets a long step show its output live."""

    def __call__(
        self,
        argv: list[str],
        *,
        input: str | None = None,
        stream: bool = False,
        anywhere: bool = False,
    ) -> Result: ...


@dataclass(frozen=True)
class Target:
    transport: str
    remote_dir: str
    ssh_target: str = ""
    ssh_options: tuple[str, ...] = ()
    compose_project: str = ""
    #: Where `deploy setup` clones the repo from, if the clone is missing.
    repo_url: str = ""
    #: Written to the host's `.env` (and so read by compose): FINANCE360_* settings.
    app_env: Mapping[str, str] = field(default_factory=dict)


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise DeployError(f"Not a KEY=value line in the target file: {raw!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_target(path: Path) -> Target:
    """Read and validate the target file; refuse placeholders, naming each one."""
    if not path.is_file():
        raise DeployError(
            f"No target file at {path}.\n"
            "Copy deploy/target.example.env to deploy/target.local.env and fill it in "
            "(docs/deploy.md)."
        )
    values = parse_env_file(path.read_text(encoding="utf-8"))
    problems = [k for k in REQUIRED if not values.get(k)]
    problems += [k for k, v in values.items() if PLACEHOLDER.search(v)]
    transport = values.get("DEPLOY_TRANSPORT", "")
    if transport == "ssh" and not values.get("DEPLOY_SSH_TARGET"):
        problems.append("DEPLOY_SSH_TARGET")
    if problems:
        names = ", ".join(sorted(set(problems)))
        raise DeployError(
            f"{path.name} still has placeholders or missing values: {names}.\n"
            "Fill them in (see deploy/target.example.env) and run again. Nothing was changed."
        )
    if transport not in TRANSPORTS:
        raise DeployError(
            f"DEPLOY_TRANSPORT must be one of {', '.join(TRANSPORTS)}, not {transport!r}"
        )
    return Target(
        transport=transport,
        remote_dir=values["DEPLOY_REMOTE_DIR"],
        ssh_target=values.get("DEPLOY_SSH_TARGET", ""),
        ssh_options=tuple(shlex.split(values.get("DEPLOY_SSH_OPTIONS", ""))),
        compose_project=values.get("DEPLOY_COMPOSE_PROJECT", ""),
        repo_url=values.get("DEPLOY_REPO_URL", ""),
        app_env={k: v for k, v in values.items() if k.startswith("FINANCE360_")},
    )


def make_runner(target: Target) -> Runner:
    def run(
        argv: list[str],
        *,
        input: str | None = None,
        stream: bool = False,
        anywhere: bool = False,
    ) -> Result:
        """``anywhere`` runs outside the clone (setup, before the clone exists)."""
        if target.transport == "ssh":
            remote = shlex.join(argv)
            if not anywhere:
                remote = f"cd {shlex.quote(target.remote_dir)} && {remote}"
            cmd = ["ssh", "-o", "BatchMode=yes", *target.ssh_options, target.ssh_target, remote]
            cwd = None
        else:
            cmd, cwd = argv, (None if anywhere else target.remote_dir)
        try:
            done = subprocess.run(  # noqa: S603
                cmd,
                cwd=cwd,
                input=input,
                text=True,
                encoding="utf-8",
                capture_output=not stream,
                check=False,
            )
        except FileNotFoundError as err:
            raise DeployError(f"'{cmd[0]}' not found on PATH.") from err
        return Result(done.returncode, done.stdout or "", done.stderr or "")

    return run


@dataclass
class Deployer:
    target: Target
    run: Runner
    say: Callable[[str], None] = field(default=lambda m: print(m, flush=True))
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    health_timeout: float = HEALTH_TIMEOUT

    # -- helpers ------------------------------------------------------------------------------

    def _ok(self, argv: list[str], what: str, *, input: str | None = None) -> str:
        result = self.run(argv, input=input)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise DeployError(f"{what} failed ({result.returncode}): {detail}")
        return result.stdout.strip()

    def _compose(self, *args: str) -> list[str]:
        project = ["-p", self.target.compose_project] if self.target.compose_project else []
        return ["docker", "compose", *project, *args]

    def _rollback_hint(self, previous: str, *, started: bool) -> str:
        if not started:
            return "Nothing was changed on the host; the previous release is still running."
        return (
            "The host may be part-way through a deploy.\n"
            f"Roll back:  deploy {previous}\n"
            "If the schema changed (the app log at startup says whether it migrated), the older "
            "release cannot read the new schema.\nRestore the pre-deploy Backup instead: "
            "docs/restore-runbook.md.\n"
            "Logs:  docker compose logs --tail 100 finance360  (on the host)"
        )

    # -- steps --------------------------------------------------------------------------------

    def preflight(self) -> str:
        """Clean clone check. Returns a label for what the host runs now (the rollback target)."""
        dirty = self._ok(["git", "status", "--porcelain", "--untracked-files=no"], "git status")
        if dirty:
            raise DeployError(
                "The clone on the host has local changes; refusing to deploy over them:\n"
                f"{dirty}\nClean or discard them on the host first."
            )
        current = self.run(["git", "describe", "--tags", "--exact-match", "HEAD"])
        if current.returncode == 0 and current.stdout.strip():
            return current.stdout.strip()
        return self._ok(["git", "rev-parse", f"--short={SHA_LENGTH}", "HEAD"], "git rev-parse")

    def resolve(self, tag: str) -> str:
        """Fetch tags without overwriting one (a moved tag fails) and return the tag's short SHA."""
        self._ok(["git", "fetch", "--quiet", "--no-tags", "origin", "tag", tag], f"fetching {tag}")
        return self._ok(
            ["git", "rev-parse", f"--short={SHA_LENGTH}", f"{tag}^{{commit}}"],
            f"resolving {tag}",
        )

    def app_running(self) -> bool:
        return bool(self._ok(self._compose("ps", "--status", "running", "-q", "finance360"), "ps"))

    def backup(self, first: bool) -> str:
        if not self.app_running():
            if first:
                return "no running app (first deploy): nothing to back up"
            raise DeployError(
                "The app is not running on the host, so there is nothing to take a pre-deploy "
                "Backup from.\nIf this is the first deploy to a new host, run again with --first. "
                "Otherwise start the app, or restore (docs/restore-runbook.md), then deploy."
            )
        self.say("Taking the pre-deploy Backup ...")
        result = self.run(
            self._compose(
                "exec",
                "-T",
                "finance360",
                "python",
                "-m",
                "finance360.backup",
                "run",
                "--reason",
                "pre-deploy",
            )  # fmt: skip
        )
        if result.returncode != 0:
            reason = {
                2: "Backups are not set up on the host (docs/backup-setup.md)",
                3: "another Backup is running; wait for it and try again",
            }.get(result.returncode, "the Backup failed")
            raise DeployError(
                f"Pre-deploy Backup failed (exit {result.returncode}): {reason}.\n"
                f"{(result.stderr or result.stdout).strip()}\n"
                "Aborted before changing anything: the previous release is still running."
            )
        return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "backup ok"

    def write_env(self, sha: str) -> None:
        """`.env` in the clone: read by compose now and by ad hoc `docker compose` commands."""
        lines = [f"GIT_SHA={sha}"]
        if self.target.compose_project:
            lines.append(f"COMPOSE_PROJECT_NAME={self.target.compose_project}")
        lines += [f"{k}={v}" for k, v in sorted(self.target.app_env.items())]
        self._ok(
            ["sh", "-c", "umask 077; cat > .env"],
            "writing .env",
            input="\n".join(lines) + "\n",
        )

    def start(self, tag: str) -> None:
        self._ok(["git", "checkout", "--quiet", "--detach", tag], f"checking out {tag}")

    def build_and_up(self) -> None:
        self.say("Building and starting (docker compose up -d --build) ...")
        result = self.run(self._compose("up", "-d", "--build"), stream=True)
        if result.returncode != 0:
            raise DeployError(f"docker compose up failed ({result.returncode}).")

    def health(self) -> dict[str, str]:
        result = self.run(self._compose("exec", "-T", "finance360", "python", "-c", HEALTH_SNIPPET))
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or "health check could not run")
        data = json.loads(result.stdout)
        return {k: str(v) for k, v in data.items()}

    def verify(self, sha: str) -> dict[str, str]:
        """Wait until /health says healthy at the tag's SHA and the current schema revision."""
        expected_schema = self._ok(
            self._compose("exec", "-T", "finance360", "python", "-c", HEAD_SNIPPET),
            "reading the schema head",
        )
        deadline = self.clock() + self.health_timeout
        last = "no answer yet"
        while True:
            try:
                data = self.health()
            except (ValueError, KeyError) as err:
                last = f"health check error: {err}"
            else:
                if (
                    data.get("status") == "healthy"
                    and data.get("git_sha") == sha
                    and data.get("schema_version") == expected_schema
                ):
                    return data
                last = (
                    f"/health reported status={data.get('status')!r} "
                    f"git_sha={data.get('git_sha')!r} schema={data.get('schema_version')!r}; "
                    f"expected git_sha={sha!r} schema={expected_schema!r}"
                )
            if self.clock() >= deadline:
                raise DeployError(f"The new release did not come up healthy: {last}")
            self.sleep(2)

    # -- the whole run ------------------------------------------------------------------------

    def deploy(self, tag: str, *, first: bool = False) -> int:
        if not TAG_PATTERN.match(tag):
            raise DeployError(
                f"{tag!r} is not a release tag. Release tags look like v1.2.3 (docs/deploy.md)."
            )
        previous = self.preflight()
        sha = self.resolve(tag)
        self.say(f"Deploying {tag} ({sha}); the host runs {previous} now.")
        backup = self.backup(first)
        self.say(f"Backup: {backup}")
        try:
            self.start(tag)
            self.write_env(sha)
            self.build_and_up()
            health = self.verify(sha)
        except DeployError as err:
            raise DeployError(f"{err}\n\n{self._rollback_hint(previous, started=True)}") from err
        self.say(
            f"\nDeployed {tag}.\n"
            f"  GIT_SHA         {health['git_sha']}\n"
            f"  schema version  {health['schema_version']}\n"
            f"  previous        {previous}  (roll back with: deploy {previous})\n"
            f"  backup          {backup}"
        )
        return 0


# -- deploy setup (ADR-0012) ------------------------------------------------------------------

DEFAULT_HTTPS_PORT = "8843"
#: Where Caddy keeps its local CA in the proxy container (the `caddy-data` volume).
CA_ROOT_PATH = "/data/caddy/pki/authorities/local/root.crt"
APP_UID = "10001"  # the container's user (docs/backup-setup.md)
MIN_FREE_KB = 3 * 1024 * 1024
CA_WAIT = 60.0

# Runs with the path as $1; prints created/exists. The key arrives on stdin, never in argv.
KEY_SCRIPT = (
    'set -e; umask 077; if [ -s "$1" ]; then echo exists; else cat > "$1"; chmod 400 "$1"; '
    "echo created; fi"
)
MKDIR_SCRIPT = 'mkdir -p "$1" && chmod 700 "$1"'
DISK_SCRIPT = 'd=$1; while [ ! -d "$d" ]; do d=$(dirname "$d"); done; df -Pk "$d"'
PORT_SCRIPT = (
    "command -v ss >/dev/null 2>&1 || exit 3; ss -ltnH | awk '{print $4}' | grep -Eq \":$1\\$\""
)

DEVICE_STEPS = """\
Trust the certificate once on each device (then renewals are invisible):
  Windows   double-click the file > Install Certificate > Local Machine > "Place all certificates
            in the following store" > Trusted Root Certification Authorities. Restart the browser.
  iOS       send the file to the device (AirDrop, email), open it, Settings > Profile Downloaded >
            Install; then Settings > General > About > Certificate Trust Settings > enable it.
  Android   Settings > Security > (Encryption & credentials) > Install a certificate > CA
            certificate > pick the file (a warning is expected).
  Firefox   uses its own store: Settings > Privacy & Security > Certificates > View > Authorities >
            Import, or set security.enterprise_roots.enabled to true.
Devices on Tailscale use the ts.net name and need none of this."""


def add_allowed_host(path: Path, host: str) -> bool:
    """Append ``host`` to FINANCE360_ALLOWED_HOSTS in the target file; nothing else is touched.

    True if it was added, False if it was already listed.
    """
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)  # keep line endings
    for i, line in enumerate(lines):
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "FINANCE360_ALLOWED_HOSTS":
            raw = value.strip().strip("\"'")
            if host.lower() in [h.strip().lower() for h in raw.split(",")]:
                return False
            ending = line[len(line.rstrip("\r\n")) :]
            lines[i] = f"FINANCE360_ALLOWED_HOSTS={raw},{host}{ending}"
            path.write_bytes("".join(lines).encode("utf-8"))
            return True
    raise DeployError(f"FINANCE360_ALLOWED_HOSTS is not set in {path.name}.")


def local_port_busy(port: int) -> bool:
    """Is a TCP port taken on this machine? (`local` transport: this machine is the host.)"""
    with socket.socket() as probe:
        try:
            probe.bind(("0.0.0.0", port))  # noqa: S104
        except OSError:
            return True
    return False


def host_path(raw: str) -> PurePosixPath:
    """A path on the host. The host is POSIX even when deploy runs on Windows, so never use Path."""
    return PurePosixPath(raw.replace("\\", "/"))


@dataclass
class Setup:
    """First-time host bootstrap (ADR-0012). Idempotent: every step checks before it acts."""

    deployer: Deployer
    config_path: Path
    cert_out: Path
    tailscale: bool = False
    #: Stand-in for tests; defaults to a CSPRNG key.
    new_key: Callable[[], str] = field(default=lambda: secrets.token_urlsafe(48))
    #: Called after the key is shown, so a person can store it before anything else happens.
    acknowledge: Callable[[], None] = field(default=lambda: None)
    ca_wait: float = CA_WAIT

    @property
    def target(self) -> Target:
        return self.deployer.target

    @property
    def run(self) -> Runner:
        return self.deployer.run

    @property
    def say(self) -> Callable[[str], None]:
        return self.deployer.say

    def _out(self, argv: list[str], what: str, *, input: str | None = None) -> str:
        result = self.run(argv, input=input, anywhere=True)
        if result.returncode != 0:
            raise DeployError(f"{what} failed ({result.returncode}): {result.stderr.strip()}")
        return result.stdout.strip()

    def https_port(self) -> int:
        raw = self.target.app_env.get("FINANCE360_HTTPS_PORT") or DEFAULT_HTTPS_PORT
        if not raw.isdigit() or not 0 < int(raw) < 65536:
            raise DeployError(f"FINANCE360_HTTPS_PORT must be a port number, not {raw!r}")
        return int(raw)

    def project(self) -> str:
        name = self.target.compose_project or host_path(self.target.remote_dir).name
        return re.sub(r"[^a-z0-9_-]", "", name.lower())

    # -- checks, before anything changes ----------------------------------------------------------

    def _our_proxy_runs(self) -> bool:
        label = f"label=com.docker.compose.project={self.project()}"
        service = "label=com.docker.compose.service=proxy"
        found = self.run(
            ["docker", "ps", "-q", "--filter", label, "--filter", service], anywhere=True
        )
        return found.returncode == 0 and bool(found.stdout.strip())

    def _port_busy(self, port: int) -> bool:
        # Another container publishing the port counts even when a socket probe can't see it
        # (Docker Desktop on Windows, or Docker without its userland proxy on Linux).
        published = self.run(
            ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.ID}}"], anywhere=True
        )
        if published.returncode == 0 and published.stdout.strip():
            return True
        if self.target.transport == "local":
            return local_port_busy(port)
        result = self.run(["sh", "-c", PORT_SCRIPT, "sh", str(port)], anywhere=True)
        if result.returncode == 3:
            raise DeployError("Cannot check the port: `ss` (iproute2) is not on the host.")
        return result.returncode == 0

    def check(self) -> int:
        """Prerequisites and the port. Installs nothing; names everything that is missing."""
        problems: list[str] = []
        tools = [
            (["docker", "--version"], "Docker is not installed on the host"),
            (["docker", "compose", "version"], "the Docker compose plugin is missing"),
            (["git", "--version"], "git is not installed on the host"),
        ]
        if self.tailscale:
            tools.append((["tailscale", "version"], "tailscale is not installed on the host"))
        for argv, message in tools:
            if self.run(argv, anywhere=True).returncode != 0:
                problems.append(message)
        disk = self.run(["sh", "-c", DISK_SCRIPT, "sh", self.target.remote_dir], anywhere=True)
        try:
            free_kb = int(disk.stdout.strip().splitlines()[-1].split()[3])
        except (IndexError, ValueError):
            problems.append("cannot read the free disk space on the host")
        else:
            if free_kb < MIN_FREE_KB:
                problems.append(f"less than {MIN_FREE_KB // 1024 // 1024} GB free for the images")
        if not self.target.repo_url and not self._clone_exists():
            problems.append("DEPLOY_REPO_URL is not set and the clone does not exist yet")
        for name in ("FINANCE360_ALLOWED_HOSTS", "FINANCE360_BACKUP_KEY_FILE"):
            if not self.target.app_env.get(name):
                problems.append(f"{name} is not set in {self.config_path.name}")
        port = self.https_port()
        if not self._our_proxy_runs() and self._port_busy(port):
            problems.append(
                f"port {port} is already in use on the host; choose another with "
                f"FINANCE360_HTTPS_PORT in {self.config_path.name}"
            )
        if problems:
            raise DeployError(
                "The host is not ready (nothing was changed, nothing was installed):\n  - "
                + "\n  - ".join(problems)
            )
        return port

    def _clone_exists(self) -> bool:
        probe = self.run(["test", "-d", f"{self.target.remote_dir}/.git"], anywhere=True)
        return probe.returncode == 0

    # -- steps --------------------------------------------------------------------------------

    def folders(self) -> None:
        remote_dir = self.target.remote_dir
        if not self._clone_exists():
            self._out(["mkdir", "-p", str(host_path(remote_dir).parent)], "making folders")
            self.say(f"Cloning {self.target.repo_url} ...")
            self._out(["git", "clone", "--quiet", self.target.repo_url, remote_dir], "git clone")
        key_dir = str(host_path(self.target.app_env["FINANCE360_BACKUP_KEY_FILE"]).parent)
        folders = [key_dir]
        if rclone := self.target.app_env.get("FINANCE360_RCLONE_CONFIG_DIR"):
            folders.append(rclone)
        for folder in folders:
            self._out(["sh", "-c", MKDIR_SCRIPT, "sh", folder], f"making {folder}")
            # The container user must read these (docs/backup-setup.md); needs root or sudo.
            owned = any(
                self.run(argv + [APP_UID, folder], anywhere=True).returncode == 0
                for argv in (["chown", "-R"], ["sudo", "-n", "chown", "-R"])
            )
            if not owned:
                self.say(
                    f"  note: could not give uid {APP_UID} ownership of {folder}; "
                    f"run on the host:  sudo chown -R {APP_UID} {folder}"
                )

    def backup_key(self) -> None:
        path = self.target.app_env["FINANCE360_BACKUP_KEY_FILE"]
        key = self.new_key()
        state = self._out(["sh", "-c", KEY_SCRIPT, "sh", path], "writing the Backup key", input=key)
        if state == "exists":
            self.say(f"Backup key: {path} already exists; left alone (never regenerated).")
            return
        self.say(
            "\nBACKUP KEY (shown once; store it in the password manager as "
            '"Finance360 Backup key" now):\n\n'
            f"    {key}\n\n"
            f"It is saved on the host at {path} (mode 400). Without it, backups cannot be read."
        )
        self.acknowledge()

    def tailscale_name(self) -> str:
        raw = self._out(["tailscale", "status", "--json"], "tailscale status")
        try:
            name = str(json.loads(raw)["Self"]["DNSName"]).rstrip(".")
        except (ValueError, KeyError, TypeError):
            name = ""
        if not name.endswith(".ts.net"):
            raise DeployError(
                "Tailscale has no *.ts.net name for the host (is MagicDNS and HTTPS enabled in "
                "the admin console, and is the host logged in?)."
            )
        return name

    def allow_tailscale_name(self, name: str) -> None:
        if add_allowed_host(self.config_path, name):
            self.say(f"Added {name} to FINANCE360_ALLOWED_HOSTS in {self.config_path.name}.")
            self.deployer.target = load_target(self.config_path)
        else:
            self.say(f"{name} is already in FINANCE360_ALLOWED_HOSTS.")

    def serve(self, name: str, port: int) -> None:
        """Tailscale Serve -> the proxy (one path in), then prove Serve kept the Host (ADR-0012)."""
        self._out(
            ["tailscale", "serve", "--bg", "--https=443", f"https+insecure://127.0.0.1:{port}"],
            "tailscale serve",
        )
        # A body that fails validation, so it never counts as a failed sign-in. 422 = the request
        # got past the host and Origin checks, so Serve preserved the Host; 403/421 = it did not.
        probe = self.run(
            [
                "curl",
                "-ks",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-X",
                "POST",
                "-H",
                f"Origin: https://{name}",
                "-H",
                "Content-Type: application/json",
                "-d",
                "{}",
                f"https://{name}/api/auth/login",
            ],  # fmt: skip
            anywhere=True,
        )
        code = probe.stdout.strip()
        if code == "422":
            self.say(f"Tailscale Serve: https://{name} reaches the app with its Host intact.")
        else:
            self.say(
                f"WARNING: through Tailscale Serve, https://{name}/api/auth/login answered "
                f"{code or probe.stderr.strip() or 'nothing'} (expected 422). If it is 403 or "
                "421, Serve is not preserving the Host header; see docs/deploy.md. Sign-in "
                "over the ts.net name will not work until that is fixed."
            )

    def export_ca(self) -> str:
        """Copy Caddy's root certificate to ``cert_out``. Returns its SHA-256 fingerprint."""
        deadline = time.monotonic() + self.ca_wait
        while True:
            got = self.run(self.deployer._compose("exec", "-T", "proxy", "cat", CA_ROOT_PATH))
            if got.returncode == 0 and "BEGIN CERTIFICATE" in got.stdout:
                break
            if time.monotonic() >= deadline:
                raise DeployError(
                    "The proxy has not created its root certificate yet: "
                    f"{got.stderr.strip() or 'no output'}.\n"
                    "Check `docker compose logs proxy` on the host; run setup again when fixed."
                )
            self.deployer.sleep(2)
        pem = got.stdout
        self.cert_out.parent.mkdir(parents=True, exist_ok=True)
        self.cert_out.write_text(pem, encoding="ascii")
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()

    def go(self, tag: str) -> int:
        port = self.check()
        self.folders()
        self.backup_key()
        name = ""
        if self.tailscale:
            name = self.tailscale_name()
            self.allow_tailscale_name(name)
        self.deployer.deploy(tag, first=not self.deployer.app_running())
        if self.tailscale:
            self.serve(name, port)
        fingerprint = self.export_ca()
        entry = self.target.app_env["FINANCE360_ALLOWED_HOSTS"].split(",")[0].strip()
        url = f"https://{entry}" if ":" in entry else f"https://{entry}:{port}"
        self.say(
            f"\nSetup complete.\n"
            f"  HTTPS port        {port}\n"
            f"  allowed hosts     {self.target.app_env['FINANCE360_ALLOWED_HOSTS']}\n"
            f"  root certificate  {self.cert_out}\n"
            f"  SHA-256           {fingerprint}\n\n{DEVICE_STEPS}\n\n"
            f"Then open {url} and finish the app's own setup with the one-off code "
            "(docs/deploy.md)."
        )
        return 0


def default_cert_path() -> Path:
    desktop = Path.home() / "Desktop"
    return (desktop if desktop.is_dir() else Path.cwd()) / "finance360-root.crt"


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["setup"]:
        return setup_main(args_in[1:])
    parser = argparse.ArgumentParser(prog="deploy", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("tag", help="release tag to deploy, e.g. v0.1.0 (or the previous one)")
    parser.add_argument("--first", action="store_true", help="first deploy: nothing to back up")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="target file")
    args = parser.parse_args(args_in)
    try:
        target = load_target(args.config)
        return Deployer(target, make_runner(target)).deploy(args.tag, first=args.first)
    except DeployError as err:
        print(f"deploy: {err}", file=sys.stderr)
        return 1


def setup_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="deploy setup", description="Bootstrap a new host, then deploy TAG (ADR-0012)."
    )
    parser.add_argument("tag", help="release tag for the first deploy, e.g. v0.1.0")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="target file")
    parser.add_argument("--cert-out", type=Path, default=None, help="where to save root.crt")
    parser.add_argument("--tailscale", action="store_true", help="also set up Tailscale Serve")
    args = parser.parse_args(argv)

    def acknowledge() -> None:
        if sys.stdin.isatty():
            try:
                input("Press Enter once the key is saved ... ")
            except EOFError:
                # Windows reports NUL as a tty, so a redirected stdin can still land here.
                print()

    try:
        target = load_target(args.config)
        setup = Setup(
            Deployer(target, make_runner(target)),
            args.config,
            args.cert_out or default_cert_path(),
            tailscale=args.tailscale,
            acknowledge=acknowledge,
        )
        return setup.go(args.tag)
    except DeployError as err:
        print(f"deploy setup: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
