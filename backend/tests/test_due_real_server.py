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
