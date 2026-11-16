"""The AI gateway (ADR-0021): redaction before the provider, the local log, and caps from the log.

Integration tests against a real migrated temporary SQLite database with a scripted provider. The
golden test is the acceptance criterion: the provider never sees a name or identifier the
household knows. Every name and number is invented.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pytest
from ai_support import (
    ACCOUNT,
    EMAIL,
    IDENTIFIER,
    INSTITUTION,
    MEMBER,
    MEMBER_FIRST,
    OTHER_MEMBER,
    VOCABULARY,
    FakeProvider,
    cited,
    household_with_names,
    settings,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.ai import caps, gateway, providers
from finance360.capabilities.ai import log as ai_log
from finance360.capabilities.ai.errors import AiError, AiOff, AiUnavailable, CapReached
from finance360.capabilities.ai.gateway import AiRequest, Part
from finance360.core.models.ai_log import AiLog

NOW = datetime(2026, 11, 10, 3, 0)  # 14:00 on 10 November in Melbourne
SECRET = "sk-test-not-a-real-key"  # synthetic: proves an exception's text never surfaces


@pytest.fixture
def names(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    household_with_names(session_factory)
    return session_factory


def request(question: str = "hello", **extra: str) -> AiRequest:
    parts = [
        Part("instructions", "Answer from the Guide."),
        Part("guide", "THE GUIDE: pages", cache=True),
        Part("conversation", question, role="user"),
    ]
    parts.extend(Part(name, text) for name, text in extra.items())
    return AiRequest("ask", "Ask panel question", gateway.GUIDANCE, parts, 600)


def run(
    factory: sessionmaker[Session],
    provider: FakeProvider,
    req: AiRequest | None = None,
    *,
    now: datetime = NOW,
    **config: object,
) -> gateway.AiResult:
    """Call the gateway as the API does: a refusal or failure is caught inside the transaction (so
    its log row commits) and raised afterwards."""
    providers.register(provider)
    refused: AiError | None = None
    result: gateway.AiResult | None = None
    try:
        with factory.begin() as s:
            try:
                result = gateway.complete(
                    s, settings(provider.key, **config), req or request(), now=now
                )
            except AiError as problem:
                refused = problem
    finally:
        providers.unregister(provider.key)
    if refused is not None:
        raise refused
    assert result is not None
    return result


def rows(factory: sessionmaker[Session]) -> list[AiLog]:
    with factory.begin() as s:
        found = list(s.scalars(select(AiLog).order_by(AiLog.id)))
        s.expunge_all()
        return found


def add_row(
    factory: sessionmaker[Session], *, at: datetime, cost: int = 0, outcome: str = "ok"
) -> None:
    with factory.begin() as s:
        s.add(
            AiLog(
                occurred_at=at,
                task="ask",
                data_class="guidance",
                provider="fake",
                model="m",
                outcome=outcome,
                sent="",
                public_parts="[]",
                received=None,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cost_usd_micros=cost,
                cost_estimated=False,
                why="seed",
            )
        )


# --- the golden test: the provider never sees what the household knows -------------------------


def test_the_provider_never_sees_a_name_identifier_email_digit_run_or_amount(
    names: sessionmaker[Session],
) -> None:
    question = (
        f"Is {ACCOUNT} at {INSTITUTION} right for {MEMBER}? {MEMBER_FIRST} and {OTHER_MEMBER} "
        f"use account {IDENTIFIER}, mail {EMAIL}, reference 5551234567, about $12,500 or 30k."
    )
    fake = FakeProvider(cited("Fine."))
    due = f"- [important] Your Position uses a balance for {ACCOUNT} ({IDENTIFIER})."
    run(names, fake, request(question, due_items=due))

    seen = fake.everything_sent
    for secret in (*VOCABULARY, EMAIL, "5551234567", "98765432", "12,500", "30k"):
        assert secret.casefold() not in seen.casefold(), secret
    assert not re.search(r"\d{6,}", seen)
    assert "[ACCOUNT_1]" in seen and "[MEMBER_1]" in seen and "[AMOUNT]" in seen


def test_the_log_holds_the_redacted_text_sent_and_the_reply_still_tokenised(
    names: sessionmaker[Session],
) -> None:
    fake = FakeProvider(cited(f"{MEMBER} should check [ACCOUNT_1]."))
    result = run(names, fake, request(f"Tell me about {ACCOUNT}"))

    # what the caller gets has the name put back; the stored reply is exactly as received
    (row,) = rows(names)
    assert row.sent == "[conversation:user]\nTell me about [ACCOUNT_1]"
    assert ACCOUNT not in row.sent
    assert row.received == cited(f"{MEMBER} should check [ACCOUNT_1].")
    assert result.text == row.received
    assert result.restore("see [ACCOUNT_1]") == f"see {ACCOUNT}"
    assert (row.task, row.data_class, row.provider, row.outcome) == (
        "ask",
        "guidance",
        "fake",
        "ok",
    )
    assert row.why == "Ask panel question"


def test_public_parts_are_logged_by_name_and_digest_not_text(names: sessionmaker[Session]) -> None:
    run(names, FakeProvider(cited("ok")))
    (row,) = rows(names)
    parts = json.loads(row.public_parts)
    assert [p["name"] for p in parts] == ["instructions", "guide"]
    assert all(re.fullmatch(r"[0-9a-f]{64}", p["sha256"]) for p in parts)
    assert "THE GUIDE" not in row.public_parts and "THE GUIDE" not in row.sent


def test_a_part_the_data_class_does_not_declare_is_refused_before_anything_happens(
    names: sessionmaker[Session],
) -> None:
    fake = FakeProvider(cited("ok"))
    with pytest.raises(gateway.UndeclaredPartError, match="position_balances"):
        run(names, fake, request(position_balances="Cash: 12,000"))
    assert fake.requests == [] and rows(names) == []


def test_the_task_model_is_used_and_haiku_is_the_ask_default(names: sessionmaker[Session]) -> None:
    fake = FakeProvider(cited("ok"))
    run(names, fake)
    assert fake.requests[0].model == "claude-haiku-5-5"
    other = FakeProvider(cited("ok"))
    run(names, other, AiRequest("other", "x", gateway.GUIDANCE, request().parts, 10))
    assert other.requests[0].model == "claude-sonnet-5-5"
    pinned = FakeProvider(cited("ok"))
    run(names, pinned, model_ask="claude-sonnet-5-5")
    assert pinned.requests[0].model == "claude-sonnet-5-5"


def test_the_guide_block_is_marked_for_caching(names: sessionmaker[Session]) -> None:
    fake = FakeProvider(cited("ok"))
    run(names, fake)
    assert [(b.text[:3], b.cache) for b in fake.requests[0].system] == [
        ("Ans", False),
        ("THE", True),
    ]


def test_a_log_row_is_kept_even_when_the_callers_transaction_rolls_back(
    names: sessionmaker[Session],
) -> None:
    providers.register(FakeProvider(RuntimeError("down")))
    try:
        with pytest.raises(AiUnavailable), names.begin() as s:
            try:
                gateway.complete(s, settings("fake"), request(), now=NOW)
            finally:
                s.rollback()
    finally:
        providers.unregister("fake")
    assert [r.outcome for r in rows(names)] == ["error"]


# --- off, failure and what the log keeps -------------------------------------------------------


def test_with_no_provider_the_gateway_is_off_and_writes_nothing(
    names: sessionmaker[Session],
) -> None:
    with names.begin() as s, pytest.raises(AiOff):
        gateway.complete(s, settings(None), request(), now=NOW)
    assert rows(names) == []


def test_a_provider_failure_is_logged_and_shows_calm_wording_never_the_exception(
    names: sessionmaker[Session],
) -> None:
    fake = FakeProvider(RuntimeError(f"boom with {SECRET}"))
    with pytest.raises(AiUnavailable) as caught:
        run(names, fake, request(f"about {ACCOUNT}"))
    assert SECRET not in caught.value.message and "boom" not in caught.value.message
    (row,) = rows(names)
    assert row.outcome == "error" and row.cost_usd_micros == 0
    assert SECRET not in (row.sent + (row.received or "") + row.public_parts)
    assert ACCOUNT not in row.sent  # what was about to be sent is still logged redacted


def test_a_provider_error_keeps_its_own_calm_message(names: sessionmaker[Session]) -> None:
    fake = FakeProvider(providers.ProviderError("The AI service refused the key."))
    with pytest.raises(AiUnavailable, match="refused the key"):
        run(names, fake)


def test_the_cost_is_integer_micros_from_the_price_table_and_cache_reads_are_logged(
    names: sessionmaker[Session],
) -> None:
    fake = FakeProvider(cited("ok"), input_tokens=1000, output_tokens=100, cache_read_tokens=8000)
    run(names, fake)
    (row,) = rows(names)
    # Haiku 5.5: 1000 in at 100000/MTok + 100 out at 500000/MTok + 8000 cached at 10000/MTok
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens) == (1000, 100, 8000)
    assert row.cost_usd_micros == 100 + 50 + 80
    assert row.cost_estimated is False


def test_an_unknown_model_is_priced_at_the_dearest_known_and_marked_estimated(
    names: sessionmaker[Session],
) -> None:
    run(names, FakeProvider(cited("ok"), input_tokens=1_000_000, output_tokens=0), model_ask="x-1")
    (row,) = rows(names)
    assert row.cost_usd_micros == 4_000_000  # Opus 5.5 input: $4 per million tokens
    assert row.cost_estimated is True


def test_rows_older_than_the_retention_are_deleted_when_a_new_one_is_written(
    names: sessionmaker[Session],
) -> None:
    add_row(names, at=NOW - timedelta(days=366))
    add_row(names, at=NOW - timedelta(days=364))
    run(names, FakeProvider(cited("ok")))
    assert [r.occurred_at for r in rows(names)] == [NOW - timedelta(days=364), NOW]
    run(names, FakeProvider(cited("ok")), log_days=30)
    assert [r.occurred_at for r in rows(names)] == [NOW, NOW]


def test_the_log_lists_newest_first(names: sessionmaker[Session]) -> None:
    for i in range(3):
        add_row(names, at=NOW - timedelta(hours=3 - i))
    with names.begin() as s:
        assert [r.id for r in ai_log.recent(s, 2)] == [3, 2]


# --- caps from the log stop a call before the provider -----------------------------------------


def test_the_monthly_cap_stops_the_call_before_the_provider_and_logs_the_refusal(
    names: sessionmaker[Session],
) -> None:
    add_row(names, at=NOW - timedelta(days=2), cost=5_000_000)  # $5.00 already spent this month
    fake = FakeProvider(cited("ok"))
    with pytest.raises(CapReached) as capped:
        run(names, fake)
    assert fake.requests == []  # never reached the provider
    assert capped.value.reason == "monthly_cap"
    assert str(capped.value.resets_on) == "2026-12-01"
    last = rows(names)[-1]
    assert (last.outcome, last.sent, last.cost_usd_micros) == ("refused_cap", "", 0)


def test_spending_just_under_the_cap_still_answers_and_the_cap_is_configurable(
    names: sessionmaker[Session],
) -> None:
    add_row(names, at=NOW - timedelta(days=2), cost=4_999_999)
    run(names, FakeProvider(cited("ok")))  # $4.999999 < $5.00
    fake = FakeProvider(cited("ok"))
    with pytest.raises(CapReached):
        run(names, fake, monthly_cap_usd_micros=1_000_000)
    assert fake.requests == []


def test_last_months_spend_does_not_count_and_the_month_follows_the_household_time_zone(
    names: sessionmaker[Session],
) -> None:
    # 01:00 on 1 November in Melbourne is 14:00 UTC on 31 October. Spend at 12:00 UTC on 31 October
    # was made in October (local), so it is not this month's.
    add_row(names, at=datetime(2026, 10, 31, 12, 0), cost=5_000_000)
    run(names, FakeProvider(cited("ok")), now=datetime(2026, 10, 31, 14, 0))
    # But spend at 15:00 UTC (02:00 on 1 November local) is.
    add_row(names, at=datetime(2026, 10, 31, 15, 0), cost=5_000_000)
    fake = FakeProvider(cited("ok"))
    with pytest.raises(CapReached):
        run(names, fake, now=datetime(2026, 10, 31, 16, 0))
    assert fake.requests == []


def test_the_daily_question_limit_stops_the_call_before_the_provider(
    names: sessionmaker[Session],
) -> None:
    for _ in range(3):
        add_row(names, at=NOW - timedelta(hours=1))
    fake = FakeProvider(cited("ok"))
    with pytest.raises(CapReached) as capped:
        run(names, fake, daily_questions=3)
    assert fake.requests == []
    assert capped.value.reason == "daily_limit" and str(capped.value.resets_on) == "2026-11-11"
    run(names, FakeProvider(cited("ok")), daily_questions=4)  # one more is allowed


def test_yesterdays_questions_and_refusals_do_not_count_toward_today(
    names: sessionmaker[Session],
) -> None:
    for _ in range(3):
        add_row(names, at=NOW - timedelta(days=1))  # 14:00 yesterday, local
    add_row(names, at=NOW - timedelta(hours=1), outcome="refused_cap")
    with names.begin() as s:
        assert caps.questions_today(s, NOW) == 0
    run(names, FakeProvider(cited("ok")), daily_questions=1)


def test_spend_is_summed_from_the_log_with_no_other_counter(names: sessionmaker[Session]) -> None:
    run(
        names,
        FakeProvider(cited("ok"), input_tokens=1_000_000, output_tokens=0),
        model_ask="claude-sonnet-5-5",
    )
    run(
        names,
        FakeProvider(cited("ok"), input_tokens=500_000, output_tokens=0),
        model_ask="claude-sonnet-5-5",
    )
    with names.begin() as s:
        assert caps.month_spend_micros(s, NOW) == 3_000_000  # $2 + $1 at Sonnet 5.5
        stored = s.scalar(select(func.sum(AiLog.cost_usd_micros)))
        assert stored == 3_000_000
