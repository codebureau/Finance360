"""Saved projection runs (ADR-0016 sections 4 and 5): the canonical result serialiser,
reproducibility, immutability, and the save / list / reopen / re-run endpoints against a real
temporary SQLite database. The synthetic household only."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from test_projection_engine import aud, projection_inputs

from finance360.capabilities.projection import engine, result_json
from finance360.capabilities.projection import input as pinput
from finance360.capabilities.projection.engine import project
from finance360.capabilities.projection.input import InputError, ProjectionInput
from finance360.capabilities.projection.spending import LAST_RETIREMENT, Phase
from finance360.capabilities.projection.sustainable import SustainableSpending, sustainable_spending
from finance360.config import Settings
from finance360.core.models.audit import AuditEvent
from finance360.core.models.projection_run import ProjectionRun
from finance360.core.money import Money
from finance360.main import create_app
from support import TestClient, owner_client, without_income_streams

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
SETTINGS = settings(max_examples=100, deadline=None)


# --- the canonical serialiser (pure) -------------------------------------------------------


@SETTINGS
@given(projection_inputs())
def test_the_result_round_trips_exactly(projection_input: ProjectionInput) -> None:
    result = project(projection_input)
    text_ = result_json.result_to_json(result)
    assert result_json.result_from_json(text_) == result
    assert result_json.result_to_json(result_json.result_from_json(text_)) == text_


@settings(max_examples=40, deadline=None)
@given(
    projection_inputs(),
)
def test_the_sustainable_output_round_trips_exactly(projection_input: ProjectionInput) -> None:
    most = sustainable_spending(projection_input, (Phase(LAST_RETIREMENT, 0, aud(1)),), 0)
    text_ = result_json.sustainable_to_json(most)
    assert result_json.sustainable_from_json(text_) == most
    capped = SustainableSpending(aud(10**11), True)
    assert result_json.sustainable_from_json(result_json.sustainable_to_json(capped)) == capped


def test_the_result_json_has_the_documented_shape() -> None:
    from test_projection_input import sample

    payload = result_json.loads(result_json.result_to_json(project(sample())))
    assert isinstance(payload, dict)
    first = payload["years"][0]
    assert first["need"] == {"ccy": "AUD", "minor": first["need"]["minor"]}
    assert isinstance(first["lines"][0]["closing"]["minor"], int)
    assert set(first["lines"][0]) == {
        "category_key",
        "member_id",
        "opening",
        "growth",
        "credited",
        "debited",
        "withdrawn",
        "closing",
    }
    assert first["debits"] == []
    assert payload["engine_version"] == engine.ENGINE_VERSION


def test_a_float_is_rejected_on_write_and_on_read() -> None:
    with pytest.raises(InputError):
        result_json.dumps({"years": [{"need": {"minor": 1.5, "ccy": "AUD"}}]})
    with pytest.raises(InputError):
        result_json.dumps({"deep": [1, {"x": 2.0}]})
    for fragment in ("1.5", "1e3", "NaN"):
        with pytest.raises(InputError):
            result_json.sustainable_from_json(
                '{"amount":{"ccy":"AUD","minor":' + fragment + '},"capped":false}'
            )


def test_malformed_result_json_raises_input_error() -> None:
    for bad in ("not json", "[]", '{"years":[],"engine_version":"1"}'):
        with pytest.raises(InputError):
            result_json.result_from_json(bad)


# --- the endpoints, against the synthetic household ----------------------------------------


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


def scenario_id(client: TestClient, name: str) -> int:
    return next(s["id"] for s in client.get("/api/scenarios").json() if s["name"] == name)


def save(client: TestClient, name: str = "Likely", **extra: Any) -> dict[str, Any]:
    res = client.post(
        "/api/projection/runs",
        json={"scenario_id": scenario_id(client, name), "as_at": AS_AT, **extra},
    )
    assert res.status_code == 201, res.text
    return res.json()


def detail(client: TestClient, run_id: int) -> dict[str, Any]:
    res = client.get(f"/api/projection/runs/{run_id}")
    assert res.status_code == 200, res.text
    return res.json()


def live(client: TestClient, name: str = "Likely") -> dict[str, Any]:
    res = client.get(
        f"/api/projection/scenarios/{scenario_id(client, name)}", params={"as_at": AS_AT}
    )
    assert res.status_code == 200, res.text
    return res.json()


def figures(p: dict[str, Any]) -> dict[str, Any]:
    """A projection without the parts that are about identity, not figures."""
    return {k: v for k, v in p.items() if k not in ("scenario", "run")}


def test_saving_a_run_lists_it_and_reopens_exactly_as_computed(sample: TestClient) -> None:
    before = live(sample)
    header = save(sample, label="  Baseline  ")
    assert header["label"] == "Baseline"
    assert header["scenario_name"] == "Likely"
    assert header["engine_version"] == engine.ENGINE_VERSION and header["current_engine"] is True
    assert header["funds_last_to_age"] == before["funds_last_to_age"]
    assert header["lasts_beyond_horizon"] == before["lasts_beyond_horizon"]

    runs = sample.get("/api/projection/runs").json()
    assert [r["id"] for r in runs] == [header["id"]]
    assert runs[0] == header

    reopened = detail(sample, header["id"])
    assert figures(reopened) == figures(before)  # rows, sustainable, provenance, members, flagged
    assert reopened["run"] == header
    assert reopened["scenario"]["name"] == "Likely"


def test_changing_an_assumption_after_saving_leaves_the_run_unchanged(sample: TestClient) -> None:
    header = save(sample)
    saved = detail(sample, header["id"])
    changed = sample.put(
        "/api/assumptions",
        json={
            "changes": [
                {
                    "key": "return_rate",
                    "scope_type": "category",
                    "scope_ref": "investments",
                    "value": "1.0",
                },
            ]
        },
    )
    assert changed.status_code == 200
    # ...and so is the spending plan: the saved run keeps the phases it used.
    replaced = sample.put(
        "/api/spending-phases",
        json={
            "phases": [{"anchor": "last_retirement", "offset_years": 0, "annual_amount": "95000"}]
        },
    )
    assert replaced.status_code == 200
    assert [p["annual_amount"] for p in live(sample)["spending"]["phases"]] == ["95000.00"]
    assert live(sample)["rows"] != saved["rows"]  # the live projection moved...
    assert detail(sample, header["id"]) == saved  # ...the saved run did not
    stated = {(x["key"], x["scope_ref"]): x["value"] for x in saved["provenance"]}
    assert stated[("return_rate", "investments")] != "1.0"  # provenance shows what it used


def test_a_saved_run_reproduces_from_its_stored_input(sample: TestClient) -> None:
    """The reproducibility contract (ADR-0016 s4): re-projecting the stored input gives the stored
    result and the stored sustainable output, reading nothing live."""
    header = save(sample, "Worst")
    with factory_of(sample)() as session:
        row = session.get(ProjectionRun, header["id"])
        assert row is not None
        assert row.sustainable_json is not None
        reloaded = pinput.from_json(row.input_json)
        assert project(reloaded) == result_json.result_from_json(row.result_json)
        saved = detail(sample, row.id)
        plan = tuple(
            Phase(
                p["anchor"],
                p["offset_years"],
                Money(int(p["annual_amount"].replace(".", "")), "AUD"),
            )
            for p in saved["spending"]["phases"]
        )
        assert sustainable_spending(
            reloaded, plan, max(m["retirement_year"] for m in saved["members"])
        ) == result_json.sustainable_from_json(row.sustainable_json)
        assert pinput.to_json(reloaded) == row.input_json  # the stored text is canonical
        # B10 onward; "+1" is the sample household's invented captured figure
        assert row.rule_pack_version == "au/2026-27/r7+1:5be77e"


def test_saving_writes_one_audit_event_with_header_fields_only(sample: TestClient) -> None:
    header = save(sample, label="Audit me")
    with factory_of(sample)() as session:
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "projection_run"))
        )
    assert len(events) == 1
    event_ = events[0]
    assert (event_.action, event_.entity_id, event_.before) == ("create", header["id"], None)
    assert event_.after == {
        "label": "Audit me",
        "scenario_id": header["scenario_id"],
        "scenario_name": "Likely",
        "engine_version": engine.ENGINE_VERSION,
        "input_version": pinput.INPUT_VERSION,
        "funds_last_to_age": header["funds_last_to_age"],
        "lasts_beyond_horizon": header["lasts_beyond_horizon"],
        "rule_pack_version": "au/2026-27/r7+1:5be77e",
    }
    assert event_.summary == "Saved a projection run of 'Likely' (Audit me)"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE projection_run SET label = 'tampered'",
        "UPDATE projection_run SET result_json = '{}'",
        "DELETE FROM projection_run",
    ],
)
def test_a_saved_run_cannot_be_updated_or_deleted(sample: TestClient, statement: str) -> None:
    save(sample)
    with factory_of(sample)() as session, pytest.raises(DBAPIError, match="append-only"):
        session.execute(text(statement))


def test_the_payload_columns_must_be_valid_json(sample: TestClient) -> None:
    header = save(sample)
    with factory_of(sample)() as session:
        row = session.get(ProjectionRun, header["id"])
        assert row is not None
        session.expunge(row)
        for column in ("input_json", "result_json", "provenance_json"):
            fields = {
                c: getattr(row, c)
                for c in (
                    "created_at",
                    "scenario_id",
                    "scenario_name",
                    "engine_version",
                    "input_version",
                    "input_json",
                    "result_json",
                    "provenance_json",
                    "funds_last_to_age",
                    "lasts_beyond_horizon",
                )
            } | {column: "{not json"}
            with pytest.raises(IntegrityError):
                session.add(ProjectionRun(**fields))
                session.flush()
            session.rollback()


def test_a_scenario_with_runs_cannot_be_deleted(sample: TestClient) -> None:
    made = sample.post("/api/scenarios", json={"name": "Keeps its runs", "copy_from": None})
    new_id = made.json()["id"]
    run = sample.post("/api/projection/runs", json={"scenario_id": new_id, "as_at": AS_AT})
    assert run.status_code == 201

    blocked = sample.delete(f"/api/scenarios/{new_id}")
    assert blocked.status_code == 422
    assert blocked.json() == {
        "errors": [
            {
                "field": "scenario",
                "message": "Keeps its runs has 1 saved projection run, so it can't be deleted.",
            }
        ]
    }
    assert any(s["id"] == new_id for s in sample.get("/api/scenarios").json())
    assert detail(sample, run.json()["id"])["scenario"]["name"] == "Keeps its runs"

    # The database says the same, should the service ever be bypassed: RESTRICT, not SET NULL.
    with factory_of(sample)() as session, pytest.raises(IntegrityError):
        session.execute(text("DELETE FROM scenario WHERE id = :id"), {"id": new_id})


def test_a_scenario_without_runs_can_still_be_deleted(sample: TestClient) -> None:
    new_id = sample.post("/api/scenarios", json={"name": "Scratch"}).json()["id"]
    assert sample.delete(f"/api/scenarios/{new_id}").status_code == 204


def test_the_list_never_reads_a_payload(sample: TestClient) -> None:
    save(sample)
    save(sample, "Best")
    statements: list[str] = []
    engine_ = factory_of(sample).kw["bind"]

    def record(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        statements.append(statement)

    event.listen(engine_, "before_cursor_execute", record)
    try:
        listed = sample.get("/api/projection/runs").json()
    finally:
        event.remove(engine_, "before_cursor_execute", record)
    assert [r["scenario_name"] for r in listed] == ["Best", "Likely"]  # newest first
    reads = [s for s in statements if "projection_run" in s]
    assert reads, "the list should have queried projection_run"
    for statement in reads:
        for payload in ("input_json", "result_json", "sustainable_json", "provenance_json"):
            assert payload not in statement


def test_a_run_survives_a_renamed_scenario_and_keeps_its_name(sample: TestClient) -> None:
    new_id = sample.post("/api/scenarios", json={"name": "Before rename"}).json()["id"]
    run_id = sample.post(
        "/api/projection/runs", json={"scenario_id": new_id, "as_at": AS_AT}
    ).json()["id"]
    assert sample.put(f"/api/scenarios/{new_id}", json={"name": "After rename"}).status_code == 200
    assert detail(sample, run_id)["run"]["scenario_name"] == "Before rename"


def test_rerun_with_the_current_engine_saves_a_new_run(
    sample: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = save(sample, label="Old engine")
    before = detail(sample, old["id"])
    monkeypatch.setattr(engine, "ENGINE_VERSION", engine.ENGINE_VERSION + 1)

    reopened = detail(sample, old["id"])
    assert reopened["run"]["current_engine"] is False
    assert figures(reopened) == figures(before)  # still shown as computed by the old engine
    assert reopened["engine_version"] == engine.ENGINE_VERSION - 1

    again = sample.post(f"/api/projection/runs/{old['id']}/rerun")
    assert again.status_code == 201, again.text
    new = again.json()
    assert new["id"] != old["id"]
    assert new["engine_version"] == engine.ENGINE_VERSION and new["current_engine"] is True
    assert new["label"] == f"Re-run of run {old['id']}"
    assert (new["scenario_id"], new["scenario_name"]) == (old["scenario_id"], "Likely")

    assert detail(sample, old["id"]) == reopened  # the old run is untouched
    assert [r["id"] for r in sample.get("/api/projection/runs").json()] == [new["id"], old["id"]]
    # Same input, so the same figures; only the engine that computed them is named.
    redone = detail(sample, new["id"])
    assert redone["rows"] == before["rows"] and redone["sustainable"] == before["sustainable"]
    assert redone["engine_version"] == engine.ENGINE_VERSION

    with factory_of(sample)() as session:
        a, b = (session.get(ProjectionRun, i) for i in (old["id"], new["id"]))
        assert a is not None and b is not None and a.input_json == b.input_json
        audited = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "projection_run")
            .order_by(AuditEvent.id.desc())
        ).first()
    assert audited is not None and audited.after is not None
    assert audited.after["rerun_of"] == old["id"]


def test_rerun_is_refused_when_the_engine_is_current(sample: TestClient) -> None:
    run = save(sample)
    res = sample.post(f"/api/projection/runs/{run['id']}/rerun")
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "run"
    with factory_of(sample)() as session:
        assert session.scalar(select(func.count()).select_from(ProjectionRun)) == 1


def _saved_before_phases(client: TestClient, run_id: int) -> int:
    """Store a copy of a run as B5 to B7 saved it: no ``spending`` block, and the spending amount
    as the retired ``annual_spending`` Assumption (B8). Inserted, not updated: runs are
    append-only."""
    import json

    with factory_of(client).begin() as session:
        row = session.get(ProjectionRun, run_id)
        assert row is not None
        provenance = json.loads(row.provenance_json)
        phases = provenance.pop("spending")["phases"]
        assert len(phases) == 1 and phases[0]["anchor"] == LAST_RETIREMENT
        provenance["assumptions"].append(
            {
                "key": "annual_spending",
                "layer": "scenario",
                "scope_ref": "",
                "scope_type": "household",
                "value": phases[0]["annual_amount"],
            }
        )
        legacy = ProjectionRun(
            created_at=row.created_at,
            label="Saved before spending phases",
            scenario_id=row.scenario_id,
            scenario_name=row.scenario_name,
            engine_version=row.engine_version,
            input_version=row.input_version,
            rule_pack_version=row.rule_pack_version,
            input_json=row.input_json,
            result_json=row.result_json,
            sustainable_json=row.sustainable_json,
            provenance_json=json.dumps(provenance, sort_keys=True),
            funds_last_to_age=row.funds_last_to_age,
            lasts_beyond_horizon=row.lasts_beyond_horizon,
        )
        session.add(legacy)
        session.flush()
        return legacy.id


def test_a_run_saved_before_spending_phases_reopens_and_reruns_the_same(
    sample: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Retire early, spend more" spends one amount from the last retirement: what a pre-B8 run
    # stored as annual_spending.
    saved = save(sample, "Retire early, spend more")
    legacy_id = _saved_before_phases(sample, saved["id"])
    legacy = detail(sample, legacy_id)
    assert legacy["spending"] is None  # rendered from the snapshot, without a phase list
    original = detail(sample, saved["id"])
    assert (legacy["rows"], legacy["sustainable"]) == (original["rows"], original["sustainable"])

    monkeypatch.setattr(engine, "ENGINE_VERSION", engine.ENGINE_VERSION + 1)
    again = sample.post(f"/api/projection/runs/{legacy_id}/rerun")
    assert again.status_code == 201, again.text
    redone = detail(sample, again.json()["id"])
    # The single-phase search reproduces the constant-spending one exactly.
    assert redone["rows"] == legacy["rows"]
    assert redone["funds_last_to_age"] == legacy["funds_last_to_age"]
    assert redone["sustainable"] == legacy["sustainable"]


def _saved_by_engine_1(client: TestClient, run_id: int) -> int:
    """Store a copy of a run as engine 1 saved it (ADR-0028 s4): input version 1 without
    ``debits``, a result without ``debited`` or per-year ``debits``, and no provenance ``debits``.
    Inserted, not updated: runs are append-only."""
    import json

    with factory_of(client).begin() as session:
        row = session.get(ProjectionRun, run_id)
        assert row is not None
        stored = json.loads(row.input_json)
        stored.pop("debits")
        stored["input_version"] = 1
        result = json.loads(row.result_json)
        result["engine_version"], result["input_version"] = 1, 1
        for year in result["years"]:
            year.pop("debits")
            for line in year["lines"]:
                line.pop("debited")
        provenance = json.loads(row.provenance_json)
        provenance.pop("debits", None)
        legacy = ProjectionRun(
            created_at=row.created_at,
            label="Saved by engine 1",
            scenario_id=row.scenario_id,
            scenario_name=row.scenario_name,
            engine_version=1,
            input_version=1,
            rule_pack_version=row.rule_pack_version,
            input_json=json.dumps(stored, sort_keys=True),
            result_json=json.dumps(result, sort_keys=True),
            sustainable_json=row.sustainable_json,
            provenance_json=json.dumps(provenance, sort_keys=True),
            funds_last_to_age=row.funds_last_to_age,
            lasts_beyond_horizon=row.lasts_beyond_horizon,
        )
        session.add(legacy)
        session.flush()
        return legacy.id


def test_a_run_saved_by_engine_1_displays_as_computed_and_reruns_to_engine_2(
    sample: TestClient,
) -> None:
    saved = save(sample, "Retire early, spend more")
    legacy_id = _saved_by_engine_1(sample, saved["id"])
    legacy = detail(sample, legacy_id)
    original = detail(sample, saved["id"])
    # Shown as engine 1 computed it: no debits, nothing debited, the same figures.
    assert (legacy["engine_version"], legacy["input_version"]) == (1, 1)
    assert legacy["run"]["current_engine"] is False
    assert legacy["rows"] == original["rows"]
    assert all(r["debits"] == [] and r["paid_personally"] == "0.00" for r in legacy["rows"])
    assert all(c["debited"] == "0.00" for r in legacy["rows"] for c in r["categories"])

    again = sample.post(f"/api/projection/runs/{legacy_id}/rerun")
    assert again.status_code == 201, again.text
    new = again.json()
    assert (new["engine_version"], new["current_engine"]) == (engine.ENGINE_VERSION, True)
    redone = detail(sample, new["id"])
    assert (redone["engine_version"], redone["input_version"]) == (2, 2)
    assert redone["rows"] == legacy["rows"] and redone["sustainable"] == legacy["sustainable"]
    with factory_of(sample)() as session:
        a, b = (session.get(ProjectionRun, i) for i in (legacy_id, new["id"]))
        assert a is not None and b is not None
        assert (a.engine_version, a.input_version) == (1, 1)  # the old row is untouched
        assert (b.engine_version, b.input_version) == (2, 2)
        assert '"debits":[]' in b.input_json and '"input_version":2' in b.input_json


def test_errors_keep_the_shared_shape(sample: TestClient) -> None:
    assert sample.get("/api/projection/runs/9999").status_code == 404
    assert sample.post("/api/projection/runs/9999/rerun").status_code == 404
    missing = sample.post("/api/projection/runs", json={"scenario_id": 9999})
    assert missing.status_code == 404
    long = sample.post(
        "/api/projection/runs",
        json={"scenario_id": scenario_id(sample, "Likely"), "label": "x" * 101},
    )
    assert long.status_code == 422
    assert long.json()["errors"][0]["field"] == "label"
    with factory_of(sample)() as session:
        assert session.scalar(select(func.count()).select_from(ProjectionRun)) == 0
