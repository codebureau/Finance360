"""The AI gateway under concurrency (ADR-0021 section 9): one call at a time per instance, and the
daily limit, which is counted from the log, cannot be beaten by asking at once. Real threads, a
real migrated SQLite database and a slow scripted provider; no network, no key."""

from __future__ import annotations

import threading
import time
from datetime import datetime

from ai_support import FakeProvider, cited, settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.ai import gateway, providers
from finance360.capabilities.ai.errors import Busy, CapReached
from finance360.core.models.ai_log import AiLog

NOW = datetime(2026, 11, 10, 3, 0)


class SlowProvider(FakeProvider):
    def complete(self, request: providers.ProviderRequest) -> providers.ProviderResponse:
        time.sleep(0.3)
        return super().complete(request)


def ask_once(factory: sessionmaker[Session], daily: int, out: list[str]) -> None:
    request = gateway.AiRequest(
        "ask",
        "Ask panel question",
        gateway.GUIDANCE,
        [gateway.Part("conversation", "Where do I begin?", role="user")],
        600,
    )
    with factory.begin() as s:
        try:
            gateway.complete(s, settings("fake", daily_questions=daily), request, now=NOW)
            out.append("answered")
        except (Busy, CapReached) as refused:
            out.append(type(refused).__name__)


def test_simultaneous_questions_never_beat_the_daily_limit_or_run_two_at_once(
    session_factory: sessionmaker[Session],
) -> None:
    fake = SlowProvider(cited("ok"))
    providers.register(fake)
    outcomes: list[str] = []
    threads = [
        threading.Thread(target=ask_once, args=(session_factory, 1, outcomes)) for _ in range(8)
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        providers.unregister("fake")

    assert len(outcomes) == 8
    assert outcomes.count("answered") == 1
    assert set(outcomes) - {"answered"} <= {"Busy", "CapReached"}
    assert len(fake.requests) == 1  # the provider was reached once
    with session_factory.begin() as s:
        # whatever the late arrivals met, no second call ever got through
        assert s.scalar(select(func.count()).where(AiLog.outcome != "refused_cap")) == 1


def test_a_second_question_while_one_is_in_flight_is_told_to_wait_and_logs_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    fake = SlowProvider(cited("ok"))
    providers.register(fake)
    outcomes: list[str] = []
    first = threading.Thread(target=ask_once, args=(session_factory, 50, outcomes))
    try:
        first.start()
        time.sleep(0.1)  # the first call is inside the provider
        ask_once(session_factory, 50, outcomes)
        first.join(timeout=30)
    finally:
        providers.unregister("fake")
    assert outcomes == ["Busy", "answered"]
    with session_factory.begin() as s:
        assert s.scalar(select(func.count()).select_from(AiLog)) == 1
