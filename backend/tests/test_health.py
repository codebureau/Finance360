import sqlite3
from pathlib import Path

from finance360.config import Settings
from finance360.core.db import db_path
from finance360.core.migrate import head_revision
from finance360.main import create_app
from support import TestClient


def _settings(tmp_path: Path, **kw: object) -> Settings:
    base = {
        "data_dir": tmp_path / "data",
        "port": 8000,
        "git_sha": "dev",
        "static_dir": None,
    }
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


def test_health_reports_status_and_git_sha(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, git_sha="abc1234"))) as client:
        res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {
        "status": "healthy",
        "git_sha": "abc1234",
        "schema_version": head_revision(),
    }


def test_startup_creates_and_migrates_the_database(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert not db_path(settings.data_dir).exists()
    with TestClient(create_app(settings)) as client:
        assert db_path(settings.data_dir).is_file()
        assert client.get("/health").json()["schema_version"] == head_revision()


def test_startup_backs_up_an_existing_database_before_migrating(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    data = settings.data_dir
    data.mkdir(parents=True)
    with sqlite3.connect(db_path(data)) as con:  # an unversioned, pre-existing database
        con.execute("CREATE TABLE precious (n INTEGER)")
        con.execute("INSERT INTO precious VALUES (7)")
    con.close()
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["schema_version"] == head_revision()
    (backup,) = (data / "backups").iterdir()
    with sqlite3.connect(backup) as con:
        assert con.execute("SELECT n FROM precious").fetchall() == [(7,)]
    con.close()


def test_startup_creates_instance_data_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert not settings.data_dir.exists()
    with TestClient(create_app(settings)):
        assert settings.data_dir.is_dir()


def test_two_instances_are_independent(tmp_path: Path) -> None:
    a = _settings(tmp_path / "a", git_sha="aaa")
    b = _settings(tmp_path / "b", git_sha="bbb")
    with TestClient(create_app(a)) as ca, TestClient(create_app(b)) as cb:
        assert ca.get("/health").json()["git_sha"] == "aaa"
        assert cb.get("/health").json()["git_sha"] == "bbb"
    assert a.data_dir != b.data_dir


def test_serves_built_ui_same_origin(tmp_path: Path) -> None:
    static = tmp_path / "ui"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>ui</html>")
    (static / "assets" / "app.js").write_text("1")
    with TestClient(create_app(_settings(tmp_path, static_dir=static))) as client:
        assert client.get("/").text == "<html>ui</html>"
        assert client.get("/some/client/route").text == "<html>ui</html>"
        assert client.get("/assets/app.js").text == "1"
        assert client.get("/health").json()["status"] == "healthy"
        assert client.get("/../../etc/passwd").text == "<html>ui</html>"
        # The page is checked on every load, so an update is never hidden behind a cached copy.
        assert client.get("/").headers["cache-control"] == "no-cache"
        assert client.get("/tax/records/18").headers["cache-control"] == "no-cache"
