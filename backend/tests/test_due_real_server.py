"""What's due writes over a real server (uvicorn, a random port) with a real sign-in and a stale
session, against the sample household. TestClient with an overridden principal hides
what this catches: the cookie, the origin check, commit-before-response and a session whose
``last_seen_at`` needs refreshing."""

from __future__ import annotations

import httpx
from test_scenarios_real_server import signed_in  # noqa: F401 - the shared real-server fixture


def test_snoozes_and_the_effort_level_work_over_a_real_server_and_are_read_back_at_once(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    http = signed_in
    household = http.get("/api/household").json()
    assert household["effort_level"] == "steady"  # the sample household's

    first = http.get("/api/due").json()["items"][0]
    made = http.put(
        "/api/due/snoozes",
        json={"rule_key": first["rule_key"], "subject_key": first["subject_key"], "snooze": "skip"},
    )
    assert made.status_code == 200, made.text
    # committed before the response returned, so the next read sees it
    after = http.get("/api/due").json()
    assert first["subject_key"] not in {i["subject_key"] for i in after["items"]}
    assert [s["snooze_id"] for s in after["snoozed"]] == [made.json()["id"]]

    assert http.delete(f"/api/due/snoozes/{made.json()['id']}").status_code == 204
    assert http.get("/api/due").json()["snoozed"] == []

    light = http.put("/api/household", json={"name": household["name"], "effort_level": "light"})
    assert light.status_code == 200, light.text
    assert http.get("/api/due").json()["level"] == "light"
    trail = http.get("/api/audit", params={"entity_type": "due_snooze"}).json()["items"]
    assert [e["action"] for e in trail][::-1] == ["create", "delete"]


def test_household_facts_are_written_over_a_real_server_and_read_back_at_once(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    """The Household facts write route (ADR-0029): a real sign-in and session, committed before the
    response returns, audited. Lives here because this file already drives the Household over a real
    server and is a slow file."""
    http = signed_in
    before = http.get("/api/household").json()["facts"]
    assert before["principal_home_name"] == "Family Home"
    assert before["couple_status"]["label"] == "default, not entered"

    saved = http.put("/api/household/facts", json={"couple_status": "couple", "homeowner": True})
    assert saved.status_code == 200, saved.text
    read = http.get("/api/household").json()["facts"]  # committed before the response returned
    assert read["couple_status"] == {"value": "couple", "source": "entered", "label": "entered"}
    assert read["homeowner"]["source"] == "entered"

    reset = http.put("/api/household/facts", json={"couple_status": None, "homeowner": None})
    assert reset.status_code == 200, reset.text
    assert http.get("/api/household").json()["facts"]["couple_status"]["source"] == "default"
    trail = http.get("/api/audit", params={"entity_type": "household"}).json()["items"]
    assert [e["summary"] for e in trail][:4][::-1] == [
        "Set the Household couple status to couple",
        "Set the Household homeowner to yes",
        "Reset the Household couple status to not entered",
        "Reset the Household homeowner to not entered",
    ]


def test_display_preferences_are_written_over_a_real_server_and_read_back_at_once(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    """The Display preference write route (ADR-0031): a real sign-in and session, committed before
    the response returns, kept per Login, not audited. Lives here because this file already drives
    a real server and is a slow file."""
    http = signed_in
    assert http.get("/api/preferences").json() == {"preferences": {}}
    saved = http.put(
        "/api/preferences",
        json={"preferences": {"position.composition": "bars", "default.share": "stacked-bar"}},
    )
    assert saved.status_code == 200, saved.text
    # committed before the response returned, so the next read sees it
    assert http.get("/api/preferences").json() == {
        "preferences": {"default.share": "stacked-bar", "position.composition": "bars"}
    }
    cleared = http.put("/api/preferences", json={"preferences": {"position.composition": None}})
    assert cleared.json() == {"preferences": {"default.share": "stacked-bar"}}
    refused = http.put("/api/preferences", json={"preferences": {"no-dot": "bars"}})
    assert refused.status_code == 422
    assert http.get("/api/preferences").json() == {"preferences": {"default.share": "stacked-bar"}}
    assert (
        http.get("/api/audit", params={"entity_type": "display_preference"}).json()["items"] == []
    )


def test_a_valuation_makes_a_notice_that_is_dismissed_over_a_real_server(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    """The Notices write route (ADR-0033): a real sign-in and session; the Valuation and its Notice
    commit together before the response returns, and dismissal is read back at once. Lives here
    because this file already drives a real server and is a slow file."""
    http = signed_in
    assert http.get("/api/notices").json() == {"items": []}  # the fixture pre-seeds none
    accounts = {a["name"]: a for a in http.get("/api/accounts").json()}
    loan = accounts["Sample Home Loan"]
    saved = http.post(
        f"/api/accounts/{loan['id']}/valuations",
        json={"amount": "-1.00", "source": "manual", "provenance_note": "Synthetic check"},
    )
    assert saved.status_code == 201, saved.text
    notice = saved.json()["notice"]
    assert notice is not None, saved.text
    assert notice["headline"] == "You added a Valuation for Sample Home Loan."
    listed = http.get("/api/notices").json()["items"]  # committed before the response returned
    assert [n["id"] for n in listed] == [notice["id"]]
    assert http.post(f"/api/notices/{notice['id']}/dismiss").status_code == 204
    assert http.get("/api/notices").json() == {"items": []}
    kept = http.get("/api/notices", params={"include_dismissed": "true"}).json()["items"]
    assert [n["dismissed"] for n in kept] == [True]
