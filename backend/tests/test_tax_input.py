"""The Tax input and result: canonical JSON, normalisation, validation and the label registry.

Rules are invented (see ``tax_support``); nothing here states an official figure.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tax_support import FY, aud, invented_rules, member, tax_input

from finance360.capabilities.tax import input as tinput
from finance360.capabilities.tax import labels, result_json, rules
from finance360.capabilities.tax.engine import TAX_ENGINE_VERSION, estimate
from finance360.capabilities.tax.input import InputError, MemberInput, TaxInput
from finance360.core.money import Money


def sample() -> TaxInput:
    return tax_input(
        member(1, salary="80000.50", help_debt=True, surcharge_exempt_days=12),
        member(2, salary="28000", government_allowances="10.25"),
        dependants=2,
    )


def test_the_input_survives_a_json_round_trip_exactly() -> None:
    inp = sample()
    text = tinput.to_json(inp)
    assert tinput.from_json(text) == inp
    assert tinput.to_json(tinput.from_json(text)) == text


def test_the_json_is_canonical_and_holds_no_float() -> None:
    text = tinput.to_json(sample())
    assert " " not in text
    payload = json.loads(text)
    assert list(payload) == sorted(payload)
    assert payload["input_version"] == 1 and payload["financial_year"] == FY
    first = payload["members"][0]["totals"]
    assert first["salary"] == {"ccy": "AUD", "minor": 8_000_050}
    assert first["help_debt"] is True and first["surcharge_exempt_days"] == 12
    assert payload["rules"]["values"]["lito_taper1_rate"] == "3"  # a percent string, not a number

    def no_floats(value: object) -> None:
        assert not isinstance(value, float)
        if isinstance(value, dict):
            for item in value.values():  # pyright: ignore[reportUnknownVariableType]
                no_floats(item)
        if isinstance(value, list):
            for item in value:  # pyright: ignore[reportUnknownVariableType]
                no_floats(item)

    no_floats(payload)


def test_a_float_in_a_payload_is_rejected_whether_or_not_it_is_wanted() -> None:
    payload = tinput.to_payload(sample())
    payload["members"][0]["totals"]["salary"]["minor"] = 1.5
    with pytest.raises(InputError):
        tinput.from_payload(payload)
    text = tinput.to_json(sample()).replace('"minor":8000050', '"minor":8000050.0')
    with pytest.raises(InputError):
        tinput.from_json(text)
    with pytest.raises(InputError):
        tinput.from_json("NaN")


def test_equal_inputs_are_equal_values_whatever_order_or_explicit_zeros() -> None:
    a = tax_input(member(2, salary="1"), member(1, salary="2", interest="0"))
    b = tax_input(member(1, salary="2"), member(2, salary="1", help_debt=False))
    assert a == b and tinput.to_json(a) == tinput.to_json(b)
    assert [m.member_id for m in a.members] == [1, 2]


def test_an_unentered_label_reads_as_its_default() -> None:
    inp = tax_input(member(1, salary="5"))
    one = inp.members[0]
    assert one.money("interest") == aud("0") and one.days("surcharge_exempt_days") == 0
    assert not one.flag("help_debt")
    with pytest.raises(InputError):
        one.money("no_such_label")


@pytest.mark.parametrize(
    "bad",
    [
        {"salary": Money(-1, "AUD")},  # negative
        {"salary": Money(1, "USD")},  # not AUD
        {"salary": 5},  # not money
        {"help_debt": 1},  # not a boolean
        {"surcharge_exempt_days": 366},  # more days than the year has
        {"surcharge_exempt_days": True},  # a flag is not a number of days
        {"surcharge_exempt_days": -1},
        {"no_such_label": Money(1, "AUD")},
    ],
)
def test_a_bad_total_is_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(InputError):
        TaxInput(
            input_version=1,
            financial_year=FY,
            members=(MemberInput(1, date(1980, 1, 1), tuple(bad.items())),),  # type: ignore[arg-type]
            dependants=0,
            rules=invented_rules(),
        )


def test_the_whole_household_is_one_or_two_different_members_for_the_year_of_the_rules() -> None:
    kwargs = {"input_version": 1, "financial_year": FY, "dependants": 0, "rules": invented_rules()}
    ok = MemberInput(1, date(1980, 1, 1))
    for members in (
        (),
        (ok, ok),
        (ok, MemberInput(2, date(1980, 1, 1)), MemberInput(3, date(1980, 1, 1))),
    ):
        with pytest.raises(InputError):
            TaxInput(members=members, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(InputError):
        TaxInput(members=(ok,), **{**kwargs, "financial_year": FY + 1})  # type: ignore[arg-type]
    with pytest.raises(InputError):
        TaxInput(members=(ok,), **{**kwargs, "dependants": -1})  # type: ignore[arg-type]
    with pytest.raises(InputError):
        TaxInput(members=(ok,), **{**kwargs, "input_version": 2})  # type: ignore[arg-type]


def test_two_members_are_a_couple_and_one_is_not() -> None:
    assert not tax_input(member(1)).has_spouse
    assert tax_input(member(1), member(2)).has_spouse


def test_an_unsupported_version_has_no_upcaster() -> None:
    payload = tinput.to_payload(sample())
    payload["input_version"] = 0
    with pytest.raises(InputError):
        tinput.from_payload(payload)
    payload["input_version"] = 2
    with pytest.raises(InputError):
        tinput.from_payload(payload)


def test_the_rules_block_round_trips_with_its_provisional_markers() -> None:
    block = invented_rules(provisional=(("tax.help.t1", "b note"), ("tax.help.r1", "a note")))
    assert block.provisional == (("tax.help.r1", "a note"), ("tax.help.t1", "b note"))
    assert rules.from_payload(rules.to_payload(block)) == block
    none = invented_rules(instant=None)
    assert none.standard_work_deduction is None
    assert rules.from_payload(rules.to_payload(none)) == none


def test_a_malformed_rules_block_is_refused() -> None:
    with pytest.raises(rules.RulesError):
        rules.from_payload({"financial_year": FY, "provisional": [], "values": {}})
    block = rules.to_payload(invented_rules())
    block["values"]["resident_brackets"] = [{"over": {"ccy": "AUD", "minor": 5}, "rate": "1"}]
    with pytest.raises(rules.RulesError):  # the first band must start at 0
        rules.from_payload(block)


def test_a_result_survives_a_json_round_trip_exactly() -> None:
    result = estimate(sample())
    text = result_json.to_json(result)
    assert result_json.from_json(text) == result
    assert result_json.to_json(result_json.from_json(text)) == text
    assert result.incomplete and result.unmodelled
    with pytest.raises(InputError):
        result_json.from_json(
            text.replace(f'"engine_version":{TAX_ENGINE_VERSION}', '"engine_version":1.5')
        )


# --- the registry ------------------------------------------------------------------------


def test_every_label_is_well_formed_and_keys_are_unique() -> None:
    keys = [label.key for label in labels.all_labels()]
    assert len(keys) == len(set(keys))
    section_keys = {k for k, _ in labels.SECTIONS}
    for label in labels.all_labels():
        assert label.name and label.direction in labels.DIRECTIONS
        assert label.kind in labels.KINDS and label.section in section_keys
        assert label.record_types, label.key
        if label.kind == labels.MONEY:
            # a memo label is fed by an income statement alone; every other money label by a line
            expected = ("income_statement",) if label.direction == labels.MEMO else ("line",)
            assert label.record_types[: len(expected)] == expected, label.key
        assert label.modelled or label.kind == labels.MONEY


def test_the_registry_lists_unmodelled_return_items_too() -> None:
    unmodelled = [
        label
        for label in labels.all_labels()
        if not label.modelled and label.direction != labels.MEMO
    ]
    assert unmodelled and all(label.section == "other_items" for label in unmodelled)
    assert labels.MODELLED == {k for k, lb in labels.LABELS.items() if lb.modelled}


# --- properties --------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    st.dictionaries(
        st.sampled_from(sorted(k for k, v in labels.LABELS.items() if v.kind == labels.MONEY)),
        st.integers(0, 10**13),
        max_size=12,
    ),
    st.integers(0, 366),
    st.booleans(),
)
def test_any_valid_input_round_trips_and_its_money_sums_exactly(
    totals: dict[str, int], days: int, flag: bool
) -> None:
    typed: list[tuple[str, Money | int | bool]] = [(k, Money(v, "AUD")) for k, v in totals.items()]
    typed.append(("surcharge_exempt_days", min(days, 365)))
    typed.append(("help_debt", flag))
    inp = tax_input(MemberInput(1, date(1980, 1, 1), tuple(typed)))
    assert tinput.from_json(tinput.to_json(inp)) == inp
    # the exact sum of the entered money is the sum of the minor units: nothing lost to rounding
    held = sum(v.amount_minor for _, v in inp.members[0].totals if isinstance(v, Money))
    assert held == sum(totals.values())
