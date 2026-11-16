"""The trace on a Tax record: which official figure it used, and its calculation replayed.

Figures asserted for the shipped pack are the ones it holds (the home office rate of FY 2025-26,
the diminishing value multiplier); everything else is invented, through the existing pack helpers.
"""

from __future__ import annotations

import dataclasses
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from finance360.capabilities.tax.record_types import depreciating_asset as asset_type
from finance360.capabilities.tax.record_types import home_office_hours as hours_type
from finance360.capabilities.tax.record_types.base import NO_RULES, Record, RecordRules
from finance360.config import Settings
from finance360.core import rulepack
from finance360.core.money import parse_amount
from finance360.main import create_app
from finance360.rulepacks.au import PACK
from finance360.rulepacks.au import tax_figures as figures
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
RATE_KEY = hours_type.RATE_KEY
DV_KEY = asset_type.DV_PERCENT_KEY


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


def find(client: TestClient, description: str) -> dict[str, Any]:
    return next(r for r in client.get("/api/tax/records").json() if r["description"] == description)


def rendered(step: dict[str, Any]) -> str:
    """A step as one line: the operands joined by their operators, then the result."""
    parts: list[str] = []
    for o in step["operands"]:
        parts += ([o["operator"]] if o["operator"] else []) + [
            o["value"] + (f" {o['unit']}" if o["unit"] else "")
        ]
    res = step["result"]
    return " ".join(parts) + f" = {res['value']}" + (f" {res['unit']}" if res["unit"] else "")


# --- the shipped pack carries the trace without changing its hash ------------------------------


def test_the_trace_metadata_is_outside_the_content_hash() -> None:
    plain = dataclasses.replace(PACK, retrieved={}, declared=())
    assert plain.content_hash() == PACK.content_hash()
    relabelled = dataclasses.replace(
        PACK,
        params=tuple(
            dataclasses.replace(p, label="Another label", page_url="https://example.invalid/")
            for p in PACK.params
        ),
    )
    assert relabelled.content_hash() == PACK.content_hash()


# --- figures_used ------------------------------------------------------------------------------


def test_the_home_office_record_names_the_rate_it_used(sample: TestClient) -> None:
    record = find(sample, "Sample home office hours, FY 2025-26")
    (used,) = record["figures_used"]
    assert used["key"] == RATE_KEY
    assert used["name"] == "Working from home: fixed rate per hour"
    assert (used["financial_year"], used["financial_year_label"]) == (2026, "2025-26")
    assert used["value"] == "0.70 an hour"
    assert used["source"] == "ATO, Working from home expenses: fixed rate method"
    assert used["url"].startswith("https://www.ato.gov.au/")
    assert used["url_confirmed"] is True  # the owner opened the page on capture
    assert used["retrieved"] == figures.RETRIEVED[2026][RATE_KEY]
    assert used["provisional"] is False and used["provisional_note"] is None
    # the sample household's invented captured figure adds "+1:<digest>" to the label
    assert used["rule_pack_version"] == f"{PACK.version_label}+1:5be77e"


def test_the_camera_names_the_multiplier_it_used(sample: TestClient) -> None:
    (used,) = find(sample, "Sample camera, diminishing value")["figures_used"]
    assert (used["key"], used["value"], used["financial_year"]) == (DV_KEY, "200%", 2026)
    assert used["name"] == "Depreciation: diminishing value multiplier"
    assert used["retrieved"] == figures.RETRIEVED[2026][DV_KEY]


def test_a_record_that_uses_no_figure_has_none_and_no_calculation(sample: TestClient) -> None:
    line = find(sample, "Interest, Sample Savings Account")
    assert line["figures_used"] == [] and line["calculation"] is None
    desk = find(sample, "Sample standing desk, claimed in full")
    assert desk["figures_used"] == []  # claimed in full reads no figure


def test_a_provisional_figure_says_so_with_its_note(sample: TestClient) -> None:
    held = {fy: dict(notes) for fy, notes in PACK.provisional.items()}
    held.setdefault(2026, {})[RATE_KEY] = "Invented note: announced, not yet law."
    use_pack(dataclasses.replace(PACK, provisional=held))
    (used,) = find(sample, "Sample home office hours, FY 2025-26")["figures_used"]
    assert used["provisional"] is True
    assert used["provisional_note"] == "Invented note: announced, not yet law."


def test_a_missing_figure_is_not_listed_as_used_but_the_record_says_what_it_waits_for(
    sample: TestClient,
) -> None:
    years = {
        fy: {k: v for k, v in held.items() if k != RATE_KEY} for fy, held in PACK.years.items()
    }
    use_pack(dataclasses.replace(PACK, years=years))
    record = find(sample, "Sample home office hours, FY 2025-26")
    assert record["figures_used"] == []
    assert record["home_office"]["missing"] == [RATE_KEY]
    calc = record["calculation"]
    assert calc["waiting_for"] == [
        {
            "key": RATE_KEY,
            "name": "Working from home: fixed rate per hour",
            "financial_year": 2026,
            "financial_year_label": "2025-26",
        }
    ]
    # the hours can still be replayed; the deduction waits for the rate
    assert [s["title"] for s in calc["steps"]] == ["Hours worked from home"]


