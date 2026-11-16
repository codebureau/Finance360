"""Tax estimates prefilled from Income streams (ADR-0019 sections 6 and 10).

Pure logic (the forecast, the joint split, the no-double-count rule) is tested without a database.
The rest runs against the synthetic household and a real temporary SQLite database through the real
API: a prefilled value is source-marked, overridable, never a Record, and pinned in a saved
estimate. Every figure is invented.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from tax_support import aud

from finance360.capabilities.tax import input as tinput
from finance360.capabilities.tax import prefill
from finance360.capabilities.tax.prefill import StreamFacts
from finance360.config import Settings
from finance360.core.models.tax_estimate import TaxEstimate
from finance360.core.models.tax_record import TaxRecord
from finance360.core.money import Money
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "fixtures" / "sample_household"
ALEX, JORDAN = 1, 2
BASE = {"dependants": 0, "member_ids": [ALEX, JORDAN]}
YEAR = 2026  # the one year the AU pack holds tax figures for
SALARY = 120_000_00


def stream(
    stream_id: int = 1,
    gross: int = SALARY,
    shares: tuple[tuple[int, int], ...] = ((ALEX, 10000),),
    start_on: date | None = None,
    end_on: date | None = None,
    event_bounded: bool = False,
    type_key: str = "salary",
) -> StreamFacts:
    return StreamFacts(
        stream_id,
        f"Stream {stream_id}",
        type_key,
        Money(gross, "AUD"),
        shares,
        start_on,
        end_on,
        event_bounded,
    )


# --- the forecast and the rule (pure) --------------------------------------------------------


def test_a_whole_year_stream_prefills_its_stated_annual_amount() -> None:
    (line,) = prefill.forecast([stream()], 2026, [ALEX])
    assert (line.member_id, line.label, line.status) == (ALEX, "salary", prefill.COUNTED)
    assert line.amount == aud("120000.00")
    assert "stated annual amount $120,000.00" in line.text


def test_a_stream_part_way_through_the_year_is_pro_rated_by_days() -> None:
    # 1 Jan to 30 Jun 2026 is 181 of 365 days.
    (line,) = prefill.forecast([stream(start_on=date(2026, 1, 1))], 2026, [ALEX])
    assert line.amount == Money(round(SALARY * 181 / 365), "AUD")
    assert "181 of 365 days" in line.text
    ended = prefill.forecast([stream(end_on=date(2025, 6, 30))], 2026, [ALEX])
    assert ended == ()  # not in force in the year: nothing, not even a note


def test_a_joint_stream_splits_with_allocate_and_the_parts_sum_exactly() -> None:
    joint = stream(gross=100_000_01, shares=((ALEX, 5000), (JORDAN, 5000)))
    lines = prefill.forecast([joint], 2026, [ALEX, JORDAN])
    assert sum(line.amount.amount_minor for line in lines) == 100_000_01
    assert sorted(line.amount.amount_minor for line in lines) == [5_000_000, 5_000_001]
    only_alex = prefill.forecast([joint], 2026, [ALEX])
    assert len(only_alex) == 1  # a Member outside the estimate has no line


def test_a_life_event_bound_is_not_applied_and_says_so() -> None:
    (event,) = prefill.forecast([stream(event_bounded=True)], 2026, [ALEX])
    assert event.status == prefill.COUNTED and event.amount == aud("120000.00")
    assert "Life event" in event.text and "Tax what-if" in event.text


def test_a_kind_with_no_return_label_is_listed_but_not_counted() -> None:
    (line,) = prefill.forecast([stream(type_key="nonsense")], 2026, [ALEX])
    assert line.status == prefill.NO_LABEL and line.amount == aud("0")
    assert prefill.totals([line]) == {}


def test_no_double_count_records_win_over_the_forecast_golden() -> None:
    """The rule: per Member and label, any Record contribution means the forecast is not counted."""
    lines = prefill.forecast(
        [stream(1, SALARY), stream(2, 50_000_00, ((JORDAN, 10000),))], 2026, [ALEX, JORDAN]
    )
    decided = prefill.decide(lines, recorded=[(ALEX, "salary"), (ALEX, "interest")])
    by_member = {line.member_id: line for line in decided}
    assert by_member[ALEX].status == prefill.RECORDS_WIN
    assert by_member[ALEX].amount == aud("0")
    assert "records" in by_member[ALEX].text and "120,000.00" in by_member[ALEX].text
    assert by_member[JORDAN].status == prefill.COUNTED
    # only Jordan's forecast counts, and Alex has no prefilled total to add to his records
    assert prefill.totals(decided) == {(JORDAN, "salary"): aud("50000.00")}
    # another label's records (interest) never suppress salary
    assert prefill.totals(prefill.decide(lines, [(ALEX, "interest")])) == {
        (ALEX, "salary"): aud("120000.00"),
        (JORDAN, "salary"): aud("50000.00"),
    }


def test_two_streams_for_one_member_both_count_when_there_are_no_records() -> None:
    lines = prefill.forecast([stream(1, 70_000_00), stream(2, 5_000_00)], 2026, [ALEX])
    assert prefill.totals(prefill.decide(lines, [])) == {(ALEX, "salary"): aud("75000.00")}
    assert prefill.totals(prefill.decide(lines, [(ALEX, "salary")])) == {}


@given(
    gross=st.integers(min_value=1, max_value=10**11),
    weights=st.lists(st.integers(min_value=1, max_value=10000), min_size=1, max_size=2),
    start=st.one_of(st.none(), st.dates(date(2025, 1, 1), date(2026, 12, 31))),
)
def test_a_split_stream_never_gains_or_loses_a_minor_unit(
    gross: int, weights: list[int], start: date | None
) -> None:
    shares = tuple((i + 1, w) for i, w in enumerate(weights))
    whole = prefill.forecast([stream(gross=gross, shares=shares, start_on=start)], 2026, [1, 2])
    one = prefill.forecast([stream(gross=gross, shares=((1, 10000),), start_on=start)], 2026, [1])
    assert sum(line.amount.amount_minor for line in whole) == sum(
        line.amount.amount_minor for line in one
    )


# --- against the synthetic household ---------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(SAMPLE / "household.json", data / "household.json")
    app = create_app(Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None))
    with owner_client(app) as c:
        yield c


def factory_of(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory  # type: ignore[attr-defined, no-any-return]


def preview(client: TestClient, year: int, whatif_id: int | None = None) -> dict[str, Any]:
    res = client.post(
        "/api/tax/estimates/preview", json={**BASE, "financial_year": year, "whatif_id": whatif_id}
    )
    assert res.status_code == 200, res.text
    return dict(res.json())


def total(view: dict[str, Any], member_id: int, label: str) -> dict[str, Any] | None:
    return next(
        (t for t in view["totals"] if t["member_id"] == member_id and t["label_key"] == label),
        None,
    )


def whatif(client: TestClient, name: str, year: int, overrides: list[dict[str, Any]]) -> int:
    res = client.post(
        "/api/tax/whatifs", json={"name": name, "financial_year": year, "overrides": overrides}
    )
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


def minor(shown: dict[str, Any]) -> int:
    return int(str(shown["amount"]).replace(".", "").replace(",", ""))


def statement_ids(client: TestClient) -> dict[int, int]:
    """Each Member's FY 2025-26 income statement record id."""
    records = client.get("/api/tax/records", params={"financial_year": YEAR}).json()
    return {r["member_id"]: r["id"] for r in records if r["record_type"] == "income_statement"}


