"""The matcher, the profile and the four Reading outcomes on in-memory Documents (ADR-0024).

No PDF is needed: pasted text loads into the same Document shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from finance360.capabilities.documents import loader, matcher
from finance360.capabilities.documents import reading as r
from finance360.capabilities.documents.checks import evaluate
from finance360.capabilities.documents.model import Document, Page, Word, lines_of
from finance360.capabilities.documents.profile import Profile, ProfileError, load_profile


def spec(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": "sample",
        "version": 1,
        "title": "Sample",
        "target": "sample.target",
        "claim": ["Sample statement", "Totals"],
        "sections": [{"key": "tax", "start": "Tax section", "end": "Other section"}],
        "fields": [
            {
                "key": "gross",
                "type": "money",
                "labels": ["Gross", "Gross pay"],
                "pair": "same_row",
                "required": True,
            },
            {
                "key": "tax",
                "type": "money",
                "labels": ["Tax"],
                "pair": "same_row",
                "section": "tax",
                "required": True,
            },
            {"key": "bonus", "type": "money", "labels": ["Bonus"], "pair": "same_row"},
            {"key": "kind", "type": "text", "labels": ["Kind"], "pair": "same_row"},
            {"key": "below", "type": "money", "labels": ["Held"], "pair": "next_line"},
            {"key": "period", "type": "fy", "labels": ["Period"], "pair": "same_row"},
        ],
        "sums": [{"total": "gross", "plus": ["bonus", "below"]}],
    }
    base.update(overrides)
    return base


PROFILE = load_profile(spec())

GOOD = """\
Sample statement
Totals shown below
Period 01/07/2025 - 30/06/2026
Gross $1,500.00
Bonus $1,000.00
Kind Annual leave
Held
$500.00
Tax section
Tax $300.00
Other section
Tax $999.00
"""


def read(text: str, profile: Profile = PROFILE, **kwargs: Any) -> r.Reading:
    document = loader.load_text(text)
    assert matcher.claims(document, profile)
    return evaluate(profile, matcher.match(document, profile), **kwargs)


def values(reading: r.Reading) -> dict[str, object]:
    return {f.key: f.value for f in reading.found}


# --- the four outcomes ----------------------------------------------------------------------


def test_ok_reads_every_figure_exactly_and_the_sum_agrees() -> None:
    reading = read(GOOD)
    assert reading.outcome == r.OK
    assert (reading.profile_key, reading.profile_version) == ("sample", 1)
    assert not reading.not_found
    assert values(reading)["gross"].amount_minor == 150000  # type: ignore[attr-defined]
    assert values(reading)["kind"] == "Annual leave"
    assert values(reading)["period"] == 2026
    assert values(reading)["below"].amount_minor == 50000  # type: ignore[attr-defined]  # next_line
    assert [(c.kind, c.status) for c in reading.checks] == [("sum", r.AGREE)]


def test_a_section_confines_the_search_so_a_repeated_label_is_not_ambiguous() -> None:
    # "Tax" appears again after the section ends; only the one inside counts.
    assert values(read(GOOD))["tax"].amount_minor == 30000  # type: ignore[attr-defined]


def test_unrecognised_when_no_claim_phrase_matches() -> None:
    document = loader.load_text("Something else entirely\nGross $1.00\n")
    assert not matcher.claims(document, PROFILE)


def test_the_claim_needs_every_phrase_compared_after_normalising() -> None:
    assert matcher.claims(loader.load_text("SAMPLE   statement\nTOTALS"), PROFILE)
    assert matcher.claims(loader.load_text("Sample statement, totals!"), PROFILE)
    assert not matcher.claims(loader.load_text("Sample statement only"), PROFILE)


def test_needs_repair_when_a_required_label_is_missing_but_the_rest_is_returned() -> None:
    reading = read(GOOD.replace("Gross $1,500.00\n", ""))
    assert reading.outcome == r.NEEDS_REPAIR
    assert reading.found  # the figures that were found are still there
    assert "gross" not in [f.key for f in reading.found]
    assert [(n.key, n.reason, n.required) for n in reading.not_found] == [
        ("gross", r.LABEL_NOT_FOUND, True)
    ]


def test_needs_repair_when_a_required_label_is_ambiguous() -> None:
    reading = read(GOOD.replace("Bonus $1,000.00", "Gross $5.00"))
    assert reading.outcome == r.NEEDS_REPAIR
    assert ("gross", r.AMBIGUOUS) in [(n.key, n.reason) for n in reading.not_found]


def test_needs_repair_when_a_section_is_missing_for_a_required_figure() -> None:
    reading = read(GOOD.replace("Tax section", "Tax heading"))
    assert reading.outcome == r.NEEDS_REPAIR
    assert ("tax", r.SECTION_NOT_FOUND) in [(n.key, n.reason) for n in reading.not_found]


def test_warnings_when_a_sum_disagrees_with_the_figures_still_filled() -> None:
    reading = read(GOOD.replace("Gross $1,500.00", "Gross $1,500.01"))
    assert reading.outcome == r.WARNINGS
    assert [(c.kind, c.status) for c in reading.checks] == [("sum", r.DISAGREE)]
    assert values(reading)["gross"].amount_minor == 150001  # type: ignore[attr-defined]


def test_warnings_when_an_optional_figure_is_missing_and_it_stays_unfilled() -> None:
    reading = read(GOOD.replace("Kind Annual leave\n", ""))
    assert reading.outcome == r.WARNINGS
    assert "kind" not in values(reading)
    assert [(n.key, n.reason, n.required) for n in reading.not_found] == [
        ("kind", r.LABEL_NOT_FOUND, False)
    ]


def test_an_ambiguous_optional_label_is_not_filled_and_says_why() -> None:
    reading = read(GOOD.replace("Kind Annual leave", "Kind Annual leave\nKind Other"))
    assert reading.outcome == r.WARNINGS
    assert ("kind", r.AMBIGUOUS) in [(n.key, n.reason) for n in reading.not_found]
    assert "kind" not in values(reading)


def test_a_label_with_no_value_beside_it_is_no_value() -> None:
    reading = read(GOOD.replace("Kind Annual leave", "Kind"))
    assert ("kind", r.NO_VALUE) in [(n.key, n.reason) for n in reading.not_found]


def test_a_value_that_is_not_an_amount_is_not_found_never_guessed() -> None:
    reading = read(GOOD.replace("Bonus $1,000.00", "Bonus nil"))
    assert ("bonus", r.NOT_AN_AMOUNT) in [(n.key, n.reason) for n in reading.not_found]
    assert [(c.kind, c.status) for c in reading.checks] == [("sum", r.SKIPPED)]


def test_a_period_for_another_year_is_a_warning_naming_the_period_field() -> None:
    reading = read(GOOD, expected_fy=2025)
    assert reading.outcome == r.WARNINGS
    period = [c for c in reading.checks if c.kind == "period"]
    assert [(c.status, c.fields) for c in period] == [(r.DISAGREE, ("period",))]
    assert read(GOOD, expected_fy=2026).outcome == r.OK


def test_matching_is_exact_on_the_start_of_the_line_not_fuzzy() -> None:
    assert "bonus" in values(read(GOOD.replace("Bonus", "bonus:")))  # case and punctuation
    reading = read(GOOD.replace("Bonus $1,000.00", "Bonuses $1,000.00"))
    assert ("bonus", r.LABEL_NOT_FOUND) in [(n.key, n.reason) for n in reading.not_found]
    reading = read(GOOD.replace("Bonus $1,000.00", "Total bonus $1,000.00"))
    assert ("bonus", r.LABEL_NOT_FOUND) in [(n.key, n.reason) for n in reading.not_found]


def test_a_label_variant_matches_when_the_preferred_one_is_not_there() -> None:
    reading = read(GOOD.replace("Gross $1,500.00", "Gross pay $1,500.00"))
    (gross,) = [f for f in reading.found if f.key == "gross"]
    assert gross.label == "Gross pay"  # the profile's variant, not the document's text


def test_an_amount_split_from_its_dollar_sign_still_reads() -> None:
    reading = read(GOOD.replace("Gross $1,500.00", "Gross $ 1,500.00"))
    assert values(reading)["gross"].amount_minor == 150000  # type: ignore[attr-defined]


# --- lines and pages ------------------------------------------------------------------------


def test_words_on_one_row_share_a_line_within_a_tolerance_that_scales_with_the_text() -> None:
    page = Page(
        1,
        600,
        800,
        (
            Word("Label", 10, 50, 100, 112),
            Word("$5.00", 300, 340, 102, 114),  # a little lower: still the same row
            Word("Next", 10, 40, 130, 142),
        ),
    )
    lines = lines_of(page)
    assert [[w.text for w in line.words] for line in lines] == [["Label", "$5.00"], ["Next"]]


def test_a_value_on_the_next_page_pairs_across_the_break() -> None:
    document = Document(
        (
            Page(1, 600, 800, (Word("Held", 10, 40, 700, 712),)),
            Page(2, 600, 800, (Word("$7.00", 10, 40, 50, 62),)),
        )
    )
    result = matcher.match(document, PROFILE)
    assert result.hits["below"].text == "$7.00"
    assert result.hits["below"].page == 2


# --- the profile ----------------------------------------------------------------------------


def test_a_label_with_a_long_digit_run_is_refused_so_no_value_is_saved_in_a_profile() -> None:
    bad = spec(claim=["Sample statement", "Account 123456"])
    with pytest.raises(ProfileError, match="holds no values"):
        load_profile(bad)
    abn = spec(claim=["Sample", "ABN 12 345 678 901"])
    with pytest.raises(ProfileError, match="holds no values"):
        load_profile(abn)


def test_short_numbers_in_a_label_are_fine() -> None:
    assert load_profile(spec(claim=["Form 2025 26"]))


@pytest.mark.parametrize(
    "broken",
    [
        {"fields": []},
        {"version": 0},
        {"extra": 1},
        {"claim": []},
        {"fields": [{"key": "x", "type": "money", "labels": ["X"], "pair": "below"}]},  # not built
        {"fields": [{"key": "x", "type": "regex", "labels": ["X"], "pair": "same_row"}]},
        {
            "fields": [
                {
                    "key": "x",
                    "type": "money",
                    "labels": ["X"],
                    "pair": "same_row",
                    "pattern": "\\d+",
                }
            ]
        },  # no regular expressions
    ],
)
def test_a_profile_outside_the_closed_vocabulary_is_refused(broken: dict[str, Any]) -> None:
    with pytest.raises(ProfileError):
        load_profile(spec(**broken))


def test_a_profile_that_names_something_it_does_not_have_is_refused() -> None:
    field = {"key": "x", "type": "money", "labels": ["X"], "pair": "same_row"}
    with pytest.raises(ProfileError, match="section"):
        load_profile(spec(fields=[{**field, "section": "nope"}]))
    with pytest.raises(ProfileError, match="not a money field"):
        load_profile(spec(fields=[field], sums=[{"total": "x", "plus": ["y"]}]))
    with pytest.raises(ProfileError, match="repeats"):
        load_profile(spec(fields=[field, field]))


def test_the_shipped_ato_profile_is_valid_and_targets_the_income_statement() -> None:
    from finance360.plugins.documents.ato_income_statement import PROFILE as ATO

    assert (ATO.key, ATO.target) == ("ato-income-statement", "tax.income_statement")
    assert {f.key for f in ATO.fields if f.required} == {"gross_payments", "tax_withheld"}
