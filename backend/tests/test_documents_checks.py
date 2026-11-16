"""The money parser, the period parser and the sum check (ADR-0024 section 4, 8).

Invariant work: money maths. Hypothesis pins that every way a statement prints an amount parses back
to the same minor units, and that a sum of parts equals its total exactly.
"""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.documents import reading as r
from finance360.capabilities.documents.checks import evaluate, parse_fy, parse_money
from finance360.capabilities.documents.matcher import Hit, Matches, Miss
from finance360.capabilities.documents.profile import load_profile
from finance360.core.money import BASE_CURRENCY, Money, format_amount

AUD = BASE_CURRENCY


def aud(minor: int) -> Money:
    return Money(minor, AUD)


def printed(minor: int, *, dollar: bool, separators: bool, style: str) -> str:
    """One way to print an amount; ``style``: plain, minus, dash_after, nb_hyphen or brackets."""
    whole, cents = divmod(abs(minor), 100)
    digits = f"{whole:,}" if separators else str(whole)
    body = f"{'$' if dollar else ''}{digits}.{cents:02d}"
    if minor >= 0 or style == "plain":
        return body
    if style == "minus":
        return "-" + body
    if style == "dash_after":
        return ("$-" if dollar else "-") + body.removeprefix("$")
    if style == "nb_hyphen":  # the ATO's non-breaking hyphen
        return "‑" + body
    return f"({body})"


# --- the parser ---------------------------------------------------------------------------------


@given(
    minor=st.integers(min_value=-(10**13), max_value=10**13),
    dollar=st.booleans(),
    separators=st.booleans(),
    style=st.sampled_from(["plain", "minus", "dash_after", "nb_hyphen", "brackets"]),
)
def test_every_printed_amount_parses_back_to_the_same_minor_units(
    minor: int, dollar: bool, separators: bool, style: str
) -> None:
    text = printed(minor, dollar=dollar, separators=separators, style=style)
    if minor < 0 and style == "plain":
        return  # plain does not mark a negative: nothing to parse back
    assert parse_money(text) == aud(minor), "a printed amount did not parse back exactly"


@given(minor=st.integers(min_value=-(10**13), max_value=10**13))
def test_the_plain_format_round_trips(minor: int) -> None:
    assert parse_money(format_amount(aud(minor))) == aud(minor)


def test_the_parser_reads_the_usual_prints_exactly() -> None:
    assert parse_money("$1,234.56") == aud(123456)
    assert parse_money("  $0.00 ") == aud(0)
    assert parse_money("1234") == aud(123400)
    assert parse_money("1,234.5") == aud(123450)
    assert parse_money("-$1,000.00") == aud(-100000)
    assert parse_money("$-1,000.00") == aud(-100000)
    assert parse_money("−$5.25") == aud(-525)  # the minus sign
    assert parse_money("($1,000.00)") == aud(-100000)


def test_a_value_that_is_not_an_amount_is_none_never_rounded() -> None:
    for text in (
        "",
        "$",
        "abc",
        "1.234",  # more decimals than cents: refused, not rounded
        "1,23,456.00",  # a misplaced separator
        "12,34",
        "$1,000.00 extra",
        "(1,000.00",  # unbalanced bracket
        "-(1,000.00)",  # two negatives
        "--5.00",
        "1e5",
        "1.5.0",
        "$1,000.00-",
    ):
        assert parse_money(text) is None, f"{text!r} should not parse"


def test_an_amount_beyond_int64_is_none() -> None:
    assert parse_money("9" * 30 + ".00") is None


# --- the period ---------------------------------------------------------------------------------


def test_the_period_is_the_year_of_the_last_date() -> None:
    assert parse_fy("01/07/2025 - 30/06/2026") == 2026
    assert parse_fy("2025-07-01 to 2026-06-30") == 2026
    assert parse_fy("1 July 2025 - 30 June 2026") == 2026
    assert parse_fy("01 Jul 2025 to 31 Dec 2025") == 2026  # a part-year is still its year
    assert parse_fy("30/06/2026") == 2026
    assert parse_fy("01/07/2026") == 2027
    assert parse_fy("2025-26") == 2026
    assert parse_fy("2025-2026") == 2026
    assert parse_fy("2025-27") is None
    assert parse_fy("no year here") is None


@given(day=st.dates(min_value=date(1990, 1, 1), max_value=date(2099, 12, 31)))
def test_the_period_of_a_single_date_matches_the_financial_year(day: date) -> None:
    expected = day.year + 1 if day.month >= 7 else day.year
    assert parse_fy(day.strftime("%d/%m/%Y")) == expected
    assert parse_fy(day.isoformat()) == expected


