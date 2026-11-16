"""The page reader (ADR-0023 section 6): finds Rule figures on a pasted page by matching words, with
no model. Its proposals go through the same deterministic checks as a model's (ADR-0022
section 5), so this file proves both halves: what the reader finds on the invented fixture page,
and that a proposal it could never make honestly (a quote that is not on the page, a number that
is not in the quote, an absurd value) is still refused by the checks, whichever path produced it.
Every page and figure here is invented.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.rulecapture import checks, reader, service
from finance360.core.ai_settings import AiSettings
from finance360.rulepacks.au import PACK

REPO = Path(__file__).resolve().parents[2]
PAGE = (REPO / "fixtures" / "sample_household" / "rule_page_sample.txt").read_text("utf-8")
TODAY = date(2026, 11, 6)
FY = 2027
MEDICARE = [s.key for s in PACK.specs if s.key.startswith(("tax.medicare.", "tax.mls."))]


def read(
    factory: sessionmaker[Session], keys: list[str], page: str = PAGE
) -> dict[str, service.Result]:
    with factory.begin() as s:
        proposed = service.propose(s, AiSettings(), "au", FY, keys, page, today=TODAY)
    assert proposed.method == "matched" and proposed.fallback is None
    return {r.key: r for r in proposed.results}


def test_the_fixture_page_gives_a_mix_of_candidates_and_failures(
    session_factory: sessionmaker[Session],
) -> None:
    by_key = read(session_factory, MEDICARE)
    assert len(by_key) == 16
    candidates = {k: r for k, r in by_key.items() if r.status == "candidate"}
    assert {k: r.value for k, r in candidates.items()} == {
        "tax.medicare.levy_rate": "2%",
        "tax.medicare.low_income.family_lower": "48000.00",
        "tax.medicare.low_income.family_per_child": "6500.00",
        "tax.medicare.low_income.family_reduction_rate": "8%",
    }
    for result in candidates.values():
        # No model, so no confidence and no log entry; the sentence is on the page.
        assert result.confidence is None and result.ai_log_id is None
        assert reader.NOTE in result.note
        assert result.quote is not None and result.quote in PAGE

    # A provisional sentence, a large change, and a slipped digit refused by the range check.
    assert by_key["tax.medicare.low_income.family_reduction_rate"].provisional_note
    assert by_key["tax.medicare.low_income.family_per_child"].jump is not None
    assert by_key["tax.medicare.levy_rate"].jump is None
    slipped = by_key["tax.medicare.low_income.single_lower"]
    assert slipped.status == "not_found" and slipped.value is None
    assert slipped.reason is not None and "outside the plausible range" in slipped.reason

    # The rest are simply not on the page.
    others = [r for k, r in by_key.items() if k not in candidates and r is not slipped]
    assert len(others) == 11 and all(r.reason is None for r in others)


def test_the_car_page_limit_is_read_as_a_whole_number(
    session_factory: sessionmaker[Session],
) -> None:
    page = "Car expenses\nThe car expenses limit is 5,000 kilometres for each car in the year.\n"
    (result,) = read(session_factory, ["tax.car.max_km"], page).values()
    assert (result.status, result.value) == ("candidate", "5000")


def test_a_sentence_answers_one_figure_only(session_factory: sessionmaker[Session]) -> None:
    page = "The Medicare levy rate for 2026-27 is 2 per cent of taxable income.\n"
    by_key = read(session_factory, ["tax.medicare.levy_rate", "tax.mls.rate_tier1"], page)
    assert by_key["tax.medicare.levy_rate"].status == "candidate"
    assert by_key["tax.mls.rate_tier1"].status == "not_found"


def test_the_reader_proposes_in_a_models_shape_without_a_confidence() -> None:
    specs = [s for s in PACK.specs if s.key == "tax.medicare.levy_rate"]
    (proposal,) = reader.read(specs, FY, PAGE)
    assert set(proposal) == {"key", "fy", "value", "quote", "provisional", "note"}
    assert (proposal["key"], proposal["fy"], proposal["value"]) == (
        "tax.medicare.levy_rate",
        FY,
        "2",
    )
    assert reader.read(specs, FY, "Nothing relevant on this page.") == []
    assert reader.read([], FY, PAGE) == []


# --- the checks hold whichever path made the proposal (ADR-0022 section 5) -----------------------


@pytest.fixture
def forged(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Make the reader propose something an honest reader never would."""

    def force(**changes: object) -> None:
        def fake(specs: object, fy: int, text: str) -> list[dict[str, object]]:
            honest = {
                "key": "tax.medicare.levy_rate",
                "fy": fy,
                "value": "2",
                "quote": "The Medicare levy rate for 2026-27 is 2 per cent of taxable income.",
                "provisional": False,
                "note": reader.NOTE,
            }
            return [{**honest, **changes}]

        monkeypatch.setattr(reader, "read", fake)

    return force


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        # The invariant: a quote must appear in the pasted text.
        (
            {"quote": "The Medicare levy rate is 2 per cent, says an invented source."},
            "does not appear in the pasted text",
        ),
        # The number must appear in the quote.
        ({"value": "3"}, "does not appear in the quoted sentence"),
        # The value must be in the plausible range.
        (
            {
                "value": "40",
                "quote": "The Medicare levy rate for 2026-27 is 2 per cent of taxable income.",
            },
            None,
        ),
        # And for the right year.
        ({"fy": 2026}, "FY 2025-26"),
    ],
    ids=["quote not on the page", "number not in the quote", "out of range", "wrong year"],
)
def test_a_forged_matcher_proposal_is_refused_by_the_same_checks_as_a_models(
    session_factory: sessionmaker[Session],
    forged,  # type: ignore[no-untyped-def]
    changes: dict[str, object],
    reason: str | None,
) -> None:
    forged(**changes)
    by_key = read(session_factory, ["tax.medicare.levy_rate"])
    result = by_key["tax.medicare.levy_rate"]
    assert result.status == "not_found" and result.value is None and result.proposal is None
    assert result.reason is not None
    if reason is not None:
        assert reason in result.reason


# --- the reader never invents a sentence ----------------------------------------------------------


@given(st.text(max_size=600))
def test_every_quote_the_reader_makes_is_on_the_page_whatever_the_page(page: str) -> None:
    specs = list(PACK.specs)
    for proposal in reader.read(specs, FY, page):
        assert checks.quote_in_text(str(proposal["quote"]), page)
        assert proposal["key"] in {s.key for s in specs}