def without_statement(client: TestClient, member_id: int, name: str = "No statement") -> int:
    held = statement_ids(client)[member_id]
    return whatif(client, name, YEAR, [{"op": "exclude_record", "record_id": held}])


def salary(view: dict[str, Any], member_id: int) -> dict[str, Any]:
    found = total(view, member_id, "salary")
    assert found is not None
    return found


def test_records_win_for_one_member_and_the_forecast_fills_the_other(sample: TestClient) -> None:
    # the base: both Members have an income statement, so no forecast counts
    base = preview(sample, YEAR)
    assert [p["status"] for p in base["prefill"]] == ["records_win", "records_win"]
    assert {t["source"] for t in base["totals"] if t["label_key"] == "salary"} == {"records"}
    # leave out Jordan's statement only: his forecast stands in, Alex's records still win
    view = preview(sample, YEAR, without_statement(sample, JORDAN))
    alex, jordan = salary(view, ALEX), salary(view, JORDAN)
    # Alex: the income statement is the whole answer; the stream's 120,000.00 is not added to it
    assert alex["source"] == "records" and alex["contributions"]
    assert minor(alex["value"]) == sum(minor(c["value"]) for c in alex["contributions"])
    assert alex["value"] == salary(base, ALEX)["value"]
    # Jordan: no records left for the year, so the forecast stands in, source-marked
    assert jordan["source"] == "prefill" and jordan["contributions"] == []
    assert jordan["value"]["amount"] == "85000.00"
    lines = {line["member_id"]: line for line in view["prefill"]}
    assert lines[ALEX]["status"] == "records_win" and lines[ALEX]["value"]["amount"] == "0.00"
    assert "records" in lines[ALEX]["text"]
    assert lines[JORDAN]["status"] == "counted" and lines[JORDAN]["value"]["amount"] == "85000.00"
    assert lines[JORDAN]["stream_name"] == "Jordan salary (Sample Employer)"
    assert "Life event" in lines[JORDAN]["text"]  # the sample streams end at retirement


