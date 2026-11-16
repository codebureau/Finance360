"""Tax record writes over a real server (uvicorn, a random port) with a real sign-in and a stale
session, against the sample household. TestClient with an overridden principal hides
what this catches: the cookie, the origin check, commit-before-response and a session whose
``last_seen_at`` needs refreshing."""

from __future__ import annotations

import httpx
from test_scenarios_real_server import signed_in  # noqa: F401 - the shared real-server fixture

FY = 2026


def test_tax_record_writes_work_over_a_real_server_and_are_read_back_at_once(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    http = signed_in
    before = http.get("/api/tax/label-totals", params={"financial_year": FY}).json()["totals"]
    alex_other = next(t for t in before if t["label_key"] == "work_other")
    assert alex_other["amount"] == "480.00"

    made = http.post(
        "/api/tax/records",
        json={
            "member_id": alex_other["member_id"],
            "record_type": "line",
            "label": "work_other",
            "amount": "200.00",
            "work_use_percent": "50",
            "from_on": "2026-01-10",
            "description": "Real server tools",
        },
    )
    assert made.status_code == 201, made.text
    record_id = made.json()["id"]
    assert made.json()["counted"] == "100.00"
    # committed before the response returned, so the next read sees it
    after = http.get("/api/tax/label-totals", params={"financial_year": FY}).json()["totals"]
    total = next(t for t in after if t["label_key"] == "work_other")
    assert total["amount"] == "580.00"
    assert record_id in [c["record_id"] for c in total["contributions"]]

    assert (
        http.put(
            f"/api/tax/records/{record_id}",
            json={
                "member_id": alex_other["member_id"],
                "label": "work_other",
                "amount": "300.00",
                "from_on": "2026-01-10",
                "description": "Real server tools",
            },
        ).status_code
        == 200
    )
    assert http.delete(f"/api/tax/records/{record_id}").status_code == 204
    assert http.get(f"/api/tax/records/{record_id}").status_code == 404

    trail = http.get(
        "/api/audit", params={"entity_type": "tax_record", "entity_id": record_id}
    ).json()["items"]
    assert [e["action"] for e in trail][::-1] == ["create", "update", "delete"]


def test_what_if_and_estimate_writes_work_over_a_real_server_and_are_read_back_at_once(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    http = signed_in
    both = {"financial_year": FY, "dependants": 0, "member_ids": [1, 2]}
    made = http.post(
        "/api/tax/whatifs",
        json={
            "name": "Real server what-if",
            "financial_year": FY,
            "overrides": [{"op": "set", "member_id": 2, "label": "salary", "amount": "95000.00"}],
        },
    )
    assert made.status_code == 201, made.text
    whatif_id = made.json()["id"]
    assert http.get(f"/api/tax/whatifs/{whatif_id}").status_code == 200  # committed before the 201

    base = http.post("/api/tax/estimates", json={**both, "label": "Real server base"})
    other = http.post("/api/tax/estimates", json={**both, "whatif_id": whatif_id})
    assert base.status_code == 201 and other.status_code == 201, (base.text, other.text)
    listed = http.get("/api/tax/estimates").json()
    assert [e["id"] for e in listed] == [other.json()["id"], base.json()["id"]]
    assert http.get(f"/api/tax/estimates/{other.json()['id']}").json()["reproduces"] is True

    compared = http.get(
        "/api/tax/estimates/compare", params={"a": base.json()["id"], "b": other.json()["id"]}
    )
    assert compared.status_code == 200, compared.text
    assert [d["label_key"] for d in compared.json()["labels"]] == ["salary"]

    # a what-if with an estimate cannot be deleted, and a saved estimate has no delete route
    assert http.delete(f"/api/tax/whatifs/{whatif_id}").status_code == 422
    assert http.delete(f"/api/tax/estimates/{base.json()['id']}").status_code == 405

    trail = http.get(
        "/api/audit", params={"entity_type": "tax_estimate", "entity_id": base.json()["id"]}
    ).json()["items"]
    assert [e["action"] for e in trail] == ["create"]
