"""The one-off setup code (ADR-0010): a file in the data directory, never logged or returned.

Reading it needs shell or volume access on the host, which is the same proof of ownership as the
recovery CLI. It exists only while no Login does.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

FILENAME = "setup-code"


def path_for(data_dir: Path) -> Path:
    return data_dir / FILENAME


def ensure(data_dir: Path) -> None:
    """Write a fresh code (mode 0600) unless one is already waiting."""
    path = path_for(data_dir)
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secrets.token_urlsafe(20) + "\n")


def matches(data_dir: Path, supplied: str) -> bool:
    """Constant-time comparison. False if no code is waiting."""
    path = path_for(data_dir)
    if not path.is_file():
        return False
    expected = path.read_text(encoding="utf-8").strip()
    return bool(expected) and hmac.compare_digest(expected.encode(), supplied.strip().encode())


def remove(data_dir: Path) -> None:
    path_for(data_dir).unlink(missing_ok=True)
