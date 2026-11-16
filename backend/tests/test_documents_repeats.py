"""The profile format's ``repeats`` and ``zero_in_sums``, on a profile that is not the ATO's.

Both are generic: any profile, any money field. The profile here reads an invented "Order" page
whose delivery fee may appear on several rows and whose discount line is omitted when it is nil.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.documents import loader, matcher
from finance360.capabilities.documents import reading as r
from finance360.capabilities.documents.checks import evaluate
from finance360.capabilities.documents.profile import ProfileError, load_profile, to_json
from finance360.core.money import BASE_CURRENCY, Money


def spec(**fee: Any) -> dict[str, Any]:
    return {
        "key": "order",
        "version": 1,
        "title": "Order",
        "target": "order.form",
        "claim": ["Order summary"],
        "fields": [
            {"key": "total", "type": "money", "labels": ["Order total"], "pair": "same_row",
             "required": True},
            {"key": "items", "type": "money", "labels": ["Items"], "pair": "same_row"},
            {"key": "fee", "type": "money", "labels": ["Delivery fee"], "pair": "same_row",
             "repeats": True, "zero_in_sums": True, **fee},
            {"key": "discount", "type": "money", "labels": ["Discount"], "pair": "same_row",
             "zero_in_sums": True, "negative_ok": True},
            {"key": "courier", "type": "text", "labels": ["Courier"], "pair": "same_row",
             "repeats": True},
        ],
        "sums": [{"total": "total", "plus": ["items", "fee", "discount"]}],
    }  # fmt: skip


def read(text: str, **fee: Any) -> r.Reading:
    profile = load_profile(spec(**fee))
    document = loader.load_text(text)
    return evaluate(profile, matcher.match(document, profile))


def aud(minor: int) -> Money:
    return Money(minor, BASE_CURRENCY)


def test_repeated_rows_add_up_exactly_and_keep_every_source_row() -> None:
    reading = read(
        "Order summary\nItems $10.00\nDelivery fee $0.10\nDelivery fee $0.20\n"
        "Delivery fee $0.30\nOrder total $10.60\n"
    )
    fee = next(f for f in reading.found if f.key == "fee")
    assert fee.value == aud(60)  # a float would give 0.6000000000000001
    assert [p.value for p in fee.parts] == [aud(10), aud(20), aud(30)]
    (check,) = reading.checks
    assert check.status == r.AGREE
    assert check.assumed_zero == ("discount",)  # omitted, so counted as zero (and said so)
    assert "discount" in {n.key for n in reading.not_found}  # but not filled


def test_repeated_text_rows_join_each_distinct_value_once() -> None:
    reading = read("Order summary\nCourier Alpha\nCourier Beta\nCourier Alpha\nOrder total $1.00\n")
    courier = next(f for f in reading.found if f.key == "courier")
    assert courier.value == "Alpha; Beta"
    assert [p.value for p in courier.parts] == ["Alpha", "Beta", "Alpha"]


def test_a_label_that_does_not_repeat_is_still_ambiguous_when_it_appears_twice() -> None:
    reading = read("Order summary\nItems $1.00\nItems $2.00\nOrder total $3.00\n")
    assert {n.key: n.reason for n in reading.not_found}["items"] == r.AMBIGUOUS


def test_one_row_that_is_not_an_amount_fails_the_whole_repeated_figure() -> None:
    reading = read("Order summary\nDelivery fee $1.00\nDelivery fee free\nOrder total $1.00\n")
    assert {n.key: n.reason for n in reading.not_found}["fee"] == r.NOT_AN_AMOUNT
    assert "fee" not in {f.key for f in reading.found}


def test_a_missing_part_that_may_not_count_as_zero_skips_the_check() -> None:
    reading = read("Order summary\nDelivery fee $1.00\nOrder total $1.00\n")  # no Items row
    (check,) = reading.checks
    assert check.status == r.SKIPPED and "items" in check.message
    assert reading.outcome == r.WARNINGS


def test_a_zero_in_sums_part_does_not_hide_a_real_disagreement() -> None:
    reading = read("Order summary\nItems $10.00\nOrder total $11.00\n")
    (check,) = reading.checks
    assert check.status == r.DISAGREE  # 10.00 + 0 + 0 is not 11.00


def test_a_stated_part_is_never_treated_as_assumed() -> None:
    reading = read("Order summary\nItems $10.00\nDiscount -$2.00\nOrder total $8.00\n")
    (check,) = reading.checks
    assert (check.status, check.assumed_zero) == (r.AGREE, ("fee",))


@given(st.lists(st.integers(min_value=0, max_value=10_000_000), min_size=1, max_size=12))
def test_repeated_rows_always_add_to_the_exact_sum_of_their_minor_units(amounts: list[int]) -> None:
    rows = "".join(f"Delivery fee ${a // 100}.{a % 100:02d}\n" for a in amounts)
    reading = read(f"Order summary\n{rows}Order total $0.00\n")
    fee = next(f for f in reading.found if f.key == "fee")
    assert fee.value == aud(sum(amounts))
    assert [p.value for p in fee.parts] == [aud(a) for a in amounts]


def test_the_new_options_round_trip_and_default_off() -> None:
    profile = load_profile(spec())
    assert load_profile(to_json(profile)) == profile
    fee = next(f for f in profile.fields if f.key == "fee")
    assert (fee.repeats, fee.zero_in_sums) == (True, True)
    older = spec()
    for field in older["fields"]:  # a profile written before these options existed
        field.pop("repeats", None)
        field.pop("zero_in_sums", None)
    loaded = load_profile(older)
    assert not any(f.repeats or f.zero_in_sums for f in loaded.fields)


def test_the_options_are_refused_where_they_make_no_sense() -> None:
    bad = spec()
    bad["fields"].append(
        {"key": "when", "type": "fy", "labels": ["When"], "pair": "same_row", "repeats": True}
    )
    with pytest.raises(ProfileError, match="repeats"):
        load_profile(bad)
    bad = spec()
    bad["fields"].append(
        {"key": "note", "type": "text", "labels": ["Note"], "pair": "same_row",
         "zero_in_sums": True}
    )  # fmt: skip
    with pytest.raises(ProfileError, match="zero"):
        load_profile(bad)
