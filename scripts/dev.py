"""Run the API (auto-reload) and Vite together. Works on native Windows, macOS and Linux.

Honours FINANCE360_DATA_DIR and FINANCE360_PORT (the API port). Vite proxies /health to the API
and picks the next free port from 5173, printing it. Ctrl+C stops both.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    env = os.environ.copy()
    port = env.setdefault("FINANCE360_PORT", "8000")
    env.setdefault("FINANCE360_DATA_DIR", str(ROOT / "data"))
    npm = shutil.which("npm")
    if npm is None:
        print("npm not found on PATH", file=sys.stderr)
        return 1

    api = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "finance360.main:app",
            "--factory",
            "--reload",
            "--reload-dir",
            str(ROOT / "backend"),
            "--port",
            port,
        ],
        cwd=ROOT,
        env=env,
    )
    ui = subprocess.Popen([npm, "run", "dev"], cwd=ROOT / "frontend", env=env)  # noqa: S603
    try:
        return ui.wait() if api.poll() is None else api.returncode
    except KeyboardInterrupt:
        return 0
    finally:
        for proc in (ui, api):
            if proc.poll() is None:
                proc.terminate()
        for proc in (ui, api):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
