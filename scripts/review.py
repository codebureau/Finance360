"""Serve a PR in the review slot on http://localhost:8360 (ADR-0006).

    review <PR#>    check the PR out in the slot, serve it, print its "How to test"
    review --stop   stop the slot and free its port
    review --reset  return the slot's data directory to the synthetic household

Standard library only (Python 3.10+). Needs git, gh (authenticated) and Docker on PATH.
Configuration, all optional:

    FINANCE360_REVIEW_SLOT     slot worktree   (default <repo folder>-review, next to the repo)
    FINANCE360_REVIEW_PORT     port            (default 8360)
    FINANCE360_REVIEW_DATA     data directory  (default <slot>-data, next to the slot)
    FINANCE360_REVIEW_PROJECT  compose project (default finance360-review)

The slot is a detached worktree of *this* repository. The script never writes to the working copy
it is run from or to any other worktree.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath

# The copy of the repo this script file lives in. The compose override always comes from here, so
# it matches the script that finishes the run. Fixtures come from the slot (see fixture_dir).
SELF_ROOT = Path(__file__).resolve().parent.parent
# Set (to the invoking working copy) only when a script has handed off to the slot's copy. It
# doubles as the no-loop guard: a script that finds it set never hands off again.
ORIGIN_ENV = "FINANCE360_REVIEW_ORIGIN"
# The working copy the user invoked `review` from; the slot must stay separate from it. After a
# handoff SELF_ROOT is the slot itself, so the invoking copy arrives via ORIGIN_ENV.
ROOT = Path(os.environ.get(ORIGIN_ENV) or SELF_ROOT).resolve()
FIXTURE_DIR = SELF_ROOT / "fixtures" / "sample_household"
OVERRIDE = Path(__file__).resolve().parent / "review.compose.yml"
FIXTURE_SKIP = {"README.md", ".gitkeep"}
# The app seeds its one Login from this file (FINANCE360_OWNER_PASSWORD_FILE in review.compose.yml)
# when none exists. Its content is a synthetic, localhost-only password (ADR-0010).
OWNER_PASSWORD_FILE = "owner-password"  # noqa: S105 - a file name, not a password
# A review.py containing this exact token can finish a run handed off by another one. Keep it:
# PRs predating the handoff lack it, so they are never handed off to.
HANDOFF_MARKER = "review-handoff-v1"


class ReviewError(Exception):
    """A user-facing failure: printed without a traceback."""


def main_checkout(root: Path) -> Path:
    """The repository's main working copy, even when ``root`` is a linked worktree of it.

    A linked worktree has a ``.git`` *file* naming ``<main>/.git/worktrees/<name>``; the main copy
    has a ``.git`` directory. No subprocess: this only reads that one small file.
    """
    dotgit = root / ".git"
    if dotgit.is_file():
        text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text[len("gitdir:") :].strip())
            if gitdir.parent.name == "worktrees":
                return gitdir.parent.parent.parent
    return root


def default_slot(checkout: PurePath) -> PurePath:
    """The default review slot: a sibling of the repo checkout named ``<repo folder>-review``."""
    return checkout.with_name(checkout.name + "-review")


@dataclass(frozen=True)
class Slot:
    path: Path
    port: int
    data: Path
    project: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Slot:
        configured = env.get("FINANCE360_REVIEW_SLOT")
        path = Path(configured) if configured else Path(default_slot(main_checkout(ROOT)))
        path = path.resolve()
        data = env.get("FINANCE360_REVIEW_DATA")
        return cls(
            path=path,
            port=int(env.get("FINANCE360_REVIEW_PORT", "8360")),
            data=Path(data).resolve() if data else path.with_name(path.name + "-data"),
            project=env.get("FINANCE360_REVIEW_PROJECT", "finance360-review"),
        )

    def validate(self) -> None:
        """Refuse paths that would make the script touch the main copy or other worktrees."""
        slot, data = self.path, self.data
        if slot == ROOT or ROOT in slot.parents or slot in ROOT.parents:
            raise ReviewError(f"Review slot {slot} must be separate from this working copy {ROOT}.")
        if data == slot or slot in data.parents or data == ROOT or ROOT in data.parents:
            raise ReviewError(f"Review data dir {data} must be outside the slot and {ROOT}.")


def extract_how_to_test(body: str) -> str:
    """Return the body of the PR's 'How to test' section, or raise ReviewError."""
    lines = body.replace("\r\n", "\n").split("\n")
    start = next(
        (i for i, ln in enumerate(lines) if re.match(r"^#{1,6}\s*how to test\s*$", ln, re.I)),
        None,
    )
    if start is None:
        raise ReviewError("The PR body has no 'How to test' section. Ask the author to add one.")
    section: list[str] = []
    in_fence = False
    for ln in lines[start + 1 :]:
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and re.match(r"^#{1,6}\s", ln):
            break
        section.append(ln)
    text = re.sub(r"<!--.*?-->", "", "\n".join(section), flags=re.S).strip()
    if not text:
        raise ReviewError("The PR's 'How to test' section is empty. Ask the author to fill it in.")
    return text


