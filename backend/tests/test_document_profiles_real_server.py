"""Document profile writes over a real server (uvicorn, a random port) with a real sign-in and a
stale session. TestClient with an overridden principal hides what this catches: the
cookie, the origin check, commit-before-response, and the registry change that follows a commit
being visible to the very next request. Every file here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from test_scenarios_real_server import signed_in  # noqa: F401 - the shared real-server fixture

REPO = Path(__file__).resolve().parents[2]
SAMPLE_PDF = (
    REPO / "fixtures" / "sample_household" / "documents" / "ato_income_statement_sample.pdf"
).read_bytes()
SHIPPED = json.loads(
    (
        REPO
        / "backend"
        / "finance360"
        / "plugins"
        / "documents"
        / "ato_income_statement"
        / "profile.json"
    ).read_text(encoding="utf-8")
)


def reading(http: httpx.Client) -> tuple[str, int]:
    res = http.post(
        "/api/documents/read",
        params={"target": "tax.income_statement"},
        content=SAMPLE_PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert res.status_code == 200, res.text
    return res.json()["profile_key"], res.json()["profile_version"]


def test_a_pasted_profile_is_saved_activated_and_retired_over_a_real_server(
    signed_in: httpx.Client,  # noqa: F811
) -> None:
    http = signed_in
    added = http.post(
        "/api/library/documents",
        params={"file_name": "sample.pdf"},
        content=SAMPLE_PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert added.status_code == 201, added.text
    document_id = added.json()["document"]["id"]
    draft = json.dumps(
        {**SHIPPED, "version": SHIPPED["version"] + 1}
    )  # the shipped key: overrides it once active

    tested = http.post(
        "/api/document-profiles/test", json={"profile_json": draft, "document_id": document_id}
    )
    assert tested.status_code == 200 and tested.json()["acceptable"] is True
    assert [p["status"] for p in http.get("/api/document-profiles").json()] == ["shipped"]

    saved = http.post(
        "/api/document-profiles", json={"profile_json": draft, "document_id": document_id}
    )
    assert saved.status_code == 201, saved.text
    pid = saved.json()["id"]
    assert saved.json()["status"] == "proposed"
    assert reading(http) == ("ato-income-statement", SHIPPED["version"])  # committed, but not live

    activated = http.post(f"/api/document-profiles/{pid}/activate")
    assert activated.status_code == 200, activated.text
    assert reading(http) == (
        "ato-income-statement",
        SHIPPED["version"] + 1,
    )  # live at once, in the same process
    assert http.get(f"/api/document-profiles/{pid}").json()["status"] == "active"

    retired = http.post(f"/api/document-profiles/{pid}/retire")
    assert retired.status_code == 200, retired.text
    assert reading(http) == (
        "ato-income-statement",
        SHIPPED["version"],
    )  # the shipped profile reads again
    trail = http.get("/api/audit", params={"entity_type": "document_profile"}).json()["items"]
    assert sorted(e["action"] for e in trail) == ["close", "create", "update"]

    # Without a session none of it is reachable.
    base = str(http.base_url)
    with httpx.Client(base_url=base, headers={"Origin": base}) as anonymous:
        assert anonymous.post(f"/api/document-profiles/{pid}/activate").status_code == 401
        assert anonymous.get("/api/document-profiles").status_code == 401
