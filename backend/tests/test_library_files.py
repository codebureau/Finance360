"""The Library's file store and the move from ``documents/tax/`` (ADR-0026 section 2).

Unit tests of the file store (pure file I/O under a temporary folder), a Hypothesis property for the
content address, and the adoption step: copy, verify, delete; idempotent; a missing, damaged or
faulty-copy file is reported with nothing lost. Every file here is invented. Live-data invariant:
the mutation proof in the PR description breaks ``file_state`` and these fail.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.library import files

PDF = b"%PDF-1.4\n% invented document, not a real one\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"invented"


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def put_legacy(root: Path, content: bytes) -> Path:
    path = files.legacy_path_for(root, sha(content))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def all_files(root: Path) -> list[Path]:
    return sorted(p for p in (root / "documents").rglob("*") if p.is_file())


# --- the store -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("receipt.pdf", "receipt.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\win.ini", "win.ini"),
        ("C:\\Users\\someone\\scan.png", "scan.png"),
        ("a\x00b\r\nc.txt", "abc.txt"),
        ("", "document"),
        ("...", "document"),
        ("dir/", "document"),
        ("x" * 400 + ".pdf", "x" * 150),
    ],
)
def test_an_uploaded_name_is_reduced_to_a_bare_file_name(raw: str, clean: str) -> None:
    assert files.clean_file_name(raw) == clean


@pytest.mark.parametrize(
    "bad", ["../x", "", "ab", "A" * 64, "g" * 64, "a" * 63, "a" * 65, "../" + "a" * 61]
)
def test_an_address_is_only_ever_a_lower_case_sha256(bad: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        files.path_for(tmp_path, bad)
    with pytest.raises(ValueError, match="SHA-256"):
        files.legacy_path_for(tmp_path, bad)


def test_the_stored_path_comes_from_the_hash_alone(tmp_path: Path) -> None:
    digest = files.store(tmp_path, PDF)
    assert digest == sha(PDF)
    assert files.path_for(tmp_path, digest) == (
        tmp_path / "documents" / "library" / digest[:2] / digest
    )
    assert files.path_for(tmp_path, digest).read_bytes() == PDF
    assert files.state(tmp_path, digest) == files.OK


def test_the_same_bytes_are_the_same_file_and_nothing_is_overwritten(tmp_path: Path) -> None:
    digest = files.store(tmp_path, PDF)
    assert files.store(tmp_path, PDF) == digest
    assert all_files(tmp_path) == [files.path_for(tmp_path, digest)]  # one file, no temp leftovers

    # A file already at the address is never replaced, even by the right bytes: it stays visible
    # to the health check until it is restored.
    files.path_for(tmp_path, digest).write_bytes(b"damaged")
    assert files.store(tmp_path, PDF) == digest
    assert files.path_for(tmp_path, digest).read_bytes() == b"damaged"
    assert files.state(tmp_path, digest) == files.CHANGED
    assert files.read(tmp_path, digest) is None


def test_a_file_that_is_not_there_is_missing(tmp_path: Path) -> None:
    assert files.state(tmp_path, sha(PDF)) == files.MISSING
    assert files.read(tmp_path, sha(PDF)) is None


@pytest.mark.parametrize(
    ("media_type", "content", "fine"),
    [
        ("application/pdf", PDF, True),
        ("application/pdf", b"just text", False),
        ("image/png", PNG, True),
        ("image/png", PDF, False),
        ("image/jpeg", b"\xff\xd8\xff\xe0jfif", True),
        ("image/jpeg", PNG, False),
        ("text/plain", "caf\u00e9".encode(), True),
        ("text/plain", b"\xff\xfe\x00bad", False),
        ("text/csv", b"a,b\n1,2\n", True),
        ("text/csv", b"a,b\x00\n", False),
    ],
)
def test_a_file_must_be_what_it_says_it_is(media_type: str, content: bytes, fine: bool) -> None:
    assert (files.content_problem(media_type, content) is None) is fine


@given(st.binary(min_size=1, max_size=2048))
def test_any_bytes_land_at_their_own_hash_and_come_back_exactly(content: bytes) -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        digest = files.store(root, content)
        assert digest == sha(content)
        path = files.path_for(root, digest)
        assert path.parent.name == digest[:2] and path.name == digest
        assert path.resolve().is_relative_to((root / "documents" / "library").resolve())
        assert files.read(root, digest) == content


# --- reads fall back to the old path until a file has moved ------------------------------------


def test_a_file_not_yet_adopted_is_read_from_the_old_path(tmp_path: Path) -> None:
    old = put_legacy(tmp_path, PDF)
    assert files.state(tmp_path, sha(PDF)) == files.OK
    assert files.read(tmp_path, sha(PDF)) == PDF
    assert old.exists() and not files.path_for(tmp_path, sha(PDF)).exists()  # reading moves nothing


def test_a_damaged_old_file_is_not_served_and_not_ok(tmp_path: Path) -> None:
    put_legacy(tmp_path, PDF).write_bytes(b"damaged")
    assert files.state(tmp_path, sha(PDF)) == files.CHANGED
    assert files.read(tmp_path, sha(PDF)) is None


def test_the_library_path_is_preferred_and_a_good_old_copy_covers_a_bad_new_one(
    tmp_path: Path,
) -> None:
    put_legacy(tmp_path, PDF)
    new = files.path_for(tmp_path, sha(PDF))
    new.parent.mkdir(parents=True)
    new.write_bytes(b"damaged")
    assert files.read(tmp_path, sha(PDF)) == PDF  # the verified old copy serves
    assert files.state(tmp_path, sha(PDF)) == files.OK


# --- adoption --------------------------------------------------------------------------------


def test_adoption_copies_verifies_and_removes_the_old_file(tmp_path: Path) -> None:
    old = put_legacy(tmp_path, PDF)
    report = files.adopt(tmp_path, [sha(PDF)])
    new = files.path_for(tmp_path, sha(PDF))
    assert (report.moved, report.in_place, report.problems) == (1, 0, ())
    assert new.read_bytes() == PDF
    assert not old.exists()
    assert not (tmp_path / "documents" / "tax").exists()  # the empty folders are tidied away
    assert all_files(tmp_path) == [new]


def test_adoption_run_twice_changes_nothing(tmp_path: Path) -> None:
    put_legacy(tmp_path, PDF)
    put_legacy(tmp_path, b"%PDF-1.4\n% a second invented one\n")
    hashes = [sha(PDF), sha(b"%PDF-1.4\n% a second invented one\n")]
    first = files.adopt(tmp_path, hashes)
    after_first = {p: p.stat().st_mtime_ns for p in all_files(tmp_path)}
    second = files.adopt(tmp_path, hashes)
    assert (first.moved, first.in_place) == (2, 0)
    assert (second.moved, second.in_place, second.problems) == (0, 2, ())
    assert {p: p.stat().st_mtime_ns for p in all_files(tmp_path)} == after_first
    assert all(files.read(tmp_path, h) is not None for h in hashes)


def test_adoption_of_a_fresh_install_does_nothing(tmp_path: Path) -> None:
    assert files.adopt(tmp_path, []) == files.AdoptionReport(0, 0, ())
    assert not (tmp_path / "documents").exists()


def test_a_file_already_in_the_library_just_drops_the_redundant_old_one(tmp_path: Path) -> None:
    """A half-finished earlier run: the copy is in place, the old file was not yet removed."""
    old = put_legacy(tmp_path, PDF)
    files.store(tmp_path, PDF)
    report = files.adopt(tmp_path, [sha(PDF)])
    assert (report.moved, report.problems) == (1, ())
    assert not old.exists() and files.read(tmp_path, sha(PDF)) == PDF


def test_a_missing_file_is_reported_and_nothing_else_is_touched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    kept = put_legacy(tmp_path, PNG)
    with caplog.at_level(logging.WARNING):
        report = files.adopt(tmp_path, [sha(PDF), sha(PNG)])
    assert report.problems == (files.AdoptionProblem(sha(PDF), files.MISSING),)
    assert report.moved == 1 and files.read(tmp_path, sha(PNG)) == PNG
    assert not kept.exists()
    assert sha(PDF)[:12] in caplog.text and "missing" in caplog.text


def test_a_damaged_source_is_reported_and_left_exactly_where_it_is(tmp_path: Path) -> None:
    old = put_legacy(tmp_path, PDF)
    old.write_bytes(b"damaged on disk")
    report = files.adopt(tmp_path, [sha(PDF)])
    assert report.problems == (files.AdoptionProblem(sha(PDF), files.CHANGED),)
    assert report.moved == 0
    assert old.read_bytes() == b"damaged on disk"  # nothing lost, nothing "repaired"
    assert not files.path_for(tmp_path, sha(PDF)).exists()  # a bad file never reaches the library
    assert files.adopt(tmp_path, [sha(PDF)]).problems == report.problems  # and it keeps saying so


def test_a_copy_that_does_not_verify_keeps_the_old_file_and_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A faulty disk writes the wrong bytes: the copy's hash is checked, so the old file stays."""
    old = put_legacy(tmp_path, PDF)

    def faulty(source: Path, temp: Path) -> None:
        temp.write_bytes(source.read_bytes()[:-3] + b"???")

    monkeypatch.setattr(files, "_write_copy", faulty)
    report = files.adopt(tmp_path, [sha(PDF)])
    assert report.problems == (files.AdoptionProblem(sha(PDF), "copy failed"),)
    assert old.read_bytes() == PDF  # still the only copy, still good
    assert not files.path_for(tmp_path, sha(PDF)).exists()
    assert all_files(tmp_path) == [old]  # and no temporary file is left behind
    monkeypatch.undo()
    assert files.adopt(tmp_path, [sha(PDF)]).problems == ()  # the next run moves it


def test_a_damaged_file_at_the_new_address_is_never_overwritten(tmp_path: Path) -> None:
    old = put_legacy(tmp_path, PDF)
    new = files.path_for(tmp_path, sha(PDF))
    new.parent.mkdir(parents=True)
    new.write_bytes(b"something else")
    report = files.adopt(tmp_path, [sha(PDF)])
    assert report.problems == (files.AdoptionProblem(sha(PDF), files.CHANGED),)
    assert new.read_bytes() == b"something else" and old.read_bytes() == PDF


@given(st.lists(st.binary(min_size=1, max_size=64), min_size=1, max_size=5, unique=True))
def test_adoption_keeps_every_byte_of_every_file(blobs: list[bytes]) -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for blob in blobs:
            put_legacy(root, blob)
        report = files.adopt(root, [sha(b) for b in blobs])
        assert report.problems == () and report.moved == len(blobs)
        assert [files.read(root, sha(b)) for b in blobs] == blobs
        assert files.adopt(root, [sha(b) for b in blobs]).moved == 0
