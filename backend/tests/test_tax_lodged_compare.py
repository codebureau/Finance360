"""``compare_to_lodged`` (ADR-0019 section 8, amended 2026-11-17): pure golden tests.

The estimate is the hand-worked golden of ``test_tax_engine`` for the invented rules (every figure
here is invented): Alex, salary 80,000.00 and 14,000.00 withheld, has taxable income 80,000.00, tax
on it 10,610.00, Medicare levy 2,400.00, no offsets, surcharge or HELP, total tax 13,010.00 and a
net outcome of a 990.00 refund.
"""

from __future__ import annotations

from tax_support import aud, member, tax_input

from finance360.capabilities.tax import compare
from finance360.capabilities.tax.engine import MemberResult, estimate


def alex() -> MemberResult:
    given = tax_input(
        member(1, salary="80000", tax_withheld="14000"),
        member(2, salary="28000", tax_withheld="1000"),
    )
    return estimate(given).members[0]


EXACT = {
    "taxable_income": "80000.00",
    "tax_on_taxable_income": "10610.00",
    "offsets": "0.00",
    "medicare_levy": "2400.00",
    "medicare_levy_surcharge": "0.00",
    "help_repayment": "0.00",
    "total_tax": "13010.00",
    "credits": "14000.00",
    "net_outcome": "990.00",
}


def lodged(items: dict[str, str], *other: tuple[str, str | None]) -> compare.LodgedResult:
    return compare.LodgedResult(
        tuple((k, aud(v)) for k, v in items.items()),
        tuple(compare.OtherLine(n, aud(a) if a else None) for n, a in other),
    )


def test_the_hand_worked_estimate_matches_an_exact_notice_on_every_item() -> None:
    got = compare.compare_to_lodged(alex(), lodged(EXACT))
    assert [(x.key, x.status) for x in got.lines] == [(k, compare.MATCHED) for k in EXACT]
    assert got.differences == () and got.unmatched == ()
    assert all(x.difference == aud("0") for x in got.lines)
    assert len(got.matched) == 9


def test_each_difference_is_the_engine_less_the_lodged_amount() -> None:
    notice = EXACT | {"medicare_levy": "2400.01", "taxable_income": "79500.00"}
    got = compare.compare_to_lodged(alex(), lodged(notice))
    by_key = {x.key: x for x in got.lines}
    assert by_key["medicare_levy"].difference == aud("-0.01")  # engine 2,400.00 < 2,400.01
    assert by_key["taxable_income"].difference == aud("500.00")
    assert {x.key for x in got.differences} == {"medicare_levy", "taxable_income"}
    assert by_key["medicare_levy"].engine == aud("2400.00")
    assert by_key["medicare_levy"].lodged == aud("2400.01")
    untouched = EXACT.keys() - {"medicare_levy", "taxable_income"}
    assert all(by_key[k].status == compare.MATCHED for k in untouched)


def test_net_outcome_within_five_cents_is_matched_within_rounding_and_the_difference_stays() -> (
    None
):
    for cents, status in [("0.05", compare.ROUNDING), ("0.06", compare.DIFFERS)]:
        notice = EXACT | {"net_outcome": f"{990 + int(cents[-2:]) / 100:.2f}"}
        [line] = [
            x
            for x in compare.compare_to_lodged(alex(), lodged(notice)).lines
            if x.key == "net_outcome"
        ]
        assert line.status == status, cents
        assert line.difference == aud(f"-{cents}")
    below = EXACT | {"net_outcome": "989.95"}
    [line] = [
        x for x in compare.compare_to_lodged(alex(), lodged(below)).lines if x.key == "net_outcome"
    ]
    assert (line.status, line.difference) == (compare.ROUNDING, aud("0.05"))


def test_only_net_outcome_gets_the_rounding_allowance() -> None:
    notice = EXACT | {"total_tax": "13010.01", "credits": "14000.01"}
    got = compare.compare_to_lodged(alex(), lodged(notice))
    assert {x.key for x in got.differences} == {"total_tax", "credits"}


def test_a_lodged_item_with_no_engine_value_is_unmatched_by_name_and_never_passed_over() -> None:
    notice = lodged(
        EXACT | {"net_outcome": "990.00"},
        ("Sample processing adjustment", "25.00"),
        ("Name only", None),
    )
    notice = compare.LodgedResult(notice.items + (("no_such_key", aud("1.00")),), notice.other)
    got = compare.compare_to_lodged(alex(), notice)
    assert [(x.key, x.is_other, x.lodged) for x in got.unmatched] == [
        ("no_such_key", False, aud("1.00")),
        ("Sample processing adjustment", True, aud("25.00")),
        ("Name only", True, None),
    ]
    assert all(x.engine is None and x.difference is None for x in got.unmatched)
    assert len(got.matched) == 9 and got.differences == ()


def test_lines_follow_the_estimates_order_whatever_the_order_given() -> None:
    reversed_items = dict(reversed(list(EXACT.items())))
    got = compare.compare_to_lodged(alex(), lodged(reversed_items))
    assert [x.key for x in got.lines] == list(compare.RESULT_KEYS)


def test_only_the_lodged_items_are_compared() -> None:
    got = compare.compare_to_lodged(alex(), lodged({"medicare_levy": "2400.00"}))
    assert [(x.key, x.status) for x in got.lines] == [("medicare_levy", compare.MATCHED)]
