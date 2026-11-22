"""Weights and the weight-sum tolerance rules (ADR-0030 section 3): unit, golden and Hypothesis.

Exact arithmetic, tolerant judgments: a weight is an integer count of parts per million that
round-trips through its percent string with no float, and a Dimension's total is accepted or
rejected at exactly the named tolerance.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.core.exposure import (
    WEIGHT_TOLERANCE_PPM,
    OutsideTolerance,
    effective_weights,
    percent_string,
)
from finance360.core.rate import PPM
from finance360.core.weight import Weight, WeightError

ORDER = ["australian_shares", "international_shares", "property", "cash", "other"]


# --- Weight ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ppm"),
    [("60", 600_000), ("99.5", 995_000), ("0.0001", 1), ("100", 1_000_000), ("12.3456", 123_456)],
)
def test_a_weight_is_parts_per_million_from_a_percent_string(text: str, ppm: int) -> None:
    assert Weight.from_percent(text).ppm == ppm
    assert Weight.from_percent(Decimal(text)).ppm == ppm


@pytest.mark.parametrize("bad", ["0", "-5", "100.0001", "12.34567", "abc", "", "1e2", "5%"])
def test_a_weight_outside_its_range_or_finer_than_4_places_is_rejected_never_rounded(
    bad: str,
) -> None:
    with pytest.raises(WeightError):
        Weight.from_percent(bad)


def test_a_float_is_refused_everywhere() -> None:
    with pytest.raises(TypeError):
        Weight.from_percent(60.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Weight(600_000.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Weight(True)  # type: ignore[arg-type]


@given(st.integers(min_value=1, max_value=PPM))
def test_a_weight_round_trips_through_its_percent_string_exactly(ppm: int) -> None:
    text = Weight(ppm).to_percent()
    assert "e" not in text.lower()
    assert Weight.from_percent(text).ppm == ppm
    assert Weight.from_percent(Decimal(text)).ppm == ppm
    assert Decimal(text) * 10_000 == ppm  # the string is the exact value, not an approximation


@given(st.integers(min_value=-5 * PPM, max_value=5 * PPM))
def test_percent_string_is_exact_for_any_integer_total(ppm: int) -> None:
    assert Decimal(percent_string(ppm)) * 10_000 == ppm


# --- the tolerance rules ---------------------------------------------------------------------


def test_the_tolerance_is_one_percentage_point() -> None:
    assert WEIGHT_TOLERANCE_PPM == 10_000


def test_golden_an_exact_total_resolves_unchanged() -> None:
    got = effective_weights(
        [("australian_shares", 600_000), ("international_shares", 400_000)], ORDER
    )
    assert got.weights == (("australian_shares", 600_000), ("international_shares", 400_000))
    assert (got.not_classified_ppm, got.trimmed) == (0, ())


def test_golden_a_shortfall_within_tolerance_goes_to_not_classified() -> None:
    got = effective_weights(
        [("australian_shares", 600_000), ("international_shares", 395_000)], ORDER
    )
    assert got.not_classified_ppm == 5_000  # 99.5%: 0.5 point short
    assert got.weights == (("australian_shares", 600_000), ("international_shares", 395_000))
    assert got.trimmed == ()


def test_golden_an_excess_within_tolerance_trims_the_largest_weight() -> None:
    got = effective_weights(
        [("australian_shares", 300_000), ("international_shares", 705_000)], ORDER
    )  # 100.5%
    assert got.trimmed == (("international_shares", 5_000),)
    assert got.weights == (("australian_shares", 300_000), ("international_shares", 700_000))
    assert got.not_classified_ppm == 0


def test_golden_a_tie_for_the_largest_goes_to_the_earliest_label_in_seed_order() -> None:
    # Entered in the opposite order to the seed order: the tie still goes to Australian shares.
    got = effective_weights(
        [("international_shares", 505_000), ("australian_shares", 505_000)], ORDER
    )  # 101%
    assert got.trimmed == (("australian_shares", 10_000),)
    assert dict(got.weights) == {"international_shares": 505_000, "australian_shares": 495_000}


def test_golden_no_weights_means_the_whole_dimension_is_not_classified() -> None:
    got = effective_weights([], ORDER)
    assert (got.weights, got.not_classified_ppm, got.trimmed) == ((), PPM, ())


@pytest.mark.parametrize("total", [PPM - WEIGHT_TOLERANCE_PPM, PPM, PPM + WEIGHT_TOLERANCE_PPM])
def test_a_total_exactly_at_the_boundary_is_accepted(total: int) -> None:
    effective_weights([("australian_shares", total)], ORDER)


@pytest.mark.parametrize(
    ("total", "difference"),
    [(PPM - WEIGHT_TOLERANCE_PPM - 1, -10_001), (PPM + WEIGHT_TOLERANCE_PPM + 1, 10_001)],
)
def test_golden_a_total_one_ppm_beyond_the_boundary_is_rejected_with_the_difference(
    total: int, difference: int
) -> None:
    with pytest.raises(OutsideTolerance) as caught:
        effective_weights([("australian_shares", total)], ORDER)
    assert caught.value.difference_ppm == difference


@given(
    st.lists(st.integers(min_value=1, max_value=PPM), min_size=1, max_size=5),
)
def test_acceptance_is_exactly_within_the_tolerance_and_a_result_always_totals_100_percent(
    raw: list[int],
) -> None:
    weights = [(ORDER[i], w) for i, w in enumerate(raw)]
    total = sum(raw)
    if abs(total - PPM) > WEIGHT_TOLERANCE_PPM:
        with pytest.raises(OutsideTolerance):
            effective_weights(weights, ORDER)
        return
    got = effective_weights(weights, ORDER)
    assert sum(w for _, w in got.weights) + got.not_classified_ppm == PPM
    assert all(w > 0 for _, w in got.weights) and got.not_classified_ppm >= 0
    # The trim only ever lowers a weight, and by exactly the excess; a shortfall only adds.
    trimmed = sum(p for _, p in got.trimmed)
    assert trimmed == max(0, total - PPM) and got.not_classified_ppm == max(0, PPM - total)
    assert all(trimmed_label in dict(weights) for trimmed_label, _ in got.trimmed)
