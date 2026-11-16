"""The ``depreciating_asset`` Record type (ADR-0019 section 2).

Golden tests of the schedule against answers worked out by hand, a Hypothesis property for the
invariants, and integration tests through the API against a real temporary SQLite database. The
diminishing value percent is **injected** (200, as a test input): no official figure is held in the
production code, and the shipped Rule pack leaves it uncaptured. Every amount is invented.
"""

from __future__ import annotations

import dataclasses
import shutil
from collections.abc import Iterator
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tax_support import invented_pack

from finance360.capabilities.tax import pack_rules, record_types
from finance360.capabilities.tax.input import InputError
from finance360.capabilities.tax.record_types import depreciating_asset as asset
from finance360.capabilities.tax.record_types.base import NO_RULES, Record, RecordRules
from finance360.config import Settings
from finance360.core import rulepack
from finance360.core.money import Money, parse_amount, scale
from finance360.core.rate import PPM, Rate
from finance360.main import create_app
from finance360.rulepacks.au import PACK
from finance360.rulepacks.au.tax_specs import SPECS
from support import TestClient, owner_client, without_home_office_hours

REPO = Path(__file__).resolve().parents[2]
ALEX, JORDAN = 1, 2
KEY = asset.DV_PERCENT_KEY


def aud(text: str) -> Money:
    return parse_amount(text, "AUD")


def rules_for(*years: int, percent: int = 200) -> RecordRules:
    return RecordRules(tuple((KEY, year, percent) for year in years))


def an_asset(
    cost: str,
    life: str,
    start: date,
    *,
    ppm: int = PPM,
    claim_in_full: bool = False,
    to_on: date | None = None,
    label: str = "business_expenses",
    record_id: int = 1,
) -> Record:
    value = asset.parse(
        {
            "label": label,
            "cost": {"minor": aud(cost).amount_minor, "ccy": "AUD"},
            "effective_life_years": life,
            "work_use_ppm": ppm,
            "claim_in_full": claim_in_full,
        }
    )
    return Record(record_id, ALEX, asset.KEY, start, to_on, value)


def rows(record: Record, percent: int = 200, through: int = 2035) -> list[tuple[Any, ...]]:
    return [
        (
            r.financial_year,
            r.days,
            r.opening.amount_minor,
            r.decline.amount_minor,
            r.deduction.amount_minor,
            r.closing.amount_minor,
        )
        for r in asset.schedule(record, percent, through)
    ]


# --- golden: each year of an asset's life, worked out by hand ----------------------------------
#
# 2,000.00 on a 4-year life at 200%: 50% a year of the opening value. Started 1 January 2026, so
# FY 2025-26 holds it for 181 days (Jan 31, Feb 28, Mar 31, Apr 30, May 31, Jun 30).
#   FY2026: 2000.00 x 181/365 x 0.5 = 495.8904 -> 495.89;            closing 1504.11
#   FY2027: 1504.11 x 365/365 x 0.5 = 752.055   -> 752.06 (a half goes to even); closing 752.05
#   FY2028: 752.05 x 366/365 x 0.5 (a leap year, 29 Feb 2028) = 377.0552 -> 377.06; closing 374.99


def test_a_mid_year_start_over_three_years_matches_the_hand_worked_answers() -> None:
    record = an_asset("2000.00", "4", date(2026, 1, 1))
    assert rows(record, through=2028) == [
        (2026, 181, 200_000, 49_589, 49_589, 150_411),
        (2027, 365, 150_411, 75_206, 75_206, 75_205),
        (2028, 366, 75_205, 37_706, 37_706, 37_499),
    ]


# 1,000.00, 5 years (40% a year), 60% for work, a full first year (1 July 2025):
#   FY2026: decline 400.00; deduction 400.00 x 60% = 240.00;           closing 600.00
#   FY2027: decline 240.00; deduction 144.00;                          closing 360.00
#   FY2028 (366 days): decline 360.00 x 366/365 x 0.4 = 144.3945 -> 144.39; deduction
#          360.00 x 366/365 x 0.4 x 0.6 = 86.6367 -> 86.64;            closing 215.61
def test_a_work_use_share_below_100_percent_cuts_the_deduction_not_the_adjustable_value() -> None:
    record = an_asset("1000.00", "5", date(2025, 7, 1), ppm=600_000)
    assert rows(record, through=2028) == [
        (2026, 365, 100_000, 40_000, 24_000, 60_000),
        (2027, 365, 60_000, 24_000, 14_400, 36_000),
        (2028, 366, 36_000, 14_439, 8_664, 21_561),
    ]


