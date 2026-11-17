"""Rule pack ages on the projection and on saved runs (ADR-0018), against a real
temporary SQLite database and the synthetic household.

The ages appear in the response and the run's provenance, never in
the stored input; B6 turns them into super access years (tests/test_life_events.py).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.config import Settings
from finance360.core.models.audit import AuditEvent
from finance360.core.models.projection_run import ProjectionRun
from finance360.main import create_app
from support import TestClient, owner_client, without_income_streams

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
# The sample household carries one invented captured figure (FY 2026-27 car rate), so its label
# reads r8+1:<digest> (ADR-0022 section 3); the digest pins exactly that capture.
LABEL = "au/2026-27/r8+1:5be77e"


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings_ = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings_)) as c:
        yield without_income_streams(c)


def factory_of(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory  # type: ignore[attr-defined, no-any-return]


def scenario_id(client: TestClient, name: str = "Likely") -> int:
    return next(s["id"] for s in client.get("/api/scenarios").json() if s["name"] == name)


def live(client: TestClient, name: str = "Likely") -> dict[str, Any]:
    res = client.get(
        f"/api/projection/scenarios/{scenario_id(client, name)}", params={"as_at": AS_AT}
    )
    assert res.status_code == 200, res.text
    return res.json()


def ages_of(p: dict[str, Any]) -> dict[tuple[int, str], tuple[int, int, str]]:
    return {
        (v["member_id"], v["key"]): (v["years"], v["months"], v["layer"])
        for v in p["rules"]["values"]
    }


def test_each_member_gets_all_three_ages_from_the_pack_with_sources(sample: TestClient) -> None:
    p = live(sample)
    assert p["rules"]["pack_version"] == LABEL
    members = [m["id"] for m in p["members"]]
    # Both synthetic Members were born in the 1980s: preservation 60, unrestricted 65, pension 67.
    assert ages_of(p) == {
        (m, key): (years, 0, "rule_pack")
        for m in members
        for key, years in (
            ("preservation_age", 60),
            ("unrestricted_super_age", 65),
            ("age_pension_age", 67),
        )
    }
    assert {x["key"] for x in p["rules"]["parameters"]} == {
        "preservation_age",
        "unrestricted_super_age",
        "age_pension_age",
    }
    assert all(x["source"] for x in p["rules"]["parameters"])


def test_the_ages_are_not_in_the_stored_input(sample: TestClient) -> None:
    sample.post("/api/projection/runs", json={"scenario_id": scenario_id(sample), "as_at": AS_AT})
    with factory_of(sample)() as session:
        row = session.scalars(select(ProjectionRun)).one()
        assert "preservation" not in row.input_json and "age_pension" not in row.input_json
        stored = json.loads(row.input_json)
    # The input keeps exactly the fields it had before B10 (no rules block until B12), plus the
    # line debits of input version 2 (ADR-0028).
    assert set(stored) == {
        "as_at",
        "currency",
        "debits",
        "drawdown_order",
        "inflation",
        "inflows",
        "input_version",
        "lines",
        "members",
        "outflows",
        "years",
    }


def test_saving_records_the_pack_version_and_reopens_with_the_same_ages(
    sample: TestClient,
) -> None:
    before = live(sample)
    saved = sample.post(
        "/api/projection/runs", json={"scenario_id": scenario_id(sample), "as_at": AS_AT}
    )
    assert saved.status_code == 201, saved.text
    header = saved.json()
    assert header["rule_pack_version"] == LABEL
    assert sample.get("/api/projection/runs").json()[0]["rule_pack_version"] == LABEL
    with factory_of(sample)() as session:
        row = session.get(ProjectionRun, header["id"])
        assert row is not None and row.rule_pack_version == LABEL
        audited = session.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "projection_run")
        ).one()
        assert audited.after is not None and audited.after["rule_pack_version"] == LABEL
    reopened = sample.get(f"/api/projection/runs/{header['id']}").json()
    assert reopened["rules"] == before["rules"]


def test_a_run_saved_before_rule_packs_keeps_null_and_still_renders(sample: TestClient) -> None:
    """A B1 to B5 run has no ``rules`` in its provenance and a null version."""
    header = sample.post(
        "/api/projection/runs", json={"scenario_id": scenario_id(sample), "as_at": AS_AT}
    ).json()
    with factory_of(sample)() as session:
        row = session.get(ProjectionRun, header["id"])
        assert row is not None
        provenance = json.loads(row.provenance_json)
        del provenance["rules"]
        legacy = ProjectionRun(
            created_at=row.created_at,
            label="Legacy",
            scenario_id=row.scenario_id,
            scenario_name=row.scenario_name,
            engine_version=row.engine_version,
            input_version=row.input_version,
            rule_pack_version=None,
            input_json=row.input_json,
            result_json=row.result_json,
            sustainable_json=row.sustainable_json,
            provenance_json=json.dumps(provenance),
            funds_last_to_age=row.funds_last_to_age,
            lasts_beyond_horizon=row.lasts_beyond_horizon,
        )
        session.add(legacy)
        session.commit()
        legacy_id = legacy.id
    listed = {r["id"]: r for r in sample.get("/api/projection/runs").json()}
    assert listed[legacy_id]["rule_pack_version"] is None
    res = sample.get(f"/api/projection/runs/{legacy_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["rules"] is None and body["rows"]  # renders, with no rule values
    assert body["rows"] == sample.get(f"/api/projection/runs/{header['id']}").json()["rows"]


def test_rerun_of_an_old_run_keeps_the_rules_it_recorded(
    sample: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finance360.capabilities.projection import engine

    old = sample.post(
        "/api/projection/runs", json={"scenario_id": scenario_id(sample), "as_at": AS_AT}
    ).json()
    monkeypatch.setattr(engine, "ENGINE_VERSION", engine.ENGINE_VERSION + 1)
    new = sample.post(f"/api/projection/runs/{old['id']}/rerun").json()
    assert new["rule_pack_version"] == LABEL
    assert (
        sample.get(f"/api/projection/runs/{new['id']}").json()["rules"]
        == sample.get(f"/api/projection/runs/{old['id']}").json()["rules"]
    )


# --- the override hook, end to end ---------------------------------------------------------


def put(client: TestClient, changes: list[dict[str, str]], scenario: int | None = None) -> Any:
    return client.put("/api/assumptions", json={"scenario_id": scenario, "changes": changes})


def test_a_scenario_override_replaces_an_age_for_every_member_and_is_labelled(
    sample: TestClient,
) -> None:
    worst = scenario_id(sample, "Worst")
    figures_before = live(sample, "Worst")
    res = put(
        sample,
        [{"key": "rule.age_pension_age", "scope_type": "household", "value": "68y6m"}],
        worst,
    )
    assert res.status_code == 200, res.text
    p = live(sample, "Worst")
    pension = {k: v for k, v in ages_of(p).items() if k[1] == "age_pension_age"}
    assert set(pension.values()) == {(68, 6, "scenario")}
    assert len(pension) == len(p["members"])
    other = {k: v for k, v in ages_of(p).items() if k[1] != "age_pension_age"}
    assert {v[2] for v in other.values()} == {"rule_pack"}  # the rest stay the pack's
    likely = live(sample, "Likely")  # another Scenario is not affected
    assert {v[:2] for k, v in ages_of(likely).items() if k[1] == "age_pension_age"} == {(67, 0)}
    # An override changes no projected figure in B10 (B6 will use the ages).
    assert p["rows"] == figures_before["rows"]
    assert p["sustainable"] == figures_before["sustainable"]


def test_a_household_default_override_applies_unless_a_scenario_states_its_own(
    sample: TestClient,
) -> None:
    assert (
        put(
            sample,
            [{"key": "rule.preservation_age", "scope_type": "household", "value": "58"}],
        ).status_code
        == 200
    )
    assert {v for k, v in ages_of(live(sample)).items() if k[1] == "preservation_age"} == {
        (58, 0, "household")
    }
    best = scenario_id(sample, "Best")
    put(sample, [{"key": "rule.preservation_age", "scope_type": "household", "value": "59"}], best)
    assert {v for k, v in ages_of(live(sample, "Best")).items() if k[1] == "preservation_age"} == {
        (59, 0, "scenario")
    }


def test_an_override_is_recorded_on_a_saved_run(sample: TestClient) -> None:
    worst = scenario_id(sample, "Worst")
    put(sample, [{"key": "rule.age_pension_age", "scope_type": "household", "value": "70"}], worst)
    run = sample.post("/api/projection/runs", json={"scenario_id": worst, "as_at": AS_AT}).json()
    reopened = sample.get(f"/api/projection/runs/{run['id']}").json()
    assert {v[:3] for k, v in ages_of(reopened).items() if k[1] == "age_pension_age"} == {
        (70, 0, "scenario")
    }
    assert reopened["rules"]["pack_version"] == LABEL  # overrides are not part of the label


@pytest.mark.parametrize(
    ("key", "scope", "ref", "value", "field"),
    [
        ("rule.nonsense", "household", "", "60", "rule.nonsense"),
        ("rule.age_pension_age", "member", "1", "67", "rule.age_pension_age.1"),
        ("rule.age_pension_age", "household", "", "66.5", "rule.age_pension_age"),
        ("rule.age_pension_age", "household", "", "sixty", "rule.age_pension_age"),
    ],
)
def test_a_bad_rule_override_is_refused_in_the_shared_error_shape(
    sample: TestClient, key: str, scope: str, ref: str, value: str, field: str
) -> None:
    res = put(sample, [{"key": key, "scope_type": scope, "scope_ref": ref, "value": value}])
    assert res.status_code == 422
    assert [e["field"] for e in res.json()["errors"]] == [field]
