"""Rule figure capture from a pasted page, over the API (ADR-0022 section 5, ADR-0023).

A scripted provider stands in for the model; the household is the synthetic sample one with its
invented captured car rate for FY 2026-27. A proposal stores nothing: only a Confirm does, through
the same write path as typed entry, with the proposal kept beside the confirmed value. With no model
(or one that is capped or failing) the word matcher reads the page instead; its figures are stored
with method ``matched`` and no AI log entry.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from ai_support import FakeProvider
from sqlalchemy import func, select

from finance360.capabilities.ai import providers
from finance360.capabilities.ai.log import utcnow
from finance360.config import Settings
from finance360.core.ai_settings import AiSettings
from finance360.core.models.ai_log import AiLog
from finance360.core.models.audit import AuditEvent
from finance360.core.models.rule_capture import RuleCapture
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
RATE = "tax.car.rate_per_km"  # the sample household has captured this for FY 2026-27
LIMIT = "tax.car.max_km"  # ...but not this one
FY = 2027
LIMIT_SENTENCE = "You can claim at most 5,000 kilometres for each car in the year."
RATE_SENTENCE = "For FY 2026-27 the cents per kilometre rate is 99 cents."
PAGE = f"The Invented Car Page\n{RATE_SENTENCE}\n{LIMIT_SENTENCE}\nUpdated for the year."


def proposal(key: str = LIMIT, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": key,
        "fy": FY,
        "value": "5000",
        "quote": LIMIT_SENTENCE,
        "provisional": False,
        "note": "",
        "confidence": "high",
    }
    return base | over


def says(*proposals: dict[str, Any]) -> str:
    return json.dumps({"proposals": list(proposals), "not_found": []})


def make_app(tmp_path: Path, ai: AiSettings) -> object:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    return create_app(Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None, ai=ai))


@pytest.fixture
def model() -> Iterator[FakeProvider]:
    fake = FakeProvider(says(proposal()), key="anthropic")
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


def propose(client: TestClient, *keys: str, page: str = PAGE, fy: int = FY) -> Any:
    return client.post(
        "/api/rule-figures/proposals",
        json={"financial_year": fy, "keys": list(keys), "page_text": page},
    )


def count(client: TestClient, model_class: type) -> int:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        return int(s.scalar(select(func.count()).select_from(model_class)) or 0)


def log(client: TestClient, task: str, micros: int, outcome: str = "ok") -> None:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        s.add(
            AiLog(
                occurred_at=utcnow() - timedelta(seconds=1),
                task=task,
                data_class="guidance" if task == "ask" else "reference",
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


def figure(client: TestClient, key: str) -> dict[str, Any]:
    figures = client.get("/api/rule-figures", params={"financial_year": FY}).json()["figures"]
    return next(f for f in figures if f["key"] == key)


def confirm_body(candidate: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {
        "financial_year": FY,
        "key": candidate["key"],
        "value": candidate["value"],
        "quote": candidate["quote"],
        "provisional_note": candidate["provisional_note"],
        "page_url": "https://example.com/invented-car-page",
        "ai_log_id": candidate["ai_log_id"],
        "ai_proposal": candidate["proposal"],
    } | over


# --- propose ---------------------------------------------------------------------------------


def test_a_proposal_returns_the_checked_candidate_and_stores_nothing(
    on: TestClient, model: FakeProvider
) -> None:
    before = (count(on, RuleCapture), count(on, AuditEvent))
    r = propose(on, LIMIT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["financial_year"], body["method"], body["fallback"]) == (FY, "ai", None)
    (candidate,) = body["proposals"]
    assert candidate | {"ai_log_id": None} == {
        "key": LIMIT,
        "label": "Car expenses: kilometre limit per car",
        "status": "candidate",
        "reason": None,
        "value": "5000",
        "quote": LIMIT_SENTENCE,
        "provisional_note": None,
        "note": "",
        "confidence": "high",
        "jump": None,
        "ai_log_id": None,
        "proposal": proposal(),
    }
    assert candidate["ai_log_id"] is not None
    assert (count(on, RuleCapture), count(on, AuditEvent)) == before  # nothing stored
    assert figure(on, LIMIT)["state"] == "missing"  # and the figure is still missing
    assert len(model.requests) == 1


def test_a_failed_proposal_comes_back_as_not_found_with_the_reason(
    on: TestClient, model: FakeProvider
) -> None:
    model.say(says(proposal(value="500000", quote="A limit of 500,000 kilometres per car.")))
    (row,) = propose(on, LIMIT, page=PAGE + "\nA limit of 500,000 kilometres per car.").json()[
        "proposals"
    ]
    assert row["status"] == "not_found" and "outside the plausible range" in row["reason"]
    assert (row["value"], row["quote"], row["proposal"]) == (None, None, None)


def test_bad_input_is_a_validation_error_in_the_shared_shape(
    on: TestClient, model: FakeProvider
) -> None:
    r = propose(on, LIMIT, page="x" * 40_001)
    assert r.status_code == 422
    (error,) = r.json()["errors"]
    assert error["field"] == "page_text" and "40,000" in error["message"]
    assert propose(on, "tax.nonsense").json()["errors"][0]["field"] == "keys"
    assert model.requests == []


# --- confirm ---------------------------------------------------------------------------------


def test_confirming_a_proposal_stores_the_figure_with_the_log_row_and_the_proposal(
    on: TestClient,
) -> None:
    (candidate,) = propose(on, LIMIT).json()["proposals"]
    r = on.post("/api/rule-figures", json=confirm_body(candidate))
    assert r.status_code == 201, r.text
    assert (r.json()["state"], r.json()["value"]) == ("captured", "5000")
    assert r.json()["capture"]["method"] == "ai"
    assert r.json()["capture"]["quote"] == LIMIT_SENTENCE
    factory = on.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        row = s.scalars(select(RuleCapture).where(RuleCapture.key == LIMIT)).one()
        audit = s.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "rule_capture", AuditEvent.entity_id == row.id
            )
        ).one()
    assert (row.method, row.ai_log_id, row.value_json) == ("ai", candidate["ai_log_id"], 5000)
    assert row.ai_value_json == proposal()
    assert row.quote == LIMIT_SENTENCE
    assert audit.action == "create"


def test_a_corrected_value_is_stored_beside_what_the_ai_proposed(on: TestClient) -> None:
    (candidate,) = propose(on, LIMIT).json()["proposals"]
    r = on.post("/api/rule-figures", json=confirm_body(candidate, value="4800"))
    assert r.status_code == 201, r.text
    factory = on.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        row = s.scalars(select(RuleCapture).where(RuleCapture.key == LIMIT)).one()
    assert (row.value_json, row.ai_value_json["value"]) == (4800, "5000")


def test_a_confirmed_value_is_validated_exactly_as_a_typed_one(on: TestClient) -> None:
    (candidate,) = propose(on, LIMIT).json()["proposals"]
    r = on.post("/api/rule-figures", json=confirm_body(candidate, value="500000"))
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "value"
    assert count(on, RuleCapture) == 1  # only the fixture's invented car rate


def test_a_large_change_needs_the_explicit_tick_even_for_a_proposal(
    on: TestClient, model: FakeProvider
) -> None:
    page = PAGE + "\nThe car claim limit is 9,000 kilometres per car."
    model.say(
        says(proposal(value="9000", quote="The car claim limit is 9,000 kilometres per car."))
    )
    (candidate,) = propose(on, LIMIT, page=page).json()["proposals"]
    assert candidate["status"] == "candidate" and "5000" in candidate["jump"]
    refused = on.post("/api/rule-figures", json=confirm_body(candidate))
    assert refused.status_code == 422 and refused.json()["errors"][0]["field"] == "confirm_jump"
    assert (
        on.post("/api/rule-figures", json=confirm_body(candidate, confirm_jump=True)).status_code
        == 201
    )


def test_a_proposal_for_a_captured_figure_corrects_it_and_the_old_row_stays(
    on: TestClient, model: FakeProvider
) -> None:
    model.say(says(proposal(RATE, value="0.99", quote=RATE_SENTENCE)))
    (candidate,) = propose(on, RATE).json()["proposals"]
    old = figure(on, RATE)["capture"]["id"]
    body = confirm_body(candidate)
    for field in ("financial_year", "key"):
        del body[field]
    r = on.put(f"/api/rule-figures/{old}", json=body)
    assert r.status_code == 200, r.text
    assert (r.json()["value"], r.json()["capture"]["method"]) == ("0.99", "ai")
    factory = on.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        rows = list(s.scalars(select(RuleCapture).where(RuleCapture.key == RATE)))
    assert [(r.method, r.ended_kind) for r in rows] == [("typed", "superseded"), ("ai", None)]
    assert rows[1].ai_log_id == candidate["ai_log_id"]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"ai_log_id": 99999}, "ai_log_id"),  # no such call
        ({"quote": None}, "quote"),  # an AI figure needs its sentence
        ({"quote": "   "}, "quote"),
        ({"ai_proposal": None}, "ai_proposal"),  # one without the other
        ({"ai_log_id": None}, "ai_proposal"),
    ],
)
def test_ai_provenance_that_does_not_hold_up_is_refused(
    on: TestClient, changes: dict[str, Any], field: str
) -> None:
    (candidate,) = propose(on, LIMIT).json()["proposals"]
    r = on.post("/api/rule-figures", json=confirm_body(candidate, **changes))
    assert r.status_code == 422, r.text
    assert field in {e["field"] for e in r.json()["errors"]}
    assert count(on, RuleCapture) == 1


def test_an_ask_log_row_cannot_stand_in_for_a_proposal(on: TestClient) -> None:
    (candidate,) = propose(on, LIMIT).json()["proposals"]
    log(on, "ask", 0)
    factory = on.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        ask_id = s.scalar(select(AiLog.id).where(AiLog.task == "ask"))
    r = on.post("/api/rule-figures", json=confirm_body(candidate, ai_log_id=ask_id))
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "ai_log_id"


def test_a_typed_capture_is_unchanged_by_all_this(on: TestClient) -> None:
    body = {"financial_year": FY, "key": LIMIT, "value": "5000", "quote": None}
    r = on.post("/api/rule-figures", json=body)
    assert r.status_code == 201
    assert r.json()["capture"]["method"] == "typed"
    factory = on.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        row = s.scalars(select(RuleCapture).where(RuleCapture.key == LIMIT)).one()
    assert (row.ai_log_id, row.ai_value_json) == (None, None)


# --- the word matcher: no model, no AI log ---------------------------------------------------

MATCH_LIMIT_SENTENCE = "Car expenses: the kilometre limit per car is 5,000 kilometres."
MATCH_PAGE = f"The Invented Car Page\n{MATCH_LIMIT_SENTENCE}\nUpdated for the year."


def matched_body(candidate: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {
        "financial_year": FY,
        "key": candidate["key"],
        "value": candidate["value"],
        "quote": candidate["quote"],
        "provisional_note": candidate["provisional_note"],
        "page_url": "https://example.com/invented-car-page",
        "matched_proposal": candidate["proposal"],
    } | over


def test_with_no_model_the_page_is_read_by_matching_words_and_nothing_is_logged(
    off: TestClient,
) -> None:
    r = propose(off, LIMIT, page=MATCH_PAGE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["method"], body["fallback"]) == ("matched", None)
    (candidate,) = body["proposals"]
    assert (candidate["status"], candidate["value"], candidate["quote"]) == (
        "candidate",
        "5000",
        MATCH_LIMIT_SENTENCE,
    )
    assert candidate["confidence"] is None and candidate["ai_log_id"] is None
    assert "confidence" not in {k for k, v in candidate["proposal"].items() if v is not None}
    assert count(off, AiLog) == 0 and count(off, RuleCapture) == 1
    status = off.get("/api/ai/status", params={"task": "rule_capture"}).json()
    assert (status["enabled"], status["answering"]) == (False, False)


def test_confirming_a_matched_figure_stores_method_matched_with_no_ai_log_entry(
    off: TestClient,
) -> None:
    (candidate,) = propose(off, LIMIT, page=MATCH_PAGE).json()["proposals"]
    r = off.post("/api/rule-figures", json=matched_body(candidate))
    assert r.status_code == 201, r.text
    assert (r.json()["state"], r.json()["value"]) == ("captured", "5000")
    assert r.json()["capture"]["method"] == "matched"
    factory = off.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        row = s.scalars(select(RuleCapture).where(RuleCapture.key == LIMIT)).one()
    assert (row.method, row.ai_log_id, row.value_json) == ("matched", None, 5000)
    assert row.ai_value_json["quote"] == MATCH_LIMIT_SENTENCE and row.quote == MATCH_LIMIT_SENTENCE
    assert count(off, AiLog) == 0


def test_a_matched_figure_corrected_by_the_person_keeps_what_was_matched(off: TestClient) -> None:
    (candidate,) = propose(off, LIMIT, page=MATCH_PAGE).json()["proposals"]
    r = off.post("/api/rule-figures", json=matched_body(candidate, value="4800"))
    assert r.status_code == 201, r.text
    factory = off.app.state.session_factory  # type: ignore[attr-defined]
    with factory.begin() as s:
        row = s.scalars(select(RuleCapture).where(RuleCapture.key == LIMIT)).one()
    assert (row.value_json, row.ai_value_json["value"]) == (4800, "5000")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"quote": None}, "quote"),  # a matched figure needs its sentence
        ({"ai_log_id": 1}, "ai_proposal"),  # the matcher is not an AI call: it has no log entry
        ({"value": "500000"}, "value"),  # validated exactly as a typed one
        ({"ai_proposal": "same", "ai_log_id": 1}, "matched_proposal"),  # AI or matched, not both
    ],
)
def test_matched_provenance_that_does_not_hold_up_is_refused(
    off: TestClient, changes: dict[str, Any], field: str
) -> None:
    (candidate,) = propose(off, LIMIT, page=MATCH_PAGE).json()["proposals"]
    changes = {k: candidate["proposal"] if v == "same" else v for k, v in changes.items()}
    r = off.post("/api/rule-figures", json=matched_body(candidate, **changes))
    assert r.status_code == 422, r.text
    assert field in {e["field"] for e in r.json()["errors"]}
    assert count(off, RuleCapture) == 1


# --- capped, failing: the matcher reads the page instead --------------------------------------


def test_the_monthly_cap_falls_back_to_the_matcher_and_says_why(
    on: TestClient, model: FakeProvider
) -> None:
    log(on, "rule_capture", 5_000_000)  # this month's whole cap, spent on reading pages
    r = propose(on, LIMIT, page=MATCH_PAGE)
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "matched" and "monthly" in r.json()["fallback"]
    assert r.json()["proposals"][0]["status"] == "candidate"
    assert model.requests == []
    status = on.get("/api/ai/status", params={"task": "rule_capture"}).json()
    assert (status["enabled"], status["reason"], status["answering"]) == (
        False,
        "monthly_cap",
        True,
    )
    typed = on.post("/api/rule-figures", json={"financial_year": FY, "key": LIMIT, "value": "5000"})
    assert typed.status_code == 201


def test_ask_questions_used_up_do_not_stop_reading_a_page(
    tmp_path: Path, model: FakeProvider
) -> None:
    app = make_app(
        tmp_path, AiSettings(provider="anthropic", api_key="synthetic-test-key", daily_questions=2)
    )
    providers.register(model)
    with owner_client(app) as client:  # type: ignore[arg-type]
        log(client, "ask", 0)
        log(client, "ask", 0)
        ask = client.get("/api/ai/status").json()
        assert (ask["enabled"], ask["reason"]) == (False, "daily_limit")
        capture = client.get("/api/ai/status", params={"task": "rule_capture"}).json()
        assert (capture["enabled"], capture["reason"]) == (True, None)
        r = propose(client, LIMIT)
        assert r.status_code == 200 and r.json()["method"] == "ai"


def test_reading_pages_does_not_use_up_the_daily_questions(
    tmp_path: Path, model: FakeProvider
) -> None:
    app = make_app(
        tmp_path, AiSettings(provider="anthropic", api_key="synthetic-test-key", daily_questions=1)
    )
    providers.register(model)
    with owner_client(app) as client:  # type: ignore[arg-type]
        assert propose(client, LIMIT).status_code == 200
        assert propose(client, LIMIT).status_code == 200
        assert client.get("/api/ai/status").json()["enabled"] is True  # Ask still has its question


def test_a_provider_failure_falls_back_to_the_matcher_with_a_calm_message(
    on: TestClient, model: FakeProvider
) -> None:
    model.say(RuntimeError("boom sk-test-not-a-real-key"))
    r = propose(on, LIMIT, page=MATCH_PAGE)
    assert r.status_code == 200
    assert "sk-test" not in r.text and "boom" not in r.text
    assert r.json()["method"] == "matched" and "matching words" in r.json()["fallback"]
    assert count(on, RuleCapture) == 1  # a proposal stores nothing
