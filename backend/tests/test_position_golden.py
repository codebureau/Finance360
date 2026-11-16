"""The sample household's Position at fixed dates against hand-calculated answers (golden), through
the API on a real temporary SQLite database loaded from ``fixtures/sample_household``.

Every number is invented. The arithmetic is shown beside each answer so a reader can redo it.
Conventions: Assets positive, Liabilities negative (a Liability Valuation is the amount owed, its
Class gives the sign); the joint savings split 50/50 with a spare cent going to the lowest Member id
(Alex Sample, id 1); a GBP figure converts to AUD at the rate on or before the date, rounded half to
even once.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def get(client: TestClient, on: str, tag: str | None = None) -> dict[str, Any]:
    res = client.get("/api/position", params={"on": on, **({"tag": tag} if tag else {})})
    assert res.status_code == 200, res.text
    return res.json()


def summary(p: dict[str, Any]) -> dict[str, Any]:
    """Class totals, Net, and per-Member / Joint figures, keyed by Member name."""
    names = {m["id"]: m["name"].split()[0] for m in p["members"]}
    asset, liability = p["classes"]
    assert (asset["class"], liability["class"]) == ("asset", "liability")
    return {
        "assets": asset["figures"]["household"],
        "liabilities": liability["figures"]["household"],
        "net": p["net"]["household"],
        "joint": p["net"]["joint"],
        "sole": {names[m["member_id"]]: m["sole"] for m in p["net"]["members"]},
        "member": {names[m["member_id"]]: m["total"] for m in p["net"]["members"]},
        "flagged": p["flagged"],
    }


def accounts(p: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["name"]: a for c in p["classes"] for k in c["categories"] for a in k["accounts"]}


def test_golden_2026_09_30(sample: TestClient) -> None:
    # Latest Valuation on or before 2026-09-30, in AUD unless noted:
    #   Everyday account (Alex)        2026-09-30    4264.24
    #   Sample Term Deposit (Jordan)   2026-09-30   10620.50  (first valued 2025-03)
    #   Joint savings (50/50)          2026-09-30   26303.79  (2630379 cents, odd: spare cent to
    #                                                           Alex, so 1315190 / 1315189)
    #   Sample Brokerage account (A)   2026-09-04   59689.22
    #   Sample Super Fund (Alex)       2026-09-30  215228.07
    #   Sample UK Pension (Jordan)     2026-09-08  GBP 68827.92 x 2.0497 (rate of 2026-08-03)
    #                                  = 141076.587624 -> 141076.59
    #   Family Home (Jordan)           2026-09-26  822091.58
    #   Family Car (Jordan)            2026-06-23   26089.07
    #   Sample Home Loan (Alex)        2026-09-30  497000.00 owed -> -497000.00
    #
    # Assets = 4264.24 + 10620.50 + 26303.79 + 59689.22 + 215228.07 + 141076.59 + 822091.58
    #          + 26089.07 = 1305363.06
    # Liabilities = -497000.00         Net = 1305363.06 - 497000.00 = 808363.06
    # Alex sole = 4264.24 + 59689.22 + 215228.07 - 497000.00 = -217818.47
    # Jordan sole = 10620.50 + 141076.59 + 822091.58 + 26089.07 = 999877.74
    # Joint = 26303.79
    #   check: -217818.47 + 999877.74 + 26303.79 = 808363.06
    # Member totals (sole + share of joint): Alex -217818.47 + 13151.90 = -204666.57
    #                                        Jordan 999877.74 + 13151.89 = 1013029.63
    #   check: -204666.57 + 1013029.63 = 808363.06
    p = get(sample, "2026-09-30")
    assert summary(p) == {
        "assets": "1305363.06",
        "liabilities": "-497000.00",
        "net": "808363.06",
        "joint": "26303.79",
        "sole": {"Alex": "-217818.47", "Jordan": "999877.74"},
        "member": {"Alex": "-204666.57", "Jordan": "1013029.63"},
        "flagged": 0,
    }
    by = accounts(p)
    uk = by["Sample UK Pension"]
    assert (uk["original"], uk["currency"], uk["rate"], uk["rate_as_at"], uk["value"]) == (
        "68827.92",
        "GBP",
        "2.0497",
        "2026-08-03",
        "141076.59",
    )
    assert by["Joint savings (Sample Bank)"]["shares"] == {"1": "13151.90", "2": "13151.89"}
    assert by["Sample Home Loan"]["original"] == "497000.00"  # as recorded: the amount owed
    assert by["Sample Home Loan"]["value"] == "-497000.00"
    # Category roll-up: Cash = 4264.24 + 10620.50 + 26303.79 = 41188.53
    cash = next(k for k in p["classes"][0]["categories"] if k["key"] == "cash")
    assert cash["figures"]["household"] == "41188.53"


def test_golden_2025_09_30(sample: TestClient) -> None:
    # Latest Valuation on or before 2025-09-30:
    #   Everyday account (Alex)        2025-09-30    3205.76
    #   Sample Term Deposit (Jordan)   2025-09-30   10150.00
    #   Joint savings (50/50)          2025-09-30   23155.16  (2315516 cents, even: 11577.58 each)
    #   Sample Brokerage account (A)   2025-09-19   49041.49
    #   Sample Super Fund (Alex)       2025-08-31  193404.28  (no September statement)
    #   Sample UK Pension (Jordan)     2025-09-03  GBP 64196.87 x 2.03885 (rate of 2025-07-01)
    #                                  = 130887.7883995 -> 130887.79
    #   Family Home (Jordan)           2025-09-24  two Valuations on the day: estimate 810194.33
    #                                              and manual 825194.33; manual outranks estimate
    #                                              -> 825194.33
    #   Family Car (Jordan)            2025-09-04   28745.01
    #   Sample Home Loan (Alex)        2025-09-30  509650.00 owed -> -509650.00
    #
    # Assets = 3205.76 + 10150.00 + 23155.16 + 49041.49 + 193404.28 + 130887.79 + 825194.33
    #          + 28745.01 = 1263783.82
    # Net = 1263783.82 - 509650.00 = 754133.82
    # Alex sole = 3205.76 + 49041.49 + 193404.28 - 509650.00 = -263998.47
    # Jordan sole = 10150.00 + 130887.79 + 825194.33 + 28745.01 = 994977.13
    # Joint = 23155.16
    #   check: -263998.47 + 994977.13 + 23155.16 = 754133.82
    # Member totals: Alex -263998.47 + 11577.58 = -252420.89
    #                Jordan 994977.13 + 11577.58 = 1006554.71
    #   check: -252420.89 + 1006554.71 = 754133.82
    p = get(sample, "2025-09-30")
    assert summary(p) == {
        "assets": "1263783.82",
        "liabilities": "-509650.00",
        "net": "754133.82",
        "joint": "23155.16",
        "sole": {"Alex": "-263998.47", "Jordan": "994977.13"},
        "member": {"Alex": "-252420.89", "Jordan": "1006554.71"},
        "flagged": 0,
    }
    assert accounts(p)["Family Home"]["source"] == "manual"
    assert accounts(p)["Family Home"]["value"] == "825194.33"


def test_golden_2024_10_20_flags_accounts_with_no_valuation_yet(sample: TestClient) -> None:
    # Before the first statements of four Accounts, so those are flagged and left out of every
    # total (not zeroed). Latest Valuation on or before 2024-10-20:
    #   Everyday, Joint savings, Super Fund, Home Loan   none yet -> flagged (4)
    #   Sample Brokerage account (A)   2024-10-18   39659.02
    #   Sample UK Pension (Jordan)     2024-10-10   GBP 61797.34 x 1.9712 (rate of 2024-10-01)
    #                                  = 121814.916608 -> 121814.92
    #   Family Home (Jordan)           2024-10-18  779136.76
    #   Family Car (Jordan)            2024-10-05   32164.14
    #
    # Assets = 39659.02 + 121814.92 + 779136.76 + 32164.14 = 972774.84
    # Liabilities = 0.00 (the loan is flagged, not zero)   Net = 972774.84
    # Alex sole = 39659.02;  Jordan sole = 121814.92 + 779136.76 + 32164.14 = 933115.82
    # Joint = 0.00 (the joint account is flagged);  check: 39659.02 + 933115.82 = 972774.84
    p = get(sample, "2024-10-20")
    assert summary(p) == {
        "assets": "972774.84",
        "liabilities": "0.00",
        "net": "972774.84",
        "joint": "0.00",
        "sole": {"Alex": "39659.02", "Jordan": "933115.82"},
        "member": {"Alex": "39659.02", "Jordan": "933115.82"},
        "flagged": 4,
    }
    by = accounts(p)
    flagged = sorted(n for n, a in by.items() if a["status"] != "ok")
    assert flagged == [
        "Everyday account (Sample Bank)",
        "Joint savings (Sample Bank)",
        "Sample Home Loan",
        "Sample Super Fund",
    ]
    assert by["Sample Home Loan"]["value"] is None  # null, not "0.00"
    assert by["Sample Home Loan"]["status"] == "no_valuation"


def test_golden_tag_filter_2026_09_30(sample: TestClient) -> None:
    # Tag "retirement" is on Sample Super Fund (Alex, 215228.07) and Sample UK Pension (Jordan,
    # 141076.59): Net = 215228.07 + 141076.59 = 356304.66, and no Joint, no Liabilities.
    p = get(sample, "2026-09-30", tag="retirement")
    assert summary(p) == {
        "assets": "356304.66",
        "liabilities": "0.00",
        "net": "356304.66",
        "joint": "0.00",
        "sole": {"Alex": "215228.07", "Jordan": "141076.59"},
        "member": {"Alex": "215228.07", "Jordan": "141076.59"},
        "flagged": 0,
    }
    assert sorted(accounts(p)) == ["Sample Super Fund", "Sample UK Pension"]
    assert p["tags"] == ["emergency fund", "everyday", "growth", "retirement"]


def test_the_default_date_is_today_in_the_household_zone(sample: TestClient) -> None:
    from finance360.core.localtime import local_today

    p = sample.get("/api/position").json()
    assert p["as_at"] == local_today("Australia/Melbourne").isoformat()


def test_a_bad_date_is_refused_and_an_unknown_tag_is_just_empty(sample: TestClient) -> None:
    assert sample.get("/api/position", params={"on": "soon"}).status_code == 422
    empty = get(sample, "2026-09-30", tag="no-such-tag")
    assert empty["classes"][0]["categories"] == [] and empty["net"]["household"] == "0.00"
