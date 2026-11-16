"""The ``car_kilometres`` Record type (ADR-0019 section 2).

Golden tests of the trip schedule against answers worked out by hand, Hypothesis properties for the
exact sum, the limit and the single rounding, and integration tests through the API against a real
temporary SQLite database. The rate per kilometre and the limit are **injected** (invented figures,
as test inputs): no official figure is held in the production code, and the shipped Rule pack leaves
both uncaptured until the owner captures them. Every amount and kilometre here is invented.
"""

from __future__ import annotations

import dataclasses
import shutil
from collections.abc import Iterator
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.tax import pack_rules, record_types
from finance360.capabilities.tax.input import InputError
from finance360.capabilities.tax.record_types import car_kilometres as car
from finance360.capabilities.tax.record_types.base import NO_RULES, Record, RecordRules
from finance360.config import Settings
from finance360.core import rulepack
from finance360.core.money import Money, parse_amount
from finance360.main import create_app
from finance360.rulepacks.au import PACK
from finance360.rulepacks.au.tax_specs import SPECS
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
ALEX, JORDAN = 1, 2
RATE, LIMIT = car.RATE_KEY, car.LIMIT_KEY
FY = 2026
#: Invented figures for the hand-worked answers: 41 cents a kilometre, 3,000 km a car.
CENTS, MAX_KM = 41, 3_000


def aud(text: str) -> Money:
    return parse_amount(text, "AUD")


def rules_for(*years: int, cents: int = CENTS, limit: int = MAX_KM) -> RecordRules:
    return RecordRules(
        tuple(figure for year in years for figure in ((RATE, year, cents), (LIMIT, year, limit)))
    )


def trip(description: str, km: Any, times: Any = 1, on: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"description": description, "km": km, "times": times}
    if on is not None:
        row["on"] = on
    return row


def a_record(
    rows: list[dict[str, Any]],
    *,
    start: date = date(2025, 7, 1),
    end: date = date(2026, 6, 30),
    label: str = "business_expenses",
    record_id: int = 1,
    member: int = ALEX,
) -> Record:
    value = car.parse({"label": label, "trips": rows})
    return Record(record_id, member, car.KEY, start, end, value)


def amount_of(record: Record, rules: RecordRules) -> Money:
    (only,) = car.contributions(record, FY, rules)
    assert isinstance(only.value, Money)
    return only.value


# --- golden: trips worked out by hand ----------------------------------------------------------
#
# Under the limit (invented 41 cents a km, 3,000 km a car):
#   40.0 km x 10 = 400.0;  12.5 km x 4 = 50.0;  7.3 km x 3 = 21.9;  total 471.9 km, all counted
#   471.9 x 0.41 = 193.479 dollars = 19347.9 cents -> 19348, so 193.48
UNDER = [
    trip("Invented client site, return", "40.0", 10),
    trip("Invented supplier, return", "12.5", 4),
    trip("Invented errand", "7.3", 3),
]


def test_a_few_trips_under_the_limit_work_out_by_hand() -> None:
    record = a_record(UNDER)
    value: car.CarKilometres = record.value
    assert [car.km_text(t.total) for t in value.trips] == ["400.0", "50.0", "21.9"]
    assert car.km_text(car.total_tenths(value)) == "471.9"
    assert car.km_text(car.counted_tenths(value, MAX_KM)) == "471.9"
    (only,) = car.contributions(record, FY, rules_for(FY))
    assert (only.record_id, only.label, only.value) == (1, "business_expenses", aud("193.48"))


# Over the limit, so the limit applies:
#   120.0 km x 20 = 2400.0;  85.5 km x 10 = 855.0;  total 3255.0 km, limit 3000 km
#   counted 3000.0 km x 0.41 = 1230.00 (without the limit it would be 3255.0 x 0.41 = 1334.55)
OVER = [trip("Invented depot, return", "120.0", 20), trip("Invented branch, return", "85.5", 10)]


def test_a_set_over_the_limit_counts_only_the_limit() -> None:
    record = a_record(OVER)
    value: car.CarKilometres = record.value
    assert car.km_text(car.total_tenths(value)) == "3255.0"
    assert car.km_text(car.counted_tenths(value, MAX_KM)) == "3000.0"
    assert amount_of(record, rules_for(FY)) == aud("1230.00")
    # a bigger limit lets more count, so the limit really is read from the rules
    assert amount_of(record, rules_for(FY, limit=4_000)) == aud("1334.55")


