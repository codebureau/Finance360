"""Library files: where they live, what is allowed in, and the move from ``documents/tax/``
(ADR-0026 section 2, ADR-0019 section 7).

Files live in the instance data directory, content-addressed:
``documents/library/<sha256[:2]>/<sha256>``. **The path comes from the hash of the bytes and from
nothing else**, never from the uploaded file name, so a hostile name cannot reach outside the
folder. A duplicate upload is the same file; a file is **never overwritten** (a file already at
the address is left as it is, so a changed one stays visible to the health check).

**Adoption.** Before the Library, files lived under ``documents/tax/``. ``adopt`` copies each to
its new address, verifies the hash of the *copy*, and only then removes the old file. Until a file
has moved, reads fall back to the old path, so a half-finished move, or a restore of an old backup,
still works. A missing or damaged source is reported and left exactly where it is; nothing is ever
deleted unless a verified copy exists.

This module does file I/O and no database work: the rows, the audit and the health check's walk
over them are in ``library.service``. ADR-0011 already backs up and restores ``documents/`` whole,
so nothing here is backup-specific.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from finance360.core.models.library import FILE_NAME_MAX

logger = logging.getLogger(__name__)

#: The largest file accepted. A scanned receipt or a statement is well under this.
MAX_BYTES = 10_000_000

#: Media types accepted (the stored type is always one of these, so a download never serves
#: anything the browser could run). Each is checked against the file's own first bytes.
MEDIA_TYPES: dict[str, str] = {
    "application/pdf": "PDF",
    "image/png": "PNG image",
    "image/jpeg": "JPEG image",
    "text/plain": "text",
    "text/csv": "CSV",
}

#: Under the instance data directory, where every Library file lives.
SUBDIR = Path("documents") / "library"
#: Where Evidence files lived before the Library (ADR-0019 section 7); read until adopted.
LEGACY_SUBDIR = Path("documents") / "tax"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1 << 20

#: What a stored file is found to be.
OK, MISSING, CHANGED = "ok", "missing", "changed"


def media_type_names() -> str:
    return ", ".join(MEDIA_TYPES.values())


def clean_file_name(raw: str) -> str:
    """A bare, printable file name from whatever was uploaded: no directories (either slash style),
    no control characters, at most ``FILE_NAME_MAX`` characters. Empty becomes ``document``."""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip().strip(".")
    return name[:FILE_NAME_MAX].strip() or "document"


def content_problem(media_type: str, content: bytes) -> str | None:
    """Why the bytes are not the declared type, or ``None``."""
    if media_type == "application/pdf":
        ok = content.startswith(b"%PDF-")
    elif media_type == "image/png":
        ok = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif media_type == "image/jpeg":
        ok = content.startswith(b"\xff\xd8\xff")
    else:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            ok = False
        else:
            ok = b"\x00" not in content
    return None if ok else f"The file is not a {MEDIA_TYPES[media_type]} file."


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _address(sha256: str) -> None:
    if not _SHA256.match(sha256):
        raise ValueError("A document address is a lower-case SHA-256 in hex.")


def path_for(data_dir: Path, sha256: str) -> Path:
    """Where the file with this hash lives. Refuses anything that is not a SHA-256 in hex."""
    _address(sha256)
    return data_dir / SUBDIR / sha256[:2] / sha256


def legacy_path_for(data_dir: Path, sha256: str) -> Path:
    """Where the file lived before the Library, under ``documents/tax/``."""
    _address(sha256)
    return data_dir / LEGACY_SUBDIR / sha256[:2] / sha256


def store(data_dir: Path, content: bytes) -> str:
    """Write the bytes at their address unless they are already there; return the hash.

    The write goes to a temporary name and is linked into place, which fails if the address is
    taken, so a file is never overwritten and a half-written file never sits at an address.
    """
    sha = sha256_of(content)
    target = path_for(data_dir, sha)
    if target.exists():
        return sha
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{sha}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(FileExistsError):  # a concurrent identical upload won
            os.link(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return sha


def file_state(path: Path, sha256: str) -> str:
    """``ok`` when the file is there and hashes to ``sha256``, else ``missing`` or ``changed``.
    The one place a file's bytes are checked against its address."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
    except (FileNotFoundError, NotADirectoryError):
        return MISSING
    return OK if digest.hexdigest() == sha256 else CHANGED


