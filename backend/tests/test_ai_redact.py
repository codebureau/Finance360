"""Redaction (ADR-0021 section 3): names, identifiers, e-mail addresses, long digit runs and amounts
become tokens; the reply is restored. Pure unit tests, a Hypothesis round-trip property and golden
cases. Every name and number is invented. Redaction is correctness-critical: a missed name leaks.
"""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.ai.redact import (
    ACCOUNT,
    ENTITY,
    MEMBER,
    NUMBER,
    Redactor,
    Vocabulary,
)

VOCAB = Vocabulary(
    (
        (MEMBER, "Zelda Quimbly"),
        (MEMBER, "Zelda"),
        (ACCOUNT, "Wombat Everyday Saver"),
        (ACCOUNT, "Wombat"),
        (ENTITY, "Bandicoot Bank"),
        (NUMBER, "062-000 98765432"),
        (NUMBER, "4417"),
    )
)


def redactor() -> Redactor:
    return Redactor(VOCAB)


def test_a_name_is_replaced_by_a_token_of_its_kind_and_restored() -> None:
    r = redactor()
    out = r.redact("Is the Wombat Everyday Saver at Bandicoot Bank up to date?")
    assert out == "Is the [ACCOUNT_1] at [ENTITY_1] up to date?"
    assert r.restore(out) == "Is the Wombat Everyday Saver at Bandicoot Bank up to date?"


def test_the_longest_name_wins_and_matching_ignores_case() -> None:
    r = redactor()
    assert r.redact("zelda quimbly and ZELDA") == "[MEMBER_1] and [MEMBER_2]"
    assert r.redact("wombat everyday saver") == "[ACCOUNT_1]"


def test_the_same_text_gets_the_same_token_in_one_call_but_a_case_variant_does_not() -> None:
    r = redactor()
    first = r.redact("Zelda asked. Zelda again.")
    assert first == "[MEMBER_1] asked. [MEMBER_1] again."
    assert r.redact("zelda") == "[MEMBER_2]"  # keyed by the exact text, so restore is exact
    assert r.restore("[MEMBER_2] [MEMBER_1]") == "zelda Zelda"


def test_a_name_inside_a_longer_word_is_left_alone_but_a_possessive_is_not() -> None:
    r = redactor()
    assert r.redact("The wombatic plan") == "The wombatic plan"
    assert r.redact("Zelda's income statement") == "[MEMBER_1]'s income statement"


def test_an_identifier_matches_with_any_spacing_or_dashes() -> None:
    for written in ("062-000 98765432", "062000 98765432", "062 000 98765432", "062000-98765432"):
        assert redactor().redact(f"my account {written} is") == "my account [NUMBER_1] is", written


def test_a_short_identifier_is_hidden_as_a_whole_word_only() -> None:
    assert redactor().redact("card 4417 and 44170") == "card [NUMBER_1] and 44170"


def test_any_run_of_six_or_more_digits_is_hidden_even_if_the_vocabulary_has_never_seen_it() -> None:
    r = redactor()
    assert r.redact("ref 123456 and 1234 5678 9012") == "ref [NUMBER_1] and [NUMBER_2]"
    assert r.redact("12345") == "12345"


def test_dates_and_financial_years_are_not_hidden_as_numbers() -> None:
    assert redactor().redact("FY 2026-27 from 2026-11-06") == "FY 2026-27 from 2026-11-06"


def test_an_email_address_is_hidden_and_restored() -> None:
    r = redactor()
    out = r.redact("write to zelda@quokkaville.example now")
    assert out == "write to [EMAIL_1] now"
    assert r.restore(out) == "write to zelda@quokkaville.example now"


def test_amounts_become_a_marker_and_restore_to_nothing() -> None:
    r = redactor()
    text = "I have $1,234.50, AUD 500, 2.5k and 3m, also 40 dollars in it."
    out = r.redact(text)
    assert out == "I have [AMOUNT], [AMOUNT], [AMOUNT] and [AMOUNT], also [AMOUNT] in it."
    assert r.restore("It costs [AMOUNT] today.") == "It costs today."
    assert "$" not in out and "1,234" not in out


def test_numbers_restore_to_nothing_and_an_unknown_token_is_left_as_it_is() -> None:
    r = redactor()
    r.redact("ref 123456")
    assert r.restore("see [NUMBER_1] there") == "see there"
    assert r.restore("who is [MEMBER_9]?") == "who is [MEMBER_9]?"


def test_blank_vocabulary_entries_do_nothing() -> None:
    r = Redactor(Vocabulary(((MEMBER, ""), (MEMBER, "   "))))
    assert r.redact("nothing to hide") == "nothing to hide"


# --- the property: restoring a redaction gives back the original -------------------------------

_LETTERS = st.text(alphabet="abcdefghijABCDEFGHIJ", min_size=3, max_size=9)
_NAMES = st.lists(
    st.one_of(_LETTERS, st.tuples(_LETTERS, _LETTERS).map(" ".join)),
    min_size=1,
    max_size=6,
    unique_by=str.casefold,
)
_FILLER = st.sampled_from(["the", "and", "balance", "please", "show", "for", "me", "what", "is"])
_EMAILS = st.sampled_from(["pat@host.example", "q.r+s@mail.example.org"])
_JOIN = st.sampled_from([" ", ", ", ". ", " - "])


@given(st.data(), _NAMES)
def test_redact_then_restore_returns_the_original_for_text_built_from_names(
    data: st.DataObject, names: list[str]
) -> None:
    kinds = [MEMBER, ACCOUNT, ENTITY]
    vocabulary = Vocabulary(tuple((kinds[i % 3], name) for i, name in enumerate(names)))
    pieces = data.draw(
        st.lists(st.one_of(st.sampled_from(names), _FILLER, _EMAILS), min_size=1, max_size=12)
    )
    text = ""
    for piece in pieces:
        text += piece + data.draw(_JOIN)
    r = Redactor(vocabulary)
    out = r.redact(text)
    assert r.restore(out) == text
    for name in names:  # and no name survives in what would be sent, as a whole word
        assert not re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", out, re.I), (
            name,
            out,
        )
