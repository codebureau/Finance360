"""Starting a year from earlier Rule figures (ADR-0022 section 4 amendment).

Integration tests against a real migrated SQLite database, through the service and through the real
app. Invariant work (an estimate reads these figures): nothing is ever read for a year that has no
capture row and no shipped figure, a carried figure is provisional until confirmed, and confirming
is a supersession, not an edit. Every figure and sentence is invented; the shipped pack is only
read.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_rule_figures_api import LIMIT, RATE, capture
from test_rule_figures_api import _client as sample_client

from finance360.capabilities.tax import pack_rules
from finance360.core import rulepack, rulepack_store
from finance360.core.audit import AuditContext
from finance360.core.models.audit import AuditEvent
from finance360.core.models.rule_capture import RuleCapture
from finance360.rulepacks.au import PACK
from finance360.rulepacks.au.tax_specs import SPECS
from support import TestClient

CTX = AuditContext("owner", "login:1", "api")
TODAY = date(2026, 11, 17)  # FY 2026-27, so FY 2027 and FY 2028 can be captured
LITO_PAGE = (
    "https://www.ato.gov.au/individuals-and-families/income-deductions-offsets-and-records/"
    "tax-offsets/low-income-tax-offset"
)
LITO_KEYS = [s.key for s in SPECS if s.key.startswith("tax.lito.")]
NOTE_2026 = "Same as 2025-26, not checked against the page"


def expected_note(key: str) -> str:
    """The note a figure carried from FY 2025-26 gets; a provisional source's note is appended."""
    earlier = PACK.provisional.get(2026, {}).get(key)
    return NOTE_2026 if earlier is None else f"{NOTE_2026}. 2025-26 was provisional: {earlier}"


#: Keys the shipped pack holds for FY 2025-26, and the one declared key it holds nothing for.
HELD_2026 = {s.key for s in SPECS} & set(PACK.years[2026])
NO_EARLIER = {s.key for s in SPECS} - HELD_2026


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    """The sample household with no captured Rule figure at all."""
    yield from sample_client(tmp_path, with_fixture_capture=False)


@pytest.fixture
def sample_as_shipped(tmp_path: Path) -> Iterator[TestClient]:
    """The sample household as the review slot loads it: FY 2027-28 started from earlier years."""
    yield from sample_client(tmp_path, with_fixture_capture=True, with_carry=True)


@pytest.fixture(autouse=True)
def restore_pack() -> Iterator[None]:
    yield
    rulepack._PACKS["au"] = PACK  # noqa: SLF001


def tax_keys(pack: rulepack.RulePack, fy: int) -> list[str]:
    return [k for k in pack.years.get(fy, {}) if k.startswith("tax.")]


def schedule(key: str, text: str) -> None:
    """Register a release whose spec for ``key`` declares a scheduled change."""
    specs = tuple(
        dataclasses.replace(s, scheduled_change=text) if s.key == key else s for s in PACK.specs
    )
    rulepack._PACKS["au"] = dataclasses.replace(PACK, specs=specs)  # noqa: SLF001


def carry(client: TestClient, fy: int) -> dict[str, Any]:
    res = client.post("/api/rule-figures/carry", json={"financial_year": fy})
    assert res.status_code == 200, res.text
    return res.json()


def year(client: TestClient, fy: int) -> dict[str, Any]:
    res = client.get("/api/rule-figures", params={"financial_year": fy})
    assert res.status_code == 200, res.text
    return res.json()


def by_key(client: TestClient, fy: int) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in year(client, fy)["figures"]}


def active(s: Session, fy: int) -> dict[str, RuleCapture]:
    rows = s.scalars(
        select(RuleCapture).where(RuleCapture.fy == fy, RuleCapture.ended_at.is_(None))
    )
    return {r.key: r for r in rows}


# --- The preview writes nothing -------------------------------------------------------------


def test_the_preview_proposes_each_missing_key_from_last_year_and_writes_nothing(
    sample: TestClient,
) -> None:
    res = sample.get("/api/rule-figures/carry", params={"financial_year": 2027})
    assert res.status_code == 200, res.text
    preview = res.json()
    assert preview["source_financial_year_label"] == "2025-26"
    assert {p["key"] for p in preview["proposals"]} == HELD_2026
    assert {p["source_financial_year_label"] for p in preview["proposals"]} == {"2025-26"}
    assert {p["key"]: p["note"] for p in preview["proposals"]} == {
        k: expected_note(k) for k in HELD_2026
    }
    assert PACK.provisional[2026]  # the appended-note path is exercised, not skipped
    assert {n["key"] for n in preview["no_earlier"]} == NO_EARLIER
    # nothing was written: still missing, the version label unmoved, no capture row, no audit
    figures = by_key(sample, 2027)
    assert {f["state"] for f in figures.values()} == {"missing"}
    assert year(sample, 2027)["rule_pack_version"] == "au/2026-27/r8"
    assert sample.get("/api/audit", params={"entity_type": "rule_capture"}).json()["items"] == []
    offer = year(sample, 2027)["carry_offer"]
    assert offer == {
        "source_financial_year": 2026,
        "source_financial_year_label": "2025-26",
        "count": len(HELD_2026),
    }


