"""The Ask and AI endpoints (ADR-0021, ADR-0023): status, Ask's two steps (search, then the written
answer), the AI log. A scripted provider stands in for the model; the household is the synthetic
sample one, so the names the provider must not see are the fixture's own (invented) Members and
Accounts.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from ai_support import FakeProvider, cited, reply
from sqlalchemy import select

from finance360.capabilities.ai import gateway, providers
from finance360.capabilities.ai.log import utcnow
from finance360.config import Settings
from finance360.core.ai_settings import AiSettings
from finance360.core.models.account import Account
from finance360.core.models.ai_log import AiLog
from finance360.core.models.household import Member
from finance360.main import create_app
from support import ORIGIN, TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]


def make_app(tmp_path: Path, ai: AiSettings) -> object:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    return create_app(Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None, ai=ai))


@pytest.fixture
def model() -> Iterator[FakeProvider]:
    fake = FakeProvider(cited("Start with the Guide.", "start-here"), key="anthropic")
    yield fake
    providers.unregister("anthropic")


@pytest.fixture
def on(tmp_path: Path, model: FakeProvider) -> Iterator[TestClient]:
    app = make_app(tmp_path, AiSettings(provider="anthropic", api_key="synthetic-test-key"))
    providers.register(model)  # replaces the real provider create_app registered
    with owner_client(app) as client:  # type: ignore[arg-type]
        yield client


@pytest.fixture
def off(tmp_path: Path) -> Iterator[TestClient]:
    with owner_client(make_app(tmp_path, AiSettings())) as client:  # type: ignore[arg-type]
        yield client


def log_rows(client: TestClient) -> list[dict[str, object]]:
    return client.get("/api/ai/log").json()


def spend(client: TestClient, micros: int, outcome: str = "ok") -> None:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        s.add(
            AiLog(
                occurred_at=utcnow() - timedelta(seconds=1),
                task="ask",
                data_class="guidance",
                provider="anthropic",
                model="m",
                outcome=outcome,
                sent="",
                public_parts="[]",
                received=None,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cost_usd_micros=micros,
                cost_estimated=False,
                why="seed",
            )
        )


# --- status ------------------------------------------------------------------------------------


def test_status_says_off_when_no_provider_is_set_and_carries_nothing_secret(
    off: TestClient,
) -> None:
    assert off.get("/api/ai/status").json() == {
        "enabled": False,
        "reason": "off",
        "resets_on": None,
        "retrieval": "keyword",
        "answering": False,
    }


def test_status_says_enabled_with_a_provider(on: TestClient) -> None:
    assert on.get("/api/ai/status").json() == {
        "enabled": True,
        "reason": None,
        "resets_on": None,
        "retrieval": "keyword",
        "answering": True,
    }


def test_status_reports_a_cap_and_when_it_lifts(on: TestClient) -> None:
    spend(on, 5_000_000)
    body = on.get("/api/ai/status").json()
    assert (body["enabled"], body["reason"]) == (False, "monthly_cap")
    assert body["resets_on"] is not None
    assert body["answering"] is True  # a model is set up; a cap only pauses it


# --- searching: step one, no model -------------------------------------------------------------


def test_search_returns_passages_with_links_and_needs_no_model(off: TestClient) -> None:
    r = off.post("/api/ask", json={"question": "How do I capture a Rule figure?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True and body["message"] == "" and body["start"] is None
    assert 1 <= len(body["passages"]) <= 3
    first = body["passages"][0]
    assert first["page"] == "rule-figures" and first["link"] == "/guide/rule-figures"
    assert first["label"] == "Rule figures: keeping official rates current"
    assert first["heading"] and first["excerpt"]
    assert log_rows(off) == []  # not an AI call: nothing logged


def test_search_finds_nothing_with_the_fixed_text_and_the_start_here_link(off: TestClient) -> None:
    body = off.post("/api/ask", json={"question": "Zzzz qqqq xylophone?"}).json()
    assert body["found"] is False and body["passages"] == []
    assert body["message"] == "I couldn't find that in the Guide. Start here may help."
    assert body["start"] == {
        "kind": "guide",
        "ref": "start-here",
        "label": "Start here",
        "link": "/guide",
    }


def test_search_never_calls_the_model_even_when_one_is_set_up(
    on: TestClient, model: FakeProvider
) -> None:
    assert (
        on.post("/api/ask", json={"question": "How do I capture a Rule figure?"}).status_code == 200
    )
    assert model.requests == [] and log_rows(on) == []


def test_search_validates_its_input_in_the_shared_shape(off: TestClient) -> None:
    r = off.post("/api/ask", json={"question": "  "})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "question"


# --- answering: step two, the written answer ---------------------------------------------------


def test_an_answer_has_text_sources_with_guide_labels_and_a_flag(
    on: TestClient, model: FakeProvider
) -> None:
    model.say(reply(True, "It is at the start.", [{"kind": "guide", "ref": "start-here"}]))
    r = on.post(
        "/api/ask/answer",
        json={"question": "Where do I begin with the Guide?", "screen": "/", "history": []},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "answer": "It is at the start.",
        "sources": [
            {
                "kind": "guide",
                "ref": "start-here",
                "label": "Start here: why are you here?",
                "link": "/guide",
            }
        ],
        "answered": True,
        "model": "claude-haiku-5-5",
    }


def test_the_provider_never_sees_a_sample_household_name_and_the_log_proves_it(
    on: TestClient, model: FakeProvider
) -> None:
    factory = on.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        secrets = [*s.scalars(select(Member.name)), *s.scalars(select(Account.name))]
    assert len(secrets) > 5
    question = f"Is {secrets[0]} or {secrets[2]} or {secrets[-1]} behind? My balance is $48,000."
    assert on.post("/api/ask/answer", json={"question": question}).status_code == 200
    for secret in secrets:
        assert secret.casefold() not in model.everything_sent.casefold(), secret
    (row,) = log_rows(on)
    assert row["outcome"] == "ok" and row["data_class"] == "guidance" and row["task"] == "ask"
    assert secrets[0] not in str(row["sent"]) and "$48,000" not in str(row["sent"])
    assert "[AMOUNT]" in str(row["sent"])
    assert json.loads(str(row["received"]))["answered"] is True


def test_an_unanswerable_question_gets_the_fixed_text_and_start_here(
    on: TestClient, model: FakeProvider
) -> None:
    model.say(reply(True, "Made up.", [{"kind": "guide", "ref": "invented"}]))
    body = on.post("/api/ask/answer", json={"question": "Something odd in the Guide?"}).json()
    assert body["answered"] is False
    assert body["answer"] == "I couldn't find that in the Guide. Start here may help."
    assert [s["ref"] for s in body["sources"]] == ["start-here"]
    assert log_rows(on)[0]["outcome"] == "unsupported"


def test_asking_with_ai_off_is_409_with_the_reason_and_nothing_is_logged(off: TestClient) -> None:
    r = off.post("/api/ask/answer", json={"question": "Where is the Guide?"})
    assert r.status_code == 409
    assert r.json() == {
        "message": "Written answers are not set up on this server.",
        "reason": "off",
        "resets_on": None,
    }
    assert log_rows(off) == []


def test_a_cap_refuses_with_409_before_the_provider_and_the_refusal_is_kept(
    on: TestClient, model: FakeProvider
) -> None:
    spend(on, 5_000_000)
    r = on.post("/api/ask/answer", json={"question": "Where is the Guide?"})
    assert r.status_code == 409
    body = r.json()
    assert body["reason"] == "monthly_cap" and body["resets_on"] is not None
    assert "monthly" in body["message"]
    assert model.requests == []  # the provider was never reached
    assert log_rows(on)[0]["outcome"] == "refused_cap"  # committed even though refused


def test_a_provider_failure_is_502_with_calm_wording_and_is_logged(
    on: TestClient, model: FakeProvider
) -> None:
    model.say(RuntimeError("secret-looking detail sk-test-not-real"))
    r = on.post("/api/ask/answer", json={"question": "Where is the Guide?"})
    assert r.status_code == 502
    assert "sk-test" not in r.text and "secret" not in r.text
    assert r.json()["message"] == "The AI service did not answer. Try again in a moment."
    assert log_rows(on)[0]["outcome"] == "error"


def test_a_second_question_while_one_is_being_answered_is_429(
    on: TestClient, model: FakeProvider
) -> None:
    assert gateway._LOCK.acquire(blocking=False)
    try:
        r = on.post("/api/ask/answer", json={"question": "Where is the Guide?"})
    finally:
        gateway._LOCK.release()
    assert r.status_code == 429 and "One moment" in r.json()["message"]
    assert model.requests == []
    assert (
        on.post("/api/ask/answer", json={"question": "Where is the Guide?"}).status_code == 200
    )  # lock released


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"question": ""}, "question"),
        ({"question": "q" * 501}, "question"),
        ({"question": "Guide", "screen": "not a path"}, "screen"),
        ({"question": "Guide", "guide_slug": "nope"}, "guide_slug"),
        ({"question": "Guide", "history": [{"role": "user", "text": "x"}]}, "history"),
    ],
)
def test_bad_input_is_422_in_the_shared_errors_shape(
    on: TestClient, model: FakeProvider, body: dict[str, object], field: str
) -> None:
    r = on.post("/api/ask/answer", json=body)
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == field and r.json()["errors"][0]["message"]
    assert model.requests == []


def test_a_malformed_body_is_422_in_the_shared_shape(on: TestClient) -> None:
    r = on.post(
        "/api/ask/answer", json={"question": "Guide", "history": [{"role": "robot", "text": "x"}]}
    )
    assert r.status_code == 422 and "errors" in r.json()


# --- the log -----------------------------------------------------------------------------------


def test_the_log_is_newest_first_and_limited(on: TestClient) -> None:
    for i in range(3):
        assert (
            on.post("/api/ask/answer", json={"question": f"Guide question {i}"}).status_code == 200
        )
    rows = log_rows(on)
    assert [r["id"] for r in rows] == [3, 2, 1]
    assert "Guide question 2" in str(rows[0]["sent"])
    assert [r["id"] for r in on.get("/api/ai/log", params={"limit": 2}).json()] == [3, 2]
    assert set(rows[0]) >= {
        "occurred_at",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cost_usd_micros",
        "cost_estimated",
        "public_parts",
        "why",
    }


def test_nothing_here_works_without_a_sign_in(tmp_path: Path) -> None:
    app = make_app(tmp_path, AiSettings())
    with TestClient(app, headers=ORIGIN) as anonymous:  # type: ignore[arg-type]
        assert anonymous.get("/api/ai/status").status_code == 401
        assert anonymous.get("/api/ai/log").status_code == 401
        assert (
            anonymous.post("/api/ask/answer", json={"question": "Where is the Guide?"}).status_code
            == 401
        )