# --- the sum check ------------------------------------------------------------------------------

PROFILE = load_profile(
    {
        "key": "sample-sum",
        "version": 1,
        "title": "Sum",
        "target": "sample.target",
        "claim": ["Sample"],
        "fields": [
            {
                "key": "total",
                "type": "money",
                "labels": ["Total"],
                "pair": "same_row",
                "required": True,
            },
            {"key": "a", "type": "money", "labels": ["A"], "pair": "same_row"},
            {"key": "b", "type": "money", "labels": ["B"], "pair": "same_row", "negative_ok": True},
            {
                "key": "c",
                "type": "money",
                "labels": ["C"],
                "pair": "same_row",
                "absent_means_zero": True,
            },
        ],
        "sums": [{"total": "total", "plus": ["a", "b", "c"]}],
    }
)


def matched(**texts: str) -> Matches:
    hits = {k: Hit(v, 1, k.upper()) for k, v in texts.items()}
    misses = {f.key: Miss(r.LABEL_NOT_FOUND) for f in PROFILE.fields if f.key not in hits}
    return Matches(hits, misses)


def sum_status(reading: r.Reading) -> str:
    (check,) = [c for c in reading.checks if c.kind == "sum"]
    return check.status


@given(
    first=st.integers(min_value=0, max_value=10**9),
    signed=st.integers(min_value=-(10**9), max_value=10**9),  # `b` may be negative
    last=st.integers(min_value=0, max_value=10**9),
    off_by=st.sampled_from([0, 0, 1, -1, 100]),
)
def test_a_sum_agrees_exactly_when_the_parts_add_to_the_total_to_the_cent(
    first: int, signed: int, last: int, off_by: int
) -> None:
    parts = [first, signed, last]
    total = sum(parts) + off_by
    if total < 0:
        return  # a total is not negative
    texts = {
        "total": format_amount(aud(total)),
        "a": format_amount(aud(parts[0])),
        "b": format_amount(aud(parts[1])),
        "c": format_amount(aud(parts[2])),
    }
    reading = evaluate(PROFILE, matched(**texts))
    assert sum_status(reading) == (r.AGREE if off_by == 0 else r.DISAGREE)


def test_a_signed_part_counts_with_its_sign() -> None:
    reading = evaluate(PROFILE, matched(total="$900.00", a="$1,000.00", b="-$100.00", c="$0.00"))
    assert sum_status(reading) == r.AGREE
    assert reading.outcome == r.OK


def test_one_cent_out_is_a_disagreement_with_the_figures_still_filled() -> None:
    reading = evaluate(PROFILE, matched(total="$900.01", a="$1,000.00", b="-$100.00", c="$0.00"))
    assert reading.outcome == r.WARNINGS
    assert sum_status(reading) == r.DISAGREE
    assert {f.key for f in reading.found} == {"total", "a", "b", "c"}  # filled: the owner must see


def test_a_missing_part_skips_the_sum_and_is_not_taken_as_zero() -> None:
    reading = evaluate(PROFILE, matched(total="$1,000.00", b="$0.00"))  # `a` is missing
    assert sum_status(reading) == r.SKIPPED
    (check,) = reading.checks
    assert check.message == "Could not check: a missing."
    assert reading.outcome == r.WARNINGS
    assert [(n.key, n.reason) for n in reading.not_found] == [("a", r.LABEL_NOT_FOUND)]


def test_a_line_the_statement_omits_when_nil_counts_as_zero_and_is_marked() -> None:
    reading = evaluate(PROFILE, matched(total="$1,000.00", a="$1,000.00", b="$0.00"))  # no `c`
    assert sum_status(reading) == r.AGREE
    (c,) = [f for f in reading.found if f.key == "c"]
    assert c.value == aud(0)
    assert c.how == r.ABSENT


def test_a_negative_where_the_field_does_not_allow_one_is_not_found() -> None:
    reading = evaluate(PROFILE, matched(total="$1,000.00", a="-$5.00", b="$0.00", c="$0.00"))
    assert [(n.key, n.reason) for n in reading.not_found] == [("a", r.NEGATIVE)]


def test_an_unparseable_required_figure_needs_repair_and_still_returns_the_rest() -> None:
    reading = evaluate(PROFILE, matched(total="about 1000", a="$1.00", b="$0.00", c="$0.00"))
    assert reading.outcome == r.NEEDS_REPAIR
    assert [f.key for f in reading.found] == ["a", "b", "c"]
    assert [(c.kind, c.status) for c in reading.checks] == [("sum", r.SKIPPED)]
    assert [(n.key, n.reason, n.required) for n in reading.not_found] == [
        ("total", r.NOT_AN_AMOUNT, True)
    ]
