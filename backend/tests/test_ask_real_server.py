"""Ask over a real server (uvicorn, a random port) with a real sign-in and a stale session, against
the sample household and a scripted provider standing in for the model (ADR-0021 and
ADR-0023). The write routes (the written answer and a proposal write the AI log; a Confirm writes a
capture) need the real path once: TestClient with an overridden principal hides the cookie, the
origin check, commit-before-response (a refusal's log row must be readable at once) and a session
whose ``last_seen_at`` needs refreshing."""

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from ai_support import FakeProvider, cited

from finance360.capabilities.ai import providers
from finance360.config import Settings
from finance360.core.ai_settings import AiSettings
from finance360.main import create_app

REPO = Path(__file__).resolve().parents[2]
PASSWORD = "sample-fixture-passphrase"  # synthetic, seeded into the temporary instance


LEVY_SENTENCE = "The Medicare levy rate for 2026-27 is 2 per cent of taxable income."
LEVY = {
    "key": "tax.medicare.levy_rate",
    "fy": 2027,
    "value": "2",
    "quote": LEVY_SENTENCE,
    "provisional": False,
    "note": "",
    "confidence": "high",
}


@pytest.fixture
def model() -> Iterator[FakeProvider]:
    fake = FakeProvider(
        cited("Capture it on the Rule figures screen.", "rule-figures"), key="anthropic"
    )
    yield fake
    providers.unregister("anthropic")


@pytest.fixture
def signed_in(tmp_path: Path, model: FakeProvider) -> Iterator[httpx.Client]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = Settings(
        data_dir=data,
        port=port,
        git_sha="dev",
        static_dir=None,
        cookie_secure=False,  # plain http on 127.0.0.1
        owner_password=PASSWORD,
        ai=AiSettings(provider="anthropic", api_key="synthetic-test-key", daily_questions=1),
    )
    app = create_app(settings)
    providers.register(model)  # replaces the real provider create_app registered
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=url, timeout=10, headers={"Origin": url}) as http:
        assert http.post("/api/ask", json={"question": "Hello?"}).status_code == 401
        assert http.post("/api/ask/answer", json={"question": "Hello?"}).status_code == 401
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with sqlite3.connect(data / "finance360.sqlite") as db:  # a session idle for ten minutes
            db.execute("UPDATE auth_session SET last_seen_at = datetime('now', '-10 minutes')")
        yield http
    server.should_exit = True
    thread.join(timeout=10)


def test_a_question_is_searched_then_answered_and_logged_and_a_refusal_is_kept_over_a_real_server(
    signed_in: httpx.Client,
) -> None:
    http = signed_in
    assert http.get("/api/ai/status").json()["enabled"] is True

    question = {"question": "How do I capture a Rule figure for Alex Sample?", "screen": "/"}
    found = http.post("/api/ask", json=question)  # step one: search, no model, nothing logged
    assert found.status_code == 200, found.text
    assert found.json()["passages"][0]["page"] == "rule-figures"
    assert http.get("/api/ai/log").json() == []

    asked = http.post("/api/ask/answer", json=question)  # step two: the written answer
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["answered"] is True and body["model"] == "claude-haiku-5-5"
    assert [(s["kind"], s["ref"]) for s in body["sources"]] == [("guide", "rule-figures")]

    # committed before the response returned, so the next read sees it, redacted
    (row,) = http.get("/api/ai/log").json()
    assert row["outcome"] == "ok" and row["provider"] == "anthropic"
    assert "Alex Sample" not in row["sent"] and "[MEMBER_" in row["sent"]
    assert "### PASSAGE page=rule-figures" in row["sent"] or "rule-figures" in row["public_parts"]

    # the daily limit (1) is reached: refused with 409, and the refusal's row is kept
    capped = http.post("/api/ask/answer", json={"question": "Where is the Guide?"})
    assert capped.status_code == 409 and capped.json()["reason"] == "daily_limit"
    assert http.get("/api/ai/status").json()["reason"] == "daily_limit"
    assert [r["outcome"] for r in http.get("/api/ai/log").json()] == ["refused_cap", "ok"]
    # ...and search is not a question: it still works with the limit reached
    assert http.post("/api/ask", json=question).status_code == 200