def test_a_what_if_overrides_a_prefilled_value_absolutely(sample: TestClient) -> None:
    held = statement_ids(sample)[JORDAN]
    stated = whatif(
        sample,
        "Stated salary",
        YEAR,
        [
            {"op": "exclude_record", "record_id": held},
            {
                "op": "set",
                "member_id": JORDAN,
                "label": "salary",
                "amount": "70000.00",
            },
        ],
    )
    view = preview(sample, YEAR, stated)
    jordan = salary(view, JORDAN)
    assert jordan["value"]["amount"] == "70000.00" and jordan["source"] == "stated"
    line = next(p for p in view["prefill"] if p["member_id"] == JORDAN)
    assert line["status"] == "overridden"
    applied = next(o for o in view["overrides"] if o["op"] == "set")
    assert applied["replaced"]["amount"] == "85000.00"  # it replaced the prefilled figure
    left_out = whatif(
        sample,
        "Left out salary",
        YEAR,
        [
            {"op": "exclude_record", "record_id": held},
            {"op": "exclude_label", "member_id": JORDAN, "label": "salary"},
        ],
    )
    gone = preview(sample, YEAR, left_out)
    assert total(gone, JORDAN, "salary") is None
    assert next(p for p in gone["prefill"] if p["member_id"] == JORDAN)["status"] == "overridden"


def test_a_prefilled_value_is_never_written_as_a_record(sample: TestClient) -> None:
    made = without_statement(sample, JORDAN)
    with factory_of(sample)() as s:
        before = s.scalar(select(func.count()).select_from(TaxRecord))
    assert salary(preview(sample, YEAR, made), JORDAN)["source"] == "prefill"
    res = sample.post(
        "/api/tax/estimates", json={**BASE, "financial_year": YEAR, "whatif_id": made}
    )
    assert res.status_code == 201, res.text
    with factory_of(sample)() as s:
        assert s.scalar(select(func.count()).select_from(TaxRecord)) == before


def test_a_saved_estimate_pins_the_prefilled_values_and_their_source(sample: TestClient) -> None:
    made = without_statement(sample, JORDAN)
    header = sample.post(
        "/api/tax/estimates", json={**BASE, "financial_year": YEAR, "whatif_id": made}
    ).json()
    # deleting the Income stream afterwards changes nothing in the saved estimate
    streams = sample.get("/api/income-streams").json()
    jordan = next(s for s in streams if s["name"].startswith("Jordan"))
    assert sample.delete(f"/api/income-streams/{jordan['id']}").status_code in (200, 204)
    got = sample.get(f"/api/tax/estimates/{header['id']}").json()
    assert got["reproduces"] is True
    pinned = salary(got["view"], JORDAN)
    assert pinned["value"]["amount"] == "85000.00" and pinned["source"] == "prefill"
    assert any(p["status"] == "counted" for p in got["view"]["prefill"])
    # the pinned input alone carries the figure and says where it came from
    with factory_of(sample)() as s:
        row = s.get(TaxEstimate, header["id"])
        assert row is not None
        pinned_input = tinput.from_json(row.input_json)
    member = next(m for m in pinned_input.members if m.member_id == JORDAN)
    assert member.money("salary") == aud("85000.00")
    kinds = {p.key for p in pinned_input.provenance}
    assert {"prefill", "prefill_not_counted"} <= kinds
