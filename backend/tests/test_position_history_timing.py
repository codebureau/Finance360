"""The Position history's default range answers quickly on the sample household.

A wall-clock check, so it runs in the slow tier (SLOW_FILES), not tier 1: a shared CI runner can
be briefly slow, and a timing assert there fails a PR that changed nothing nearby. The limit is
loose on purpose. It catches a regression of an order of magnitude, such as one query per point
per Account, not small drift.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def test_the_default_range_is_fast(sample: TestClient) -> None:
    sample.get("/api/position/history")  # warm-up: the first request pays one-off start-up costs
    started = time.perf_counter()
    res = sample.get("/api/position/history")
    elapsed = time.perf_counter() - started
    assert res.status_code == 200, res.text
    assert len(res.json()["points"]) >= 24
    assert elapsed < 2.0
