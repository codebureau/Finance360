"""Spending phases end to end (ADR-0017 section 6): the service against a real migrated
SQLite database, then the HTTP routes and the projection that uses them, on the synthetic household.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.projection import scenarios, seed, spending
from finance360.config import Settings
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.models.scenario import Scenario, SpendingPhase
from finance360.core.money import Money
from finance360.main import create_app
from support import TestClient, owner_client

CTX = AuditContext("owner", "login:1", "api")
TODAY = date(2026, 10, 25)
AS_AT = "2026-10-25"
REPO = Path(__file__).resolve().parents[2]
RETIRE = spending.LAST_RETIREMENT
EARLY = "Retire early, spend more"


# --- the service ---------------------------------------------------------------------------


@pytest.fixture
def s(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        household.ensure_household(session)
        household.add_member(session, CTX, "Alex Sample", date(1984, 3, 12), today=TODAY)
        seed.seed_assumptions(session)
        session.commit()
        yield session


def scenario(session: Session, key: str) -> Scenario:
    return session.scalars(select(Scenario).where(Scenario.key == key)).one()


def trail(session: Session) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "spending_phase")
            .order_by(AuditEvent.id)
        )
    )


def plan(session: Session, scenario_id: int | None) -> list[tuple[str, int, int]]:
    session.expire_all()
    return [
        (p.anchor, p.offset_years, p.annual_amount.amount_minor)
        for p in scenarios.layer_phases(session, scenario_id)
    ]


def test_a_list_is_stored_as_integer_minor_units_and_read_back_in_order(s: Session) -> None:
    scenarios.set_spending_phases(
        s, CTX, None, [(RETIRE, 10, "55000.50"), ("start", 0, "1,000"), (RETIRE, 0, "70000")]
    )
    assert plan(s, None) == [("start", 0, 100_000), (RETIRE, 0, 7_000_000), (RETIRE, 10, 5_500_050)]
    row = s.scalars(select(SpendingPhase).where(SpendingPhase.offset_years == 10)).one()
    assert (row.amount_minor, row.amount_ccy, row.scenario_id) == (5_500_050, "AUD", None)


def test_each_add_change_and_removal_is_its_own_audit_record(s: Session) -> None:
    scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "70000"), (RETIRE, 10, "55000")])
    scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "72000")])  # change one, drop one
    created = trail(s)
    assert [(e.action, e.after is None) for e in created] == [
        ("create", False),
        ("create", False),
        ("update", False),
        ("delete", True),
    ]
    first, _, update, delete = created
    assert first.after == {
        "scenario": None,
        "anchor": RETIRE,
        "offset_years": 0,
        "annual_amount": {"minor": 7_000_000, "ccy": "AUD"},
    }
    assert update.before["annual_amount"] == {"minor": 7_000_000, "ccy": "AUD"}  # type: ignore[index]
    assert update.after["annual_amount"] == {"minor": 7_200_000, "ccy": "AUD"}  # type: ignore[index]
    assert update.summary == (
        "Changed a spending phase in Household default from the last retirement "
        "from 70000.00 to 72000.00 a year"
    )
    assert delete.before["offset_years"] == 10  # type: ignore[index]
    assert delete.summary == (
        "Removed a spending phase from Household default from the last retirement + 10 years"
    )
    assert first.entity_id == update.entity_id


def test_an_unchanged_list_writes_nothing(s: Session) -> None:
    scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "70000")])
    before = len(trail(s))
    assert scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "70000.00")]) == []
    assert len(trail(s)) == before


def test_a_bad_list_is_refused_whole_and_writes_nothing(s: Session) -> None:
    scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "70000")])
    with pytest.raises(ValidationError) as raised:
        scenarios.set_spending_phases(
            s, CTX, None, [(RETIRE, 0, "50000"), (RETIRE, 0, "40000"), ("start", 0, "0")]
        )
    assert [e.field for e in raised.value.errors] == [
        "spending_phases.1.offset_years",
        "spending_phases.2.annual_amount",
    ]
    assert plan(s, None) == [(RETIRE, 0, 7_000_000)]  # the old list is untouched
    assert len(trail(s)) == 1


def test_a_scenarios_list_replaces_the_default_as_a_whole_and_none_inherits_again(
    s: Session,
) -> None:
    scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "70000"), (RETIRE, 10, "55000")])
    likely = scenario(s, "likely").id
    scenarios.set_spending_phases(s, CTX, likely, [("start", 0, "90000")])
    assert plan(s, likely) == [("start", 0, 9_000_000)]
    assert plan(s, None) == [(RETIRE, 0, 7_000_000), (RETIRE, 10, 5_500_000)]
    assert scenarios.scenarios_with_own_spending(s) == {likely: "Likely"}

    scenarios.set_spending_phases(s, CTX, likely, None)  # back to the Household default list
    assert plan(s, likely) == []
    assert scenarios.scenarios_with_own_spending(s) == {}
    assert "inherits again" in trail(s)[-1].summary


def test_changing_the_default_list_names_the_scenarios_that_state_their_own(s: Session) -> None:
    scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "70000")])
    best, worst = scenario(s, "best").id, scenario(s, "worst").id
    assert (
        scenarios.set_spending_phases(s, CTX, best, [(RETIRE, 0, "80000")]) == []
    )  # not a default
    scenarios.set_spending_phases(s, CTX, worst, [(RETIRE, 0, "60000")])
    reviews = scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "71000")])
    assert [(r.key, r.scope_type, r.scope_ref, r.scenarios) for r in reviews] == [
        ("spending_phases", "household", "", ("Best", "Worst"))
    ]
    # Likely states none, so it moved with the default and is not named; an unchanged list is quiet.
    assert scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "71000")]) == []


def test_cloning_copies_the_list_and_deleting_removes_it_and_audits_it(s: Session) -> None:
    source = scenarios.create_scenario(s, CTX, "Source")
    scenarios.set_spending_phases(s, CTX, source.id, [(RETIRE, 0, "10"), (RETIRE, 5, "5")])
    clone = scenarios.create_scenario(s, CTX, "Clone", clone_from=source.id)
    assert plan(s, clone.id) == plan(s, source.id) == [(RETIRE, 0, 1_000), (RETIRE, 5, 500)]
    scenarios.set_spending_phases(s, CTX, source.id, [(RETIRE, 0, "99")])  # no parent link
    assert plan(s, clone.id) == [(RETIRE, 0, 1_000), (RETIRE, 5, 500)]
    scenarios.delete_scenario(s, CTX, clone.id)
    assert s.scalar(select(func.count()).select_from(SpendingPhase)) == 1  # only the source's
    deleted = s.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_type == "scenario", AuditEvent.action == "delete"
        )
    ).one()
    assert [p["offset_years"] for p in deleted.before["spending_phases"]] == [0, 5]  # type: ignore[index]


def test_the_table_refuses_a_second_phase_at_the_same_point_in_a_layer(s: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "10")])
    s.add(
        SpendingPhase(
            scenario_id=None, anchor=RETIRE, offset_years=0, amount_minor=1, amount_ccy="AUD"
        )
    )
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()


def test_an_unknown_scenario_is_not_found(s: Session) -> None:
    with pytest.raises(NotFoundError):
        scenarios.set_spending_phases(s, CTX, 9999, [(RETIRE, 0, "10")])


def test_an_unset_list_is_an_error_naming_it_and_a_set_one_resolves(s: Session) -> None:
    scenarios.set_assumptions(
        s,
        CTX,
        None,
        [
            scenarios.Change("annual_saving", "household", "", "0"),
            scenarios.Change("retirement_age", "member", "1", "60"),
        ],
    )
    likely = scenario(s, "likely").id
    with pytest.raises(ValidationError) as raised:
        scenarios.resolve_for(s, likely)
    assert [e.field for e in raised.value.errors] == ["spending_phases"]
    assert "Spending has no phases" in raised.value.errors[0].message
    scenarios.set_spending_phases(s, CTX, None, [(RETIRE, 0, "70000")])
    _, resolved = scenarios.resolve_for(s, likely)
    assert resolved.spending_phases == (spending.Phase(RETIRE, 0, Money(7_000_000, "AUD")),)


# --- HTTP, on the synthetic household ------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def phase_json(anchor: str, offset: Any, amount: str) -> dict[str, Any]:
    return {"anchor": anchor, "offset_years": offset, "annual_amount": amount}


def ids(client: TestClient) -> dict[str, int]:
    return {s["name"]: s["id"] for s in client.get("/api/scenarios").json()}


def projection(client: TestClient, name: str) -> dict[str, Any]:
    res = client.get(f"/api/projection/scenarios/{ids(client)[name]}", params={"as_at": AS_AT})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_fixture_has_the_converted_spending_a_slower_phase_and_a_working_years_phase(
    sample: TestClient,
) -> None:
    layer = sample.get("/api/spending-phases").json()
    assert layer == {
        "scenario_id": None,
        "phases": [
            phase_json("start", 0, "150000.00"),  # the working years: the fixture has salaries
            phase_json(RETIRE, 0, "70000.00"),
            phase_json(RETIRE, 10, "55000.00"),
        ],
        "overridden_by": [EARLY],
    }
    own = sample.get("/api/spending-phases", params={"scenario_id": ids(sample)[EARLY]}).json()
    assert own["phases"] == [phase_json("start", 0, "165000.00"), phase_json(RETIRE, 0, "85000.00")]
    listed = {s["name"]: s["spending_is_default"] for s in sample.get("/api/scenarios").json()}
    assert listed == {
        "Best": True,
        "Likely": True,
        "Worst": True,
        EARLY: False,
        "Tax on, Alex's pay climbs": True,
    }


def test_the_projection_reports_the_phases_it_used_resolved_to_years(sample: TestClient) -> None:
    p = projection(sample, "Likely")
    last = max(m["retirement_year"] for m in p["members"])
    assert p["spending"]["layer"] == "household"
    assert [
        (
            x["anchor"],
            x["offset_years"],
            x["annual_amount"],
            x["from_year"],
            x["until_year"],
            x["empty"],
        )
        for x in p["spending"]["phases"]
    ] == [
        ("start", 0, "150000.00", 0, last, False),
        (RETIRE, 0, "70000.00", last, last + 10, False),
        (RETIRE, 10, "55000.00", last + 10, None, False),
    ]
    # The working years' spending (the salaries fund it) until the last retirement, then 70,000.
    assert all(r["need"] == "150000.00" for r in p["rows"] if r["t"] < last)
    assert p["rows"][last]["need"] == "70000.00"


def test_the_slower_phase_lowers_the_need_ten_years_after_the_last_retirement(
    sample: TestClient,
) -> None:
    p = projection(sample, "Likely")
    last = max(m["retirement_year"] for m in p["members"])
    need = {r["t"]: r["need"] for r in p["rows"]}
    assert need[last] == need[last + 9]
    assert need[last + 10] == need[len(need) - 1]
    assert Decimal(need[last + 10]) < Decimal(need[last])


def test_retiring_earlier_moves_the_phases_with_it(sample: TestClient) -> None:
    before = projection(sample, "Likely")["spending"]["phases"]
    res = sample.put(
        "/api/assumptions",
        json={
            "changes": [
                {"key": "retirement_age", "scope_type": "member", "scope_ref": "2", "value": "55"}
            ]
        },
    )
    assert res.status_code == 200, res.text
    after = projection(sample, "Likely")["spending"]["phases"]
    # Jordan's retirement was the last (62); now Alex's (60) is, 4 years earlier than 62 - 40.
    assert after[0]["from_year"] == before[0]["from_year"] == 0  # the start phase does not move
    assert after[1]["from_year"] < before[1]["from_year"]
    assert after[2]["from_year"] - after[1]["from_year"] == 10  # the offset is unchanged


def test_putting_a_default_list_replaces_it_whole_and_flags_the_scenario_with_its_own(
    sample: TestClient,
) -> None:
    res = sample.put(
        "/api/spending-phases",
        json={"phases": [phase_json("start", 0, "30000"), phase_json(RETIRE, 0, "60000")]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [p["annual_amount"] for p in body["phases"]] == [
        "30000.00",
        "60000.00",
    ]  # the 55,000 went
    assert body["review"] == [
        {"key": "spending_phases", "scope_type": "household", "scope_ref": "", "scenarios": [EARLY]}
    ]
    assert projection(sample, "Likely")["spending"]["phases"][0]["anchor"] == "start"
    # The Scenario with its own list did not move.
    assert [x["annual_amount"] for x in projection(sample, EARLY)["spending"]["phases"]] == [
        "165000.00",
        "85000.00",
    ]


def test_a_scenario_list_can_be_set_and_cleared(sample: TestClient) -> None:
    worst = ids(sample)["Worst"]
    res = sample.put(
        "/api/spending-phases",
        json={
            "scenario_id": worst,
            "phases": [phase_json("start", 0, "80000"), phase_json(RETIRE, 0, "40000")],
        },
    )
    assert res.status_code == 200 and res.json()["review"] == []
    assert projection(sample, "Worst")["spending"]["layer"] == "scenario"
    cleared = sample.put("/api/spending-phases", json={"scenario_id": worst, "phases": None})
    assert cleared.status_code == 200 and cleared.json()["phases"] == []
    assert projection(sample, "Worst")["spending"]["layer"] == "household"


def test_validation_uses_the_shared_error_shape_and_writes_nothing(sample: TestClient) -> None:
    res = sample.put(
        "/api/spending-phases",
        json={
            "phases": [
                phase_json(RETIRE, 0, "70000"),
                phase_json(RETIRE, 0, "-1"),
                phase_json("someday", 0, "1"),
            ]
        },
    )
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {
        "spending_phases.1.offset_years",  # a duplicate is reported even beside a bad amount
        "spending_phases.1.annual_amount",
        "spending_phases.2.anchor",
    }
    assert [p["annual_amount"] for p in sample.get("/api/spending-phases").json()["phases"]] == [
        "150000.00",
        "70000.00",
        "55000.00",
    ]
    assert (
        sample.put("/api/spending-phases", json={"scenario_id": 9999, "phases": []}).status_code
        == 404
    )
    odd = sample.put("/api/spending-phases", json={"phases": [phase_json(RETIRE, 1.5, "1")]})
    assert odd.status_code == 422 and "errors" in odd.json()


def test_the_edits_are_in_the_audit_trail(sample: TestClient) -> None:
    sample.put("/api/spending-phases", json={"phases": [phase_json(RETIRE, 0, "71000")]})
    trail_ = sample.get("/api/audit", params={"entity_type": "spending_phase"}).json()["items"]
    assert (
        trail_[0]["summary"]
        == "Removed a spending phase from Household default from the last retirement + 10 years"
    )
    assert {i["action"] for i in trail_} >= {"delete", "update"}
