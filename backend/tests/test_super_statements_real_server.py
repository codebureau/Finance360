"""Super statement writes over a real server (uvicorn, a random port) with a real sign-in and a
stale session, against the sample household. TestClient with an overridden principal
hides what this catches: the cookie, the origin check, commit-before-response and a session whose
``last_seen_at`` needs refreshing."""

from __future__ import annotations

import httpx
from test_scenarios_real_server import signed_in  # noqa: F401 - the shared real-server fixture

BODY_KEYS = (
    "period_start",
    "period_end",
    "opening",
    "contributions_in",
    "investment_returns",
    "withdrawals",
    "closing",
    "contributions_tax",
    "fees_before_rebate",
    "rebate",
    "fees_inside_investment",
    "division_293_released",
    "other_withdrawals",
    "insurance_premiums",
    "concessional_received",
    "non_concessional_received",
)


def _fund(http: httpx.Client) -> int:
    accounts = http.get("/api/accounts").json()
    return next(a["id"] for a in accounts if a["name"] == "Sample Super Fund")


def _june_valuations(http: httpx.Client, fund: int) -> list[str]:
    history = http.get(f"/api/accounts/{fund}/valuations").json()
    return [v["amount"] for v in history if v["as_at"] == "2026-06-30" and v["status"] == "current"]


def test_super_statement_writes_work_over_a_real_server_and_are_read_back_at_once(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    http = signed_in
    fund = _fund(http)
    [loaded] = http.get(f"/api/accounts/{fund}/super-statements").json()
    assert loaded["closing"] == "211500.00"
    body = {key: loaded[key] for key in BODY_KEYS}
    body["options"] = [{"name": o["name"], "value": o["value"]} for o in loaded["options"]]

    # A failed check needs its tick; the 422 keeps the shared shape and writes nothing.
    off = http.put(f"/api/super-statements/{loaded['id']}", json={**body, "closing": "211600.00"})
    assert off.status_code == 422
    assert {e["field"] for e in off.json()["errors"]} == {"checks.roll", "checks.options"}
    assert http.get(f"/api/accounts/{fund}/super-statements").json()[0]["id"] == loaded["id"]
    assert _june_valuations(http, fund) == ["211500.00"]

    fixed = http.put(
        f"/api/super-statements/{loaded['id']}",
        json={**body, "closing": "211600.00", "ticked_checks": ["roll", "options"]},
    )
    assert fixed.status_code == 200, fixed.text
    # committed before the response returned, so the next reads see the statement and its Valuation
    [current] = http.get(f"/api/accounts/{fund}/super-statements").json()
    assert (current["id"], current["closing"]) == (fixed.json()["id"], "211600.00")
    assert _june_valuations(http, fund) == ["211600.00"]

    assert http.delete(f"/api/super-statements/{current['id']}").status_code == 204
    assert http.get(f"/api/accounts/{fund}/super-statements").json() == []
    assert _june_valuations(http, fund) == []
    trail = http.get(
        "/api/audit", params={"entity_type": "super_statement", "account_id": fund}
    ).json()["items"]
    assert [e["action"] for e in trail][::-1] == ["create", "supersede", "supersede"]