def _exe(name: str) -> str:
    exe = shutil.which(name)
    if exe is None:
        raise ReviewError(f"'{name}' not found on PATH. Install it first (see scripts/review.py).")
    return exe


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> None:
    result = subprocess.run(  # noqa: S603
        [_exe(cmd[0]), *cmd[1:]], cwd=cwd, env=env, check=False
    )
    if check and result.returncode != 0:
        raise ReviewError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def capture(cmd: list[str], *, cwd: Path | None = None) -> str:
    # gh and git write UTF-8. Without an explicit encoding Windows decodes as cp1252, and a PR
    # body holding a character such as an arrow fails to decode.
    result = subprocess.run(  # noqa: S603
        [_exe(cmd[0]), *cmd[1:]],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReviewError(f"{' '.join(cmd)} failed: {detail}")
    return result.stdout


def ensure_worktree(slot: Slot) -> None:
    """Use the slot if it is already a worktree; create it (detached) if it does not exist."""
    if (slot.path / ".git").exists():
        return
    if slot.path.exists() and any(slot.path.iterdir()):
        raise ReviewError(f"{slot.path} exists but is not a git worktree. Move it aside first.")
    print(f"Creating review worktree at {slot.path}")
    run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(slot.path), "HEAD"])


def checkout_pr(slot: Slot, pr: int) -> None:
    # The slot is ours: discard any local edits, then detach onto the PR head.
    run(["git", "reset", "--hard", "--quiet"], cwd=slot.path)
    # GitHub's pull/<n>/head ref is what `gh pr checkout --detach` fetches for an open PR; using it
    # directly also works for fork PRs and for closed PRs whose branch was deleted.
    run(["git", "fetch", "--quiet", "origin", f"pull/{pr}/head"], cwd=slot.path)
    run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=slot.path)


def should_hand_off(env: Mapping[str, str], mine: Path, theirs: Path) -> bool:
    """True when the slot's review tooling differs from ours and its review.py supports handoff."""
    if env.get(ORIGIN_ENV):  # already handed off: never loop
        return False
    pairs = [(mine, theirs), (OVERRIDE, theirs.with_name(OVERRIDE.name))]
    try:
        if not theirs.is_file() or HANDOFF_MARKER not in theirs.read_text(encoding="utf-8"):
            return False
        return any(a.read_bytes() != b.read_bytes() for a, b in pairs if b.is_file())
    except OSError:
        return False


def hand_off(slot: Slot, argv: list[str]) -> int:
    """Finish the run with the slot's own review.py (and compose override)."""
    script = slot.path / "scripts" / "review.py"
    print(f"This PR changes the review tooling: continuing with its {script}")
    env = {**os.environ, ORIGIN_ENV: str(ROOT)}
    return subprocess.run([sys.executable, str(script), *argv], env=env, check=False).returncode  # noqa: S603


def lockfiles_changed(slot: Slot, before: str, after: str) -> bool:
    if before == after:
        return False
    out = capture(["git", "diff", "--name-only", before, after], cwd=slot.path)
    return any(Path(f).name in {"uv.lock", "package-lock.json"} for f in out.splitlines())


def fixture_dir(slot: Slot) -> Path:
    """The slot's fixture, so `--reset` matches the PR being served whichever copy of this script
    runs. A slot not checked out yet has none, and falls back to ours."""
    own = slot.path / "fixtures" / "sample_household"
    return own if own.is_dir() else FIXTURE_DIR


def load_fixture(slot: Slot) -> None:
    """Copy the synthetic household into the data dir. Later slices extend the fixture."""
    fixture = fixture_dir(slot)
    if not fixture.is_dir():
        raise ReviewError(f"Fixture directory missing: {fixture}")
    slot.data.mkdir(parents=True, exist_ok=True)
    for src in fixture.iterdir():
        if src.name in FIXTURE_SKIP:
            continue
        dest = slot.data / src.name
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)


def ensure_owner_password(slot: Slot) -> None:
    """Make sure the seed password file exists: data dirs made before auth lack it, and the app
    refuses to start when FINANCE360_OWNER_PASSWORD_FILE points at nothing."""
    target = slot.data / OWNER_PASSWORD_FILE
    if not target.exists():
        shutil.copy2(fixture_dir(slot) / OWNER_PASSWORD_FILE, target)


