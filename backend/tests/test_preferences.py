"""Display preferences (ADR-0031 section 3): the service and the API, on a real migrated database.

Every key and value is invented presentation text: nothing financial is stored.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.auth import logins
from finance360.capabilities.preferences import service
from finance360.config import Settings
from finance360.core.errors import ValidationError
from finance360.core.models.display_preference import DisplayPreference
from finance360.main import create_app
from support import OWNER, TestClient, owner_client


@pytest.fixture
def login_id(session_factory: sessionmaker[Session]) -> int:
    with session_factory.begin() as s:
        return logins.create_login(s, "synthetic-test-passphrase-1", now=datetime(2026, 1, 1)).id


def test_a_new_login_has_no_preferences_and_a_set_value_is_read_back(
    session_factory: sessionmaker[Session], login_id: int
) -> None:
    with session_factory.begin() as s:
        assert service.get_preferences(s, login_id) == {}
        out = service.put_preferences(
            s, login_id, {"position.composition": "bars", "default.share": "donut"}
        )
        assert out == {"position.composition": "bars", "default.share": "donut"}
    with session_factory() as s:
        assert service.get_preferences(s, login_id) == {
            "default.share": "donut",
            "position.composition": "bars",
        }


def test_a_partial_put_changes_only_the_named_keys_and_null_forgets_one(
    session_factory: sessionmaker[Session], login_id: int
) -> None:
    with session_factory.begin() as s:
        service.put_preferences(s, login_id, {"a.view": "donut", "b.view": "bars"})
        out = service.put_preferences(s, login_id, {"a.view": "stacked-bar", "b.view": None})
    assert out == {"a.view": "stacked-bar"}
    with session_factory() as s:
        rows = s.scalars(select(DisplayPreference)).all()
        assert [(r.key, r.value) for r in rows] == [("a.view", "stacked-bar")]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("nodot", "donut"),
        ("Upper.case", "donut"),
        ("a.view", "Donut"),
        ("a.view", ""),
        ("a.view", "x" * 25),
        ("a.view", "12,345.67 AUD"),
        ("a" * 60 + ".view", "donut"),
        ("a. view", "donut"),
    ],
)
def test_a_key_or_value_that_could_hold_text_or_figures_is_refused_and_nothing_is_kept(
    session_factory: sessionmaker[Session], login_id: int, key: str, value: str
) -> None:
    with session_factory.begin() as s:
        service.put_preferences(s, login_id, {"ok.view": "donut"})
    with pytest.raises(ValidationError) as caught, session_factory.begin() as s:
        service.put_preferences(s, login_id, {"ok.view": "bars", key: value})
    assert [e.field for e in caught.value.errors] == [key]
    with session_factory() as s:
        assert service.get_preferences(s, login_id) == {"ok.view": "donut"}


def test_rows_per_login_are_capped(session_factory: sessionmaker[Session], login_id: int) -> None:
    with session_factory.begin() as s:
        service.put_preferences(
            s, login_id, {f"view.k{i}": "donut" for i in range(service.MAX_PER_LOGIN)}
        )
    with pytest.raises(ValidationError), session_factory.begin() as s:
        service.put_preferences(s, login_id, {"view.one-more": "donut"})
    with session_factory() as s:
        assert len(service.get_preferences(s, login_id)) == service.MAX_PER_LOGIN


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app: FastAPI = create_app(
        Settings(data_dir=tmp_path, port=8000, git_sha="dev", static_dir=None)
    )
    with owner_client(app) as http:  # starts the app, so the database exists
        with app.state.session_factory.begin() as s:
            login = logins.create_login(s, "synthetic-test-passphrase-1", now=datetime(2026, 1, 1))
            assert login.id == OWNER.login_id
        yield http


def test_the_api_reads_and_writes_the_signed_in_logins_map(client: TestClient) -> None:
    assert client.get("/api/preferences").json() == {"preferences": {}}
    put = client.put("/api/preferences", json={"preferences": {"position.composition": "bars"}})
    assert put.status_code == 200, put.text
    assert put.json() == {"preferences": {"position.composition": "bars"}}
    assert client.get("/api/preferences").json() == {
        "preferences": {"position.composition": "bars"}
    }
    gone = client.put("/api/preferences", json={"preferences": {"position.composition": None}})
    assert gone.json() == {"preferences": {}}


def test_the_api_refuses_a_bad_key_in_the_shared_error_shape(client: TestClient) -> None:
    bad = client.put("/api/preferences", json={"preferences": {"nodot": "donut"}})
    assert bad.status_code == 422
    assert bad.json() == {"errors": [{"field": "nodot", "message": "Not a valid preference key."}]}
    assert client.get("/api/preferences").json() == {"preferences": {}}