# Exactly at the limit: 150.0 km x 20 = 3000.0 km, all counted, 3000.0 x 0.41 = 1230.00.
# One tenth under (2999.9 km): 2999.9 x 41 = 122995.9 cents -> 122996, so 1229.96.
# One tenth over (3000.1 km): the extra tenth does not count, so still 1230.00.
def test_the_total_exactly_at_the_limit_counts_in_full_and_a_tenth_over_adds_nothing() -> None:
    at = a_record([trip("Invented loop", "150.0", 20)])
    assert amount_of(at, rules_for(FY)) == aud("1230.00")
    under = a_record([trip("Invented loop", "2999.9")])
    assert amount_of(under, rules_for(FY)) == aud("1229.96")
    over = a_record([trip("Invented loop", "3000.1")])
    assert amount_of(over, rules_for(FY)) == aud("1230.00")


# Rounded once, with tenths of a kilometre at a rate that does not divide evenly:
#   three trips of 0.5 km = 1.5 km x 41 = 61.5 cents -> 62 (half to even)
#   rounding each trip first: 0.5 x 41 = 20.5 -> 20, three times = 60: what one rounding avoids.
#   2.5 km x 41 = 102.5 cents -> 102 (half to even, the other way);  0.3 km x 41 = 12.3 -> 12
def test_the_deduction_is_rounded_once_for_the_record_not_per_trip() -> None:
    three = a_record(
        [trip("Invented a", "0.5"), trip("Invented b", "0.5"), trip("Invented c", "0.5")]
    )
    assert amount_of(three, rules_for(FY)) == aud("0.62")
    assert amount_of(a_record([trip("Invented a", "2.5")]), rules_for(FY)) == aud("1.02")
    assert amount_of(a_record([trip("Invented a", "0.1", 3)]), rules_for(FY)) == aud("0.12")


def test_a_record_counts_only_in_the_year_it_sits_in() -> None:
    record = a_record(UNDER)
    for year in (FY - 1, FY + 1):
        assert car.contributions(record, year, rules_for(year, FY)) == ()
    assert record_types.overlaps(record, FY) and not record_types.overlaps(record, FY + 1)
    # the figures of another year do not stand in for them
    assert car.contributions(record, FY, rules_for(FY + 1)) == ()


def test_a_label_total_sums_every_record_feeding_it_and_drills_down_to_each() -> None:
    first = a_record(UNDER, record_id=7)
    second = a_record([trip("Invented a", "2.5")], record_id=8, member=JORDAN, label="work_car")
    other = a_record([trip("Invented a", "2.5")], record_id=9, label="business_expenses")
    totals = record_types.label_totals([first, second, other], FY, rules_for(FY))
    mine = totals[(ALEX, "business_expenses")]
    assert mine.total == aud("193.48") + aud("1.02")
    assert [(c.record_id, c.value) for c in mine.contributions] == [
        (7, aud("193.48")),
        (9, aud("1.02")),
    ]
    assert totals[(JORDAN, "work_car")].total == aud("1.02")


# --- the figures are never guessed or skipped --------------------------------------------------


def test_an_uncaptured_figure_is_reported_never_skipped_silently() -> None:
    record = a_record(UNDER)
    assert car.missing_figures(record, NO_RULES) == (RATE, LIMIT)
    assert car.contributions(record, FY, NO_RULES) == ()
    assert record_types.label_totals([record], FY, NO_RULES) == {}  # no number is invented
    assert record_types.unavailable([record], FY, NO_RULES) == ((record, (RATE, LIMIT)),)
    # each is needed: with only one of them the record still waits for the other
    only_rate = RecordRules(((RATE, FY, CENTS),))
    only_limit = RecordRules(((LIMIT, FY, MAX_KM),))
    assert car.missing_figures(record, only_rate) == (LIMIT,)
    assert car.missing_figures(record, only_limit) == (RATE,)
    assert car.contributions(record, FY, only_rate) == ()
    assert car.missing_figures(record, rules_for(FY - 1, FY + 1)) == (RATE, LIMIT)
    assert car.missing_figures(record, rules_for(FY)) == ()
    assert record_types.unavailable([record], FY + 1, NO_RULES) == ()  # not in that year at all