def test_a_part_year_effective_life_is_exact() -> None:
    # 1,200.00 over 2.5 years at 200% is 80% a year: 960.00 off in a full first year.
    record = an_asset("1200.00", "2.5", date(2025, 7, 1))
    first = asset.schedule(record, 200, 2026)[0]
    assert (first.decline, first.closing) == (aud("960.00"), aud("240.00"))


# 800.00, 50% for work, claimed in full: 400.00 in the year of the start date, nothing after.
def test_claim_in_full_deducts_the_work_use_share_in_the_start_year_only() -> None:
    record = an_asset("800.00", "10", date(2025, 8, 20), ppm=500_000, claim_in_full=True)
    assert rows(record) == [(2026, 0, 80_000, 80_000, 40_000, 0)]
    in_year = record_types.label_totals([record], 2026, NO_RULES)  # needs no pack figure
    assert in_year[(ALEX, "business_expenses")].total == aud("400.00")
    assert record_types.label_totals([record], 2027, NO_RULES) == {}
    assert record_types.label_totals([record], 2025, NO_RULES) == {}


# The same 2,000.00 / 4 year asset, last used 31 March 2027.
#   FY2027 holds it for 274 days (1 Jul 2026 to 31 Mar 2027): 1504.11 x 274/365 x 0.5 =
#   564.5560 -> 564.56; closing 939.55. It counts in nothing after.
def test_an_asset_counts_only_in_the_years_it_overlaps() -> None:
    record = an_asset("2000.00", "4", date(2026, 1, 1), to_on=date(2027, 3, 31))
    assert rows(record) == [
        (2026, 181, 200_000, 49_589, 49_589, 150_411),
        (2027, 274, 150_411, 56_456, 56_456, 93_955),
    ]
    rules = rules_for(2026)
    by_year = {fy: record_types.label_totals([record], fy, rules) for fy in range(2024, 2031)}
    assert by_year[2025] == {}  # before it started
    assert by_year[2026][(ALEX, "business_expenses")].total == aud("495.89")
    assert by_year[2027][(ALEX, "business_expenses")].total == aud("564.56")
    assert by_year[2028] == {}  # after the last day it was used


def test_an_open_ended_asset_overlaps_every_year_from_its_start() -> None:
    record = an_asset("2000.00", "4", date(2026, 1, 1))
    assert not record_types.overlaps(record, 2025)
    assert all(record_types.overlaps(record, fy) for fy in (2026, 2027, 2060))


def test_a_label_total_sums_every_record_feeding_it_and_drills_down_to_each() -> None:
    camera = an_asset("2000.00", "4", date(2026, 1, 1), record_id=7)
    chair = an_asset("900.00", "5", date(2025, 7, 1), claim_in_full=True, record_id=8)
    total = record_types.label_totals([camera, chair], 2026, rules_for(2026))[
        (ALEX, "business_expenses")
    ]
    assert total.total == aud("495.89") + aud("900.00")
    assert [(c.record_id, c.value) for c in total.contributions] == [
        (7, aud("495.89")),
        (8, aud("900.00")),
    ]


# --- the multiplier is never guessed or skipped ----------------------------------------------


def test_an_uncaptured_multiplier_is_reported_never_skipped_silently() -> None:
    record = an_asset("2000.00", "4", date(2026, 1, 1))
    assert asset.missing_figures(record, NO_RULES) == (KEY,)
    assert asset.contributions(record, 2026, NO_RULES) == ()
    assert record_types.label_totals([record], 2026, NO_RULES) == {}  # no number is invented
    assert record_types.unavailable([record], 2026, NO_RULES) == ((record, (KEY,)),)
    # Only the start year's figure counts: another year's does not stand in for it.
    assert asset.missing_figures(record, rules_for(2025, 2027)) == (KEY,)
    assert asset.missing_figures(record, rules_for(2026)) == ()
    assert record_types.unavailable([record], 2025, NO_RULES) == ()  # not in that year at all


