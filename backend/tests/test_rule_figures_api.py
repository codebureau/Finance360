"""Captured Rule figures through the API, end to end (ADR-0022).

A figure captured over the API changes what a Tax record counts, what its trace says, what an
estimate uses and the pack version label; withdrawing it puts everything back. Every figure here is
invented; the shipped pack is only read, never asserted beyond "it differs from the invented one".
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from finance360.config import Settings
from finance360.core.models.rule_capture import RuleCapture
from finance360.core.rulepack import fy_of
from finance360.main import create_app
from finance360.rulepacks.au import PACK
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
RATE = "tax.car.rate_per_km"
LIMIT = "tax.car.max_km"
LEVY = "tax.medicare.levy_rate"
BASE = "au/2026-27/r7"


def _client(
    tmp_path: Path, *, with_fixture_capture: bool, with_carry: bool = False
) -> Iterator[TestClient]:
    data = tmp_path / ("data-captured" if with_fixture_capture else "data-plain")
    data.mkdir()
    if with_carry:  # the sample's carried year: its own file, so other tests never see it
        shutil.copy(
            REPO / "fixtures" / "sample_household" / "rule_figures_carry.json",
            data / "rule_figures_carry.json",
        )
    source = REPO / "fixtures" / "sample_household" / "household.json"
    fixture = json.loads(source.read_text("utf-8"))
    if not with_fixture_capture:
        del fixture["rule_figures"]  # these tests start from a household with nothing captured
    (data / "household.json").write_text(json.dumps(fixture), encoding="utf-8")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    """The sample household without its invented captured figure."""
    yield from _client(tmp_path, with_fixture_capture=False)


@pytest.fixture
def sample_as_shipped(tmp_path: Path) -> Iterator[TestClient]:
    """The sample household as the review slot loads it, invented capture included."""
    yield from _client(tmp_path, with_fixture_capture=True)


def figures(client: TestClient, fy: int) -> dict[str, dict[str, Any]]:
    res = client.get("/api/rule-figures", params={"financial_year": fy})
    assert res.status_code == 200, res.text
    return {f["key"]: f for f in res.json()["figures"]}


def capture(client: TestClient, fy: int, key: str, value: str, **more: Any) -> dict[str, Any]:
    body = {"financial_year": fy, "key": key, "value": value, "quote": "An invented sentence."}
    res = client.post("/api/rule-figures", json={**body, **more})
    assert res.status_code == 201, res.text
    return res.json()


def a_car_record(client: TestClient, member: int) -> dict[str, Any]:
    res = client.post(
        "/api/tax/records",
        json={
            "member_id": member,
            "record_type": "car_kilometres",
            "label": "business_expenses",
            "from_on": "2026-07-01",
            "to_on": "2027-06-30",
            "description": "Invented car kilometres",
            "trips": [{"description": "Invented trip", "km": "100", "times": "2"}],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def member_ids(client: TestClient) -> list[int]:
    return [m["id"] for m in client.get("/api/household").json()["members"]]


def test_a_captured_car_rate_makes_a_record_count_and_its_trace_says_captured(
    sample: TestClient,
) -> None:
    alex = member_ids(sample)[0]
    before = a_car_record(sample, alex)
    assert before["counted"] is None  # the release holds nothing for FY 2026-27
    assert before["car"]["missing"] == [RATE, LIMIT]
    assert before["calculation"]["waiting_for"] and before["figures_used"] == []
    listed = figures(sample, 2027)
    assert listed[RATE]["state"] == "missing" and listed[RATE]["value"] is None
    assert listed[RATE]["previous"]["financial_year_label"] == "2025-26"
    assert listed[RATE]["plausible_range"] and listed[RATE]["where"]

    rate = capture(sample, 2027, RATE, "0.90", page_url="https://example.invalid/page")
    assert (rate["state"], rate["value"], rate["capture"]["method"]) == (
        "captured",
        "0.90",
        "typed",
    )
    matched = {
        "key": LIMIT,
        "fy": 2027,
        "value": "5000",
        "quote": "An invented sentence.",
        "provisional": False,
        "note": "Matched by words on the page.",
    }
    capture(sample, 2027, LIMIT, "5000", matched_proposal=matched)  # as the page reader's Confirm

    record = sample.get(f"/api/tax/records/{before['id']}").json()
    assert record["car"]["rate_per_km"] == "0.90" and record["car"]["limit_km"] == 5000
    assert record["counted"] == "180.00"  # 200 km at the invented rate
    used = {f["key"]: f for f in record["figures_used"]}
    figure = used[RATE]
    assert figure["origin"] == "captured" and figure["capture_id"] == rate["capture"]["id"]
    assert figure["quote"] == "An invented sentence." and figure["captured_by"] == "owner"
    assert figure["url"] == "https://example.invalid/page" and figure["url_confirmed"] is True
    assert figure["shipped_value"] is None and figure["captured_at"]
    # The trace card says how each captured figure was found (ADR-0023 section 6).
    assert (figure["method"], used[LIMIT]["method"]) == ("typed", "matched")
    assert figure["rule_pack_version"].startswith(BASE + "+2:")
    assert record["calculation"]["waiting_for"] == []

    setup = sample.get("/api/tax/setup").json()
    assert setup["rule_pack_version"].startswith(BASE + "+2:")

    # Withdrawing both puts the record back exactly as it was.
    for key in (RATE, LIMIT):
        res = sample.delete(f"/api/rule-figures/{figures(sample, 2027)[key]['capture']['id']}")
        assert res.status_code == 200 and res.json()["state"] == "missing"
    again = sample.get(f"/api/tax/records/{before['id']}").json()
    assert again["counted"] is None and again["figures_used"] == []
    assert again["car"]["missing"] == [RATE, LIMIT]
    assert sample.get("/api/tax/setup").json()["rule_pack_version"] == BASE


def test_a_capture_over_a_shipped_figure_shows_the_shipped_value_and_withdrawing_restores_it(
    sample: TestClient,
) -> None:
    alex = next(
        r
        for r in sample.get("/api/tax/records").json()
        if r["record_type"] == "car_kilometres" and r["member_id"] == member_ids(sample)[0]
    )
    shipped = figures(sample, 2026)[RATE]["value"]
    assert shipped is not None and figures(sample, 2026)[RATE]["state"] == "shipped"
    shipped_cents = int(shipped.replace(".", ""))
    mine = f"{(shipped_cents + 2) / 100:.2f}"
    row = capture(sample, 2026, RATE, mine)
    assert row["state"] == "captured" and row["shipped_value"] == shipped
    changed = sample.get(f"/api/tax/records/{alex['id']}").json()
    assert changed["counted"] != alex["counted"] and changed["car"]["rate_per_km"] == mine
    figure = next(f for f in changed["figures_used"] if f["key"] == RATE)
    assert figure["origin"] == "captured" and figure["shipped_value"] == f"{shipped} a km"
    other = next(f for f in changed["figures_used"] if f["key"] == LIMIT)
    assert other["origin"] == "shipped" and other["capture_id"] is None and other["quote"] is None

    res = sample.delete(f"/api/rule-figures/{row['capture']['id']}")
    assert res.status_code == 200 and res.json()["value"] == shipped
    restored = sample.get(f"/api/tax/records/{alex['id']}").json()
    assert restored == alex  # byte for byte what it was before the capture


def test_an_estimate_uses_a_captured_figure_labels_it_and_withdrawing_restores_it(
    sample: TestClient,
) -> None:
    ids = member_ids(sample)
    body = {"financial_year": 2026, "dependants": 0, "member_ids": ids}
    plain = sample.post("/api/tax/estimate", json=body).json()
    assert plain["rule_pack_version"] == BASE
    shipped = figures(sample, 2026)[LEVY]["value"]
    assert shipped is not None
    row = capture(sample, 2026, LEVY, "2.5%", confirm_jump=True)
    after = sample.post("/api/tax/estimate", json=body).json()
    assert after["rule_pack_version"].startswith(BASE + "+1:")
    assert after["household"]["medicare_levy"] != plain["household"]["medicare_levy"]
    assert sample.delete(f"/api/rule-figures/{row['capture']['id']}").status_code == 200
    assert sample.post("/api/tax/estimate", json=body).json() == plain


def test_correcting_supersedes_and_the_history_is_kept_in_the_audit_trail(
    sample: TestClient,
) -> None:
    first = capture(sample, 2027, RATE, "0.90")
    cid = first["capture"]["id"]
    second = sample.put(
        f"/api/rule-figures/{cid}", json={"value": "0.92", "quote": "A corrected invented line."}
    )
    assert second.status_code == 200, second.text
    assert second.json()["value"] == "0.92" and second.json()["capture"]["id"] != cid
    assert second.json()["capture"]["quote"] == "A corrected invented line."
    trail = sample.get("/api/audit", params={"entity_type": "rule_capture"}).json()["items"]
    assert sorted(e["action"] for e in trail) == ["create", "supersede"]
    gone = sample.delete(f"/api/rule-figures/{cid}")  # the superseded row is no longer active
    assert gone.status_code == 422 and gone.json()["errors"][0]["field"] == "id"


def test_validation_errors_keep_the_shared_shape(sample: TestClient) -> None:
    res = sample.post(
        "/api/rule-figures", json={"financial_year": 2027, "key": RATE, "value": "lots"}
    )
    assert res.status_code == 422
    (error,) = res.json()["errors"]
    assert error["field"] == "value" and error["message"]
    big = sample.post("/api/rule-figures", json={"financial_year": 2027, "key": RATE, "value": "9"})
    assert big.status_code == 422 and "plausible range" in big.json()["errors"][0]["message"]
    jump = sample.post(
        "/api/rule-figures", json={"financial_year": 2027, "key": RATE, "value": "1.50"}
    )
    assert jump.status_code == 422
    assert jump.json()["errors"][0]["field"] == "confirm_jump"
    ok = sample.post(
        "/api/rule-figures",
        json={"financial_year": 2027, "key": RATE, "value": "1.50", "confirm_jump": True},
    )
    assert ok.status_code == 201
    twice = sample.post(
        "/api/rule-figures",
        json={"financial_year": 2027, "key": RATE, "value": "1.40", "confirm_jump": True},
    )
    assert twice.status_code == 422 and "Correct it instead" in twice.json()["errors"][0]["message"]
    assert sample.delete("/api/rule-figures/9999").status_code == 404
    textual = sample.post(
        "/api/rule-figures", json={"financial_year": "2027", "key": RATE, "value": "0.90"}
    )
    assert textual.status_code == 422  # the year is a number, not text


def test_a_capture_that_no_longer_fits_is_listed_and_the_app_carries_on(sample: TestClient) -> None:
    app = sample.app
    with app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        s.add(
            RuleCapture(
                jurisdiction="au", key=RATE, fy=2027,
                value_json={"ccy": "AUD", "minor": 99_999_999},
                retrieved_on=date(2026, 11, 5), method="typed",
                captured_at=datetime(2026, 11, 5), captured_kind="owner", captured_by="login:1",
            )
        )  # fmt: skip
    res = sample.get("/api/rule-figures", params={"financial_year": 2027}).json()
    (problem,) = res["no_longer_fit"]
    assert problem["key"] == RATE and problem["financial_year_label"] == "2026-27"
    assert problem["reason"]
    assert res["rule_pack_version"] == BASE  # ignored: the shipped pack applies
    assert {f["key"]: f for f in res["figures"]}[RATE]["state"] == "missing"
    assert sample.get("/api/tax/setup").status_code == 200
    assert sample.get("/api/tax/records").status_code == 200


def test_the_sample_household_shows_one_invented_captured_figure_and_leaves_2025_26_alone(
    sample: TestClient, sample_as_shipped: TestClient
) -> None:
    # Catches: the fixture capture changing a shipped FY 2025-26 figure, or not being loaded once.
    captured = figures(sample_as_shipped, 2027)[RATE]
    assert (captured["state"], captured["value"]) == ("captured", "0.95")
    assert captured["capture"]["captured_by"] == "fixture"
    assert captured["capture"]["quote"].startswith("INVENTED")
    assert captured["capture"]["page_url"].startswith("https://example.com/")
    others = [k for k, f in figures(sample_as_shipped, 2027).items() if f["state"] == "captured"]
    assert others == [RATE]  # one only
    # the shipped year is untouched
    assert figures(sample_as_shipped, 2026) == figures(sample, 2026)
    audit = sample_as_shipped.get("/api/audit", params={"entity_type": "rule_capture"}).json()
    assert [(e["action"], e["source"]) for e in audit["items"]] == [("create", "fixture")]


def test_the_fixture_capture_is_loaded_only_while_there_are_none(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        capture_id = figures(c, 2027)[RATE]["capture"]["id"]
        assert c.delete(f"/api/rule-figures/{capture_id}").status_code == 200
    with owner_client(create_app(settings)) as c:  # a restart: a withdrawn capture stays withdrawn
        assert figures(c, 2027)[RATE]["state"] == "missing"


def test_the_year_list_says_which_years_can_be_captured(sample: TestClient) -> None:
    year = sample.get("/api/rule-figures", params={"financial_year": 2027}).json()
    assert year["first_financial_year"] == min(PACK.years)  # the pack's first year
    assert year["last_financial_year"] == fy_of(date.today()) + 1  # next year, announced
