"""Identifier normalisation (ADR-0013 section 2): unit and Hypothesis."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.core.instrument import is_isin_shaped, normalise_identifier, tidy_identifier


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("VAS", "vas"),
        ("  vas  ", "vas"),
        ("Sample   Mining\tLimited", "sample mining limited"),
        ("SBME.AX", "sbme.ax"),
        ("STRASSE", "strasse"),
        ("Straße", "strasse"),  # casefold, not just lower
        ("a b", "a b"),  # a no-break space is whitespace too
    ],
)
def test_normalisation_is_case_and_whitespace_insensitive(raw: str, norm: str) -> None:
    assert normalise_identifier(raw) == norm


def test_tidy_keeps_case_for_display() -> None:
    assert tidy_identifier("  Sample   Fund ") == "Sample Fund"


def test_inner_characters_are_not_dropped() -> None:
    assert normalise_identifier("A-B") != normalise_identifier("AB")
    assert normalise_identifier("a b") != normalise_identifier("ab")


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("AU0000SMPL01", True),
        ("au0000smpl01", True),
        ("AU0000SMPL0", False),
        ("AU0000SMPL0X", False),
    ],
)
def test_isin_shape(value: str, ok: bool) -> None:
    assert is_isin_shaped(value) is ok


_text = st.text(max_size=40)
_space = st.sampled_from([" ", "  ", "\t", "\n", " ", "   "])


@given(_text)
def test_normalisation_is_idempotent(value: str) -> None:
    once = normalise_identifier(value)
    assert normalise_identifier(once) == once


@given(_text, _space, _space)
def test_padding_and_case_never_change_the_key(value: str, pad_left: str, pad_right: str) -> None:
    key = normalise_identifier(value)
    assert normalise_identifier(pad_left + value + pad_right) == key


@given(
    st.lists(st.text(alphabet="abcXYZ09.-", min_size=1, max_size=8), min_size=1, max_size=5), _space
)
def test_any_whitespace_between_words_collapses_to_one_space(words: list[str], gap: str) -> None:
    assert normalise_identifier(gap.join(words)) == " ".join(w.casefold() for w in words)


@given(st.text(alphabet="abcXYZ", min_size=1, max_size=10))
def test_case_variants_collide(value: str) -> None:
    assert normalise_identifier(value.upper()) == normalise_identifier(value.lower())
