"""AI settings (ADR-0021 section 4): off by default, validated loudly, the key kept secret, and the
price table. Every key here is obviously synthetic."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.ai import pricing
from finance360.config import Settings
from finance360.core.ai_settings import AiSettings, micros_to_usd, usd_to_micros
from finance360.main import create_app
from finance360.plugins.ai_providers.anthropic import AnthropicProvider

KEY = "test-key-not-real-0000"


def test_ai_is_off_by_default_with_the_documented_defaults() -> None:
    ai = Settings.from_env({}).ai
    assert ai.provider is None
    assert ai.model == "claude-sonnet-5-5"
    assert ai.model_ask == "claude-haiku-5-5"
    assert ai.model_for("ask") == "claude-haiku-5-5" and ai.model_for("anything") == ai.model
    assert ai.model_rule_capture is None and ai.model_for("rule_capture") == "claude-sonnet-5-5"
    assert (ai.monthly_cap_usd_micros, ai.daily_questions, ai.log_days) == (5_000_000, 50, 365)


def test_empty_values_mean_not_set() -> None:
    ai = AiSettings.from_env(
        {
            "FINANCE360_AI_PROVIDER": " ",
            "FINANCE360_AI_MODEL": "",
            "FINANCE360_AI_MODEL_ASK": "",
            "FINANCE360_AI_MODEL_RULE_CAPTURE": "",
            "FINANCE360_AI_MONTHLY_CAP_USD": "",
            "FINANCE360_AI_DAILY_QUESTIONS": "",
            "FINANCE360_AI_API_KEY_FILE": "",
        }
    )
    assert ai == AiSettings()


def test_every_variable_is_read() -> None:
    ai = AiSettings.from_env(
        {
            "FINANCE360_AI_PROVIDER": "Anthropic",
            "FINANCE360_AI_API_KEY": KEY,
            "FINANCE360_AI_MODEL": "claude-opus-5-5",
            "FINANCE360_AI_MODEL_ASK": "claude-sonnet-5-5",
            "FINANCE360_AI_MODEL_RULE_CAPTURE": "claude-opus-5-5",
            "FINANCE360_AI_MONTHLY_CAP_USD": "12.50",
            "FINANCE360_AI_DAILY_QUESTIONS": "7",
            "FINANCE360_AI_LOG_DAYS": "30",
        }
    )
    assert (ai.provider, ai.model, ai.model_ask) == (
        "anthropic",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
    )
    assert ai.model_for("rule_capture") == "claude-opus-5-5"
    assert (ai.monthly_cap_usd_micros, ai.daily_questions, ai.log_days) == (12_500_000, 7, 30)


def test_the_anthropic_provider_needs_a_key_and_a_file_is_preferred(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FINANCE360_AI_API_KEY"):
        AiSettings.from_env({"FINANCE360_AI_PROVIDER": "anthropic"})
    key_file = tmp_path / "key"
    key_file.write_text(f"{KEY}\n", encoding="utf-8")
    both = {
        "FINANCE360_AI_PROVIDER": "anthropic",
        "FINANCE360_AI_API_KEY": "from-the-environment",
        "FINANCE360_AI_API_KEY_FILE": str(key_file),
    }
    assert AiSettings.from_env(both).api_key == KEY
    with pytest.raises(FileNotFoundError):
        AiSettings.from_env({**both, "FINANCE360_AI_API_KEY_FILE": str(tmp_path / "missing")})


def test_the_key_is_in_no_repr(tmp_path: Path) -> None:
    ai = AiSettings.from_env({"FINANCE360_AI_PROVIDER": "anthropic", "FINANCE360_AI_API_KEY": KEY})
    assert KEY not in repr(ai) and KEY not in repr(Settings(tmp_path, 1, "x", None, ai=ai))
    provider = AnthropicProvider(KEY)
    assert KEY not in repr(provider) and KEY not in str(provider)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FINANCE360_AI_MONTHLY_CAP_USD", "abc"),
        ("FINANCE360_AI_MONTHLY_CAP_USD", "-1"),
        ("FINANCE360_AI_MONTHLY_CAP_USD", "NaN"),
        ("FINANCE360_AI_MONTHLY_CAP_USD", "Infinity"),
        ("FINANCE360_AI_MONTHLY_CAP_USD", "1.0000001"),
        ("FINANCE360_AI_DAILY_QUESTIONS", "0"),
        ("FINANCE360_AI_DAILY_QUESTIONS", "many"),
        ("FINANCE360_AI_LOG_DAYS", "0"),
        ("FINANCE360_AI_LOG_DAYS", "99999"),
    ],
)
def test_a_wrong_value_fails_loudly_naming_the_variable(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        AiSettings.from_env({name: value})


def test_an_unknown_provider_fails_at_startup_and_unset_starts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FINANCE360_AI_PROVIDER: unknown provider 'ollama'"):
        create_app(Settings(tmp_path, 1, "x", None, ai=AiSettings(provider="ollama")))
    # The offline `sample` provider is gone (ADR-0023): Search only is the real default now.
    with pytest.raises(ValueError, match="FINANCE360_AI_PROVIDER: unknown provider 'sample'"):
        create_app(Settings(tmp_path, 1, "x", None, ai=AiSettings(provider="sample")))
    create_app(Settings(tmp_path, 1, "x", None))


@given(st.decimals(min_value=0, max_value=10_000, places=6))
def test_dollars_become_integer_micros_exactly(amount: Decimal) -> None:
    micros = usd_to_micros(f"{amount:f}")
    assert micros == int(amount * 1_000_000)


@given(st.decimals(min_value=0, max_value=10_000, places=2))
def test_cents_round_trip_through_integer_micros_exactly(amount: Decimal) -> None:
    assert micros_to_usd(usd_to_micros(f"{amount:.2f}")) == f"{amount:.2f}"


# --- the price table ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "usage", "micros"),
    [
        # input, output, cache read, cache write tokens
        ("claude-haiku-5-5", (1_000_000, 0, 0, 0), 100_000),  # $0.10 / MTok in
        ("claude-haiku-5-5", (0, 1_000_000, 0, 0), 500_000),  # $0.50 / MTok out
        ("claude-haiku-5-5", (0, 0, 1_000_000, 0), 10_000),  # $0.01 / MTok cache hit
        ("claude-haiku-5-5", (0, 0, 0, 1_000_000), 125_000),  # $0.125 / MTok cache write
        ("claude-sonnet-5-5", (1_000_000, 1_000_000, 1_000_000, 1_000_000), 14_600_000),
        ("claude-opus-5-5", (1_000_000, 1_000_000, 1_000_000, 1_000_000), 29_200_000),
    ],
)
def test_prices_match_the_pricing_page_checked_on_2026_11_06(
    model: str, usage: tuple[int, int, int, int], micros: int
) -> None:
    assert pricing.cost_micros(
        model,
        input_tokens=usage[0],
        output_tokens=usage[1],
        cache_read_tokens=usage[2],
        cache_write_tokens=usage[3],
    ) == (micros, False)


def test_each_part_rounds_up_so_a_real_call_never_costs_nothing() -> None:
    assert pricing.cost_micros("claude-haiku-5-5", input_tokens=1, output_tokens=1) == (2, False)
    assert pricing.cost_micros("claude-haiku-5-5", input_tokens=0, output_tokens=0) == (0, False)


def test_an_unknown_model_costs_the_dearest_known_price_and_is_estimated() -> None:
    assert pricing.cost_micros("claude-future-9", input_tokens=1_000_000, output_tokens=0) == (
        4_000_000,
        True,
    )
