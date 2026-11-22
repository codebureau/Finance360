"""Household facts (ADR-0029 section 1): the pure resolver (unit, Hypothesis) and the service and
API with audit (integration, real migrated SQLite, synthetic data only)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.household import facts as f
from finance360.config import Settings
from finance360.core.models.audit import AuditEvent
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
NONE = f.StoredFacts()
NO_HOMES: frozenset[int] = frozenset()


# --- the pure resolver ---------------------------------------------------------------------


def test_blank_facts_default_from_the_members_and_say_so() -> None:
    two = f.resolve_facts(NONE, 2, frozenset({7}))
    assert (two.couple_status.value, two.couple_status.source) == ("couple", f.DEFAULT)
    assert two.couple_status.label == "default, not entered"
    assert (two.homeowner.value, two.homeowner.source) == (True, f.DEFAULT)
    home = two.principal_home_account_id
    assert (home.value, home.source) == (7, f.DEFAULT)
    assert two.state == f.STATE_OK and two.reasons == ()
    one = f.resolve_facts(NONE, 1, NO_HOMES)
    assert one.couple_status.value == "single"
    assert one.principal_home_account_id.value is None  # no Property Account: nothing to exempt
    assert one.state == f.STATE_OK


def test_entered_facts_are_labelled_entered() -> None:
    stored = f.StoredFacts("single", False, 7)
    got = f.resolve_facts(stored, 1, frozenset({7}))
    assert got.couple_status.label == "entered" and got.homeowner.label == "entered"
    assert got.homeowner.value is False
    assert got.principal_home_account_id.value is None  # a non-homeowner: stored home is ignored


def test_a_homeowner_with_several_property_accounts_and_no_choice_needs_one() -> None:
    got = f.resolve_facts(NONE, 2, frozenset({7, 8}))
    assert got.state == f.STATE_NEEDS_HOME_CHOICE
    assert got.principal_home_account_id.value is None
    assert got.reasons == ("Choose which Property Account is the principal home.",)
    chosen = f.resolve_facts(f.StoredFacts(principal_home_account_id=8), 2, frozenset({7, 8}))
    assert chosen.state == f.STATE_OK
    home = chosen.principal_home_account_id
    assert (home.value, home.source) == (8, f.ENTERED)


def test_a_non_homeowner_with_several_property_accounts_needs_no_choice() -> None:
    got = f.resolve_facts(f.StoredFacts(homeowner=False), 2, frozenset({7, 8}))
    assert got.state == f.STATE_OK


def test_a_chosen_home_that_is_not_an_open_property_is_treated_as_unset() -> None:
    stale = f.StoredFacts(principal_home_account_id=99)
    home = f.resolve_facts(stale, 2, frozenset({7})).principal_home_account_id
    assert (home.value, home.source) == (7, f.DEFAULT)
    assert f.resolve_facts(stale, 2, frozenset({7, 8})).state == f.STATE_NEEDS_HOME_CHOICE


@pytest.mark.parametrize(
    ("status", "members"),
    [("couple", 1), ("single", 2), (None, 3), ("couple", 3), (None, 0)],
)
def test_a_household_that_does_not_fit_is_not_modelled(status: str | None, members: int) -> None:
    got = f.resolve_facts(f.StoredFacts(couple_status=status), members, frozenset({7}))
    assert got.state == f.STATE_NOT_MODELLED and got.reasons


def test_not_modelled_wins_over_needing_a_choice() -> None:
    got = f.resolve_facts(NONE, 3, frozenset({7, 8}))
    assert got.state == f.STATE_NOT_MODELLED


@given(
    status=st.sampled_from([None, "single", "couple"]),
    homeowner=st.sampled_from([None, True, False]),
    home=st.one_of(st.none(), st.integers(1, 6)),
    members=st.integers(0, 4),
    open_ids=st.frozensets(st.integers(1, 6), max_size=4),
)
def test_resolution_invariants(
    status: str | None,
    homeowner: bool | None,
    home: int | None,
    members: int,
    open_ids: frozenset[int],
) -> None:
    got = f.resolve_facts(f.StoredFacts(status, homeowner, home), members, open_ids)
    chosen = got.principal_home_account_id.value
    assert chosen is None or chosen in open_ids  # never an Account that is not open
    if got.homeowner.value is False:
        assert chosen is None
    if got.state == f.STATE_OK:
        assert members in (1, 2) and not got.reasons
    if got.state == f.STATE_NEEDS_HOME_CHOICE:
        assert chosen is None and len(open_ids) > 1 and got.homeowner.value is True
    contradicts = status is not None and (status == "couple") != (members == 2)
    if members in (1, 2) and not contradicts:
        assert got.state != f.STATE_NOT_MODELLED


# --- service and API -----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _put(client: TestClient, **body: Any) -> Any:
    return client.put("/api/household/facts", json=body)


def _audit(client: TestClient) -> list[AuditEvent]:
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        assert isinstance(s, Session)
        events = s.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "household").order_by(AuditEvent.id)
        )
        return list(events)


def _property(client: TestClient, name: str) -> int:
    owner = client.get("/api/household").json()["members"][0]["id"]
    res = client.post(
        "/api/accounts",
        json={
            "name": name,
            "category_key": "property",
            "account_type_key": "principal_residence",
            "ownership": "sole",
            "owners": [{"member_id": owner}],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_the_sample_household_has_its_home_entered_and_the_rest_defaulted(
    client: TestClient,
) -> None:
    facts = client.get("/api/household").json()["facts"]
    assert facts["principal_home_name"] == "Family Home"
    assert facts["principal_home_account_id"]["source"] == "entered"
    assert facts["couple_status"]["value"] == "couple"
    assert facts["couple_status"]["label"] == "default, not entered"
    assert facts["homeowner"]["value"] is True and facts["homeowner"]["source"] == "default"
    assert facts["state"] == "ok"
    assert [a["name"] for a in facts["property_accounts"]] == ["Family Home"]
    # The fixture's home is set through the service, so it is audited with source ``fixture``.
    (event,) = [e for e in _audit(client) if "principal home" in e.summary]
    assert event.source == "fixture"
    assert event.summary == "Set the Household principal home to Family Home"


def test_setting_clearing_and_resetting_each_fact_is_audited(client: TestClient) -> None:
    base = len(_audit(client))
    assert _put(client, couple_status="single").status_code == 200
    assert _put(client, homeowner=False).status_code == 200
    got = _put(client, couple_status=None, homeowner=None, principal_home_account_id=None).json()
    assert got["facts"]["couple_status"]["source"] == "default"
    assert got["facts"]["stored_couple_status"] is None
    assert got["facts"]["stored_homeowner"] is None
    assert got["facts"]["stored_principal_home_account_id"] is None
    events = _audit(client)[base:]
    assert [e.summary for e in events] == [
        "Set the Household couple status to single",
        "Set the Household homeowner to no",
        "Reset the Household couple status to not entered",
        "Reset the Household homeowner to not entered",
        "Reset the Household principal home to not entered",
    ]
    assert events[0].before == {"couple_status": None}
    assert events[0].after == {"couple_status": "single"}
    assert events[1].after == {"homeowner": False}
    assert events[3].before == {"homeowner": False} and events[3].after == {"homeowner": None}
    assert all(e.action == "update" and e.source == "api" for e in events)


def test_an_unchanged_fact_writes_no_audit_event(client: TestClient) -> None:
    _put(client, couple_status="couple")
    base = len(_audit(client))
    _put(client, couple_status="couple")
    _put(client)  # nothing given: nothing changes
    assert len(_audit(client)) == base


def test_a_fact_left_out_is_left_alone(client: TestClient) -> None:
    _put(client, couple_status="couple", homeowner=True)
    got = _put(client, homeowner=False).json()["facts"]
    assert got["stored_couple_status"] == "couple" and got["stored_homeowner"] is False


def test_a_second_property_with_no_home_chosen_needs_a_choice(client: TestClient) -> None:
    _put(client, principal_home_account_id=None)
    second = _property(client, "Sample Unit")
    facts = client.get("/api/household").json()["facts"]
    assert facts["state"] == "needs_home_choice"
    assert facts["principal_home_account_id"]["value"] is None
    assert len(facts["property_accounts"]) == 2
    chosen = _put(client, principal_home_account_id=second).json()["facts"]
    assert chosen["state"] == "ok" and chosen["principal_home_name"] == "Sample Unit"


def test_a_home_must_be_an_open_property_account(client: TestClient) -> None:
    everyday = next(
        a["id"] for a in client.get("/api/accounts").json() if a["category_key"] != "property"
    )
    for bad in (everyday, 99999):
        res = _put(client, principal_home_account_id=bad)
        assert res.status_code == 422
        assert res.json()["errors"][0]["field"] == "principal_home_account_id"
    before = len(_audit(client))
    res = _put(client, couple_status="partners")
    assert res.status_code == 422 and res.json()["errors"][0]["field"] == "couple_status"
    assert len(_audit(client)) == before


def test_a_closed_home_is_treated_as_unset(client: TestClient) -> None:
    second = _property(client, "Sample Unit")
    home = client.get("/api/household").json()["facts"]["stored_principal_home_account_id"]
    closed = client.post(f"/api/accounts/{home}/close", json={"confirm_non_zero": True})
    assert closed.status_code == 200, closed.text
    facts = client.get("/api/household").json()["facts"]
    assert facts["stored_principal_home_account_id"] == home  # still stored, but not effective
    assert facts["principal_home_account_id"]["value"] == second  # the only open Property
    assert facts["principal_home_account_id"]["source"] == "default"


def test_a_contradicting_couple_status_is_accepted_and_reported_not_modelled(
    client: TestClient,
) -> None:
    facts = _put(client, couple_status="single").json()["facts"]
    assert facts["state"] == "not_modelled" and facts["reasons"]


def test_a_restart_does_not_undo_the_owners_reset_of_the_home(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        assert _put(c, principal_home_account_id=None).status_code == 200
    with owner_client(create_app(settings)) as c:
        facts = c.get("/api/household").json()["facts"]
        assert facts["stored_principal_home_account_id"] is None
        assert len([e for e in _audit(c) if "principal home" in e.summary]) == 2