def test_no_figure_is_ever_read_for_a_year_without_a_capture_row_or_a_shipped_figure(
    session_factory: sessionmaker[Session],
) -> None:
    # Catches: a carry that works at read time (a hidden carry-forward) instead of by a visible row.
    # The preview alone, and a year with nothing captured, leave every key unread for that year.
    with session_factory() as s:
        preview = rulepack_store.carry_preview(s, "au", 2027, today=TODAY)
        assert len(preview.proposals) == len(HELD_2026)
        pack = rulepack_store.effective_pack(s, "au")
        assert pack is not None
        assert pack is PACK and not tax_keys(pack, 2027)
        with pytest.raises(pack_rules.TaxRulesError, match="no tax figures for FY 2026-27"):
            pack_rules.build_rules(pack, 2027)
    with session_factory.begin() as s:
        rulepack_store.carry_figures(s, CTX, "au", 2028, today=TODAY)
        row = active(s, 2028)[LITO_KEYS[0]]
        rulepack_store.confirm_unchanged(s, CTX, row.id, today=TODAY)
    with session_factory() as s:
        pack = rulepack_store.effective_pack(s, "au")
        assert pack is not None
        rows = {(r.key, r.fy) for r in s.scalars(select(RuleCapture)) if r.ended_at is None}
        for fy in range(2024, 2032):
            for key in pack.years.get(fy, {}):
                assert key in PACK.years.get(fy, {}) or (key, fy) in rows, (key, fy)
        # FY 2026-27 was never started: it still reads nothing, though FY 2027-28 now holds figures
        assert not tax_keys(pack, 2027)
        with pytest.raises(pack_rules.TaxRulesError, match="no tax figures for FY 2026-27"):
            pack_rules.build_rules(pack, 2027)
        assert pack_rules.build_rules(pack, 2028).financial_year == 2028


# --- Accepting ------------------------------------------------------------------------------


