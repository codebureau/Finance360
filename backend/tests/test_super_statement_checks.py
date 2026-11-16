"""The Super statement checks (ADR-0025 section 3): pure functions over minor units.

Golden cases with known answers, and Hypothesis properties: a generated consistent statement always
passes; moving any one part by one cent makes the matching check fail.
"""

from __future__ import annotations

from collections.abc import Mapping

from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.position.super_statements import (
    OUTFLOW_PARTS,
    CheckResult,
    evaluate_checks,
    options_tolerance,
    outflow_parts_total,
    roll_difference,
)

CCY = "AUD"
MONEY = st.integers(min_value=0, max_value=10**12)
SIGNED = st.integers(min_value=-(10**11), max_value=10**12)


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name: r for r in results}


def _roll(opening: int, contributions: int, returns: int, withdrawals: int, closing: int) -> dict:
    return {
        "opening": opening,
        "contributions_in": contributions,
        "investment_returns": returns,
        "withdrawals": withdrawals,
        "closing": closing,
    }


def test_the_roll_golden_case() -> None:
    amounts = _roll(18997916, 1740000, 752834, 340750, 21150000)
    assert roll_difference(18997916, 1740000, 752834, 340750, 21150000) == 0
    assert _by_name(evaluate_checks(amounts, [], CCY))["roll"].status == "ok"


def test_a_roll_one_cent_out_warns_and_says_by_how_much() -> None:
    amounts = _roll(18997916, 1740000, 752834, 340750, 21150001)
    roll = _by_name(evaluate_checks(amounts, [], CCY))["roll"]
    assert roll.status == "warn"
    assert "AUD 211500.00" in roll.message and "AUD 211500.01" in roll.message
    assert "-0.01" in roll.message


def test_outflows_golden_case_the_rebate_subtracts_and_inside_fees_are_excluded() -> None:
    amounts = {
        **_roll(0, 0, 0, 340750, 0),
        "contributions_tax": 216000,
        "fees_before_rebate": 103000,
        "rebate": 15450,
        "fees_inside_investment": 64000,  # stated, but not part of withdrawals
        "division_293_released": 0,
        "insurance_premiums": 37200,
    }
    assert outflow_parts_total(amounts) == 340750
    assert _by_name(evaluate_checks(amounts, [], CCY))["outflows"].status == "ok"
    wrong = {**amounts, "withdrawals": 340751}
    assert _by_name(evaluate_checks(wrong, [], CCY))["outflows"].status == "warn"


def test_no_breakdown_means_the_outflows_check_does_not_apply() -> None:
    amounts = {**_roll(0, 0, 0, 500, 0), "fees_inside_investment": 640}
    assert outflow_parts_total(amounts) is None  # fees inside alone are not a part
    assert _by_name(evaluate_checks(amounts, [], CCY))["outflows"].status == "not_applicable"


def test_a_stated_zero_is_a_part_but_not_stated_is_not() -> None:
    assert outflow_parts_total({"division_293_released": 0}) == 0
    assert outflow_parts_total({"division_293_released": None}) is None


def test_options_golden_case_one_cent_per_line_is_allowed() -> None:
    amounts = _roll(0, 0, 0, 0, 21150000)
    ok = _by_name(evaluate_checks(amounts, [6825000, 9000000, 5325001], CCY))["options"]
    assert ok.status == "ok" and "rounding" in ok.message
    three_out = _by_name(evaluate_checks(amounts, [6825000, 9000000, 5325003], CCY))["options"]
    assert three_out.status == "ok"  # 3 cents over, 3 lines
    four_out = _by_name(evaluate_checks(amounts, [6825000, 9000000, 5325004], CCY))["options"]
    assert four_out.status == "warn"
    short = _by_name(evaluate_checks(amounts, [6825000, 9000000, 5324996], CCY))["options"]
    assert short.status == "warn"


def test_no_option_lines_means_the_options_check_does_not_apply() -> None:
    assert _by_name(evaluate_checks(_roll(0, 0, 0, 0, 5), [], CCY))["options"].status == (
        "not_applicable"
    )


@given(
    opening=MONEY,
    contributions=MONEY,
    returns=SIGNED,
    withdrawals=MONEY,
    nudge=st.sampled_from([-1, 1]),
    which=st.sampled_from(
        ["opening", "contributions_in", "investment_returns", "withdrawals", "closing"]
    ),
)
def test_a_consistent_roll_passes_and_one_cent_on_any_part_fails(
    opening: int, contributions: int, returns: int, withdrawals: int, nudge: int, which: str
) -> None:
    closing = opening + contributions + returns - withdrawals
    amounts: Mapping[str, int | None] = _roll(opening, contributions, returns, withdrawals, closing)
    assert _by_name(evaluate_checks(amounts, [], CCY))["roll"].status == "ok"
    moved = {**amounts, which: (amounts[which] or 0) + nudge}
    assert _by_name(evaluate_checks(moved, [], CCY))["roll"].status == "warn"


@given(
    parts=st.fixed_dictionaries({name: st.none() | MONEY for name in OUTFLOW_PARTS}),
    nudge=st.sampled_from([-1, 1]),
)
def test_a_consistent_breakdown_passes_and_one_cent_on_withdrawals_fails(
    parts: dict[str, int | None], nudge: int
) -> None:
    total = outflow_parts_total(parts)
    if total is None:
        assert _by_name(evaluate_checks({**_roll(0, 0, 0, 0, 0), **parts}, [], CCY))[
            "outflows"
        ].status == ("not_applicable")
        return
    amounts = {**_roll(0, 0, 0, total, 0), **parts}
    assert _by_name(evaluate_checks(amounts, [], CCY))["outflows"].status == "ok"
    off = {**amounts, "withdrawals": total + nudge}
    assert _by_name(evaluate_checks(off, [], CCY))["outflows"].status == "warn"


@given(
    values=st.lists(MONEY, min_size=1, max_size=8),
    data=st.data(),
)
def test_option_values_pass_within_one_cent_a_line_and_fail_beyond(
    values: list[int], data: st.DataObject
) -> None:
    tolerance = options_tolerance(len(values))
    gap = data.draw(st.integers(min_value=-tolerance - 3, max_value=tolerance + 3))
    amounts = _roll(0, 0, 0, 0, sum(values) - gap)
    result = _by_name(evaluate_checks(amounts, values, CCY))["options"]
    assert (result.status == "ok") == (abs(gap) <= tolerance)