def test_the_rate_and_the_limit_are_declared_for_guided_capture() -> None:
    from finance360.rulepacks.capture import FigureError, parse_figure

    rate = next(s for s in SPECS if s.key == RATE)
    limit = next(s for s in SPECS if s.key == LIMIT)
    for spec in (rate, limit):
        assert spec.source == "ATO, Car expenses: cents per kilometre method"
        assert spec.page_url.startswith("https://www.ato.gov.au/") and spec.where
    assert rate.label == "Car expenses: cents per kilometre rate" and rate.unit == "km"
    assert limit.label == "Car expenses: kilometre limit per car" and limit.value_suffix == " km"
    assert parse_figure(rate, "0.40") == Money(40, "AUD")  # a typed rate; nothing is held in code
    for typed in ("0.40 per km", "0.40/km", "0.40 per kilometre"):
        assert parse_figure(rate, typed) == Money(40, "AUD")
    for typed in ("0.05", "4", "forty", "0.40 per hour", "0.40 per year"):
        with pytest.raises(FigureError):
            parse_figure(rate, typed)
    assert parse_figure(limit, "4000") == 4000
    for typed in ("999", "20001", "4000.5", "lots"):
        with pytest.raises(FigureError):
            parse_figure(limit, typed)
    assert RATE in pack_rules.RECORD_KEYS and LIMIT in pack_rules.RECORD_KEYS


# --- the payload ----------------------------------------------------------------------------

GOOD = {"label": "business_expenses", "trips": [trip("Invented client site", "42.5", 2)]}


def bad(**changes: Any) -> dict[str, Any]:
    return {**GOOD, **changes}


@pytest.mark.parametrize(
    "payload",
    [
        bad(label="salary"),
        bad(label="work_other"),
        bad(trips=[]),  # at least one trip
        bad(trips="x"),
        bad(trips=[trip("", "5")]),  # a description is required
        bad(trips=[trip("   ", "5")]),
        bad(trips=[trip("x" * 201, "5")]),
        bad(trips=[trip("Invented", "0")]),  # more than 0
        bad(trips=[trip("Invented", "0.0")]),
        bad(trips=[trip("Invented", "-3")]),
        bad(trips=[trip("Invented", "4.25")]),  # at most one decimal place
        bad(trips=[trip("Invented", "1e1")]),
        bad(trips=[trip("Invented", "x")]),
        bad(trips=[trip("Invented", "")]),
        bad(trips=[trip("Invented", 5)]),  # kilometres are text
        bad(trips=[trip("Invented", 5.5)]),  # a float is never accepted
        bad(trips=[trip("Invented", "100000.1")]),
        bad(trips=[trip("Invented", "5", 0)]),  # times is a whole number from 1
        bad(trips=[trip("Invented", "5", -1)]),
        bad(trips=[trip("Invented", "5", "1.5")]),
        bad(trips=[trip("Invented", "5", "x")]),
        bad(trips=[trip("Invented", "5", 2.0)]),
        bad(trips=[trip("Invented", "5", True)]),
        bad(trips=[trip("Invented", "5", 10_001)]),
        bad(trips=[trip("Invented", "5", 1, "14 August")]),
        bad(trips=[trip("Invented", "5", 1, "2025-13-01")]),
        bad(trips=[trip("Invented", "5")] * (car.MAX_TRIPS + 1)),
    ],
)
def test_a_payload_that_does_not_fit_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(InputError):
        car.parse(payload)


def test_the_most_trips_and_the_edges_are_accepted() -> None:
    value = car.parse(
        bad(
            trips=[trip("Invented", "0.1", 1)] * (car.MAX_TRIPS - 1)
            + [trip("Invented edge", "100000.0", 10_000, "2026-06-30")]
        )
    )
    assert len(value.trips) == car.MAX_TRIPS and value.trips[-1].times == 10_000


def test_the_payload_round_trips_in_canonical_text_with_no_float() -> None:
    value = car.parse(
        {
            "label": "work_car",
            "trips": [
                {"description": " Invented a ", "km": "42", "times": "3", "on": "2025-08-14"},
                {"description": "Invented b", "km": "0.5"},  # times defaults to 1
                {"description": "Invented c", "km": "7.0", "times": "", "on": ""},
            ],
        }
    )
    payload = car.to_payload(value)
    assert payload == {
        "label": "work_car",
        "trips": [
            {"description": "Invented a", "km": "42.0", "times": 3, "on": "2025-08-14"},
            {"description": "Invented b", "km": "0.5", "times": 1},
            {"description": "Invented c", "km": "7.0", "times": 1},
        ],
    }
    assert car.parse(payload) == value


# --- properties -------------------------------------------------------------------------------


@st.composite
def schedules(draw: st.DrawFn) -> tuple[Record, list[tuple[int, int]], int, int]:
    rows = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=1, max_value=car.MAX_TRIP_TENTHS),
                st.integers(min_value=1, max_value=car.MAX_TIMES),
            ),
            min_size=1,
            max_size=40,
        )
    )
    cents = draw(st.integers(min_value=1, max_value=500))
    limit = draw(st.integers(min_value=1_000, max_value=20_000))
    record = a_record(
        [trip(f"Invented {i}", car.km_text(km), times) for i, (km, times) in enumerate(rows)]
    )
    return record, rows, cents, limit


