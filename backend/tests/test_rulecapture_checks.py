"""The deterministic checks on a proposed Rule figure (ADR-0022 section 5): the quote must be in the
pasted text and the figure must be in the quote. Pure; every sentence and figure is invented.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.rulecapture.checks import normalise, number_in_quote, quote_in_text
from finance360.core.money import Money
from finance360.core.rate import Rate
from finance360.core.rulepack import Bracket


def aud(minor: int) -> Money:
    return Money(minor, "AUD")


# --- the quote must be in the pasted text ---------------------------------------------------


def test_a_quote_matches_after_whitespace_case_and_typographic_normalisation() -> None:
    page = "Intro.\n\n  The invented   rate is\n95 cents per kilometre – it’s fixed.\nMore."
    assert quote_in_text("the invented rate is 95 cents per kilometre - it's fixed.", page)


@pytest.mark.parametrize(
    "quote",
    [
        "",
        "   ",
        "The invented rate is 96 cents per kilometre.",  # a different number
        "A sentence the page never says.",
    ],
)
def test_a_quote_not_in_the_text_or_empty_is_refused(quote: str) -> None:
    assert not quote_in_text(quote, "The invented rate is 95 cents per kilometre.")


def test_normalise_is_idempotent() -> None:
    once = normalise("  A  “Quoted” \n Line ")
    assert normalise(once) == once == 'a "quoted" line'


# --- the figure must be in the quote --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "quote"),
    [
        (aud(88), "The rate is $0.88 per kilometre."),
        (aud(88), "The rate is 88 cents per kilometre."),
        (aud(88), "The rate is 88c a kilometre."),
        (aud(88), "Rate: 0.880."),
        (aud(123_400), "The cap is $1,234 a year."),
        (aud(123_450), "The cap is $1,234.50 a year."),
        (aud(5_000_000), "Income of $50000 or less."),
        (Rate(165_000), "The offset is 16.5% of the tax."),
        (Rate(20_000), "The levy is 2 per cent of taxable income."),
        (Rate(200_000), "The rate is 20.00 percent."),
        (5000, "A limit of 5,000 kilometres per car."),
        (5000, "A limit of 5000 kilometres per car."),
        (
            (Bracket(aud(0), Rate(0)), Bracket(aud(1_820_000), Rate(160_000))),
            "Nil up to $18,200, then 16c for each $1 over $18,200 (16%).",
        ),
    ],
)
def test_the_number_is_found_in_the_quote(value: object, quote: str) -> None:
    assert number_in_quote(value, quote)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "quote"),
    [
        (aud(88), "The rate is $0.89 per kilometre."),
        (aud(88), "The rate is 8.8 cents per kilometre."),
        (aud(88), "The rate is 880 cents per kilometre."),
        (aud(88), "It is eighty-eight cents."),  # spelled-out numbers are not matched
        (aud(88), "The 2026 rate applies."),
        (
            aud(88),
            "The rate is 88 per kilometre.",
        ),  # 88 without dollars or a cents word is not 0.88
        (aud(123_400), "The cap is $12,340 a year."),
        (Rate(165_000), "The offset is 16.4% of the tax."),
        (5000, "A limit of 50,000 kilometres per car."),
        (5000, "A limit of 500 kilometres per car."),
        (
            (Bracket(aud(0), Rate(0)), Bracket(aud(1_820_000), Rate(160_000))),
            "Nil up to $18,200, then 17c for each $1 over $18,200 (17%).",
        ),
    ],
)
def test_a_number_not_in_the_quote_is_refused(value: object, quote: str) -> None:
    assert not number_in_quote(value, quote)  # type: ignore[arg-type]


@given(st.integers(min_value=0, max_value=10**9))
def test_a_money_figure_is_found_written_as_dollars_and_cents(minor: int) -> None:
    written = f"${minor // 100:,}.{minor % 100:02d}"
    assert number_in_quote(aud(minor), f"The cap is {written} a year.")


@given(st.integers(min_value=0, max_value=10**6), st.integers(min_value=1, max_value=10**6))
def test_a_different_whole_number_is_never_found(shown: int, other: int) -> None:
    if shown == other:
        return
    assert not number_in_quote(other, f"A limit of {shown:,} kilometres.")