def test_the_multiplier_changes_the_answer_so_it_is_read_from_the_rules() -> None:
    record = an_asset("1000.00", "5", date(2025, 7, 1))  # started in FY2026
    (at_200,) = asset.contributions(record, 2026, rules_for(2026, percent=200))
    (at_150,) = asset.contributions(record, 2026, rules_for(2026, percent=150))
    assert at_200.value == aud("400.00")  # 1000.00 x 200% / 5
    assert at_150.value == aud("300.00")  # 1000.00 x 150% / 5


def test_the_multiplier_is_declared_for_guided_capture_as_a_whole_percent() -> None:
    from finance360.rulepacks.capture import FigureError, parse_figure

    spec = next(s for s in SPECS if s.key == KEY)
    assert spec.page_url.startswith("https://www.ato.gov.au/") and spec.where and spec.source
    assert parse_figure(spec, "200") == 200  # a typed whole percent, nothing is held in code
    for typed in ("2", "2.5", "two hundred", "2000"):  # off-unit or out of range
        with pytest.raises(FigureError):
            parse_figure(spec, typed)


# --- the payload ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"label": "salary", "cost": {"minor": 100, "ccy": "AUD"}, "effective_life_years": "4"},
        {"label": "work_other", "cost": {"minor": 0, "ccy": "AUD"}, "effective_life_years": "4"},
        {"label": "work_other", "cost": {"minor": 100, "ccy": "USD"}, "effective_life_years": "4"},
        {"label": "work_other", "cost": {"minor": 100, "ccy": "AUD"}},
        {"label": "work_other", "cost": {"minor": 100, "ccy": "AUD"}, "effective_life_years": 4},
        {"label": "work_other", "cost": {"minor": 100, "ccy": "AUD"}, "effective_life_years": "0"},
        {
            "label": "work_other",
            "cost": {"minor": 100, "ccy": "AUD"},
            "effective_life_years": "101",
        },
        {
            "label": "work_other",
            "cost": {"minor": 100, "ccy": "AUD"},
            "effective_life_years": "4.123",
        },
        {
            "label": "work_other",
            "cost": {"minor": 100, "ccy": "AUD"},
            "effective_life_years": "4",
            "work_use_ppm": 0,
        },
        {
            "label": "work_other",
            "cost": {"minor": 100, "ccy": "AUD"},
            "effective_life_years": "4",
            "claim_in_full": "yes",
        },
        {
            "label": "work_other",
            "cost": {"minor": 100.5, "ccy": "AUD"},
            "effective_life_years": "4",
        },
    ],
)
def test_a_payload_that_does_not_fit_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(InputError):
        asset.parse(payload)


def test_the_payload_round_trips_with_no_float() -> None:
    value = asset.parse(
        {
            "label": "work_other",
            "cost": {"minor": 123_456, "ccy": "AUD"},
            "effective_life_years": "2.50",
            "work_use_ppm": 333_333,
        }
    )
    payload = asset.to_payload(value)
    assert payload["effective_life_years"] == "2.5"
    assert asset.parse(payload) == value
    assert not any(isinstance(v, float) for v in payload.values())


# --- property: the invariants -----------------------------------------------------------------


@st.composite
def assets(draw: st.DrawFn) -> tuple[Record, int]:
    cost = draw(st.integers(min_value=1, max_value=10**10))
    life = draw(st.integers(min_value=100, max_value=10_000))  # hundredths of a year
    ppm = draw(st.integers(min_value=1, max_value=PPM))
    start = date(2020, 1, 1) + timedelta(days=draw(st.integers(0, 3000)))
    ended = draw(st.one_of(st.none(), st.integers(0, 4000)))
    to_on = None if ended is None else start + timedelta(days=ended)
    percent = draw(st.integers(min_value=100, max_value=300))
    value = asset.DepreciatingAsset(
        "business_expenses",
        Money(cost, "AUD"),
        Fraction(life, 100),
        Rate(ppm),
        draw(st.booleans()),
    )
    return Record(1, ALEX, asset.KEY, start, to_on, value), percent


