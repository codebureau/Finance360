"""``core.notices``: what moves the picture, and the words (ADR-0033). Pure: no database."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from finance360.core.money import Money
from finance360.core.notices import (
    NOTICE_MIN_NET_CHANGE,
    Picture,
    QuestionState,
    diff,
    direction,
    headline,
)
from finance360.core.questions import (
    COULD_NOT_CHECK,
    NOT_READY,
    PARTLY_READY,
    READY,
    Readiness,
)

STATES: list[Readiness] = [READY, PARTLY_READY, NOT_READY, COULD_NOT_CHECK]


def aud(minor: int) -> Money:
    return Money(minor, "AUD")


def picture(net: int, state: Readiness = READY) -> Picture:
    return Picture(aud(net), (QuestionState("own", "What do we own and owe?", "/position", state),))


def test_golden_a_net_change_reads_in_words_with_its_direction() -> None:
    effect = diff(picture(10_000_000), picture(10_120_000))
    assert effect is not None
    assert effect.text == "Net position moved by $1,200.00 (up)."
    assert effect.delta == aud(120_000)
    assert effect.link_path == "/position"
    down = diff(picture(5_000_000), picture(4_999_000))
    assert down is not None and down.text == "Net position moved by $10.00 (down)."
    assert down.delta == aud(-1_000)


def test_golden_a_readiness_change_alone_is_named_by_its_wording() -> None:
    effect = diff(picture(500, PARTLY_READY), picture(500, READY))
    assert effect is not None
    assert effect.text == "What do we own and owe? went from Partly ready to Ready."
    assert effect.delta is None
    assert effect.link_path == "/position"


def test_both_lines_when_the_net_and_a_question_move() -> None:
    effect = diff(picture(0, PARTLY_READY), picture(250_000, READY))
    assert effect is not None
    assert effect.text == (
        "Net position moved by $2,500.00 (up). "
        "What do we own and owe? went from Partly ready to Ready."
    )


def test_the_tolerance_edge_is_one_whole_unit() -> None:
    assert NOTICE_MIN_NET_CHANGE == 100
    assert diff(picture(1_000), picture(1_099)) is None
    assert diff(picture(1_000), picture(901)) is None
    up = diff(picture(1_000), picture(1_100))
    down = diff(picture(1_000), picture(900))
    assert up is not None and up.delta == aud(100)
    assert down is not None and down.delta == aud(-100)


def test_no_change_makes_no_notice() -> None:
    assert diff(picture(1_000, READY), picture(1_000, READY)) is None


def test_a_check_that_could_not_run_is_not_a_change_the_write_made() -> None:
    assert diff(picture(1_000, READY), picture(1_000, COULD_NOT_CHECK)) is None
    assert diff(picture(1_000, COULD_NOT_CHECK), picture(1_000, READY)) is None


def test_the_headline_uses_the_kind_and_the_account_name_only() -> None:
    assert headline("valuation.added", "Sample Savings") == (
        "You added a Valuation for Sample Savings."
    )
    assert headline("valuation.corrected", "Sample Savings") == (
        "You corrected a Valuation for Sample Savings."
    )


def test_direction_comes_from_the_sign() -> None:
    assert (direction(aud(5)), direction(aud(-5)), direction(aud(0)), direction(None)) == (
        "up",
        "down",
        None,
        None,
    )


nets = st.integers(min_value=-(10**12), max_value=10**12)
states = st.sampled_from(STATES)


@given(before=nets, after=nets, state=states)
def test_property_the_delta_is_exact_and_the_tolerance_only_decides_whether_to_speak(
    before: int, after: int, state: Readiness
) -> None:
    effect = diff(picture(before, state), picture(after, state))
    if abs(after - before) < NOTICE_MIN_NET_CHANGE:
        assert effect is None
    else:
        assert effect is not None
        assert effect.delta == aud(after - before)  # never rounded, never the tolerance


@given(net=nets, wiggle=st.integers(-(NOTICE_MIN_NET_CHANGE - 1), NOTICE_MIN_NET_CHANGE - 1))
def test_property_a_readiness_change_alone_makes_a_notice_with_no_figure(
    net: int, wiggle: int
) -> None:
    effect = diff(picture(net, PARTLY_READY), picture(net + wiggle, READY))
    assert effect is not None
    assert effect.delta is None
    assert "Net position" not in effect.text
    assert "went from Partly ready to Ready" in effect.text
