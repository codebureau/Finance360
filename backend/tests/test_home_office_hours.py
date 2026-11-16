"""The ``home_office_hours`` Record type (ADR-0019 section 2).

Golden tests of the monthly worksheet against answers worked out by hand, Hypothesis properties for
the exact sum and the single rounding, and integration tests through the API against a real
temporary SQLite database. The rate per hour is **injected** (an invented figure, as a test input):
no official figure is held in the production code, and the shipped Rule pack leaves it uncaptured
until the owner captures it. Every amount here is invented.
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
from finance360.capabilities.tax.record_types import home_office_hours as hours
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
KEY = hours.RATE_KEY
FY = 2026


def aud(text: str) -> Money:
    return parse_amount(text, "AUD")


def rules_for(*years: int, cents: int = 41) -> RecordRules:
    return RecordRules(tuple((KEY, year, cents) for year in years))


def month(name: str, working: str, leave: str, office: str) -> dict[str, str]:
    return {"month": name, "working_days": working, "leave_days": leave, "office_days": office}


def a_sheet(
    per_day: str,
    rows: list[dict[str, str]],
    *,
    start: date = date(2025, 7, 1),
    end: date = date(2026, 6, 30),
    label: str = "business_expenses",
    record_id: int = 1,
) -> Record:
    value = hours.parse(
        {
            "label": label,
            "arrangement": "regular",
            "hours_per_day": per_day,
            "home_weekdays": ["mon", "tue", "thu", "fri"],
            "months": rows,
        }
    )
    return Record(record_id, ALEX, hours.KEY, start, end, value)


def hours_row(name: str, hours_worked: str) -> dict[str, str]:
    return {"month": name, "hours": hours_worked}


def a_casual_sheet(
    rows: list[dict[str, str]],
    *,
    start: date = date(2025, 7, 1),
    end: date = date(2026, 6, 30),
    label: str = "work_other",
    record_id: int = 1,
) -> Record:
    value = hours.parse({"label": label, "arrangement": "casual", "months": rows})
    return Record(record_id, JORDAN, hours.KEY, start, end, value)


# --- golden: a worksheet worked out by hand ----------------------------------------------------
#
# 7.50 hours a day, three months, at an invented 41 cents an hour:
#   July:      22 working - 1.5 leave - 4 office     = 16.5 home days x 7.5 = 123.75 hours
#   August:    21 working - 0 leave - 3.5 office     = 17.5 home days x 7.5 = 131.25 hours
#   September: 22 working - 2.25 leave - 1 office    = 18.75 home days x 7.5 = 140.625 hours
#   total 395.625 hours x 0.41 = 162.20625 dollars = 16220.625 cents -> 16221, so 162.21
THREE_MONTHS = [
    month("2025-07", "22", "1.5", "4"),
    month("2025-08", "21", "0", "3.5"),
    month("2025-09", "22", "2.25", "1"),
]


def test_a_worksheet_matches_the_hand_worked_answer() -> None:
    record = a_sheet("7.5", THREE_MONTHS)
    value: hours.HomeOfficeHours = record.value
    assert [hours.hours_text(hours.row_hours(r, value.hours_per_day)) for r in value.months] == [
        "123.75",
        "131.25",
        "140.625",
    ]
    assert [hours.hundredths_text(r.home) for r in value.months] == ["16.50", "17.50", "18.75"]
    assert hours.hours_text(hours.total_hours(value)) == "395.625"
    (only,) = hours.contributions(record, FY, rules_for(FY))
    assert (only.record_id, only.label, only.value) == (1, "business_expenses", aud("162.21"))


# A casual worksheet is the hours actually worked from home each month, at an invented 41 cents:
#   July 18.5 + August 22 + September 15.25 + October 9.75 = 65.5 hours
#   65.5 x 0.41 = 26.855 dollars = 2685.5 cents -> 2686 (half to even: 2685.5 goes to 2686), 26.86
def test_a_casual_worksheet_adds_the_hours_entered_and_rounds_once() -> None:
    rows = [
        hours_row("2025-07", "18.5"),
        hours_row("2025-08", "22"),
        hours_row("2025-09", "15.25"),
        hours_row("2025-10", "9.75"),
    ]
    record = a_casual_sheet(rows)
    value: hours.HomeOfficeHours = record.value
    assert value.arrangement == "casual" and value.hours_per_day is None
    assert [hours.hours_text(hours.row_hours(r, None)) for r in value.months] == [
        "18.50",
        "22.00",
        "15.25",
        "9.75",
    ]
    assert hours.hours_text(hours.total_hours(value)) == "65.50"
    (only,) = hours.contributions(record, FY, rules_for(FY))
    assert (only.record_id, only.label, only.value) == (1, "work_other", aud("26.86"))
    # an odd half: 0.5 hours x 41 = 20.5 cents -> 20 (half to even)
    half = a_casual_sheet([hours_row("2025-07", "0.5")])
    assert hours.contributions(half, FY, rules_for(FY))[0].value == aud("0.20")


# The monthly form of a regular arrangement keeps the office days a month, 2 here, beside the rows
# (the rows are the working; the figure only says how they were prefilled):
#   July: 23 working - 0 leave - 2 office = 21 home days x 7.6 = 159.6 hours; at 41 cents:
#   159.6 x 41 = 6543.6 cents -> 6544, so 65.44
def test_a_regular_worksheet_kept_as_office_days_a_month_works_out_from_its_rows() -> None:
    value = hours.parse(
        {
            "label": "business_expenses",
            "arrangement": "regular",
            "hours_per_day": "7.6",
            "office_days_per_month": "2",
            "months": [month("2025-07", "23", "0", "2")],
        }
    )
    assert value.office_days_per_month == 200 and value.home_weekdays is None
    assert hours.hours_text(hours.total_hours(value)) == "159.60"
    record = Record(1, ALEX, hours.KEY, date(2025, 7, 1), date(2026, 6, 30), value)
    assert hours.contributions(record, FY, rules_for(FY))[0].value == aud("65.44")
    assert hours.to_payload(value)["office_days_per_month"] == "2.00"


# Three rows of one home day at half an hour: 0.5 hours each, 1.5 hours in all, at 41 cents.
#   rounded once:  1.5 x 41 = 61.5 cents  -> 62 (half to even)
#   each row first: 0.5 x 41 = 20.5 -> 20, three times = 60: what the single rounding avoids.
def test_the_deduction_is_rounded_once_for_the_record_not_per_row() -> None:
    rows = [month("2025-07", "1", "0", "0"), month("2025-08", "1", "0", "0")]
    rows.append(month("2025-09", "1", "0", "0"))
    record = a_sheet("0.5", rows)
    (only,) = hours.contributions(record, FY, rules_for(FY))
    assert only.value == aud("0.62")
    # a half goes to even the other way too: 2.5 hours x 41 = 102.5 cents -> 102
    record = a_sheet("2.5", [month("2025-07", "1", "0", "0")])
    assert hours.contributions(record, FY, rules_for(FY))[0].value == aud("1.02")


def test_a_record_counts_only_in_the_year_it_sits_in() -> None:
    record = a_sheet("7.5", THREE_MONTHS)
    for year in (FY - 1, FY + 1):
        assert hours.contributions(record, year, rules_for(year, FY)) == ()
    assert record_types.overlaps(record, FY) and not record_types.overlaps(record, FY + 1)
    # the figure of another year does not stand in for it
    assert hours.contributions(record, FY, rules_for(FY + 1)) == ()


def test_a_label_total_sums_every_record_feeding_it_and_drills_down_to_each() -> None:
    first = a_sheet("7.5", THREE_MONTHS, record_id=7)
    second = a_sheet("0.5", [month("2025-07", "1", "0", "0")] * 1, record_id=8)
    total = record_types.label_totals([first, second], FY, rules_for(FY))[
        (ALEX, "business_expenses")
    ]
    assert total.total == aud("162.21") + aud("0.20")  # 0.5 h x 41 = 20.5 cents -> 20
    assert [(c.record_id, c.value) for c in total.contributions] == [
        (7, aud("162.21")),
        (8, aud("0.20")),
    ]


# --- the rate is never guessed or skipped -----------------------------------------------------


def test_an_uncaptured_rate_is_reported_never_skipped_silently() -> None:
    record = a_sheet("7.5", THREE_MONTHS)
    assert hours.missing_figures(record, NO_RULES) == (KEY,)
    assert hours.contributions(record, FY, NO_RULES) == ()
    assert record_types.label_totals([record], FY, NO_RULES) == {}  # no number is invented
    assert record_types.unavailable([record], FY, NO_RULES) == ((record, (KEY,)),)
    assert hours.missing_figures(record, rules_for(FY - 1, FY + 1)) == (KEY,)
    assert hours.missing_figures(record, rules_for(FY)) == ()
    assert record_types.unavailable([record], FY + 1, NO_RULES) == ()  # not in that year at all


def test_the_rate_changes_the_answer_so_it_is_read_from_the_rules() -> None:
    record = a_sheet("8", [month("2025-07", "10", "0", "0")])  # 80 hours
    assert hours.contributions(record, FY, rules_for(FY, cents=50))[0].value == aud("40.00")
    assert hours.contributions(record, FY, rules_for(FY, cents=30))[0].value == aud("24.00")


def test_the_rate_is_declared_for_guided_capture_as_dollars_per_hour() -> None:
    from finance360.rulepacks.capture import FigureError, parse_figure

    spec = next(s for s in SPECS if s.key == KEY)
    assert spec.page_url.startswith("https://www.ato.gov.au/") and spec.where and spec.source
    assert parse_figure(spec, "0.40") == Money(40, "AUD")  # a typed rate; nothing is held in code
    assert parse_figure(spec, "0.40 per hour") == Money(40, "AUD")
    for typed in ("0.01", "40", "four", "0.40 per year"):  # out of range, off-unit or not a number
        with pytest.raises(FigureError):
            parse_figure(spec, typed)


# --- the payload ----------------------------------------------------------------------------

GOOD = {
    "label": "business_expenses",
    "hours_per_day": "7.6",
    "months": [month("2025-07", "20", "0", "0")],
}


def bad(**changes: Any) -> dict[str, Any]:
    return {**GOOD, **changes}


@pytest.mark.parametrize(
    "payload",
    [
        bad(label="salary"),
        bad(hours_per_day="0"),
        bad(hours_per_day="24.01"),
        bad(hours_per_day="7.555"),
        bad(hours_per_day="-1"),
        bad(hours_per_day="1e1"),
        bad(hours_per_day=7.6),
        bad(hours_per_day=None),
        bad(months=[]),
        bad(months=[month("2025-07", "20", "0", "0")] * 2),  # a month twice
        bad(months=[month("2025-13", "20", "0", "0")]),
        bad(months=[month("July", "20", "0", "0")]),
        bad(months=[month("2025-07", "20", "0", "0.005")]),
        bad(months=[month("2025-07", "-1", "0", "0")]),
        bad(months=[month("2025-07", "32", "0", "0")]),
        bad(months=[month("2025-07", "20", "15", "6")]),  # home days would be negative
        bad(
            months=[{"month": "2025-07", "working_days": 20, "leave_days": "0", "office_days": "0"}]
        ),
        bad(months="2025-07"),
    ],
)
def test_a_payload_that_does_not_fit_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(InputError):
        hours.parse(payload)


def test_the_payload_round_trips_in_canonical_text_with_no_float() -> None:
    value = hours.parse(
        bad(
            arrangement="regular",
            home_weekdays=["fri", "mon"],
            hours_per_day="7.60",
            months=[month("2025-07", "20.5", "1", "0.25")],
        )
    )
    payload = hours.to_payload(value)
    assert payload == {
        "label": "business_expenses",
        "arrangement": "regular",
        "hours_per_day": "7.60",
        "home_weekdays": ["mon", "fri"],  # in week order
        "months": [month("2025-07", "20.50", "1.00", "0.25")],
    }
    assert hours.parse(payload) == value
    casual = hours.parse(CASUAL_GOOD)
    assert hours.to_payload(casual) == {
        "label": "work_other",
        "arrangement": "casual",
        "months": [hours_row("2025-07", "18.50")],
    }
    assert hours.parse(hours.to_payload(casual)) == casual


def test_a_payload_stored_before_the_arrangement_existed_reads_as_regular_with_no_pattern() -> None:
    legacy = {
        "label": "business_expenses",
        "hours_per_day": "7.60",
        "months": [month("2025-07", "20.00", "0.00", "0.00")],
    }
    value = hours.parse(legacy)
    assert value.arrangement == "regular" and value.hours_per_day == 760
    assert value.home_weekdays is None and value.office_days_per_month is None
    assert hours.hours_text(hours.total_hours(value)) == "152.00"
    assert hours.to_payload(value)["arrangement"] == "regular"


CASUAL_GOOD = {
    "label": "work_other",
    "arrangement": "casual",
    "months": [hours_row("2025-07", "18.5")],
}


@pytest.mark.parametrize(
    "payload",
    [
        bad(arrangement="sometimes"),
        bad(arrangement=None),
        bad(arrangement="regular", home_weekdays=[]),
        bad(arrangement="regular", home_weekdays=["mon", "sat"]),
        bad(arrangement="regular", home_weekdays=["mon", "mon"]),
        bad(arrangement="regular", home_weekdays="mon"),
        bad(arrangement="regular", home_weekdays=["mon"], office_days_per_month="2"),  # both
        bad(arrangement="regular", office_days_per_month="-1"),
        bad(arrangement="regular", office_days_per_month="32"),
        bad(arrangement="regular", office_days_per_month="1.555"),
        bad(arrangement="regular", home_weekdays=["mon"], months=[hours_row("2025-07", "5")]),
        {**CASUAL_GOOD, "hours_per_day": "7.6"},
        {**CASUAL_GOOD, "home_weekdays": ["mon"]},
        {**CASUAL_GOOD, "office_days_per_month": "2"},
        {**CASUAL_GOOD, "months": [month("2025-07", "20", "0", "0")]},  # the wrong row shape
        {**CASUAL_GOOD, "months": [hours_row("2025-07", "-1")]},
        {**CASUAL_GOOD, "months": [hours_row("2025-07", "744.01")]},
        {**CASUAL_GOOD, "months": [hours_row("2025-07", "1.555")]},
        {**CASUAL_GOOD, "months": [hours_row("2025-07", "")]},
        {**CASUAL_GOOD, "months": [hours_row("2025-07", "1"), hours_row("2025-07", "2")]},
        {**CASUAL_GOOD, "months": []},
    ],
)
def test_a_payload_with_the_wrong_arrangement_or_row_shape_is_rejected(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(InputError):
        hours.parse(payload)


# --- properties -------------------------------------------------------------------------------


@st.composite
def worksheets(draw: st.DrawFn) -> tuple[Record, int]:
    per_day = draw(st.integers(min_value=1, max_value=hours.MAX_HOURS_PER_DAY))
    count = draw(st.integers(min_value=1, max_value=12))
    names = draw(
        st.permutations(
            [f"2025-{m:02d}" for m in range(7, 13)] + [f"2026-{m:02d}" for m in range(1, 7)]
        )
    )
    rows: list[dict[str, str]] = []
    for name in names[:count]:
        working = draw(st.integers(min_value=0, max_value=hours.MAX_DAYS))
        leave = draw(st.integers(min_value=0, max_value=working))
        office = draw(st.integers(min_value=0, max_value=working - leave))
        rows.append(
            month(
                name,
                hours.hundredths_text(working),
                hours.hundredths_text(leave),
                hours.hundredths_text(office),
            )
        )
    cents = draw(st.integers(min_value=1, max_value=500))
    return a_sheet(hours.hundredths_text(per_day), rows), cents


@given(worksheets())
def test_the_total_is_the_exact_sum_of_the_rows_and_the_deduction_is_rounded_once(
    drawn: tuple[Record, int],
) -> None:
    record, cents = drawn
    value: hours.HomeOfficeHours = record.value
    assert value.hours_per_day is not None
    per_day = Fraction(value.hours_per_day, 100)
    exact_hours = sum(
        (Fraction(r.working - r.leave - r.office, 100) * per_day for r in value.months), Fraction(0)
    )
    assert all(r.home >= 0 for r in value.months)
    assert Fraction(hours.total_hours(value), 10_000) == exact_hours
    # the deduction is exactly hours x rate in cents, rounded half to even once, never negative
    expected = round(exact_hours * cents)
    rules = rules_for(FY, cents=cents)
    found = hours.contributions(record, FY, rules)
    amount = found[0].value if found else Money.zero("AUD")
    assert isinstance(amount, Money)
    assert amount.amount_minor == expected >= 0
    assert abs(Fraction(amount.amount_minor) - exact_hours * cents) <= Fraction(1, 2)
    assert hours.deduction(value, Money(cents, "AUD")).amount_minor == expected


@st.composite
def casual_sheets(draw: st.DrawFn) -> tuple[Record, list[int], int]:
    count = draw(st.integers(min_value=1, max_value=12))
    names = draw(
        st.permutations(
            [f"2025-{m:02d}" for m in range(7, 13)] + [f"2026-{m:02d}" for m in range(1, 7)]
        )
    )
    entered = [
        draw(st.integers(min_value=0, max_value=hours.MAX_MONTH_HOURS)) for _ in range(count)
    ]
    rows = [hours_row(n, hours.hundredths_text(h)) for n, h in zip(names, entered, strict=False)]
    cents = draw(st.integers(min_value=1, max_value=500))
    return a_casual_sheet(rows), entered, cents


@given(casual_sheets())
def test_a_casual_total_is_the_exact_sum_of_the_hours_entered_and_rounded_once(
    drawn: tuple[Record, list[int], int],
) -> None:
    record, entered, cents = drawn
    value: hours.HomeOfficeHours = record.value
    exact_hours = sum((Fraction(h, 100) for h in entered), Fraction(0))
    assert Fraction(hours.total_hours(value), 10_000) == exact_hours
    expected = round(exact_hours * cents)
    assert hours.deduction(value, Money(cents, "AUD")).amount_minor == expected >= 0
    found = hours.contributions(record, FY, rules_for(FY, cents=cents))
    amount = found[0].value if found else Money.zero("AUD")
    assert isinstance(amount, Money) and amount.amount_minor == expected


# --- through the API, against a real temporary SQLite database --------------------------------


def pack_with_rate(cents: int | None) -> rulepack.RulePack:
    """The shipped pack with the rate per hour replaced by an invented one (or removed)."""
    spec = next(s for s in SPECS if s.key == KEY)
    params = tuple(p for p in PACK.params if p.key != KEY)
    years = {fy: {k: v for k, v in held.items() if k != KEY} for fy, held in PACK.years.items()}
    if cents is not None:
        params = (*params, spec.to_param())
        years.setdefault(FY, {})[KEY] = Money(cents, "AUD")
    return dataclasses.replace(PACK, params=params, years=years)


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


def a_body(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "member_id": ALEX,
        "record_type": "home_office_hours",
        "label": "business_expenses",
        "from_on": "2025-07-01",
        "to_on": "2026-06-30",
        "description": "Invented home office hours",
        "arrangement": "regular",
        "hours_per_day": "7.5",
        "home_weekdays": ["mon", "tue", "thu", "fri"],
        "months": THREE_MONTHS,
    }
    base.update(changes)
    return base


FOUR_CASUAL_MONTHS = [
    hours_row("2025-07", "18.5"),
    hours_row("2025-08", "22"),
    hours_row("2025-09", "15.25"),
    hours_row("2025-10", "9.75"),
]


def a_casual_body(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "member_id": JORDAN,
        "record_type": "home_office_hours",
        "label": "work_other",
        "from_on": "2025-07-01",
        "to_on": "2025-10-31",
        "description": "Invented casual home office hours",
        "arrangement": "casual",
        "months": FOUR_CASUAL_MONTHS,
    }
    base.update(changes)
    return base


def fixture_record(client: TestClient, member: int = ALEX) -> dict[str, Any]:
    return next(
        r
        for r in client.get("/api/tax/records").json()
        if r["record_type"] == "home_office_hours" and r["member_id"] == member
    )


def test_the_shipped_pack_declares_the_rate_for_capture() -> None:
    assert KEY in {s.key for s in SPECS}
    assert KEY in pack_rules.RECORD_KEYS


def test_the_shipped_pack_works_out_the_fixture_worksheet(sample: TestClient) -> None:
    # Captured from the official page for FY 2025-26: 70 cents an hour.
    #
    # Alex, regular: 195 home days x 7.60 = 1,482.00 hours x 0.70 = 1,037.40
    sheet = fixture_record(sample)
    assert sheet["counted"] == "1037.40"
    assert sheet["home_office"]["missing"] == []
    assert sheet["home_office"]["rate_per_hour"] == "0.70"
    # Jordan, casual: 18.5 + 22 + 15.25 + 9.75 = 65.50 hours x 0.70 = 45.85
    casual = fixture_record(sample, JORDAN)
    assert casual["counted"] == "45.85" and casual["home_office"]["total_hours"] == "65.50"


def test_the_fixture_worksheet_is_in_the_register_and_says_a_figure_is_missing(
    sample: TestClient,
) -> None:
    use_pack(pack_with_rate(None))
    found = fixture_record(sample)
    assert found["label_key"] == "business_expenses" and found["member_id"] == ALEX
    assert found["counted"] is None  # the rate is missing, so nothing is worked out
    sheet = found["home_office"]
    assert (
        sheet["missing"] == [KEY] and sheet["rate_per_hour"] is None and sheet["deduction"] is None
    )
    assert sheet["hours_per_day"] == "7.60" and sheet["total_hours"] == "1482.00"
    assert sheet["arrangement"] == "regular"
    assert sheet["home_weekdays"] == ["mon", "tue", "thu", "fri"]
    assert sheet["office_days_per_month"] is None
    assert len(sheet["months"]) == 12
    july = sheet["months"][0]  # the calendar prefill: 23 weekdays, 5 Wednesdays
    assert (july["working_days"], july["office_days"]) == ("23.00", "5.00")
    assert (july["home_days"], july["hours"]) == ("18.00", "136.80")
    august = sheet["months"][1]  # 5 days of leave typed over the prefill
    assert (august["month"], august["month_label"]) == ("2025-08", "August 2025")
    assert (august["home_days"], august["hours"]) == ("12.00", "91.20")  # (21 - 5 - 4) x 7.6
    april = sheet["months"][9]  # an extra office day: 6 against the 5 Wednesdays
    assert (april["office_days"], april["home_days"], april["hours"]) == (
        "6.00",
        "16.00",
        "121.60",
    )
    casual = fixture_record(sample, JORDAN)["home_office"]
    assert casual["arrangement"] == "casual" and casual["hours_per_day"] is None
    assert casual["home_weekdays"] is None
    first = casual["months"][0]
    assert (first["month"], first["hours"], first["working_days"]) == ("2025-09", "18.50", None)
    assert [m["hours"] for m in casual["months"]] == ["18.50", "22.00", "15.25", "9.75"]


def test_the_label_totals_name_what_they_leave_out_and_an_estimate_will_not_skip_it(
    sample: TestClient,
) -> None:
    use_pack(pack_with_rate(None))
    res = sample.get("/api/tax/label-totals", params={"financial_year": FY}).json()
    assert {u["description"] for u in res["uncounted"]} == {
        "Sample home office hours, FY 2025-26",
        "Sample casual home office hours, Sep to Dec 2025",
    }
    assert all(u["missing"] == [KEY] and "FY 2025-26" in u["note"] for u in res["uncounted"])
    estimate = sample.post(
        "/api/tax/estimate",
        json={"financial_year": FY, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert estimate.status_code == 422
    (error,) = estimate.json()["errors"]
    assert error["field"] == "financial_year"
    assert "Sample home office hours" in error["message"] and KEY in error["message"]
    assert "Capture it on Rule figures (FY 2025-26)." in error["message"]
    assert "rulepack_update" not in error["message"]
    alone = sample.post(
        "/api/tax/estimate", json={"financial_year": FY, "dependants": 0, "member_ids": [JORDAN]}
    )
    assert alone.status_code == 422 and "casual home office hours" in alone.text


def test_with_the_rate_the_worksheet_the_label_total_and_the_estimate_agree(
    sample: TestClient,
) -> None:
    # an invented rate: Alex's 1,482.00 hours x 0.41 = 607.62 dollars; Jordan's 65.50 hours x 0.41
    # = 26.855 dollars = 2685.5 cents -> 2686 (half to even), 26.86
    use_pack(pack_with_rate(41))
    record = fixture_record(sample)
    sheet = record["home_office"]
    assert sheet["missing"] == [] and sheet["rate_per_hour"] == "0.41"
    assert sheet["deduction"] == "607.62" and record["counted"] == "607.62"
    assert fixture_record(sample, JORDAN)["counted"] == "26.86"
    totals = sample.get("/api/tax/label-totals", params={"financial_year": FY}).json()
    assert totals["uncounted"] == []
    expenses = next(t for t in totals["totals"] if t["label_key"] == "business_expenses")
    # the camera (495.89), the worksheet and the car (849.55 at the shipped 0.88 a km), each
    # drilling down to its record
    assert expenses["amount"] == "1953.06"
    assert (record["id"], "607.62") in [
        (c["record_id"], c["amount"]) for c in expenses["contributions"]
    ]
    estimate = sample.post(
        "/api/tax/estimate",
        json={"financial_year": FY, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert estimate.status_code == 200, estimate.text
    # 120,000.00 salary + 412.37 interest + (6,000.00 business income - 495.89 - 607.62 - 849.55)
    assert (
        estimate.json()["members"][0]["total_income"] == "236080.00"
    )  # each label drops its cents
    other_year = sample.get("/api/tax/label-totals", params={"financial_year": FY + 1}).json()
    assert all(
        t["label_key"] != "business_expenses" or t["amount"] != "607.62"
        for t in other_year["totals"]
    )


def test_a_worksheet_is_entered_edited_and_audited_through_the_register(sample: TestClient) -> None:
    use_pack(pack_with_rate(41))
    created = sample.post("/api/tax/records", json=a_body())
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["home_office"]["total_hours"] == "395.625"
    assert record["home_office"]["deduction"] == "162.21" and record["counted"] == "162.21"
    assert record["home_office"]["months"][2]["hours"] == "140.625"
    edited = sample.put(f"/api/tax/records/{record['id']}", json=a_body(hours_per_day="8"))
    assert edited.status_code == 200
    assert edited.json()["home_office"]["total_hours"] == "422.00"
    trail = sample.get(
        "/api/audit", params={"entity_type": "tax_record", "entity_id": record["id"]}
    ).json()["items"]
    assert [e["action"] for e in trail] == ["update", "create"]
    changed = {c["field"]: (c["before"], c["after"]) for c in trail[0]["changes"]}
    assert changed["Hours per day"] == ("7.50", "8.00")
    assert sample.delete(f"/api/tax/records/{record['id']}").status_code == 204


def test_a_casual_record_is_entered_edited_and_audited_through_the_register(
    sample: TestClient,
) -> None:
    use_pack(pack_with_rate(41))
    created = sample.post("/api/tax/records", json=a_casual_body())
    assert created.status_code == 201, created.text
    record = created.json()
    sheet = record["home_office"]
    assert sheet["arrangement"] == "casual" and sheet["hours_per_day"] is None
    assert sheet["home_weekdays"] is None and sheet["office_days_per_month"] is None
    assert [m["hours"] for m in sheet["months"]] == ["18.50", "22.00", "15.25", "9.75"]
    assert sheet["total_hours"] == "65.50"
    # 65.50 hours x 0.41 = 2685.5 cents -> 2686 (half to even)
    assert sheet["deduction"] == "26.86" and record["counted"] == "26.86"
    edited = sample.put(
        f"/api/tax/records/{record['id']}",
        json=a_casual_body(months=[*FOUR_CASUAL_MONTHS[:3], hours_row("2025-10", "10.25")]),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["home_office"]["total_hours"] == "66.00"  # 18.5 + 22 + 15.25 + 10.25
    trail = sample.get(
        "/api/audit", params={"entity_type": "tax_record", "entity_id": record["id"]}
    ).json()["items"]
    assert [e["action"] for e in trail] == ["update", "create"]
    changed = {c["field"] for c in trail[0]["changes"]}
    assert "Months" in changed or any("onth" in f for f in changed)
    assert sample.delete(f"/api/tax/records/{record['id']}").status_code == 204


def test_a_regular_record_kept_as_office_days_a_month_is_stored_and_shown(
    sample: TestClient,
) -> None:
    body = a_body(home_weekdays=None, office_days_per_month="2")
    res = sample.post("/api/tax/records", json=body)
    assert res.status_code == 201, res.text
    sheet = res.json()["home_office"]
    assert sheet["office_days_per_month"] == "2.00" and sheet["home_weekdays"] is None


@pytest.mark.parametrize(
    ("changes", "fields"),
    [
        ({"arrangement": None}, {"arrangement"}),
        ({"arrangement": "sometimes"}, {"arrangement"}),
        # a regular record needs exactly one pattern
        ({"home_weekdays": None}, {"home_weekdays"}),
        ({"home_weekdays": [], "office_days_per_month": ""}, {"home_weekdays"}),
        ({"home_weekdays": ["mon", "sat"]}, {"home_weekdays"}),
        ({"office_days_per_month": "2"}, {"home_weekdays"}),  # both
        ({"home_weekdays": None, "office_days_per_month": "x"}, {"office_days_per_month"}),
        ({"home_weekdays": None, "office_days_per_month": "40"}, {"office_days_per_month"}),
        ({"hours_per_day": ""}, {"hours_per_day"}),
        (
            {"months": [{**THREE_MONTHS[0], "hours": "5"}]},
            {"months.0.hours"},
        ),
    ],
)
def test_a_regular_record_with_the_wrong_arrangement_fields_is_refused_field_by_field(
    sample: TestClient, changes: dict[str, Any], fields: set[str]
) -> None:
    res = sample.post("/api/tax/records", json=a_body(**changes))
    assert res.status_code == 422, res.text
    assert {e["field"] for e in res.json()["errors"]} == fields


@pytest.mark.parametrize(
    ("changes", "fields"),
    [
        ({"hours_per_day": "7.6"}, {"hours_per_day"}),
        ({"home_weekdays": ["mon"]}, {"home_weekdays"}),
        ({"office_days_per_month": "2"}, {"office_days_per_month"}),
        (
            {"months": [month("2025-07", "20", "0", "0")]},
            {
                "months.0.hours",
                "months.0.working_days",
                "months.0.leave_days",
                "months.0.office_days",
            },
        ),
        ({"months": [hours_row("2025-07", "-3")]}, {"months.0.hours"}),
        ({"months": [hours_row("2025-07", "")]}, {"months.0.hours"}),
        ({"months": [hours_row("2025-07", "1.555"), hours_row("2025-08", "x")]}, None),
    ],
)
def test_a_casual_record_with_the_wrong_fields_is_refused_field_by_field(
    sample: TestClient, changes: dict[str, Any], fields: set[str] | None
) -> None:
    res = sample.post("/api/tax/records", json=a_casual_body(**changes))
    assert res.status_code == 422, res.text
    found = {e["field"] for e in res.json()["errors"]}
    assert found == (fields if fields is not None else {"months.0.hours", "months.1.hours"})


def test_a_stored_record_without_an_arrangement_still_shows_as_regular(sample: TestClient) -> None:
    """A payload written before the arrangement existed is JSON on ``tax_record``: no migration."""
    import json

    from finance360.core.models.tax_record import TaxRecord

    record_id = fixture_record(sample)["id"]
    legacy = {
        "label": "business_expenses",
        "hours_per_day": "7.60",
        "months": [month("2025-07", "20.00", "1.00", "2.00")],
    }
    with sample.app.state.session_factory.begin() as session:  # type: ignore[attr-defined]
        row = session.get(TaxRecord, record_id)
        assert row is not None
        row.payload_json = json.dumps(legacy, sort_keys=True, separators=(",", ":"))
    shown = sample.get(f"/api/tax/records/{record_id}").json()["home_office"]
    assert shown["arrangement"] == "regular" and shown["hours_per_day"] == "7.60"
    assert shown["home_weekdays"] is None and shown["office_days_per_month"] is None
    assert shown["total_hours"] == "129.20"  # (20 - 1 - 2) = 17 home days x 7.6
    # the next save asks for the pattern the old record never had
    body = a_body(home_weekdays=None, months=legacy["months"])
    res = sample.put(f"/api/tax/records/{record_id}", json=body)
    assert [e["field"] for e in res.json()["errors"]] == ["home_weekdays"]


def test_blank_leave_and_office_days_are_none_but_working_days_are_required(
    sample: TestClient,
) -> None:
    blank = {"month": "2025-07", "working_days": "10", "leave_days": "", "office_days": ""}
    res = sample.post("/api/tax/records", json=a_body(months=[blank]))
    assert res.status_code == 201, res.text
    assert res.json()["home_office"]["months"][0]["home_days"] == "10.00"
    res = sample.post("/api/tax/records", json=a_body(months=[{**blank, "working_days": ""}]))
    assert [e["field"] for e in res.json()["errors"]] == ["months.0.working_days"]


def test_a_record_across_a_year_boundary_is_refused_so_the_household_enters_one_per_year(
    sample: TestClient,
) -> None:
    res = sample.post(
        "/api/tax/records",
        json=a_body(
            from_on="2025-06-01", to_on="2025-07-31", months=[month("2025-06", "10", "0", "0")]
        ),
    )
    assert res.status_code == 422
    (error,) = res.json()["errors"]
    assert error["field"] == "to_on" and "one financial year" in error["message"]
    assert "1 July 2024 to 30 June 2025" in error["message"]
    # one record per year is accepted
    assert (
        sample.post(
            "/api/tax/records",
            json=a_body(
                from_on="2025-06-01",
                to_on="2025-06-30",
                months=[month("2025-06", "10", "0", "0")],
            ),
        ).status_code
        == 201
    )


def test_every_problem_with_a_worksheet_is_collected_against_its_own_field(
    sample: TestClient,
) -> None:
    res = sample.post(
        "/api/tax/records",
        json=a_body(
            hours_per_day="0",
            to_on=None,
            months=[
                month("2025-07", "10", "8", "3"),  # more leave and office than working days
                month("2024-12", "10", "0", "0"),  # before the record's dates
                month("2025-07", "10", "0", "0"),  # July again
                month("2025-08", "ten", "0", "0"),
            ],
        ),
    )
    assert res.status_code == 422
    fields = {e["field"]: e["message"] for e in res.json()["errors"]}
    assert set(fields) == {
        "to_on",
        "hours_per_day",
        "months.0.home_days",
        "months.1.month",
        "months.3.working_days",
    }
    assert "July 2025" in fields["months.0.home_days"]
    assert "outside" in fields["months.1.month"]


def test_a_month_outside_the_dates_and_a_repeated_month_are_refused(sample: TestClient) -> None:
    res = sample.post(
        "/api/tax/records",
        json=a_body(
            from_on="2025-09-01",
            to_on="2025-11-30",
            months=[
                month("2025-08", "10", "0", "0"),
                month("2025-12", "10", "0", "0"),
                month("2025-10", "10", "0", "0"),
                month("2025-10", "10", "0", "0"),
            ],
        ),
    )
    assert {e["field"] for e in res.json()["errors"]} == {
        "months.0.month",
        "months.1.month",
        "months.3.month",
    }
    res = sample.post("/api/tax/records", json=a_body(label="salary"))
    assert [e["field"] for e in res.json()["errors"]] == ["label"]
    res = sample.post("/api/tax/records", json=a_body(months=None))
    assert [e["field"] for e in res.json()["errors"]] == ["months"]


def test_negative_home_days_are_refused_naming_the_month(sample: TestClient) -> None:
    res = sample.post(
        "/api/tax/records",
        json=a_body(months=[month("2025-07", "5", "3", "3")]),
    )
    assert res.status_code == 422
    (error,) = res.json()["errors"]
    assert error["field"] == "months.0.home_days"
    assert "July 2025" in error["message"] and "more than the working days" in error["message"]
