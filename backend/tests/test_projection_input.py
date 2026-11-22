"""The Projection input: validation, canonical JSON, exact round trips, no floats (ADR-0015 s2)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_projection_engine import aud, make, projection_inputs

from finance360.capabilities.projection import input as pinput
from finance360.capabilities.projection.input import (
    INPUT_VERSION,
    Inflow,
    InputError,
    Line,
    MemberRef,
    Outflow,
    ProjectionInput,
    age_on,
    anniversary,
)
from finance360.core.money import Money
from finance360.core.rate import Rate

SETTINGS = settings(max_examples=200, deadline=None)


def sample() -> ProjectionInput:
    return make(
        members=[MemberRef(2, date(1986, 9, 30)), MemberRef(1, date(1984, 3, 12))],
        lines=[
            Line(2, "investments", aud(5_000_000), Rate(70_000), 0),
            Line(1, "cash", aud(100_000), Rate(30_000), 0),
            Line(1, "superannuation", aud(21_522_807), Rate(70_000), 18),
        ],
        years=50,
        inflation=25_000,
        inflows=[Inflow(2, "investments", aud(1_000_000), 0, 18)],
        outflows=[Outflow(aud(7_000_000), 20, None)],
    )


# --- round trip and canonical form --------------------------------------------------------


@SETTINGS
@given(projection_inputs())
def test_the_input_round_trips_exactly(projection_input: ProjectionInput) -> None:
    text = pinput.to_json(projection_input)
    assert pinput.from_json(text) == projection_input
    assert pinput.to_json(pinput.from_json(text)) == text
    assert pinput.from_payload(pinput.to_payload(projection_input)) == projection_input


def test_the_json_is_canonical_with_documented_shapes() -> None:
    payload = json.loads(pinput.to_json(sample()))
    assert list(payload) == sorted(payload)
    assert payload["inflation"] == "2.5"
    assert payload["input_version"] == INPUT_VERSION
    assert payload["as_at"] == "2026-01-01"
    assert payload["lines"][0] == {
        "accessible_from_year": 0,
        "category_key": "cash",
        "member_id": 1,
        "return_rate": "3",
        "start": {"ccy": "AUD", "minor": 100_000},
    }
    # Lines are ordered by drawdown position, then member id; members by id.
    assert [(x["category_key"], x["member_id"]) for x in payload["lines"]] == [
        ("cash", 1),
        ("investments", 2),
        ("superannuation", 1),
    ]
    assert [m["member_id"] for m in payload["members"]] == [1, 2]
    assert payload["outflows"][0]["until_year"] is None


def test_the_order_the_pieces_are_given_in_does_not_change_the_input() -> None:
    a = sample()
    b = ProjectionInput(
        input_version=INPUT_VERSION,
        as_at=a.as_at,
        currency=a.currency,
        members=tuple(reversed(a.members)),
        years=a.years,
        inflation=a.inflation,
        lines=tuple(reversed(a.lines)),
        inflows=tuple(reversed(a.inflows)),
        outflows=tuple(reversed(a.outflows)),
        drawdown_order=a.drawdown_order,
    )
    assert a == b and pinput.to_json(a) == pinput.to_json(b)


# --- no floats ----------------------------------------------------------------------------


def payload_of(value: ProjectionInput) -> dict[str, Any]:
    return json.loads(pinput.to_json(value))


def test_a_float_anywhere_in_a_payload_is_rejected() -> None:
    good = payload_of(sample())
    bad_payloads: list[dict[str, Any]] = []
    for mutate in (
        lambda p: p.update(years=50.0),
        lambda p: p["lines"][0]["start"].update(minor=100_000.0),
        lambda p: p["lines"][0].update(accessible_from_year=0.5),
        lambda p: p["inflows"][0].update(until_year=18.0),
        lambda p: p.update(unrelated={"deep": [1, 2.5]}),  # even where it isn't wanted
        lambda p: p.update(inflation=0.025),
    ):
        candidate = json.loads(json.dumps(good))
        mutate(candidate)
        bad_payloads.append(candidate)
    for payload in bad_payloads:
        with pytest.raises(InputError):
            pinput.from_payload(payload)


@pytest.mark.parametrize("fragment", ['"years":50.0', '"years":5e1', '"years":NaN'])
def test_a_float_in_json_text_is_rejected(fragment: str) -> None:
    text = pinput.to_json(sample()).replace('"years":50', fragment)
    assert fragment in text
    with pytest.raises(InputError):
        pinput.from_json(text)


def test_booleans_are_not_integers_in_a_payload() -> None:
    payload = payload_of(sample())
    payload["years"] = True
    with pytest.raises(InputError):
        pinput.from_payload(payload)


def test_malformed_payloads_raise_input_error() -> None:
    for text in ("", "[]", "{", '{"input_version":1}'):
        with pytest.raises(InputError):
            pinput.from_json(text)


# --- validation ---------------------------------------------------------------------------


def _with(**changes: Any) -> ProjectionInput:
    base = sample()
    fields: dict[str, Any] = {
        "input_version": base.input_version,
        "as_at": base.as_at,
        "currency": base.currency,
        "members": base.members,
        "years": base.years,
        "inflation": base.inflation,
        "lines": base.lines,
        "inflows": base.inflows,
        "outflows": base.outflows,
        "drawdown_order": base.drawdown_order,
        "debits": base.debits,
    }
    fields.update(changes)
    return ProjectionInput(**fields)


def test_the_sample_is_valid() -> None:
    assert _with() == sample()


@pytest.mark.parametrize(
    "changes",
    [
        {"input_version": 1},  # a past version loads only through ``from_payload``
        {"currency": "ZZZ"},
        {"years": 0},
        {"years": 121},
        {"years": True},
        {"members": ()},
        {"members": (MemberRef(1, date(1984, 3, 12)), MemberRef(1, date(1990, 1, 1)))},
        {"drawdown_order": ()},
        {"drawdown_order": ("cash", "cash", "investments", "superannuation")},
        {"drawdown_order": ("cash", "investments")},  # a line's Category is missing
        {"lines": (Line(1, "cash", aud(1), Rate(0), 0), Line(1, "cash", aud(2), Rate(0), 0))},
        {"lines": (Line(9, "cash", aud(1), Rate(0), 0),)},  # an unknown Member
        {"lines": (Line(1, "cash", Money(1, "USD"), Rate(0), 0),)},  # not the input's currency
        {"lines": (Line(1, "cash", aud(-1), Rate(0), 0),)},
        {"lines": (Line(1, "cash", aud(1), Rate(0), -1),)},
        {"inflows": (Inflow(1, "investments", aud(5), 0, None),)},  # no such line
        {"inflows": (Inflow(2, "investments", aud(0), 0, None),)},
        {"inflows": (Inflow(2, "investments", aud(5), 3, 2),)},
        {"outflows": (Outflow(aud(-5), 0, None),)},
        {"outflows": (Outflow(aud(5), -1, None),)},
        {"outflows": (Outflow(Money(5, "USD"), 0, None),)},
    ],
)
def test_invalid_inputs_are_rejected(changes: Mapping[str, Any]) -> None:
    with pytest.raises(InputError):
        _with(**changes)


def test_a_past_version_loads_through_its_upcaster(monkeypatch: pytest.MonkeyPatch) -> None:
    current = payload_of(sample())
    old = {k: v for k, v in current.items() if k != "years"} | {
        "input_version": INPUT_VERSION - 1,
        "horizon": current["years"],
    }

    def upcast(body: Mapping[str, Any]) -> Mapping[str, Any]:
        new = {k: v for k, v in body.items() if k != "horizon"}
        return new | {"input_version": INPUT_VERSION, "years": body["horizon"]}

    monkeypatch.setitem(pinput._UPCASTERS, INPUT_VERSION - 1, upcast)  # pyright: ignore[reportPrivateUsage]
    assert pinput.from_payload(old) == sample()
    with pytest.raises(InputError):
        pinput.from_payload(old | {"input_version": INPUT_VERSION - 3})
    with pytest.raises(InputError):
        pinput.from_payload(current | {"input_version": INPUT_VERSION + 1})


# --- ages and the leap-day rule -----------------------------------------------------------


@pytest.mark.parametrize(
    ("born", "on", "age"),
    [
        (date(1984, 3, 12), date(2026, 3, 11), 41),
        (date(1984, 3, 12), date(2026, 3, 12), 42),  # a birthday counts from the day it falls
        (date(1984, 3, 12), date(2026, 12, 31), 42),
        (date(2000, 2, 29), date(2028, 2, 28), 27),
        (date(2000, 2, 29), date(2028, 2, 29), 28),
        (date(2000, 2, 29), date(2027, 2, 27), 26),
        (date(2000, 2, 29), date(2027, 2, 28), 27),  # no 29 February: the 28th is the birthday
        (date(2000, 2, 29), date(2027, 3, 1), 27),
    ],
)
def test_age_on_whole_years_with_the_leap_day_rule(born: date, on: date, age: int) -> None:
    assert age_on(born, on) == age


def test_a_leap_day_falls_on_the_28th_in_a_year_without_one() -> None:
    assert anniversary(date(2028, 2, 29), 2029) == date(2029, 2, 28)
    assert anniversary(date(2028, 2, 29), 2032) == date(2032, 2, 29)
    assert anniversary(date(2026, 1, 1), 2030) == date(2030, 1, 1)


@given(
    st.dates(date(1900, 1, 1), date(2100, 12, 31)),
    st.dates(date(1900, 1, 1), date(2100, 12, 31)),
)
def test_age_is_never_negative_once_born_and_grows_by_one_each_anniversary(
    a: date, b: date
) -> None:
    born, on = sorted((a, b))
    age = age_on(born, on)
    assert age >= 0
    assert age_on(born, anniversary(born, born.year + age)) == age
    assert age_on(born, anniversary(born, born.year + age + 1)) == age + 1
