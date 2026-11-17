"""Captured Rule figure writes over a real server (uvicorn, a random port) with a real sign-in and a
stale session. TestClient with an overridden principal hides what this
catches: the cookie, the origin check, commit-before-response and a session whose ``last_seen_at``
needs refreshing. Every figure is invented."""

from __future__ import annotations

import httpx
from test_scenarios_real_server import signed_in  # noqa: F401 - the shared real-server fixture

RATE = "tax.car.rate_per_km"
LITO_PAGE = (
    "https://www.ato.gov.au/individuals-and-families/income-deductions-offsets-and-records/"
    "tax-offsets/low-income-tax-offset"
)


def test_capture_correct_and_withdraw_work_over_a_real_server_and_are_read_back_at_once(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    http = signed_in
    # FY 2027-28: the sample household's one invented capture is for FY 2026-27, so this year is
    # untouched and the label starts at "+1" (that capture).
    year = {"financial_year": 2028}
    before = http.get("/api/rule-figures", params=year).json()["rule_pack_version"]
    assert before == "au/2026-27/r8+1:5be77e"

    made = http.post(
        "/api/rule-figures",
        json={**year, "key": RATE, "value": "0.90", "quote": "An invented sentence."},
    )
    assert made.status_code == 201, made.text
    capture_id = made.json()["capture"]["id"]
    # committed before the response returned, so the next read sees it
    listed = http.get("/api/rule-figures", params=year).json()
    rate = next(f for f in listed["figures"] if f["key"] == RATE)
    assert (rate["state"], rate["value"]) == ("captured", "0.90")
    assert listed["rule_pack_version"].startswith("au/2027-28/r8+2:")  # last year moves

    fixed = http.put(f"/api/rule-figures/{capture_id}", json={"value": "0.92"})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["value"] == "0.92" and fixed.json()["capture"]["id"] != capture_id

    gone = http.delete(f"/api/rule-figures/{fixed.json()['capture']['id']}")
    assert gone.status_code == 200 and gone.json()["state"] == "missing"
    after = http.get("/api/rule-figures", params=year).json()
    assert after["rule_pack_version"] == before

    trail = http.get("/api/audit", params={"entity_type": "rule_capture"}).json()["items"]
    # the fixture's own capture comes first; then this test's three writes
    assert [e["action"] for e in trail][::-1] == ["create", "create", "supersede", "delete"]


def test_starting_a_year_from_last_year_and_confirming_a_page_work_over_a_real_server(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    http = signed_in
    refused = http.post("/api/rule-figures/carry", json={"financial_year": 2099})
    assert refused.status_code == 422, refused.text  # far beyond a year that can be captured

    # FY 2026-27 holds only the sample's car rate: every other key is missing there
    preview = http.get("/api/rule-figures/carry", params={"financial_year": 2027}).json()
    assert preview["source_financial_year_label"] == "2025-26" and preview["proposals"]
    started = http.post("/api/rule-figures/carry", json={"financial_year": 2027})
    assert started.status_code == 200, started.text
    written = [p["key"] for p in started.json()["proposals"]]
    listed = http.get("/api/rule-figures", params={"financial_year": 2027}).json()
    figures = {f["key"]: f for f in listed["figures"]}
    assert all(figures[k]["capture"]["method"] == "carried" for k in written)  # read back at once
    assert RATE not in written and figures[RATE]["capture"]["method"] == "typed"
    assert listed["carry_offer"] is None  # nothing left that an earlier year can supply

    confirmed = http.post(
        "/api/rule-figures/confirm-page", json={"financial_year": 2027, "page_url": LITO_PAGE}
    )
    assert confirmed.status_code == 200, confirmed.text
    keys = confirmed.json()["confirmed"]
    assert len(keys) == 5 and all(k.startswith("tax.lito.") for k in keys)
    again = {
        f["key"]: f
        for f in http.get("/api/rule-figures", params={"financial_year": 2027}).json()["figures"]
    }
    assert all(again[k]["capture"]["method"] == "typed" for k in keys)
    assert all(again[k]["provisional_note"] is None for k in keys)
    one = next(k for k in written if k not in keys)
    single = http.post(f"/api/rule-figures/{again[one]['capture']['id']}/confirm")
    assert single.status_code == 200 and single.json()["capture"]["method"] == "typed"