@given(assets())
def test_deductions_never_exceed_the_work_use_share_of_the_cost_and_value_never_goes_negative(
    drawn: tuple[Record, int],
) -> None:
    record, percent = drawn
    value: asset.DepreciatingAsset = record.value
    allowed = scale(value.cost, value.work_use.ppm, PPM)
    schedule = asset.schedule(record, percent, 2100)
    claimed = Money.zero("AUD")
    previous = value.cost
    for row in schedule:
        assert row.opening == previous
        assert 0 <= row.decline.amount_minor <= row.opening.amount_minor
        assert row.closing == row.opening - row.decline
        assert row.closing.amount_minor >= 0
        assert row.deduction.amount_minor >= 0
        claimed = claimed + row.deduction
        assert claimed <= allowed
        previous = row.closing
    if value.claim_in_full:
        assert [r.deduction for r in schedule] == [allowed]


# --- through the API, against a real temporary SQLite database --------------------------------


def pack_with_multiplier(*years: int, percent: int = 200) -> rulepack.RulePack:
    """The invented pack with an invented multiplier held for ``years`` (not an official figure)."""
    base = invented_pack()
    spec = next(s for s in SPECS if s.key == KEY)
    held = {
        fy: {**values, **({KEY: percent} if fy in years else {})}
        for fy, values in {**{y: {} for y in years}, **base.years}.items()
    }
    return dataclasses.replace(base, params=(*base.params, spec.to_param()), years=held)


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


def use_pack(pack: rulepack.RulePack) -> None:
    rulepack.unregister("au")
    rulepack.register(pack)


def an_asset_body(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "member_id": ALEX,
        "record_type": "depreciating_asset",
        "label": "business_expenses",
        "amount": "1000.00",
        "effective_life_years": "5",
        "from_on": "2025-07-01",
        "description": "Invented printer",
    }
    base.update(changes)
    return base


def test_the_shipped_pack_holds_the_captured_multiplier() -> None:
    # Captured from the official page with scripts/rulepack_update.py, for FY 2025-26 only.
    held = pack_rules.record_rules(PACK)
    assert (held.figure(KEY, 2026), held.figure(KEY, 2025)) == (200, None)
    assert KEY in {s.key for s in SPECS}


def test_the_fixture_assets_are_in_the_register_and_say_a_figure_is_missing(
    sample: TestClient,
) -> None:
    use_pack(invented_pack())  # no multiplier for any year
    found = {
        r["description"]: r
        for r in sample.get("/api/tax/records", params={"financial_year": 2026}).json()
        if r["record_type"] == "depreciating_asset"
    }
    camera = found["Sample camera, diminishing value"]
    assert camera["label_key"] == "business_expenses" and camera["amount"] == "2000.00"
    assert camera["counted"] is None  # the figure is missing, so nothing is worked out
    assert camera["asset"]["missing"] == [KEY] and camera["asset"]["schedule"] == []
    desk = found["Sample standing desk, claimed in full"]
    assert desk["counted"] == "400.00" and desk["work_use_percent"] == "50"
    assert desk["asset"]["claim_in_full"] is True
    assert [(y["financial_year"], y["deduction"]) for y in desk["asset"]["schedule"]] == [
        (2026, "400.00")
    ]