# --- the calculation, replayed -----------------------------------------------------------------


def test_a_regular_home_office_record_replays_days_hours_and_deduction(sample: TestClient) -> None:
    calc = find(sample, "Sample home office hours, FY 2025-26")["calculation"]
    assert calc["waiting_for"] == []
    days, deduction = calc["steps"]
    assert rendered(days) == "195.00 home days × 7.60 hours a day = 1482.00 hours"
    assert rendered(deduction) == "1482.00 hours × 0.70 an hour = 1037.40"
    assert (
        deduction["note"] == "Rounded once, to the cent, for the whole record (not month by month)."
    )


def test_a_casual_home_office_record_replays_the_sum_of_its_months(sample: TestClient) -> None:
    calc = find(sample, "Sample casual home office hours, Sep to Dec 2025")["calculation"]
    months, deduction = calc["steps"]
    assert rendered(months) == "18.50 + 22.00 + 15.25 + 9.75 = 65.50 hours"
    assert [o["label"] for o in months["operands"]] == [
        "September 2025",
        "October 2025",
        "November 2025",
        "December 2025",
    ]
    assert rendered(deduction) == "65.50 hours × 0.70 an hour = 45.85"


def test_an_asset_year_replays_opening_days_multiplier_and_life(sample: TestClient) -> None:
    steps = find(sample, "Sample camera, diminishing value")["calculation"]["steps"]
    assert rendered(steps[0]) == "2000.00 × 181 days ÷ 365 × 200 % ÷ 4 years = 495.89"
    assert steps[0]["title"] == "FY 2025-26: decline in value"
    assert steps[0]["note"] == "Rounded once, to the cent."
    # 100% work use: no second step in the year, and the next year opens at what is left
    assert [s["title"] for s in steps[:2]] == [
        "FY 2025-26: decline in value",
        "FY 2026-27: decline in value",
    ]
    assert steps[1]["operands"][0]["value"] == "1504.11"
    assert steps[1]["operands"][1]["value"] == "365"


def test_a_work_use_share_adds_the_deduction_step(sample: TestClient) -> None:
    body = {
        "member_id": 1,
        "record_type": "depreciating_asset",
        "label": "business_expenses",
        "amount": "1000.00",
        "effective_life_years": "5",
        "work_use_percent": "60",
        "from_on": "2025-07-01",
        "description": "Invented printer",
    }
    made = sample.post("/api/tax/records", json=body)
    assert made.status_code == 201, made.text
    steps = made.json()["calculation"]["steps"]
    assert rendered(steps[0]) == "1000.00 × 365 days ÷ 365 × 200 % ÷ 5 years = 400.00"
    assert rendered(steps[1]) == "400.00 × 60 % = 240.00"
    assert steps[1]["title"] == "FY 2025-26: deduction"


def test_a_claim_in_full_replays_cost_times_work_use(sample: TestClient) -> None:
    (step,) = find(sample, "Sample standing desk, claimed in full")["calculation"]["steps"]
    assert rendered(step) == "800.00 × 50 % = 400.00"
    assert step["title"] == "FY 2025-26: claimed in full"


def test_an_asset_without_its_multiplier_replays_nothing_and_waits_for_it(
    sample: TestClient,
) -> None:
    years = {fy: {k: v for k, v in held.items() if k != DV_KEY} for fy, held in PACK.years.items()}
    use_pack(dataclasses.replace(PACK, years=years))
    calc = find(sample, "Sample camera, diminishing value")["calculation"]
    assert calc["steps"] == []
    assert [(w["key"], w["financial_year_label"]) for w in calc["waiting_for"]] == [
        (DV_KEY, "2025-26")
    ]
    assert find(sample, "Sample camera, diminishing value")["figures_used"] == []


# --- the replay agrees with the counted amount (pure) ------------------------------------------


def an_asset(life: str, *, share: int = 1_000_000, cost: str = "1000.00") -> Record:
    value = asset_type.parse(
        {
            "label": "business_expenses",
            "cost": {"minor": parse_amount(cost, "AUD").amount_minor, "ccy": "AUD"},
            "effective_life_years": life,
            "work_use_ppm": share,
        }
    )
    return Record(1, 1, asset_type.KEY, date(2025, 7, 1), None, value)


def test_the_last_year_of_an_asset_says_the_decline_was_limited_to_what_is_left() -> None:
    record = an_asset("1")  # 200% / 1 year: written off in the first year, then nothing left
    rules = RecordRules(((DV_KEY, 2026, 200),))
    steps = asset_type.calculation(record, rules)
    assert rendered_pure(steps[0]).endswith("= 1000.00")
    assert (
        steps[0].note
        == "Limited to the opening value: an asset is never written down below nothing."
    )


def rendered_pure(step: Any) -> str:
    return " ".join(f"{o.operator or ''} {o.value}".strip() for o in step.operands) + (
        f" = {step.result.value}"
    )


def test_a_record_type_with_no_figures_has_no_calculation_without_rules() -> None:
    record = an_asset("4")
    assert asset_type.calculation(record, NO_RULES) == ()