def test_reading_a_page_and_confirming_a_figure_work_over_a_real_server(
    signed_in: httpx.Client, model: FakeProvider
) -> None:
    """The new proposal route writes the AI log; the confirm is the existing capture
    write carrying the proposal. Ask's one daily question is used up first, to show that reading a
    page is neither counted by that limit nor stopped by it."""
    http = signed_in
    # As in the screen, a read comes first (the page and the AI status load before anything is
    # asked), which refreshes the stale session and commits before the gateway writes its log row.
    assert http.get("/api/ai/status").json()["enabled"] is True
    assert (
        http.post(
            "/api/ask/answer", json={"question": "Where do I begin with the Guide?"}
        ).status_code
        == 200
    )
    assert http.get("/api/ai/status").json()["reason"] == "daily_limit"
    status = http.get("/api/ai/status", params={"task": "rule_capture"}).json()
    assert status["enabled"] is True

    page = (REPO / "fixtures" / "sample_household" / "rule_page_sample.txt").read_text("utf-8")
    key = "tax.medicare.levy_rate"
    model.say(json.dumps({"proposals": [LEVY], "not_found": []}))
    proposed = http.post(
        "/api/rule-figures/proposals",
        json={"financial_year": 2027, "keys": [key], "page_text": page},
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["method"] == "ai"
    (candidate,) = proposed.json()["proposals"]
    assert (candidate["status"], candidate["value"]) == ("candidate", "2%")

    # the call is logged, with the banner's name removed and the page's figures kept
    rows = http.get("/api/ai/log").json()
    row = next(r for r in rows if r["task"] == "rule_capture")
    assert row["id"] == candidate["ai_log_id"] and row["data_class"] == "reference"
    assert "Alex Sample" not in row["sent"] and "[MEMBER_" in row["sent"]
    assert "$48,000" in row["sent"]

    # nothing was stored by proposing
    listed = http.get("/api/rule-figures", params={"financial_year": 2027}).json()
    assert next(f for f in listed["figures"] if f["key"] == key)["state"] == "missing"

    confirmed = http.post(
        "/api/rule-figures",
        json={
            "financial_year": 2027,
            "key": key,
            "value": candidate["value"],
            "quote": candidate["quote"],
            "ai_log_id": candidate["ai_log_id"],
            "ai_proposal": candidate["proposal"],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["capture"]["method"] == "ai"
    after = http.get("/api/rule-figures", params={"financial_year": 2027}).json()
    levy = next(f for f in after["figures"] if f["key"] == key)
    assert (levy["state"], levy["value"]) == ("captured", "2%")


def test_a_failing_model_falls_back_to_matching_words_and_a_matched_figure_is_confirmed(
    signed_in: httpx.Client, model: FakeProvider
) -> None:
    """ADR-0023: the model fails, the word matcher reads the page, and the Confirm stores method
    ``matched`` with no AI log entry, over the real path (cookie, origin check, stale session)."""
    http = signed_in
    assert http.get("/api/ai/status", params={"task": "rule_capture"}).json()["enabled"] is True
    model.say(RuntimeError("provider down"))
    page = (REPO / "fixtures" / "sample_household" / "rule_page_sample.txt").read_text("utf-8")
    key = "tax.medicare.levy_rate"
    proposed = http.post(
        "/api/rule-figures/proposals",
        json={"financial_year": 2027, "keys": [key], "page_text": page},
    )
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["method"] == "matched" and "matching words" in body["fallback"]
    (candidate,) = body["proposals"]
    assert (candidate["status"], candidate["value"], candidate["ai_log_id"]) == (
        "candidate",
        "2%",
        None,
    )
    confirmed = http.post(
        "/api/rule-figures",
        json={
            "financial_year": 2027,
            "key": key,
            "value": candidate["value"],
            "quote": candidate["quote"],
            "matched_proposal": candidate["proposal"],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["capture"]["method"] == "matched"
    assert [r["outcome"] for r in http.get("/api/ai/log").json()] == [
        "error"
    ]  # the failed call only
