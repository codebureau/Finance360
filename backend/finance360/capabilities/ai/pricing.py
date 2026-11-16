"""Model prices and the cost of a call, in integer micro-dollars (ADR-0021 section 4). Pure.

Prices are US micro-dollars per million tokens (so ``$2 / MTok`` is ``2_000_000``). Checked against
https://platform.claude.com/docs/en/about-claude/pricing on 2026-11-06 (base input tokens, 5-minute
cache writes, cache hits, output tokens). Haiku 5.5 is priced for prompts up to 100,000 tokens; the
Guide size test keeps Ask's prompt a small fraction of that. Update this table when a model changes.

A model not listed is priced at the dearest known entry and the log marks the cost estimated.
"""

from __future__ import annotations

from dataclasses import dataclass

PER = 1_000_000


@dataclass(frozen=True)
class Price:
    """Micro-dollars per million tokens."""

    input: int
    output: int
    cache_read: int
    cache_write: int


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(
        input=100_000, output=500_000, cache_read=10_000, cache_write=125_000
    ),
    "claude-sonnet-5-5": Price(
        input=2_000_000, output=10_000_000, cache_read=100_000, cache_write=2_500_000
    ),
    "claude-opus-5-5": Price(
        input=4_000_000, output=20_000_000, cache_read=200_000, cache_write=5_000_000
    ),
}


def _dearest() -> Price:
    return max(PRICES.values(), key=lambda p: p.output)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def cost_micros(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[int, bool]:
    """``(cost in micro-dollars, estimated)``. Each part rounds up, so a call never costs zero."""
    price = PRICES.get(model)
    estimated = price is None
    price = price or _dearest()
    total = sum(
        _ceil_div(tokens * rate, PER)
        for tokens, rate in (
            (input_tokens, price.input),
            (output_tokens, price.output),
            (cache_read_tokens, price.cache_read),
            (cache_write_tokens, price.cache_write),
        )
    )
    return total, estimated
