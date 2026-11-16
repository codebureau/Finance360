"""What's due for Rule figures (ADR-0022 section 7).

Integration tests through the real app and a real temporary SQLite database. Invariant work (live
data): "Keep mine" is an acknowledgement about one shipped value, so a release that ships a
different value raises it again; "Use shipped" really withdraws the capture, audited. Every figure
is invented; a changed release is simulated by registering a modified copy of the shipped pack.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_rule_figures_api import LIMIT, RATE, capture
from test_rule_figures_api import _client as sample_client

from finance360.core import rulepack
from finance360.core.money import Money
from finance360.rulepacks.au import PACK
from support import TestClient

NOT_CAPTURED = "rule_figures.not_captured"
DIFFERS = "rule_figures.release_differs"
NO_LONGER_FITS = "rule_figures.no_longer_fits"
NOW_FIRM = "rule_figures.now_firm"
_HELD = PACK.years[2026][RATE]
assert isinstance(_HELD, Money)
SHIPPED_RATE: Money = _HELD


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    """The synthetic household with nothing captured (and no FY 2026-27 car record)."""
    yield from sample_client(tmp_path, with_fixture_capture=False)


@pytest.fixture
def sample_as_shipped(tmp_path: Path) -> Iterator[TestClient]:
    """The household as the review slot loads it, invented capture included."""
    yield from sample_client(tmp_path, with_fixture_capture=True)


@pytest.fixture(autouse=True)
def restore_pack() -> Iterator[None]:
    yield
    rulepack._PACKS["au"] = PACK  # noqa: SLF001


def release(**changes: Any) -> None:
    """Register a different release of the AU pack, as a later deploy would carry."""
    rulepack._PACKS["au"] = dataclasses.replace(PACK, **changes)  # noqa: SLF001


def shipping(key: str, fy: int, value: rulepack.Value) -> dict[str, Any]:
    years = {y: dict(v) for y, v in PACK.years.items()}
    years[fy][key] = value
    return {"years": years, "revision": PACK.revision + 1}


def due(client: TestClient, rule: str) -> list[dict[str, Any]]:
    res = client.get("/api/due", params={})
    assert res.status_code == 200, res.text
    return [i for i in res.json()["items"] if i["rule_key"] == rule]


def snoozed(client: TestClient, rule: str) -> list[dict[str, Any]]:
    return [s for s in client.get("/api/due").json()["snoozed"] if s["item"]["rule_key"] == rule]


def capture_a_differing_rate(client: TestClient) -> int:
    """Capture an invented rate for FY 2025-26, where the release ships a different one."""
    assert SHIPPED_RATE.amount_minor != 90
    return int(capture(client, 2026, RATE, "0.90")["capture"]["id"])


# --- Not captured --------------------------------------------------------------------------


def test_a_record_waiting_for_a_figure_is_listed_per_year_and_clears_when_captured(
    sample: TestClient,
) -> None:
    # The synthetic household has a Jordan car record for FY 2026-27, a year the release lacks.
    items = due(sample, NOT_CAPTURED)
    assert {i["subject_key"] for i in items} == {f"{RATE}@fy2027", f"{LIMIT}@fy2027"}
    rate = next(i for i in items if i["subject_key"] == f"{RATE}@fy2027")
    assert rate["title"].startswith("FY 2026-27: ") and rate["title"].endswith("not captured")
    assert rate["link"] == "/rule-figures?fy=2027&missing=1"
    assert rate["importance"] == "important" and rate["actions"] == []

    capture(sample, 2027, RATE, "0.90")
    assert [i["subject_key"] for i in due(sample, NOT_CAPTURED)] == [f"{LIMIT}@fy2027"]
    capture(sample, 2027, LIMIT, "5000")
    assert due(sample, NOT_CAPTURED) == []


def test_nothing_is_asked_when_no_record_needs_a_missing_figure(sample: TestClient) -> None:
    for record in sample.get("/api/tax/records").json():
        if record["from_on"] >= "2026-07-01":
            assert sample.delete(f"/api/tax/records/{record['id']}").status_code == 204
    assert due(sample, NOT_CAPTURED) == []


def test_the_review_slot_household_shows_only_the_missing_kilometre_limit(
    sample_as_shipped: TestClient,
) -> None:
    # The invented rate is captured by the fixture, the limit is not.
    items = due(sample_as_shipped, NOT_CAPTURED)
    assert [i["subject_key"] for i in items] == [f"{LIMIT}@fy2027"]
    assert due(sample_as_shipped, DIFFERS) == []  # the fixture never changes a shipped year


# --- Release differs -----------------------------------------------------------------------


def test_a_capture_that_differs_from_the_release_is_flagged_with_both_figures(
    sample: TestClient,
) -> None:
    capture_id = capture_a_differing_rate(sample)
    [item] = due(sample, DIFFERS)
    assert "FY 2025-26" in item["title"] and f"r{PACK.revision}" in item["title"]
    assert "0.90" in item["effect"] and "0.88" in item["effect"]
    assert item["link"] == "/rule-figures?fy=2026"
    assert [(a["kind"], a["capture_id"]) for a in item["actions"]] == [
        ("keep_mine", capture_id),
        ("use_shipped", capture_id),
    ]


def test_a_capture_equal_to_the_release_raises_nothing(sample: TestClient) -> None:
    capture(sample, 2026, RATE, "0.88")  # the same as the release ships
    assert due(sample, DIFFERS) == [] and due(sample, NOW_FIRM) == []


def test_keep_mine_is_remembered_until_a_release_ships_a_different_value(
    sample: TestClient,
) -> None:
    # Catches: an acknowledgement that survives a changed shipped value (it must name the value).
    capture_a_differing_rate(sample)
    [item] = due(sample, DIFFERS)
    res = sample.put(
        "/api/due/snoozes",
        json={"rule_key": DIFFERS, "subject_key": item["subject_key"], "snooze": "skip"},
    )
    assert res.status_code == 200, res.text
    assert due(sample, DIFFERS) == [] and len(snoozed(sample, DIFFERS)) == 1

    # A later release that still ships the same value does not raise it again.
    release(**{**shipping(RATE, 2026, SHIPPED_RATE), "revision": PACK.revision + 1})
    assert due(sample, DIFFERS) == []

    # A release that changes the shipped value does.
    release(**shipping(RATE, 2026, Money(91, "AUD")))
    [again] = due(sample, DIFFERS)
    assert again["subject_key"] != item["subject_key"]
    assert "0.91" in again["effect"] and f"r{PACK.revision + 1}" in again["title"]


def test_use_shipped_withdraws_the_capture_and_is_audited(sample: TestClient) -> None:
    # Catches: "Use shipped" that hides the item without ending the capture, or leaves no audit.
    capture_id = capture_a_differing_rate(sample)
    res = sample.delete(f"/api/rule-figures/{capture_id}")
    assert res.status_code == 200, res.text
    assert due(sample, DIFFERS) == []
    figures = sample.get("/api/rule-figures", params={"financial_year": 2026}).json()["figures"]
    rate = next(f for f in figures if f["key"] == RATE)
    assert rate["state"] == "shipped" and rate["value"] == "0.88" and rate["capture"] is None
    trail = sample.get("/api/audit", params={"entity_type": "rule_capture"}).json()["items"]
    assert {(e["action"], e["entity_id"]) for e in trail} == {
        ("create", capture_id),
        ("delete", capture_id),
    }
    # A second "Use shipped" on the same item is refused, not silently accepted.
    assert sample.delete(f"/api/rule-figures/{capture_id}").status_code == 422


# --- No longer fits ------------------------------------------------------------------------


def test_a_capture_the_release_now_rejects_is_flagged(sample: TestClient) -> None:
    capture_id = int(capture(sample, 2027, RATE, "0.90")["capture"]["id"])
    assert due(sample, NO_LONGER_FITS) == []
    # The new release narrows the plausible range of the key so that 0.90 is outside it.
    narrowed = tuple(
        dataclasses.replace(s, low=1, high=50) if s.key == RATE else s for s in PACK.specs
    )
    release(specs=narrowed, revision=PACK.revision + 1)
    [item] = due(sample, NO_LONGER_FITS)
    assert item["subject_key"] == f"capture:{capture_id}"
    assert "FY 2026-27" in item["title"] and "no longer fits" in item["title"]
    assert item["link"] == "/rule-figures?fy=2027"


# --- Now firm ------------------------------------------------------------------------------


def test_a_provisional_capture_the_release_now_ships_firm_is_flagged(sample: TestClient) -> None:
    capture_id = int(
        capture(sample, 2026, RATE, "0.90", provisional_note="Announced, not yet law.")["capture"][
            "id"
        ]
    )
    # The release ships FY 2025-26's rate firm, so the provisional capture is redundant.
    [item] = due(sample, NOW_FIRM)
    assert item["subject_key"] == f"capture:{capture_id}@firm" and item["importance"] == "optional"
    assert "now firm" in item["title"]
    assert [(a["kind"], a["capture_id"]) for a in item["actions"]] == [("use_shipped", capture_id)]
    assert due(sample, DIFFERS) == []  # the shipped figure applies, so nothing differs
    # A firm capture is never "now firm".
    sample.delete(f"/api/rule-figures/{capture_id}")
    capture(sample, 2026, RATE, "0.90")
    assert due(sample, NOW_FIRM) == []