def owner_password(slot: Slot) -> str:
    return (slot.data / OWNER_PASSWORD_FILE).read_text(encoding="utf-8").strip()


def compose_cmd(slot: Slot) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env["FINANCE360_PORT"] = str(slot.port)
    env["FINANCE360_REVIEW_DATA"] = str(slot.data)
    compose = [
        "docker", "compose", "-p", slot.project,
        "-f", str(slot.path / "docker-compose.yml"), "-f", str(OVERRIDE),
    ]  # fmt: skip
    return compose, env


def migrate(slot: Slot, compose: list[str], env: Mapping[str, str]) -> None:
    """Migrate the slot's database in the PR's own image, before the app starts.

    The runner (finance360.core.migrate) backs up an existing database to <data>/backups/ first,
    but only when a migration is pending, so re-serving an unchanged PR creates no backup. Skipped
    for PRs that predate Alembic. The image is built first so `run` never uses a stale one.
    """
    if (slot.path / "alembic.ini").exists():
        run([*compose, "build"], env=env)
        run(
            [*compose, "run", "--rm", "finance360", "python", "-m", "finance360.core.migrate"],
            env=env,
        )


def wait_healthy(port: int, timeout: float = 90) -> None:
    url = f"http://localhost:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    raise ReviewError(f"Slot did not become healthy at {url} within {timeout:.0f}s.")


def serve(slot: Slot) -> None:
    if not (slot.path / "docker-compose.yml").exists():
        raise ReviewError("This PR has no docker-compose.yml (it predates the app skeleton).")
    if not slot.data.exists() or not any(slot.data.iterdir()):
        load_fixture(slot)
    ensure_owner_password(slot)
    compose, env = compose_cmd(slot)
    env["GIT_SHA"] = capture(["git", "rev-parse", "--short", "HEAD"], cwd=slot.path).strip()
    migrate(slot, compose, env)
    run([*compose, "up", "--build", "-d", "--force-recreate"], env=env)
    wait_healthy(slot.port)
    print(f"\nServing on http://localhost:{slot.port} (data: {slot.data})")
    print(f"Sign in with the synthetic fixture password: {owner_password(slot)}")


def stop(slot: Slot) -> bool:
    """Remove the slot's containers (found by compose project label). True if any existed."""
    ids = capture(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={slot.project}"]
    ).split()
    if ids:
        run(["docker", "rm", "-f", *ids])
        run(["docker", "network", "rm", f"{slot.project}_default"], check=False)
    return bool(ids)


def cmd_pr(slot: Slot, pr: int, argv: list[str]) -> int:
    body = capture(["gh", "pr", "view", str(pr), "--json", "body", "--jq", ".body"], cwd=ROOT)
    steps = extract_how_to_test(body)  # fail before touching the slot
    ensure_worktree(slot)
    before = capture(["git", "rev-parse", "HEAD"], cwd=slot.path).strip()
    checkout_pr(slot, pr)
    after = capture(["git", "rev-parse", "HEAD"], cwd=slot.path).strip()
    if lockfiles_changed(slot, before, after):
        print("Lockfiles changed: dependency layers of the image will be rebuilt.")
    if should_hand_off(os.environ, Path(__file__).resolve(), slot.path / "scripts" / "review.py"):
        return hand_off(slot, argv)
    serve(slot)
    print(f"\n=== How to test PR #{pr} ===\n{steps}\n")
    return 0


def cmd_stop(slot: Slot) -> None:
    print("Stopped the review slot." if stop(slot) else "Review slot was not running.")


def cmd_reset(slot: Slot, argv: list[str]) -> int:
    # The slot holds a PR whose tooling may differ (e.g. its compose override): restart with it.
    if should_hand_off(os.environ, Path(__file__).resolve(), slot.path / "scripts" / "review.py"):
        return hand_off(slot, argv)
    was_running = stop(slot)
    if slot.data.exists():
        shutil.rmtree(slot.data)
    load_fixture(slot)
    print(f"Reset {slot.data} to the synthetic household.")
    if was_running:
        serve(slot)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review", description=(__doc__ or "").split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("pr", nargs="?", type=int, help="pull request number to serve")
    group.add_argument("--stop", action="store_true", help="stop the slot and free the port")
    group.add_argument("--reset", action="store_true", help="restore the synthetic household")
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv)
    try:
        slot = Slot.from_env(os.environ)
        slot.validate()
        if args.stop:
            cmd_stop(slot)
        elif args.reset:
            return cmd_reset(slot, argv)
        else:
            return cmd_pr(slot, args.pr, argv)
    except ReviewError as err:
        print(f"review: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