def state(data_dir: Path, sha256: str) -> str:
    """``ok`` when the file hashes to its address at the Library path, or (not yet adopted) at the
    old path; otherwise ``changed`` if a file is there but wrong, else ``missing``."""
    found = [
        file_state(path_for(data_dir, sha256), sha256),
        file_state(legacy_path_for(data_dir, sha256), sha256),
    ]
    if OK in found:
        return OK
    return CHANGED if CHANGED in found else MISSING


def read(data_dir: Path, sha256: str) -> bytes | None:
    """The bytes, only if they still hash to the address; ``None`` otherwise. The Library path is
    tried first, then the old one."""
    for path in (path_for(data_dir, sha256), legacy_path_for(data_dir, sha256)):
        try:
            content = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if sha256_of(content) == sha256:
            return content
    return None


def remove(data_dir: Path, sha256: str) -> None:
    """Delete the file from wherever it is (both paths), and tidy the empty shard folders."""
    for path in (path_for(data_dir, sha256), legacy_path_for(data_dir, sha256)):
        path.unlink(missing_ok=True)
        _tidy(path)


def _tidy(path: Path) -> None:
    for folder in (path.parent, path.parent.parent):
        with contextlib.suppress(OSError):  # not empty, or already gone: leave it
            folder.rmdir()


# --- adoption ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdoptionProblem:
    sha256: str
    #: ``missing`` (no file at either path), ``changed`` (a file is there but does not hash to its
    #: address) or ``copy failed`` (the copy did not verify, so the old file was kept).
    problem: str


@dataclass(frozen=True)
class AdoptionReport:
    #: Files copied, verified and removed from the old path in this run.
    moved: int
    #: Files already in place (nothing to do).
    in_place: int
    problems: tuple[AdoptionProblem, ...]


def _write_copy(source: Path, temp: Path) -> None:
    """Copy ``source`` to the new file ``temp``, flushed to disk. Its own function so a test can
    stand in a faulty disk."""
    with source.open("rb") as src, temp.open("xb") as dst:
        while chunk := src.read(_CHUNK):
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())


def _copy_verified(source: Path, target: Path, sha256: str) -> bool:
    """Copy to a temporary name, verify the *copy's* hash, then link it into place. True when
    ``target`` now holds the right bytes. Nothing at ``target`` changes unless the copy verified."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{sha256}.{uuid.uuid4().hex}.tmp"
    try:
        _write_copy(source, temp)
        if file_state(temp, sha256) != OK:
            return False
        with contextlib.suppress(FileExistsError):  # a concurrent upload of the same file won
            os.link(temp, target)
        return file_state(target, sha256) == OK
    except OSError:
        return False
    finally:
        temp.unlink(missing_ok=True)


def adopt(data_dir: Path, hashes: Iterable[str]) -> AdoptionReport:
    """Move each file from ``documents/tax/`` to ``documents/library/``. Idempotent: a second run
    finds everything in place and changes nothing. Never raises for a bad file; reports it."""
    moved = in_place = 0
    problems: list[AdoptionProblem] = []

    def report(sha256: str, problem: str) -> None:
        problems.append(AdoptionProblem(sha256, problem))
        # The hash only: never the file name or anything from the file.
        logger.warning("Library file %s: %s", sha256[:12], problem)

    for sha256 in hashes:
        new, old = path_for(data_dir, sha256), legacy_path_for(data_dir, sha256)
        in_new, in_old = file_state(new, sha256), file_state(old, sha256)
        if in_new == OK:
            if in_old == MISSING:
                in_place += 1
                continue
            old.unlink(missing_ok=True)  # a verified copy exists: the old one is redundant
            _tidy(old)
            moved += 1
        elif in_old == OK:
            if in_new == CHANGED:  # something else sits at the new address: never overwrite it
                report(sha256, CHANGED)
            elif _copy_verified(old, new, sha256):
                old.unlink(missing_ok=True)
                _tidy(old)
                moved += 1
            else:
                report(sha256, "copy failed")
        elif CHANGED in (in_new, in_old):
            report(sha256, CHANGED)
        else:
            report(sha256, MISSING)
    return AdoptionReport(moved, in_place, tuple(problems))