@given(schedules())
def test_the_total_is_the_exact_sum_and_the_deduction_is_limited_then_rounded_once(
    drawn: tuple[Record, list[tuple[int, int]], int, int],
) -> None:
    record, rows, cents, limit = drawn
    value: car.CarKilometres = record.value
    exact_km = sum((Fraction(km * times, 10) for km, times in rows), Fraction(0))
    assert Fraction(car.total_tenths(value), 10) == exact_km
    counted = min(exact_km, Fraction(limit))
    assert Fraction(car.counted_tenths(value, limit), 10) == counted
    # exactly the counted kilometres x the rate in cents, rounded half to even once, never negative
    expected = round(counted * cents)
    found = car.contributions(record, FY, rules_for(FY, cents=cents, limit=limit))
    amount = found[0].value if found else Money.zero("AUD")
    assert isinstance(amount, Money)
    assert amount.amount_minor == expected >= 0
    assert abs(Fraction(amount.amount_minor) - counted * cents) <= Fraction(1, 2)
    assert car.deduction(value, Money(cents, "AUD"), limit).amount_minor == expected


# --- through the API, against a real temporary SQLite database --------------------------------


def pack_with_figures(
    cents: int | None, limit: int | None, years: tuple[int, ...] = (FY, FY + 1)
) -> rulepack.RulePack:
    """The shipped pack with the car figures replaced by invented ones (or removed)."""
    keys = {RATE, LIMIT}
    params = tuple(p for p in PACK.params if p.key not in keys)
    held = {
        fy: {k: v for k, v in values.items() if k not in keys} for fy, values in PACK.years.items()
    }
    for spec in SPECS:
        if spec.key == RATE and cents is not None:
            params = (*params, spec.to_param())
            for year in years:
                held.setdefault(year, {})[RATE] = Money(cents, "AUD")
        if spec.key == LIMIT and limit is not None:
            params = (*params, spec.to_param())
            for year in years:
                held.setdefault(year, {})[LIMIT] = limit
    return dataclasses.replace(PACK, params=params, years=held)


def use_pack(pack: rulepack.RulePack) -> None:
    rulepack.unregister("au")
    rulepack.register(pack)


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c
    rulepack.unregister("au")
    rulepack.register(PACK)


