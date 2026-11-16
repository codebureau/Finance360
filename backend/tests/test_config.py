from pathlib import Path

import pytest

from finance360.config import Settings


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.port == 8000
    assert s.git_sha == "dev"
    assert s.static_dir is None


def test_env_overrides(tmp_path: Path) -> None:
    s = Settings.from_env(
        {"FINANCE360_DATA_DIR": str(tmp_path), "FINANCE360_PORT": "9123", "GIT_SHA": "abc"}
    )
    assert s.data_dir == tmp_path.resolve()
    assert s.port == 9123
    assert s.git_sha == "abc"


def test_empty_values_fall_back_to_defaults() -> None:
    s = Settings.from_env({"FINANCE360_PORT": "", "GIT_SHA": ""})
    assert (s.port, s.git_sha) == (8000, "dev")


@pytest.mark.parametrize("bad", ["0", "70000", "-1"])
def test_port_out_of_range_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        Settings.from_env({"FINANCE360_PORT": bad})