def test_the_label_totals_name_what_they_leave_out_and_an_estimate_will_not_skip_it(
    sample: TestClient,
) -> None:
    without_home_office_hours(sample)  # the invented pack has no rate per hour
    use_pack(invented_pack())  # no multiplier for any year
    res = sample.get("/api/tax/label-totals", params={"financial_year": 2026}).json()
    keys = {(t["member_id"], t["label_key"]) for t in res["totals"]}
    assert (JORDAN, "work_other") in keys  # the desk, claimed in full, needs no figure
    assert (ALEX, "business_expenses") not in keys
    (left_out,) = res["uncounted"]
    assert left_out["description"] == "Sample camera, diminishing value"
    assert left_out["missing"] == [KEY] and "FY 2025-26" in left_out["note"]

    use_pack(invented_pack(2026))
    estimate = sample.post(
        "/api/tax/estimate",
        json={"financial_year": 2026, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert estimate.status_code == 422
    (error,) = estimate.json()["errors"]
    assert error["field"] == "financial_year"
    assert "Sample camera" in error["message"] and KEY in error["message"]
    assert "Capture it on Rule figures (FY 2025-26)." in error["message"]
    assert "rulepack_update" not in error["message"]
    # Jordan alone has nothing missing, so Jordan's estimate calculates.
    alone = sample.post(
        "/api/tax/estimate", json={"financial_year": 2026, "dependants": 0, "member_ids": [JORDAN]}
    )
    assert alone.status_code == 200


def test_with_the_figure_the_schedule_and_the_label_totals_agree(
    sample: TestClient,
) -> None:
    without_home_office_hours(sample)  # the invented pack has no rate per hour
    use_pack(pack_with_multiplier(2026))
    camera = next(
        r
        for r in sample.get("/api/tax/records").json()
        if r["description"] == "Sample camera, diminishing value"
    )
    assert camera["asset"]["missing"] == [] and camera["asset"]["multiplier_percent"] == 200
    assert [
        (y["financial_year"], y["days"], y["opening"], y["deduction"], y["closing"])
        for y in camera["asset"]["schedule"][:3]
    ] == [
        (2026, 181, "2000.00", "495.89", "1504.11"),
        (2027, 365, "1504.11", "752.06", "752.05"),
        (2028, 366, "752.05", "377.06", "374.99"),
    ]
    totals = sample.get("/api/tax/label-totals", params={"financial_year": 2026}).json()
    assert totals["uncounted"] == []
    expenses = next(t for t in totals["totals"] if t["label_key"] == "business_expenses")
    assert expenses["amount"] == "495.89"
    assert [(c["record_id"], c["amount"]) for c in expenses["contributions"]] == [
        (camera["id"], "495.89")
    ]
    later = sample.get("/api/tax/label-totals", params={"financial_year": 2027}).json()
    assert (
        next(t for t in later["totals"] if t["label_key"] == "business_expenses")["amount"]
        == "752.06"
    )
    in_register = sample.get(
        "/api/tax/records", params={"financial_year": 2027, "label": "business_expenses"}
    ).json()
    assert [(r["id"], r["counted"]) for r in in_register] == [(camera["id"], "752.06")]


def test_the_shipped_pack_counts_the_camera_against_the_business_in_the_estimate(
    sample: TestClient,
) -> None:
    res = sample.post(
        "/api/tax/estimate",
        json={"financial_year": 2026, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert res.status_code == 200, res.text
    alex = res.json()["members"][0]
    # 120,000.00 salary + 412.37 interest
    # + (6,000.00 business income - 495.89 camera - 1,037.40 home office: 1,482.00 hours at 0.70
    #    - 849.55 car: 965.4 km at 0.88)
    assert alex["total_income"] == "235650.53"
    assert not any("Business loss" in n for n in alex["notes"])


def test_an_asset_is_entered_edited_and_audited_through_the_register(sample: TestClient) -> None:
    created = sample.post("/api/tax/records", json=an_asset_body(work_use_percent="60"))
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["asset"]["effective_life_years"] == "5" and record["work_use_percent"] == "60"
    assert record["amount"] == "1000.00" and record["counted"] is None
    edited = sample.put(
        f"/api/tax/records/{record['id']}", json=an_asset_body(effective_life_years="2.5")
    )
    assert edited.status_code == 200
    assert edited.json()["asset"]["effective_life_years"] == "2.5"
    trail = sample.get(
        "/api/audit", params={"entity_type": "tax_record", "entity_id": record["id"]}
    ).json()["items"]
    assert [e["action"] for e in trail] == ["update", "create"]
    changed = {c["field"]: (c["before"], c["after"]) for c in trail[0]["changes"]}
    assert changed["Effective life years"] == ("5", "2.5")
    assert sample.delete(f"/api/tax/records/{record['id']}").status_code == 204


def test_every_problem_with_an_asset_is_collected_against_its_own_field(
    sample: TestClient,
) -> None:
    res = sample.post(
        "/api/tax/records",
        json=an_asset_body(amount="0", effective_life_years="0", work_use_percent="0"),
    )
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {
        "amount",
        "effective_life_years",
        "work_use_percent",
    }
    res = sample.post("/api/tax/records", json=an_asset_body(label="salary"))
    assert [e["field"] for e in res.json()["errors"]] == ["label"]
    res = sample.post("/api/tax/records", json=an_asset_body(effective_life_years=None))
    assert [e["field"] for e in res.json()["errors"]] == ["effective_life_years"]