def wire(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trips as the API takes them: every value is text (``times`` too)."""
    return [{**r, "times": str(r["times"])} if "times" in r else r for r in rows]


def a_body(**changes: Any) -> dict[str, Any]:
    """A new record in FY 2026-27, so it never meets the fixture's FY 2025-26 records."""
    base: dict[str, Any] = {
        "member_id": ALEX,
        "record_type": "car_kilometres",
        "label": "business_expenses",
        "from_on": "2026-07-01",
        "to_on": "2027-06-30",
        "description": "Invented car kilometres",
        "trips": wire(UNDER),
    }
    base.update(changes)
    base["trips"] = wire(base["trips"])
    return base


def fixture_record(client: TestClient, member: int = ALEX) -> dict[str, Any]:
    return next(
        r
        for r in client.get("/api/tax/records").json()
        if r["record_type"] == "car_kilometres"
        and r["member_id"] == member
        and r["from_on"] == "2025-07-01"  # FY 2025-26: the year the release holds figures for
    )


def test_the_shipped_pack_declares_both_figures_for_capture() -> None:
    assert {RATE, LIMIT} <= {s.key for s in SPECS}


def test_the_shipped_pack_works_out_the_fixture_records(sample: TestClient) -> None:
    # Captured from the official page for FY 2025-26: 0.88 a km, limited to 5,000 km a car.
    assert fixture_record(sample)["counted"] == "849.55"  # 965.4 km x 0.88 = 849.552
    assert fixture_record(sample, JORDAN)["counted"] == "679.18"  # 771.8 km x 0.88 = 679.184


def test_the_fixture_records_say_a_figure_is_missing_until_it_is_captured(
    sample: TestClient,
) -> None:
    use_pack(pack_with_figures(None, None))
    alex = fixture_record(sample)
    assert (alex["label_key"], alex["counted"]) == ("business_expenses", None)
    shown = alex["car"]
    assert shown["missing"] == [RATE, LIMIT]
    assert shown["rate_per_km"] is None and shown["limit_km"] is None
    assert shown["deduction"] is None and shown["counted_km"] is None and shown["capped"] is False
    assert shown["total_km"] == "965.4"
    assert [(t["km"], t["times"], t["total_km"]) for t in shown["trips"]] == [
        ("42.0", 12, "504.0"),
        ("18.5", 8, "148.0"),
        ("6.2", 20, "124.0"),
        ("31.0", 3, "93.0"),
        ("96.4", 1, "96.4"),
    ]
    assert [t["on"] for t in shown["trips"]] == [None, None, None, "2025-10-18", "2026-03-07"]
    jordan = fixture_record(sample, JORDAN)
    assert jordan["label_key"] == "work_car" and jordan["return_item"] == "D1"
    assert jordan["car"]["total_km"] == "771.8"


def test_with_the_figures_the_record_the_label_total_and_the_estimate_agree(
    sample: TestClient,
) -> None:
    # invented 41 cents a km and 3,000 km: Alex's 965.4 km are all counted, 965.4 x 0.41 =
    # 395.814 dollars = 39581.4 cents -> 39581, so 395.81. Jordan's 771.8 km x 41 = 31643.8 cents
    # -> 31644, so 316.44.
    use_pack(pack_with_figures(CENTS, MAX_KM))
    alex = fixture_record(sample)
    shown = alex["car"]
    assert shown["missing"] == [] and shown["rate_per_km"] == "0.41" and shown["limit_km"] == 3000
    assert (shown["counted_km"], shown["capped"], shown["deduction"]) == ("965.4", False, "395.81")
    assert alex["counted"] == "395.81"
    assert fixture_record(sample, JORDAN)["counted"] == "316.44"
    totals = sample.get("/api/tax/label-totals", params={"financial_year": FY}).json()
    assert totals["uncounted"] == []
    expenses = next(t for t in totals["totals"] if t["label_key"] == "business_expenses")
    # the camera (495.89), the home office hours (1,037.40 at the captured rate) and the trips,
    # each drilling down to its record
    assert expenses["amount"] == "1929.10"
    assert (alex["id"], "395.81") in [
        (c["record_id"], c["amount"]) for c in expenses["contributions"]
    ]
    work_car = next(
        t for t in totals["totals"] if t["label_key"] == "work_car" and t["member_id"] == JORDAN
    )
    assert work_car["amount"] == "316.44"
    assert [(c["record_id"], c["amount"]) for c in work_car["contributions"]] == [
        (fixture_record(sample, JORDAN)["id"], "316.44")
    ]
    estimate = sample.post(
        "/api/tax/estimate",
        json={"financial_year": FY, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert estimate.status_code == 200, estimate.text
    # 120,000.00 salary + 412.37 interest + (6,000.00 business income - 1,929.10 expenses)
    assert (
        estimate.json()["members"][0]["total_income"] == "236104.00"
    )  # each label drops its cents
    # FY + 1 holds Jordan's fixture record waiting for a figure; FY + 2 holds nothing
    other_year = sample.get("/api/tax/label-totals", params={"financial_year": FY + 2}).json()
    assert all(t["label_key"] != "work_car" for t in other_year["totals"])


def test_the_limit_applies_through_the_register(sample: TestClient) -> None:
    use_pack(pack_with_figures(CENTS, 800))  # an invented 800 km limit: Alex's 965.4 km are over
    shown = fixture_record(sample)["car"]
    # 800.0 km x 0.41 = 328.00; the 165.4 km above the limit do not count
    assert (shown["total_km"], shown["counted_km"], shown["capped"]) == ("965.4", "800.0", True)
    assert shown["deduction"] == "328.00"
    assert fixture_record(sample, JORDAN)["car"]["capped"] is False  # 771.8 km: under it


def test_the_label_totals_name_what_they_leave_out_and_an_estimate_will_not_skip_it(
    sample: TestClient,
) -> None:
    use_pack(pack_with_figures(None, None))
    res = sample.get("/api/tax/label-totals", params={"financial_year": FY}).json()
    uncounted = [u for u in res["uncounted"] if "car kilometres" in u["description"]]
    assert {u["description"] for u in uncounted} == {
        "Sample business car kilometres, FY 2025-26",
        "Sample work car kilometres, FY 2025-26",
    }
    assert all(u["missing"] == [RATE, LIMIT] and "FY 2025-26" in u["note"] for u in uncounted)
    estimate = sample.post(
        "/api/tax/estimate",
        json={"financial_year": FY, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert estimate.status_code == 422
    (error,) = estimate.json()["errors"]
    assert error["field"] == "financial_year"
    assert "Sample business car kilometres" in error["message"]
    assert RATE in error["message"] and LIMIT in error["message"]
    assert "Capture it on Rule figures (FY 2025-26)." in error["message"]
    assert "rulepack_update" not in error["message"]
    alone = sample.post(
        "/api/tax/estimate", json={"financial_year": FY, "dependants": 0, "member_ids": [JORDAN]}
    )
    assert alone.status_code == 422 and "Sample work car kilometres" in alone.text


def without_the_fixture_capture(client: TestClient) -> None:
    """The sample household carries one invented FY 2026-27 car rate; these tests swap in their own
    pack for that year, so take the capture away first."""
    year = client.get("/api/rule-figures", params={"financial_year": 2027}).json()
    (rate,) = [f for f in year["figures"] if f["key"] == RATE and f["capture"]]
    assert client.delete(f"/api/rule-figures/{rate['capture']['id']}").status_code == 200


def test_a_record_is_entered_edited_and_audited_through_the_register(sample: TestClient) -> None:
    use_pack(pack_with_figures(CENTS, MAX_KM))
    without_the_fixture_capture(sample)
    created = sample.post("/api/tax/records", json=a_body())
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["car"]["total_km"] == "471.9" and record["counted"] == "193.48"
    assert record["car"]["trips"][2]["total_km"] == "21.9"
    edited = sample.put(
        f"/api/tax/records/{record['id']}",
        json=a_body(trips=wire([*UNDER, trip("Invented extra", "100.0", 2, "2026-12-01")])),
    )
    assert edited.status_code == 200, edited.text
    # 471.9 + 200.0 = 671.9 km x 0.41 = 275.479 dollars = 27547.9 cents -> 27548
    assert edited.json()["car"]["total_km"] == "671.9" and edited.json()["counted"] == "275.48"
    assert edited.json()["car"]["trips"][3]["on"] == "2026-12-01"
    trail = sample.get(
        "/api/audit", params={"entity_type": "tax_record", "entity_id": record["id"]}
    ).json()["items"]
    assert [e["action"] for e in trail] == ["update", "create"]
    assert any(c["field"] == "Trips" for c in trail[0]["changes"])
    assert "671.9 km" in trail[0]["summary"]
    assert sample.delete(f"/api/tax/records/{record['id']}").status_code == 204


def test_a_trip_without_times_counts_once(sample: TestClient) -> None:
    use_pack(pack_with_figures(CENTS, MAX_KM))
    body = a_body(trips=[{"description": "Invented one-off", "km": "10"}])
    res = sample.post("/api/tax/records", json=body)
    assert res.status_code == 201, res.text
    assert res.json()["car"]["trips"][0]["times"] == 1
    assert res.json()["car"]["total_km"] == "10.0"


@pytest.mark.parametrize(
    ("changes", "fields"),
    [
        ({"trips": []}, {"trips"}),
        ({"trips": [trip("", "5")]}, {"trips.0.description"}),
        ({"trips": [trip("Invented", "0")]}, {"trips.0.km"}),
        ({"trips": [trip("Invented", "-1")]}, {"trips.0.km"}),
        ({"trips": [trip("Invented", "4.25")]}, {"trips.0.km"}),
        ({"trips": [trip("Invented", "x")]}, {"trips.0.km"}),
        ({"trips": [trip("Invented", "5", "0")]}, {"trips.0.times"}),
        ({"trips": [trip("Invented", "5", "1.5")]}, {"trips.0.times"}),
        ({"trips": [trip("Invented", "5", "2", "not a date")]}, {"trips.0.on"}),
        # a date outside the record's dates, on either side
        ({"trips": [trip("Invented", "5", "1", "2026-06-30")]}, {"trips.0.on"}),
        ({"trips": [trip("Invented", "5", "1", "2027-07-01")]}, {"trips.0.on"}),
        (
            {"trips": [trip("Invented", "5"), trip("", "0", "0", "1999-01-01")]},
            {
                "trips.1.description",
                "trips.1.km",
                "trips.1.times",
                "trips.1.on",
            },
        ),
        ({"label": "work_other"}, {"label"}),
        ({"label": "salary"}, {"label"}),
        ({"to_on": None}, {"to_on"}),
        ({"member_id": None}, {"member_id"}),
    ],
)
def test_a_record_with_the_wrong_fields_is_refused_field_by_field(
    sample: TestClient, changes: dict[str, Any], fields: set[str]
) -> None:
    res = sample.post("/api/tax/records", json=a_body(**changes))
    assert res.status_code == 422, res.text
    assert {e["field"] for e in res.json()["errors"]} == fields


def test_a_record_with_too_many_trips_is_refused(sample: TestClient) -> None:
    ok = sample.post(
        "/api/tax/records",
        json=a_body(trips=wire([trip(f"Invented {i}", "1.0") for i in range(car.MAX_TRIPS)])),
    )
    assert ok.status_code == 201, ok.text
    too_many = a_body(
        member_id=JORDAN,
        from_on="2027-07-01",  # FY 2027-28: Jordan's FY 2026-27 fixture record is in the way
        to_on="2028-06-30",
        trips=wire([trip(f"Invented {i}", "1.0") for i in range(car.MAX_TRIPS + 1)]),
    )
    res = sample.post("/api/tax/records", json=too_many)
    assert res.status_code == 422
    assert [e["field"] for e in res.json()["errors"]] == ["trips"]


def test_a_record_must_sit_within_one_financial_year(sample: TestClient) -> None:
    res = sample.post("/api/tax/records", json=a_body(from_on="2027-06-01", to_on="2027-07-31"))
    assert res.status_code == 422
    (error,) = res.json()["errors"]
    assert error["field"] == "to_on"
    assert "car kilometres record sits within one financial year" in error["message"]
    assert "1 July 2026 to 30 June 2027" in error["message"]


def test_a_second_record_for_the_same_member_and_year_is_refused(sample: TestClient) -> None:
    # Alex already has car kilometres for FY 2025-26 (the fixture)
    res = sample.post("/api/tax/records", json=a_body(from_on="2025-07-01", to_on="2025-12-31"))
    assert res.status_code == 422, res.text
    (error,) = res.json()["errors"]
    assert error["field"] == "member_id"
    assert error["message"] == (
        "This Member already has car kilometres for FY 2025-26: add trips to that record."
    )
    # another year, or another Member, is fine
    assert sample.post("/api/tax/records", json=a_body()).status_code == 201
    other = a_body(member_id=JORDAN, from_on="2024-07-01", to_on="2025-06-30")
    assert sample.post("/api/tax/records", json=other).status_code == 201


def test_an_edit_that_moves_the_dates_into_a_taken_year_is_refused(sample: TestClient) -> None:
    made = sample.post("/api/tax/records", json=a_body())  # FY 2026-27
    assert made.status_code == 201, made.text
    record_id = made.json()["id"]
    # saving it unchanged is not "a second record"
    assert sample.put(f"/api/tax/records/{record_id}", json=a_body()).status_code == 200
    moved = sample.put(
        f"/api/tax/records/{record_id}", json=a_body(from_on="2025-07-01", to_on="2026-06-30")
    )
    assert moved.status_code == 422
    (error,) = moved.json()["errors"]
    assert error["field"] == "member_id" and "FY 2025-26" in error["message"]
    # the fixture record itself can still be edited in its own year
    own = next(
        r
        for r in sample.get("/api/tax/records").json()
        if r["record_type"] == "car_kilometres" and r["member_id"] == ALEX and r["id"] != record_id
    )
    again = sample.put(
        f"/api/tax/records/{own['id']}",
        json=a_body(from_on="2025-07-01", to_on="2026-06-30", description=own["description"]),
    )
    assert again.status_code == 200, again.text


def test_a_record_in_a_year_no_pack_figure_covers_says_which_year_it_waits_for(
    sample: TestClient,
) -> None:
    use_pack(pack_with_figures(CENTS, MAX_KM, years=(FY,)))
    without_the_fixture_capture(sample)
    record = sample.post("/api/tax/records", json=a_body()).json()  # FY 2026-27
    assert record["car"]["missing"] == [RATE, LIMIT] and record["counted"] is None
    assert [w["financial_year_label"] for w in record["calculation"]["waiting_for"]] == [
        "2026-27",
        "2026-27",
    ]


# --- the trace: which figures it used, and the calculation replayed -----------------------------


def rendered(step: Any) -> str:
    """A step as one line: the operands joined by their operators, then the result."""
    parts: list[str] = []
    for o in step.operands:
        parts += ([o.operator] if o.operator else []) + [o.value + (f" {o.unit}" if o.unit else "")]
    res = step.result
    return " ".join(parts) + f" = {res.value}" + (f" {res.unit}" if res.unit else "")


def test_the_steps_for_trips_under_the_limit_are_exact() -> None:
    steps = car.calculation(a_record(UNDER), rules_for(FY))
    assert [s.title for s in steps] == ["Kilometres driven", "Deduction"]
    assert rendered(steps[0]) == "40.0 km × 10 + 12.5 km × 4 + 7.3 km × 3 = 471.9 km"
    assert [o.label for o in steps[0].operands if o.unit] == [
        "Invented client site, return",
        "Invented supplier, return",
        "Invented errand",
    ]
    assert rendered(steps[1]) == "471.9 km × 0.41 a km = 193.48"
    assert steps[1].note == "Rounded once, to the cent, for the whole record (not trip by trip)."


def test_a_trip_made_once_is_listed_without_a_times_term() -> None:
    record = a_record([trip("Invented a", "5.5"), trip("Invented b", "2", 3)])
    assert rendered(car.calculation(record, NO_RULES)[0]) == "5.5 km + 2.0 km × 3 = 11.5 km"


def test_the_steps_when_the_limit_applies_name_the_limit() -> None:
    steps = car.calculation(a_record(OVER), rules_for(FY))
    assert [s.title for s in steps] == [
        "Kilometres driven",
        "Limited to the 3,000 km limit",
        "Deduction",
    ]
    assert rendered(steps[0]) == "120.0 km × 20 + 85.5 km × 10 = 3255.0 km"
    assert rendered(steps[1]) == "3255.0 km = 3000.0 km"
    assert (
        steps[1].note == "The limit is for one car in one year: kilometres above it do not count."
    )
    assert rendered(steps[2]) == "3000.0 km × 0.41 a km = 1230.00"
    # exactly at the limit nothing is limited, so there is no limit step
    at = car.calculation(a_record([trip("Invented loop", "150.0", 20)]), rules_for(FY))
    assert [s.title for s in at] == ["Kilometres driven", "Deduction"]


def test_many_trips_are_summarised_but_few_are_listed_exactly() -> None:
    eight = a_record([trip(f"Invented {i}", "1.0", 2) for i in range(8)])
    assert rendered(car.calculation(eight, NO_RULES)[0]).count("×") == 8  # every row listed
    nine = a_record([trip(f"Invented {i}", "1.0", 2) for i in range(9)])
    (step,) = car.calculation(nine, NO_RULES)
    assert rendered(step) == "18.0 km = 18.0 km"
    assert [o.label for o in step.operands] == ["sum of 9 trips"]


def test_the_steps_wait_for_the_figures_they_need() -> None:
    record = a_record(OVER)
    only_limit = RecordRules(((LIMIT, FY, MAX_KM),))
    assert [s.title for s in car.calculation(record, only_limit)] == [
        "Kilometres driven",
        "Limited to the 3,000 km limit",
    ]
    only_rate = RecordRules(((RATE, FY, CENTS),))
    assert [s.title for s in car.calculation(record, only_rate)] == ["Kilometres driven"]
    assert [s.title for s in car.calculation(record, NO_RULES)] == ["Kilometres driven"]


def test_the_record_reads_both_figures_of_its_year() -> None:
    assert car.figures_read(a_record(UNDER)) == ((RATE, FY), (LIMIT, FY))
    later = a_record(UNDER, start=date(2026, 7, 1), end=date(2027, 6, 30))
    assert car.figures_read(later) == ((RATE, FY + 1), (LIMIT, FY + 1))


def test_the_record_page_names_both_figures_it_used_and_the_working(sample: TestClient) -> None:
    use_pack(pack_with_figures(CENTS, 800))
    record = fixture_record(sample)
    used = {u["key"]: u for u in record["figures_used"]}
    assert set(used) == {RATE, LIMIT}
    assert used[RATE]["name"] == "Car expenses: cents per kilometre rate"
    assert used[RATE]["value"] == "0.41 a km"
    assert used[LIMIT]["name"] == "Car expenses: kilometre limit per car"
    assert used[LIMIT]["value"] == "800 km"
    source = "ATO, Car expenses: cents per kilometre method"
    assert all(u["source"] == source for u in used.values())
    assert all(u["financial_year_label"] == "2025-26" for u in used.values())
    calc = record["calculation"]
    assert calc["waiting_for"] == []
    assert [s["title"] for s in calc["steps"]] == [
        "Kilometres driven",
        "Limited to the 800 km limit",
        "Deduction",
    ]
    assert calc["steps"][2]["result"]["value"] == "328.00"


def test_a_record_with_a_figure_missing_lists_it_as_waited_for_not_used(
    sample: TestClient,
) -> None:
    use_pack(pack_with_figures(CENTS, None))
    record = fixture_record(sample)
    assert [u["key"] for u in record["figures_used"]] == [RATE]
    assert [w["key"] for w in record["calculation"]["waiting_for"]] == [LIMIT]
    assert record["car"]["missing"] == [LIMIT] and record["counted"] is None