def test_accepting_stores_one_provisional_carried_row_per_missing_key(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        result = rulepack_store.carry_figures(s, CTX, "au", 2027, today=TODAY)
        assert len(result.proposals) == len(HELD_2026)
    with session_factory() as s:
        rows = active(s, 2027)
        assert set(rows) == HELD_2026  # the key with no earlier figure stays missing
        for key, row in rows.items():
            assert row.method == "carried" and row.quote is None and row.ended_at is None
            assert row.provisional_note == expected_note(key)
            assert row.ai_log_id is None and row.ai_value_json is None
            spec = next(sp for sp in SPECS if sp.key == key)
            assert row.page_url == spec.page_url
            assert row.retrieved_on == PACK.retrieved_on(2026, key)  # the source's day, copied
        assert rows[LITO_KEYS[0]].retrieved_on == date(2026, 10, 27)
        events = s.scalars(select(AuditEvent).where(AuditEvent.entity_type == "rule_capture")).all()
        assert len(events) == len(HELD_2026)  # one audit event per key
        assert {e.action for e in events} == {"create"}
        assert all("from FY 2025-26" in e.summary for e in events)
        pack = rulepack_store.effective_pack(s, "au")
        assert pack is not None
        # the value is last year's, exactly
        for key in HELD_2026:
            assert pack.years[2027][key] == PACK.years[2026][key], key
        # accepting again writes nothing more: every key now holds an active row
        again = rulepack_store.carry_figures(s, CTX, "au", 2027, today=TODAY)
        assert again.proposals == ()


def test_a_key_that_already_holds_a_row_is_left_alone(sample: TestClient) -> None:
    made = capture(sample, 2027, RATE, "0.93")["capture"]
    preview = sample.get("/api/rule-figures/carry", params={"financial_year": 2027}).json()
    assert RATE not in {p["key"] for p in preview["proposals"]}
    carry(sample, 2027)
    rate = by_key(sample, 2027)[RATE]
    assert rate["value"] == "0.93" and rate["capture"]["id"] == made["id"]
    assert rate["capture"]["method"] == "typed" and rate["provisional_note"] is None


def test_each_key_takes_its_nearest_earlier_year_and_the_note_names_that_year(
    sample: TestClient,
) -> None:
    capture(sample, 2027, LIMIT, "6000")  # FY 2026-27 holds its own kilometre limit
    result = carry(sample, 2028)
    sources = {p["key"]: p["source_financial_year_label"] for p in result["proposals"]}
    assert sources[LIMIT] == "2026-27"
    assert sources[RATE] == "2025-26"
    assert result["source_financial_year_label"] == "2026-27"  # the latest source among them
    figures = by_key(sample, 2028)
    assert figures[LIMIT]["value"] == "6000"
    assert figures[LIMIT]["provisional_note"] == "Same as 2026-27, not checked against the page"
    assert figures[RATE]["provisional_note"] == NOTE_2026


def test_carrying_on_from_a_carried_figure_keeps_the_provisional_trail(
    sample: TestClient,
) -> None:
    carry(sample, 2027)
    carry(sample, 2028)
    note = by_key(sample, 2028)[RATE]["provisional_note"]
    assert note == (
        f"Same as 2026-27, not checked against the page. 2026-27 was provisional: {NOTE_2026}"
    )


# --- The estimate, and confirming -----------------------------------------------------------


def estimate(client: TestClient, fy: int) -> dict[str, Any]:
    members = [m["id"] for m in client.get("/api/household").json()["members"]]
    res = client.post(
        "/api/tax/estimate", json={"financial_year": fy, "dependants": 0, "member_ids": members}
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_the_estimate_runs_and_its_provisional_list_names_every_unconfirmed_carried_figure(
    sample: TestClient,
) -> None:
    missing = sample.post(
        "/api/tax/estimate",
        json={"financial_year": 2027, "dependants": 0, "member_ids": [1, 2]},
    )
    assert missing.status_code == 422  # not started: an estimate never borrows another year
    carry(sample, 2027)
    data = estimate(sample, 2027)
    named = {p["key"]: p["note"] for p in data["provisional"]}
    required = set(pack_rules.REQUIRED.values())
    assert required <= set(named)  # every figure the estimate used is named
    assert set(named) == HELD_2026
    assert named == {k: expected_note(k) for k in HELD_2026}
    # confirming one page clears exactly its figures from the list
    sample.post(
        "/api/rule-figures/confirm-page", json={"financial_year": 2027, "page_url": LITO_PAGE}
    )
    after = {p["key"] for p in estimate(sample, 2027)["provisional"]}
    assert after == set(named) - set(LITO_KEYS)


def test_confirming_one_figure_unchanged_supersedes_the_carried_row(sample: TestClient) -> None:
    carry(sample, 2027)
    before = by_key(sample, 2027)[RATE]
    old_id = before["capture"]["id"]
    res = sample.post(f"/api/rule-figures/{old_id}/confirm")
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["value"] == before["value"]
    assert after["provisional_note"] is None
    assert after["capture"]["method"] == "typed" and after["capture"]["id"] != old_id
    assert after["capture"]["page_url"] == before["page_url"]
    assert after["capture"]["retrieved_on"] >= TODAY.isoformat()  # the day it was confirmed
    assert after["capture"]["retrieved_on"] != before["capture"]["retrieved_on"]
    # the carried row stays as history, ended as superseded by the new one
    trail = sample.get("/api/audit", params={"entity_type": "rule_capture"}).json()["items"]
    assert [e["action"] for e in trail].count("supersede") == 1
    assert sample.post(f"/api/rule-figures/{old_id}/confirm").status_code == 422  # already ended


def test_a_figure_that_was_not_carried_cannot_be_confirmed_unchanged(sample: TestClient) -> None:
    typed_id = capture(sample, 2027, RATE, "0.93")["capture"]["id"]
    res = sample.post(f"/api/rule-figures/{typed_id}/confirm")
    assert res.status_code == 422
    assert "not carried" in res.json()["errors"][0]["message"]


def test_a_changed_figure_is_corrected_as_today_and_supersedes(sample: TestClient) -> None:
    carry(sample, 2027)
    old_id = by_key(sample, 2027)[RATE]["capture"]["id"]
    res = sample.put(
        f"/api/rule-figures/{old_id}",
        json={"value": "0.91", "quote": "An invented sentence.", "confirm_jump": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["value"] == "0.91" and res.json()["provisional_note"] is None
    assert res.json()["capture"]["method"] == "typed"
    trail = sample.get("/api/audit", params={"entity_type": "rule_capture"}).json()["items"]
    assert [e["action"] for e in trail].count("supersede") == 1


def test_confirming_a_page_clears_its_figures_in_one_action_with_one_audit_event_each(
    sample: TestClient,
) -> None:
    carry(sample, 2027)
    res = sample.post(
        "/api/rule-figures/confirm-page", json={"financial_year": 2027, "page_url": LITO_PAGE}
    )
    assert res.status_code == 200, res.text
    assert sorted(res.json()["confirmed"]) == sorted(LITO_KEYS)
    assert res.json()["needs_own_confirm"] == []
    figures = by_key(sample, 2027)
    for key in LITO_KEYS:
        assert figures[key]["provisional_note"] is None
        assert figures[key]["capture"]["method"] == "typed"
        assert figures[key]["value"] == by_key(sample, 2026)[key]["value"]
    # figures from other pages are untouched, and still provisional
    assert figures[RATE]["capture"]["method"] == "carried"
    trail = sample.get("/api/audit", params={"entity_type": "rule_capture"}).json()["items"]
    assert [e["action"] for e in trail].count("supersede") == len(LITO_KEYS)


def test_a_page_confirm_is_all_or_nothing(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory.begin() as s:
        rulepack_store.carry_figures(s, CTX, "au", 2027, today=TODAY)
    real = rulepack_store.correct_figure
    calls: list[int] = []

    def fail_on_the_third(*args: Any, **kwargs: Any) -> RuleCapture:
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(rulepack_store, "correct_figure", fail_on_the_third)
    with pytest.raises(RuntimeError), session_factory.begin() as s:
        rulepack_store.confirm_page(s, CTX, "au", 2027, LITO_PAGE, today=TODAY)
    with session_factory() as s:  # the transaction rolled back: all five are still carried
        assert {r.method for k, r in active(s, 2027).items() if k in LITO_KEYS} == {"carried"}


# --- Scheduled changes ----------------------------------------------------------------------


def test_a_scheduled_change_shows_on_the_figure_and_keeps_it_out_of_the_page_confirm(
    sample: TestClient,
) -> None:
    held_back = LITO_KEYS[0]
    schedule(held_back, "Invented: legislated to change from 1 July 2028.")
    preview = sample.get("/api/rule-figures/carry", params={"financial_year": 2027}).json()
    shown = {p["key"]: p["scheduled_change"] for p in preview["proposals"]}
    assert shown[held_back] == "Invented: legislated to change from 1 July 2028."
    assert shown[LITO_KEYS[1]] is None
    carry(sample, 2027)
    assert by_key(sample, 2027)[held_back]["scheduled_change"] is not None  # on the carried row
    res = sample.post(
        "/api/rule-figures/confirm-page", json={"financial_year": 2027, "page_url": LITO_PAGE}
    )
    body = res.json()
    assert sorted(body["confirmed"]) == sorted(set(LITO_KEYS) - {held_back})
    assert [n["key"] for n in body["needs_own_confirm"]] == [held_back]
    figures = by_key(sample, 2027)
    assert figures[held_back]["capture"]["method"] == "carried"  # still provisional
    assert figures[held_back]["provisional_note"] == NOTE_2026
    # it is confirmed on its own row, which is allowed
    own = sample.post(f"/api/rule-figures/{figures[held_back]['capture']['id']}/confirm")
    assert own.status_code == 200 and own.json()["provisional_note"] is None


# --- Source pages ---------------------------------------------------------------------------


def test_the_low_income_tax_offset_keys_point_at_their_own_page() -> None:
    assert len(LITO_KEYS) == 5
    assert {s.page_url for s in SPECS if s.key in LITO_KEYS} == {LITO_PAGE}


def test_no_spec_points_at_a_parent_index_page() -> None:
    ato = "https://www.ato.gov.au/"
    parents = {
        ato + "individuals-and-families/income-deductions-offsets-and-records",
        ato + "individuals-and-families/medicare-and-private-health-insurance",
        ato + "individuals-and-families/investments-and-assets/capital-gains-tax",
        ato + "tax-rates-and-codes",
        ato + "businesses-and-organisations",
    }
    for spec in SPECS:
        assert spec.page_url.startswith(ato), spec.key
        assert spec.page_url.rstrip("/") not in parents, spec.key


# --- The sample household -------------------------------------------------------------------


def test_the_sample_household_has_a_year_started_from_earlier_figures_some_confirmed(
    sample_as_shipped: TestClient,
) -> None:
    figures = by_key(sample_as_shipped, 2028)

    def made(method: str) -> set[str]:
        return {k for k, f in figures.items() if f["capture"] and f["capture"]["method"] == method}

    confirmed, carried = made("typed"), made("carried")
    assert confirmed == {*LITO_KEYS, "tax.medicare.levy_rate"}
    assert RATE in carried and len(carried) > 20
    # the car rate carried from FY 2026-27 (the fixture's own capture), not from FY 2025-26
    assert figures[RATE]["provisional_note"] == "Same as 2026-27, not checked against the page"
    named = {p["key"] for p in estimate(sample_as_shipped, 2028)["provisional"]}
    assert carried & set(pack_rules.REQUIRED.values()) <= named
    assert not named & confirmed
