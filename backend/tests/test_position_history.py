"""The Position history series: dates (unit), equality with the single-date Position (Hypothesis),
and the sample household's golden points through the API on a real temporary SQLite database.

Golden answers are the ones hand-calculated in ``test_position_golden``; this file only checks the
series reproduces them at the same dates.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_position_calculation import ALL, CATEGORIES, PROP, accounts_st, days, fx_st

from finance360.capabilities.position.calculation import (
    AccountFacts,
    compute_history,
    compute_position,
    history_dates,
)
from finance360.config import Settings
from finance360.core.fx import FxPoint
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]


# --- dates -------------------------------------------------------------------------------


def test_month_ends_then_the_end_date_when_it_is_not_one() -> None:
    assert history_dates(date(2026, 1, 15), date(2026, 4, 10)) == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 10),
    ]


def test_an_end_that_is_a_month_end_is_not_repeated_and_leap_february_is_right() -> None:
    assert history_dates(date(2028, 1, 31), date(2028, 3, 31)) == [
        date(2028, 1, 31),
        date(2028, 2, 29),
        date(2028, 3, 31),
    ]


def test_a_range_inside_one_month_is_just_its_end_and_a_backwards_range_is_empty() -> None:
    assert history_dates(date(2026, 5, 3), date(2026, 5, 9)) == [date(2026, 5, 9)]
    assert history_dates(date(2026, 5, 9), date(2026, 5, 3)) == []


def test_quarter_step_takes_every_third_month_end_across_a_year_boundary() -> None:
    assert history_dates(date(2025, 11, 30), date(2026, 6, 30), "quarter") == [
        date(2025, 11, 30),
        date(2026, 2, 28),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


# --- Hypothesis: a point is the single-date Position -------------------------------------


@PROP
@given(accounts_st(), fx_st, st.lists(days, max_size=6), st.sampled_from([None, "x", "y"]))
def test_each_history_point_equals_the_direct_single_date_position(
    accounts: list[AccountFacts], fx: list[FxPoint], dates: list[date], tag: str | None
) -> None:
    history = compute_history(
        dates=dates, accounts=accounts, categories=CATEGORIES, member_ids=ALL, fx=fx, tag=tag
    )
    assert len(history) == len(dates)
    for on, point in zip(dates, history, strict=True):
        direct = compute_position(
            on=on, accounts=accounts, categories=CATEGORIES, member_ids=ALL, fx=fx, tag=tag
        )
        assert point == direct


# --- Golden, through the API -------------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def history(client: TestClient, **params: str) -> dict[str, Any]:
    res = client.get("/api/position/history", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def point(body: dict[str, Any], on: str) -> dict[str, Any]:
    return next(p for p in body["points"] if p["on"] == on)


def test_golden_points_match_the_position_golden_answers(sample: TestClient) -> None:
    body = history(sample, **{"from": "2025-09-30", "to": "2026-09-30"})
    dates = [p["on"] for p in body["points"]]
    assert dates[0] == "2025-09-30" and dates[-1] == "2026-09-30" and len(dates) == 13
    p25, p26 = point(body, "2025-09-30"), point(body, "2026-09-30")
    assert (p25["assets"]["household"], p25["liabilities"]["household"]) == (
        "1263783.82",
        "-509650.00",
    )
    assert (p25["net"]["household"], p25["net"]["joint"], p25["flagged"]) == (
        "754133.82",
        "23155.16",
        0,
    )
    assert (p26["assets"]["household"], p26["liabilities"]["household"]) == (
        "1305363.06",
        "-497000.00",
    )
    assert (p26["net"]["household"], p26["net"]["joint"], p26["flagged"]) == (
        "808363.06",
        "26303.79",
        0,
    )
    assert {m["total"] for m in p26["net"]["members"]} == {"-204666.57", "1013029.63"}


def test_a_point_with_unvalued_accounts_is_flagged_not_zeroed(sample: TestClient) -> None:
    body = history(sample, **{"from": "2024-10-20", "to": "2024-10-20"})
    assert len(body["points"]) == 1
    p = body["points"][0]
    # Golden 2024-10-20: four Accounts have no Valuation yet and are left out of the figures
    # (the loan is not counted as zero owed); Net is the Assets alone.
    assert (p["flagged"], p["net"]["household"], p["assets"]["household"]) == (
        4,
        "972774.84",
        "972774.84",
    )
    assert p["liabilities"]["household"] == "0.00"


def test_month_ends_before_a_late_account_is_valued_are_flagged(sample: TestClient) -> None:
    # Sample Term Deposit opened 2025-01-01 and is first valued 2025-03-15: Jan and Feb 2025 are
    # incomplete, Mar 2025 onwards complete (nothing else is unvalued by then).
    body = history(sample, **{"from": "2025-01-31", "to": "2025-04-30"})
    assert [(p["on"], p["flagged"]) for p in body["points"]] == [
        ("2025-01-31", 1),
        ("2025-02-28", 1),
        ("2025-03-31", 0),
        ("2025-04-30", 0),
    ]


def test_the_tag_filter_narrows_every_point(sample: TestClient) -> None:
    body = history(sample, **{"from": "2026-09-30", "to": "2026-09-30", "tag": "retirement"})
    assert body["tag"] == "retirement"
    assert body["points"][0]["net"]["household"] == "356304.66"
    assert body["points"][0]["liabilities"]["household"] == "0.00"


def test_the_default_range_spans_the_data_to_today_and_is_fast(sample: TestClient) -> None:
    started = time.perf_counter()
    body = history(sample)
    elapsed = time.perf_counter() - started
    dates = [p["on"] for p in body["points"]]
    assert dates[0] == "2024-10-31" and len(dates) >= 24
    assert dates == sorted(set(dates))
    assert elapsed < 1.0
    assert [m["name"] for m in body["members"]] == ["Alex Sample", "Jordan Sample"]
    assert "retirement" in body["tags"]


def test_bad_input_is_refused_in_the_usual_error_shape(sample: TestClient) -> None:
    assert sample.get("/api/position/history", params={"step": "fortnight"}).status_code == 422
    assert sample.get("/api/position/history", params={"from": "soon"}).status_code == 422
    huge = sample.get("/api/position/history", params={"from": "1900-01-01", "to": "2900-01-01"})
    assert huge.status_code == 422
    assert huge.json() == {
        "errors": [{"field": "from", "message": "Too many points: narrow the range."}]
    }


def test_a_reversed_range_is_a_422_not_an_empty_series(sample: TestClient) -> None:
    res = sample.get("/api/position/history", params={"from": "2026-01-31", "to": "2025-01-31"})
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "from"
    same = sample.get("/api/position/history", params={"from": "2026-01-31", "to": "2026-01-31"})
    assert same.status_code == 200 and len(same.json()["points"]) == 1
